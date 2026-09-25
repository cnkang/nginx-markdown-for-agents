#!/usr/bin/env python3
"""Fuzz qualification gate validator.

Real mode runs every blocking fuzz target with the pinned
``cargo +nightly-YYYY-MM-DD fuzz``.
until BOTH floors from the blocking-fuzz-target manifest are met:
elapsed time >= required_minutes * 60 AND executed units >=
required_executions (libFuzzer stops at whichever limit it hits first,
so the later of the two limits is chased with follow-up invocations).
Each run uses the fixed seed recorded in the manifest, requires zero
crashes and zero sanitizer findings, preserves the target corpus and the
raw libFuzzer log, and writes a qualification record for release evidence.

Fixture mode validates a pre-made qualification record against the same
threshold semantics, rejecting it with an identifiable reason:

  - malformed         record is not JSON or lacks required structure
  - stale-digest      record candidate_sha differs from the manifest
  - below-threshold   elapsed or executions below the manifest floors
  - blocking-pending  a blocking target status is not pass
  - missing-observation  per-target observations are incomplete

Exit codes:
  0 = qualification passed, or skipped via RELEASE_GATE_ALLOW_SKIP_FUZZ=1
  1 = qualification failed or could not be established
"""


from __future__ import annotations

import argparse
import codecs
import contextlib
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from lib.path_validation import (  # noqa: E402
    validate_filename_strict,
    validate_read_path,
    validate_write_path_within_root,
)
from lib.executable_validation import (  # noqa: E402
    resolve_approved_executable,
    resolve_rustup_tool_shim,
)

SCHEMA_VERSION = "release.fuzz-qualification.v2"
CORPUS_ROOT = REPO_ROOT / "components" / "rust-converter" / "fuzz" / "corpus"
FUZZ_CRATE_DIR = REPO_ROOT / "components" / "rust-converter"
FUZZ_TOOLCHAIN = "nightly-2026-09-21"
FUZZ_CARGO_FUZZ_PACKAGE_VERSION = "0.13.1"
_EXPECTED_FUZZ_TOOLCHAIN_IDENTITY = {
    "rustup_toolchain": FUZZ_TOOLCHAIN,
    "rustc_version": "rustc 1.100.0-nightly (bba531001 2026-09-20)",
    "rustc_commit_hash": "bba531001d4de6d7f49693e0836a2668ca063282",
    "rustc_commit_date": "2026-09-20",
    "rustc_host": "x86_64-unknown-linux-gnu",
    "rustc_release": "1.100.0-nightly",
    "llvm_version": "23.1.1",
    "cargo_version": "cargo 1.100.0-nightly (495c385d0 2026-09-16)",
    "cargo_fuzz_version": "cargo-fuzz 0.13.1",
}


def _cargo_package_version(cargo_toml: Path | None = None) -> str:
    """Read a literal or workspace-inherited Cargo package version."""
    cargo_toml = cargo_toml or FUZZ_CRATE_DIR / "Cargo.toml"
    try:
        cargo_toml = validate_read_path(cargo_toml, purpose="Cargo manifest")
        with cargo_toml.open("rb") as cargo_file:
            document = tomllib.load(cargo_file)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid Cargo manifest {cargo_toml}: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read Cargo manifest {cargo_toml}: {exc}") from exc

    package = document.get("package")
    version = package.get("version") if isinstance(package, dict) else None
    if isinstance(version, dict) and version.get("workspace") is True:
        workspace = document.get("workspace")
        workspace_package = (
            workspace.get("package") if isinstance(workspace, dict) else None
        )
        version = (
            workspace_package.get("version")
            if isinstance(workspace_package, dict)
            else None
        )
    if not isinstance(version, str) or not version:
        raise ValueError(f"package version not found in {cargo_toml}")
    return version


def _release_artifact_paths(version: str) -> dict[str, str]:
    """Build release artifact defaults for any valid Cargo package version."""
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ValueError(f"invalid Cargo package version: {version!r}")
    root = Path("artifacts") / "release" / version
    return {
        "manifest": (root / "blocking-fuzz-target-manifest.json").as_posix(),
        "corpus_manifest": (root / "corpus-seed-manifest.json").as_posix(),
        "record": (root / "fuzz-qualification-record.json").as_posix(),
        "log_dir": (root / "fuzz-logs").as_posix(),
    }


def _default_artifact_paths() -> dict[str, str]:
    """Resolve release artifact defaults only when a CLI path needs them."""
    return _release_artifact_paths(_cargo_package_version())

SKIP_ENV = "RELEASE_GATE_ALLOW_SKIP_FUZZ"
TIME_CONTINUATION_CEILING = 3600
# Total time the executions-floor chase may spend per target.  Bounding the
# chase keeps a pathologically slow target from consuming the release job's
# budget until CI kills the job before the validator can emit its record.
# Sized from the slowest blocking target: the multilayer decode fuzzer
# measures on the order of ten executions per second on the CI runner
# (observed: 9717 executions in its 900-second soak), so 100k executions
# need roughly 9100 seconds of chase beyond the 900-second soak; the budget
# must cover that plus one full invocation margin, with headroom for
# runner-to-runner rate variance (the arithmetic test pins the supported
# floor).
TIME_CONTINUATION_BUDGET = 16000
# Shared fuzz envelope for the whole real-mode run.  The per-target budgets
# multiply across the fourteen blocking targets, so a single monotonic
# deadline bounds the whole fuzz phase; exhausted targets fail fast with a
# reason and the record is still emitted instead of CI killing the job
# before it exists.
#
# The fuzz soak runs in its own job (the release workflow's
# fuzz-qualification job), so the whole 360-minute job budget belongs to
# it.  The envelope is one term of a job-limit equation asserted by unit
# test:
#   SETUP_ALLOWANCE + FUZZ_JOB_BUDGET + INVOCATION_TIMEOUT_MARGIN
#   + REPLAY_ALLOWANCE <= RELEASE_JOB_LIMIT
# The blocking targets run on a small worker pool: a strictly serial
# schedule cannot fit the slow decode target's executions chase next to the
# other targets' soaks at the measured CI execution rate (the first real
# run failed exactly there).  With three workers the chase overlaps the
# fast targets' soaks, and the worst case -- the slow target scheduled
# behind four fast soaks on its worker -- stays inside the envelope for any
# sustained slow-target rate at or above the floor pinned by the arithmetic
# test, comfortably below the measured band.  The remainder of the job
# limit after the equation below covers post-envelope work --
# qualification-record writing, the diagnostic artifact upload, and retry
# variance in the setup steps -- so the budget leaves roughly a quarter
# hour of that slack.
FUZZ_JOB_BUDGET = 19300
# Blocking targets run on this many concurrent workers inside the fuzz job.
# The runner has four cores: three fuzz processes leave headroom for the
# replay and shutdown work of a finishing invocation, and the measured
# single-process execution rate holds without CPU contention.  Memory is
# the other half of the budget: three concurrent ASan fuzzers times their
# per-process footprint stay inside the runner's 16 GB (each invocation's
# streams are drained into the head/tail-bounded captures of
# `_BoundedStream`, so a chatty run cannot grow the captured output
# without bound).
TARGET_WORKER_COUNT = 3
# Terms of the job-limit equation above.  The setup allowance covers the
# toolchain install steps before the first soak; the replay allowance
# bounds the corpus replay plus shutdown of the single invocation that may
# be running when the envelope expires.
RELEASE_JOB_LIMIT_SECONDS = 21600
SETUP_ALLOWANCE_SECONDS = 420
REPLAY_ALLOWANCE_SECONDS = 120
# Floor for the post-envelope reserve (record writing, the diagnostic
# artifact upload, setup retry variance).  The budget equation above only
# bounds the total from above; this floor is asserted separately by unit
# test so a later increase of FUZZ_JOB_BUDGET cannot silently consume the
# reserve that the previous shared-job layout lacked.
MIN_POST_ENVELOPE_RESERVE_SECONDS = 800
MAX_FUZZ_INVOCATIONS = 8
# Subprocess margin over the fuzzer's own time cap: it covers process
# startup, corpus replay (which -max_total_time does not count) and the
# shutdown stats dump.  A chase invocation's cap is reduced by this margin
# so its total wall-clock allowance never exceeds the remaining
# continuation budget, while the allowance always exceeds the cap itself.
# A single invocation may therefore overshoot the shared deadline by up to
# this margin plus its replay time; that tolerance is absorbed by the
# slack between FUZZ_JOB_BUDGET and the release job's 360-minute cap.
INVOCATION_TIMEOUT_MARGIN = 900
BLOCKING_FUZZ_TARGET_MANIFEST_LABEL = "blocking-fuzz-target manifest"
FUZZ_TARGET_LABEL = "fuzz target"
RECORD_OUTPUT_LABEL = "fuzz qualification record"

CANDIDATE_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
STAT_EXECS_PATTERN = re.compile(r"stat::number_of_executed_units:\s*(\d+)")
STAT_ELAPSED_PATTERN = re.compile(r"stat::elapsed_seconds:\s*([\d.]+)")
DONE_RUNS_PATTERN = re.compile(r"Done\s+(\d+)\s+runs?\s+in\s+([\d.]+)\s+second")
_FAILURE_MARKER_TEXTS = (
    "ERROR: libFuzzer",
    "==ERROR: AddressSanitizer",
    "SUMMARY: AddressSanitizer",
    "SUMMARY: UndefinedBehaviorSanitizer",
    "runtime error:",
)
FAILURE_MARKER_PATTERN = re.compile("|".join(_FAILURE_MARKER_TEXTS))
_MARKER_SCAN_OVERLAP_CHARS = max(map(len, _FAILURE_MARKER_TEXTS)) - 1

REQUIRED_TARGET_FIELDS = ("name", "seed", "required_minutes",
                          "required_executions", "blocking")
REQUIRED_SEED_FIELDS = ("target", "seed_path", "digest")
OBSERVATION_FIELDS = ("elapsed_seconds_total", "executions_total", "crashes",
                      "sanitizer_findings")
PER_TARGET_IDENTITY_FIELDS = ("target", "seed", "corpus_dir", "seed_path", "raw_log_ref",
                              "status")

# (field, kind, positive, non-empty, expected description)
TARGET_FIELD_SPECS = (
    ("name", str, False, True, "a non-empty string"),
    ("seed", int, False, False, "an integer"),
    ("required_minutes", (int, float), True, False, "a positive number"),
    ("required_executions", int, True, False, "a positive integer"),
    ("blocking", bool, False, False, "a boolean"),
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Return the CLI parser for the fuzz qualification gate."""
    parser = argparse.ArgumentParser(
        description="Validate fuzz qualification evidence for blocking targets")
    parser.add_argument("--mode", choices=("real", "fixture"), default="real")
    parser.add_argument("--manifest")
    parser.add_argument("--corpus-manifest")
    parser.add_argument("--record")
    parser.add_argument("--record-input",
                        help="fixture mode: qualification record to validate")
    parser.add_argument(
        "--output",
        help=(
            "real mode: compatibility option; output is always written to "
            "the versioned canonical record path, so the supplied path must "
            "equal DEFAULT_RECORD (validated by _write_record)"
        ),
    )
    parser.add_argument("--allow-skip-fuzz", action="store_true",
                        help="exit 0 when the pinned fuzz toolchain is unavailable")
    parser.add_argument(
        "--git-head",
        action="store_true",
        help="require the candidate manifest SHA to equal git HEAD",
    )
    return parser


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git_head_sha() -> str:
    """Return the checked-out commit SHA used for candidate binding."""
    git = resolve_approved_executable("git")
    if git is None:
        raise ValueError("unable to resolve git HEAD: approved git not found")
    try:
        result = subprocess.run(
            [git, "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"unable to resolve git HEAD: {exc}") from exc
    if result.returncode != 0:
        raise ValueError(f"unable to resolve git HEAD: {result.stderr.strip()}")
    return result.stdout.strip()


def _run_id_from(started_at: str) -> str:
    """Derive a timestamp-based run id from the ISO-8601 start time."""
    return "fuzz-qualification-" + started_at.replace(":", "").replace("+00:00", "Z")


def load_json(path: str | Path, label: str) -> dict:
    """Load a JSON object, failing closed with a malformed reason."""
    validated_path = validate_read_path(path, purpose=label)
    try:
        data = json.loads(validated_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"malformed: unable to read {label} {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"malformed: {label} must be a JSON object")
    return data


def _check_candidate_sha(value, label: str) -> str | None:
    """Return an error string when the candidate sha is not 40 lowercase hex."""
    if not isinstance(value, str) or not CANDIDATE_SHA_PATTERN.fullmatch(value):
        return f"malformed: {label} candidate_sha must be 40 lowercase hex"
    return None


def _validate_scalar(value, kind, positive: bool, non_empty: bool) -> bool:
    """Return whether a scalar manifest value has the expected shape.

    Booleans are excluded from integer/number kinds so ``True`` cannot
    masquerade as a seed or execution count.
    """
    if not isinstance(value, kind):
        return False
    if isinstance(value, bool) and kind is not bool:
        return False
    return False if non_empty and not value else not positive or value > 0


def _validate_target_entry(entry, index: int) -> str | None:
    """Return an error string when a manifest target entry is malformed."""
    if not isinstance(entry, dict):
        return f"malformed: targets[{index}] must be an object"
    if missing := [
        field for field in REQUIRED_TARGET_FIELDS if field not in entry
    ]:
        return (f"malformed: targets[{index}] missing fields: "
                + ", ".join(missing))
    for field, kind, positive, non_empty, description in TARGET_FIELD_SPECS:
        if _validate_scalar(entry[field], kind, positive, non_empty):
            continue
        return (f"malformed: targets[{index}].{field} must be "
                f"{description}")
    try:
        validate_filename_strict(entry["name"], purpose=FUZZ_TARGET_LABEL)
    except ValueError as exc:
        return f"malformed: targets[{index}].name is invalid: {exc}"
    return None


def validate_target_manifest(data: dict) -> list[dict]:
    """Validate the blocking-fuzz-target manifest, returning target entries."""
    if not isinstance(data.get("schema_version"), str):
        raise ValueError("malformed: manifest schema_version must be a string")
    if error := _check_candidate_sha(
        data.get("candidate_sha"), BLOCKING_FUZZ_TARGET_MANIFEST_LABEL
    ):
        raise ValueError(error)
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("malformed: manifest targets must be a non-empty array")
    errors = []
    for index, entry in enumerate(targets):
        if error := _validate_target_entry(entry, index):
            errors.append(error)
    if errors:
        raise ValueError("; ".join(errors))
    seen = set()
    duplicates = []
    for entry in targets:
        name = entry["name"]
        if name in seen:
            duplicates.append(name)
        seen.add(name)
    if duplicates:
        raise ValueError("malformed: duplicate target names: "
                         + ", ".join(sorted(set(duplicates))))
    return targets


def _validate_seed_path(seed_path, target: str) -> str | None:
    """Return an error string when a seed path is outside the corpus or absent."""
    if not isinstance(seed_path, str) or not seed_path:
        return f"malformed: seed_path for {target} must be a non-empty string"
    path = validate_read_path(
        REPO_ROOT / seed_path,
        must_exist=False,
        purpose=f"seed corpus for {target}",
    )
    corpus_resolved = CORPUS_ROOT.resolve()
    try:
        path.relative_to(corpus_resolved)
    except ValueError:
        return f"malformed: seed_path for {target} escapes the corpus root"
    if not path.exists():
        return f"seed corpus for {target} does not exist on disk: {path}"
    return None


def _validate_seed_digest(entry: dict, target: str) -> str | None:
    """Return an error string when the seed file content mismatches its
    manifest digest.

    Verifying only path and existence lets a seed file drift from the
    digest recorded in the manifest; the content check fails closed so a
    modified or replaced seed is rejected before fuzzing runs.
    """
    seed_path = entry.get("seed_path")
    manifest_digest = entry.get("digest")
    if not isinstance(seed_path, str) or not isinstance(manifest_digest, str):
        return None  # shape errors are reported by _validate_seed_entry
    if not manifest_digest.startswith("sha256:"):
        return (f"malformed: seed digest for {target} must use the "
                "sha256: prefix")
    try:
        validated_seed_path = validate_read_path(
            REPO_ROOT / seed_path,
            purpose=f"seed corpus for {target}",
        )
        validated_seed_path.relative_to(CORPUS_ROOT.resolve())
        raw = validated_seed_path.read_bytes()
    except (OSError, ValueError) as exc:
        return f"seed corpus for {target} is unreadable: {exc}"
    actual = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    if actual != manifest_digest:
        return (f"stale-digest: seed corpus for {target} content does not "
                f"match the manifest digest ({manifest_digest[:16]}... != "
                f"{actual[:16]}...)")
    return None


def _validate_seed_entry(entry, index: int) -> str | None:
    """Return an error string when a seed manifest entry is malformed."""
    if not isinstance(entry, dict):
        return f"malformed: seeds[{index}] must be an object"
    if missing := [
        field for field in REQUIRED_SEED_FIELDS if field not in entry
    ]:
        return (f"malformed: seeds[{index}] missing fields: "
                + ", ".join(missing))
    return next(
        (
            f"malformed: seeds[{index}].{field} must be a non-empty string"
            for field in REQUIRED_SEED_FIELDS
            if not _validate_scalar(entry[field], str, False, True)
        ),
        None,
    )


def validate_corpus_seeds(data: dict, expected_sha: str,
                          blocking_names: set[str]) -> dict[str, dict]:
    """Validate the corpus-seed manifest and index seeds by target name."""
    if not isinstance(data.get("schema_version"), str):
        raise ValueError("malformed: corpus-seed manifest schema_version "
                         "must be a string")
    if data.get("candidate_sha") != expected_sha:
        raise ValueError("stale-digest: corpus-seed manifest candidate sha "
                         "mismatch")
    seeds = data.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        raise ValueError("malformed: corpus-seed manifest seeds must be "
                         "a non-empty array")
    by_target = {}
    for index, entry in enumerate(seeds):
        if error := _validate_seed_entry(entry, index):
            raise ValueError(error)
        target = entry["target"]
        if target in by_target:
            raise ValueError(f"duplicate corpus seed target: {target!r}")
        by_target[target] = entry
    if missing_seeds := sorted(blocking_names - set(by_target)):
        raise ValueError("blocking targets missing corpus seed entries: "
                         + ", ".join(missing_seeds))
    for name in blocking_names:
        if error := _validate_seed_path(by_target[name]["seed_path"], name):
            raise ValueError(error)
        if error := _validate_seed_digest(by_target[name], name):
            raise ValueError(error)
    return by_target


def _resolve_fuzz_cargo() -> str | None:
    """Cargo for pinned fuzz work: the Rustup shim, not the concrete binary.

    ``resolve_approved_executable`` returns the concrete active-toolchain
    binary, which rejects ``+toolchain`` directives; fuzz qualification needs
    the shim so Rustup resolves the pinned fuzz toolchain.
    """
    return resolve_rustup_tool_shim("cargo")


def _cargo_fuzz_available() -> bool:
    """Return whether the pinned fuzz Cargo toolchain can be invoked."""
    cargo = _resolve_fuzz_cargo()
    if cargo is None:
        return False
    try:
        result = subprocess.run(
            [cargo, f"+{FUZZ_TOOLCHAIN}", "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def validate_toolchain_identity(identity: object) -> list[str]:
    """Reject missing, malformed, or drifted fuzz toolchain provenance."""
    if not isinstance(identity, dict):
        return ["missing-observation: toolchain_identity must be an object"]
    reasons = []
    expected_fields = set(_EXPECTED_FUZZ_TOOLCHAIN_IDENTITY)
    for field in sorted(expected_fields):
        if field not in identity:
            reasons.append(
                f"missing-observation: toolchain_identity missing {field}")
        elif not isinstance(identity[field], str) or (
                identity[field] != _EXPECTED_FUZZ_TOOLCHAIN_IDENTITY[field]):
            reasons.append(
                f"malformed: toolchain_identity {field} does not match the "
                "pinned fuzz toolchain")
    if set(identity) - expected_fields:
        reasons.append("malformed: toolchain_identity has unexpected fields")
    return reasons


def _run_toolchain_version_command(command: list[str], label: str) -> str:
    """Return bounded stdout from a toolchain identity command."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(
            f"unable to collect fuzz toolchain identity for {label}") from exc
    if result.returncode != 0:
        raise ValueError(
            f"unable to collect fuzz toolchain identity for {label}")
    output = result.stdout.strip()
    if not output or len(output) > 4096:
        raise ValueError(
            f"invalid fuzz toolchain identity output for {label}")
    return output


def _collect_fuzz_toolchain_identity() -> dict:
    """Capture and pin-check the exact compiler and cargo-fuzz versions."""
    if os.environ.get("FUZZ_TOOLCHAIN") != FUZZ_TOOLCHAIN:
        raise ValueError("FUZZ_TOOLCHAIN does not match the pinned toolchain")
    if os.environ.get("RUSTUP_TOOLCHAIN") != FUZZ_TOOLCHAIN:
        raise ValueError("RUSTUP_TOOLCHAIN does not match the pinned toolchain")
    cargo = _resolve_fuzz_cargo()
    rustc = resolve_rustup_tool_shim("rustc")
    if cargo is None or rustc is None:
        raise ValueError("Rustup cargo/rustc shims are unavailable")

    rustc_output = _run_toolchain_version_command(
        [rustc, f"+{FUZZ_TOOLCHAIN}", "-vV"], "rustc -vV")
    rustc_lines = rustc_output.splitlines()
    rustc_fields = {}
    if rustc_lines and rustc_lines[0].startswith("rustc "):
        rustc_fields["rustc_version"] = rustc_lines[0]
    for line in rustc_lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            rustc_fields[key.strip()] = value.strip()
    identity = {
        "rustup_toolchain": FUZZ_TOOLCHAIN,
        "rustc_version": rustc_fields.get("rustc_version"),
        "rustc_commit_hash": rustc_fields.get("commit-hash"),
        "rustc_commit_date": rustc_fields.get("commit-date"),
        "rustc_host": rustc_fields.get("host"),
        "rustc_release": rustc_fields.get("release"),
        "llvm_version": rustc_fields.get("LLVM version"),
        "cargo_version": _run_toolchain_version_command(
            [cargo, f"+{FUZZ_TOOLCHAIN}", "--version"], "cargo --version"),
        "cargo_fuzz_version": _run_toolchain_version_command(
            [cargo, f"+{FUZZ_TOOLCHAIN}", "fuzz", "--version"],
            "cargo fuzz --version"),
    }
    if reasons := validate_toolchain_identity(identity):
        raise ValueError("; ".join(reasons))
    return identity


# Cap on the captured output a single fuzz invocation may retain.  The
# runner holds three concurrent workers; a crash-heavy invocation or a
# chatty progress stream must not grow the captured output without bound
# inside the 16 GB runner budget.  Head and tail stay (the startup banner
# is head, the stats dump and crash reports are tail), and the elision is
# marked so post-mortem readers know bytes were dropped.  The retained
# text is also what the statistics parse consumes, while the failure
# marker is matched on every line as it streams in (`_BoundedStream`
# keeps bounded marker evidence), so a crash past the cap still fails the
# target instead of classifying as a pass.
_MAX_CAPTURE_CHARS = 4_000_000
_ELISION_TEMPLATE = "\n[... {dropped} chars elided by the capture cap ...]\n"
# Upper bound on the retained marker evidence; a bounded slice keeps a
# pathological marker line from growing the evidence buffer.
_MAX_MARKER_EVIDENCE_CHARS = 400
# Pipe read size, the cap on a line still awaiting its newline, and the
# post-exit reader join grace (see _join_readers).  A writer that never
# emits a newline must not grow the carry-over buffer without bound, so a
# longer partial line is flushed through the same bounded retention as a
# complete line (real fuzzer lines are orders of magnitude shorter).
_STREAM_READ_BYTES = 1 << 16
_MAX_PENDING_LINE_CHARS = 1 << 20
_STREAM_JOIN_GRACE_SECONDS = 30
_PROCESS_TERMINATION_GRACE_SECONDS = 1.0
_PROCESS_KILL_REAP_SECONDS = 1.0
_ACTIVE_FUZZ_PROCESSES: set[subprocess.Popen] = set()
_ACTIVE_FUZZ_PROCESSES_LOCK = threading.Lock()
_FUZZ_CANCEL_REQUESTED = threading.Event()
# Grace for the interrupt cleanup join: long enough for workers to reach
# their next queue boundary and for the in-flight invocation's readers to
# drain, short enough that an interrupt is not held up indefinitely.
_INTERRUPT_JOIN_GRACE_SECONDS = 30


def _bounded_capture(text: str) -> str:
    """Keep the head and tail of a captured stream within the cap."""
    if len(text) <= _MAX_CAPTURE_CHARS:
        return text
    keep = _MAX_CAPTURE_CHARS // 2
    dropped = len(text) - 2 * keep
    return text[:keep] + _ELISION_TEMPLATE.format(dropped=dropped) + text[-keep:]


def _split_lines(chunk: str) -> tuple[list[str], str]:
    """Split a chunk into complete lines plus a trailing partial line."""
    segments = chunk.split("\n")
    partial = segments.pop()
    return [segment + "\n" for segment in segments], partial


def _marker_evidence_text(text: str, marker: re.Match[str]) -> str:
    """Keep bounded context around a matched marker, including its signature."""
    line_start = text.rfind("\n", 0, marker.start()) + 1
    line_end = text.find("\n", marker.end())
    if line_end < 0:
        line_end = len(text)
    prefix = f"{marker.group(0)}: "
    context_limit = max(0, _MAX_MARKER_EVIDENCE_CHARS - len(prefix))
    line = text[line_start:line_end].strip()
    return prefix + line[-context_limit:] if context_limit else prefix


class _BoundedStream:
    """Drain one subprocess stream, retaining head, tail and failure markers.

    ``subprocess.run(capture_output=True)`` buffers the whole stream in
    memory before any cap can be applied, so a runaway fuzzer could grow
    the gate's RSS without bound.  Instead a reader thread consumes the
    pipe while the process runs and retains at most ``_MAX_CAPTURE_CHARS``
    characters: the first half is the head, the last half is a rolling
    tail (crash reports and the stats dump land there), and the middle is
    dropped and counted -- the same head/elision/tail shape
    ``_bounded_capture`` gives the full text, so the stored output is
    unchanged.  Every line is matched against the failure marker pattern
    on the way through, because a marker inside the dropped middle must
    still fail the target instead of classifying as a pass; the bounded
    marker evidence (``marker_finding``) is the marker text plus the line
    that carries it.  One reader thread feeds one instance; the small lock
    only covers the case where a reader outlives the post-exit join grace
    (a killed process's child still holding the pipe) and the caller reads
    the retained result while that reader is still appending to it.
    """

    def __init__(self) -> None:
        self._head_limit = _MAX_CAPTURE_CHARS // 2
        self._tail_limit = _MAX_CAPTURE_CHARS - self._head_limit
        self._head: list[str] = []
        self._tail: deque[str] = deque()
        self._head_size = 0
        self._tail_size = 0
        self._total = 0
        self._pending = ""
        self._marker_evidence: str | None = None
        self._marker_scan_overlap = ""
        self._lock = threading.Lock()

    def feed(self, chunk: str) -> None:
        """Consume one decoded chunk, retaining head, tail and markers."""
        with self._lock:
            lines, partial = _split_lines(self._pending + chunk)
            if len(partial) > _MAX_PENDING_LINE_CHARS:
                # A line that never ends must not grow the carry-over
                # buffer without bound; flush it through the retention
                # instead and restart the carry-over.
                lines.append(partial)
                partial = ""
            self._pending = partial
            for line in lines:
                self._feed_segment(line)

    def finish(self) -> None:
        """Flush the trailing partial line once the writer closed the pipe."""
        with self._lock:
            if self._pending:
                segment, self._pending = self._pending, ""
                self._feed_segment(segment)

    def _feed_segment(self, segment: str) -> None:
        self._total += len(segment)
        if self._marker_evidence is None:
            combined = self._marker_scan_overlap + segment
            if marker := FAILURE_MARKER_PATTERN.search(combined):
                self._marker_evidence = _marker_evidence_text(combined, marker)
            if segment.endswith("\n"):
                self._marker_scan_overlap = ""
            else:
                self._marker_scan_overlap = combined[-_MARKER_SCAN_OVERLAP_CHARS:]
        self._retain(segment)

    def _retain(self, segment: str) -> None:
        """Keep the segment's head prefix, then roll it through the tail.

        Head and tail together retain exactly the first and last
        ``_MAX_CAPTURE_CHARS / 2`` characters of the stream, so
        ``text()`` reproduces ``_bounded_capture`` over the full text.
        Whole tail segments are evicted first; a partial front segment is
        trimmed only when it holds the excess, which keeps the retained
        size exact without re-scanning the deque.
        """
        if self._head_size < self._head_limit:
            room = self._head_limit - self._head_size
            self._head.append(segment[:room])
            self._head_size += min(len(segment), room)
            segment = segment[room:]
            if not segment:
                return
        self._tail.append(segment)
        self._tail_size += len(segment)
        while self._tail and self._tail_size - len(self._tail[0]) >= self._tail_limit:
            self._tail_size -= len(self._tail.popleft())
        if self._tail_size > self._tail_limit:
            excess = self._tail_size - self._tail_limit
            front = self._tail.popleft()
            self._tail.appendleft(front[excess:])
            self._tail_size -= excess

    def text(self) -> str:
        """Return the capped stream, eliding and counting the dropped middle."""
        with self._lock:
            dropped = self._total - self._head_size - self._tail_size
            if dropped <= 0:
                return "".join(self._head) + "".join(self._tail)
            return "".join(self._head) + _ELISION_TEMPLATE.format(
                dropped=dropped) + "".join(self._tail)

    def marker_finding(self) -> str | None:
        """Return the failure marker seen anywhere in the stream, if any."""
        with self._lock:
            return self._marker_evidence


def _drain_stream(pipe, stream: _BoundedStream) -> None:
    """Read one pipe to EOF into the bounded stream (reader-thread body).

    Chunks are decoded incrementally: a multi-byte character split across a
    read boundary must not become replacement characters, which is what
    ``subprocess``'s own text mode does for a buffered read.
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        for chunk in iter(lambda: pipe.read(_STREAM_READ_BYTES), b""):
            stream.feed(decoder.decode(chunk))
        stream.feed(decoder.decode(b"", final=True))
    finally:
        stream.finish()
        pipe.close()


def _join_readers(readers: list[threading.Thread]) -> None:
    """Join the stream readers, bounded by the post-exit flush grace.

    The process has already been reaped when this runs, so the readers
    only drain what the pipe still holds; the bound keeps a process that
    inherited the pipe (a fuzzer child outliving a killed cargo) from
    holding the gate open.  The readers are daemon threads, so one still
    draining after the grace cannot block interpreter exit either.
    """
    deadline = time.monotonic() + _STREAM_JOIN_GRACE_SECONDS
    for reader in readers:
        reader.join(max(0.0, deadline - time.monotonic()))


def _signal_fuzz_process_group(
    process: subprocess.Popen, signal_number: int
) -> None:
    """Signal the isolated fuzz invocation and any descendants it spawned."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal_number)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    if process.poll() is None:
        if signal_number == getattr(signal, "SIGTERM", None):
            process.terminate()
        else:
            process.kill()


def _terminate_fuzz_process_group(process: subprocess.Popen) -> None:
    """Bound TERM grace, then KILL every remaining process in the group."""
    _signal_fuzz_process_group(process, signal.SIGTERM)
    time.sleep(_PROCESS_TERMINATION_GRACE_SECONDS)
    _signal_fuzz_process_group(
        process, getattr(signal, "SIGKILL", signal.SIGTERM)
    )
    try:
        process.wait(timeout=_PROCESS_KILL_REAP_SECONDS)
    except subprocess.TimeoutExpired:
        # SIGKILL has been sent. Do not let a stuck kernel task hold the
        # release gate indefinitely while its asynchronous exit completes.
        pass


def _register_fuzz_process(process: subprocess.Popen) -> None:
    """Track a new process and close the interrupt-registration race."""
    with _ACTIVE_FUZZ_PROCESSES_LOCK:
        _ACTIVE_FUZZ_PROCESSES.add(process)
        cancel_requested = _FUZZ_CANCEL_REQUESTED.is_set()
    if cancel_requested:
        _terminate_fuzz_process_group(process)


def _unregister_fuzz_process(process: subprocess.Popen) -> None:
    """Remove a completed invocation from the cancellation registry."""
    with _ACTIVE_FUZZ_PROCESSES_LOCK:
        _ACTIVE_FUZZ_PROCESSES.discard(process)


def _cancel_active_fuzz_processes() -> None:
    """Terminate all active fuzz process groups together on parent interrupt."""
    with _ACTIVE_FUZZ_PROCESSES_LOCK:
        processes = list(_ACTIVE_FUZZ_PROCESSES)
    for process in processes:
        _signal_fuzz_process_group(process, signal.SIGTERM)
    if processes:
        time.sleep(_PROCESS_TERMINATION_GRACE_SECONDS)
    kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
    for process in processes:
        _signal_fuzz_process_group(process, kill_signal)


def _close_fuzz_process_pipes(process: subprocess.Popen) -> None:
    """Close pipes that have no reader or outlived the bounded reader join."""
    for pipe in (process.stdout, process.stderr):
        if pipe is None or pipe.closed:
            continue
        with contextlib.suppress(OSError):
            pipe.close()


def _invoke_fuzz(target: str, flags: list[str], timeout: float) -> dict:
    """Run one isolated cargo fuzz process group and capture its output."""
    cargo = _resolve_fuzz_cargo()
    started = time.monotonic()
    if cargo is None:
        return {"returncode": -1, "stdout": "",
                "stderr": "spawn failed: Rustup cargo shim not found",
                "wall_elapsed": time.monotonic() - started,
                "marker_finding": None}
    if _FUZZ_CANCEL_REQUESTED.is_set():
        return {"returncode": -1, "stdout": "",
                "stderr": "cancelled: parent interrupted",
                "wall_elapsed": time.monotonic() - started,
                "marker_finding": None}
    validated_target = validate_filename_strict(target, purpose=FUZZ_TARGET_LABEL)
    command = [
        cargo, f"+{FUZZ_TOOLCHAIN}", "fuzz", "run", validated_target,
        "--", *flags
    ]
    try:
        process = subprocess.Popen(
            command, cwd=FUZZ_CRATE_DIR, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        return {"returncode": -1, "stdout": "", "stderr": f"spawn failed: {exc}",
                "wall_elapsed": time.monotonic() - started,
                "marker_finding": None}
    _register_fuzz_process(process)
    stdout_stream, stderr_stream = _BoundedStream(), _BoundedStream()
    readers = [
        threading.Thread(target=_drain_stream, args=(pipe, stream), daemon=True)
        for pipe, stream in ((process.stdout, stdout_stream),
                             (process.stderr, stderr_stream))
    ]
    started_readers: list[threading.Thread] = []
    timed_out = False
    try:
        for reader in readers:
            reader.start()
            started_readers.append(reader)
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_fuzz_process_group(process)
    except KeyboardInterrupt:
        _FUZZ_CANCEL_REQUESTED.set()
        _terminate_fuzz_process_group(process)
        raise
    except BaseException:
        _terminate_fuzz_process_group(process)
        raise
    finally:
        try:
            _join_readers(started_readers)
        finally:
            _close_fuzz_process_pipes(process)
            _unregister_fuzz_process(process)
    result = {
        "stdout": stdout_stream.text(),
        "wall_elapsed": time.monotonic() - started,
        "marker_finding": (
            stdout_stream.marker_finding() or stderr_stream.marker_finding()),
    }
    if timed_out:
        exc = subprocess.TimeoutExpired(command, timeout)
        result["returncode"] = -1
        result["stderr"] = _bounded_capture(
            f"timed out: {exc}\n{stderr_stream.text()}")
        return result
    result["returncode"] = returncode
    result["stderr"] = stderr_stream.text()
    return result


def _parse_fuzz_output(stdout: str, stderr: str,
                       marker_finding: str | None = None
                       ) -> tuple[int, float, str | None]:
    """Extract executed units, elapsed seconds, and the first failure marker.

    Statistics come from the retained (capped) output; ``marker_finding``
    makes the marker check stream-aware.  A streamed failure marker must
    win even when its line was dropped into the elided middle of the
    capped text, because the crash that produced it is exactly what the
    target's status must report: a marker past the cap is otherwise
    invisible and the run classifies as a pass.  A call without
    ``marker_finding`` still parses the marker from the capped text, so
    direct callers keep working.
    """
    combined = stdout + "\n" + stderr
    match = STAT_EXECS_PATTERN.search(combined)
    executions = int(match.group(1)) if match else 0
    match = STAT_ELAPSED_PATTERN.search(combined)
    elapsed = float(match.group(1)) if match else 0.0
    if elapsed <= 0.0:
        if match := DONE_RUNS_PATTERN.search(combined):
            elapsed = float(match.group(2))
    finding = marker_finding
    if finding is None:
        if marker := FAILURE_MARKER_PATTERN.search(combined):
            finding = f"{marker.group(0)}: {_marker_line(combined, marker.start())}"
    return executions, elapsed, finding


def _marker_line(combined: str, start: int) -> str:
    """Return the full line containing a failure marker."""
    line_start = combined.rfind("\n", 0, start) + 1
    line_end = combined.find("\n", start)
    if line_end < 0:
        line_end = len(combined)
    return combined[line_start:line_end].strip()


def _classify_finding(finding: str) -> tuple[int, int]:
    """Return (crashes, sanitizer_findings) counts for a failure finding."""
    if not finding:
        return 0, 0
    if finding.startswith((
        "fuzz run failed with exit code",
        "fuzz run produced no statistics",
        "spawn failed:",
        "timed out:",
        "threshold not reached within",
        "fuzz job budget exhausted",
        "executions floor not reached within",
    )):
        return 0, 0
    if any(marker in finding for marker in (
            "AddressSanitizer", "UndefinedBehaviorSanitizer", "runtime error:")):
        return 0, 1
    return 1, 0


def _soak_outcome(invocation: dict) -> tuple[int, float, str | None]:
    """Return (executions, elapsed, failure) for one fuzz invocation.

    Statistics come from the retained stream text; the failure marker
    comes from the streaming scan (``marker_finding``) when present, so a
    marker anywhere in the invocation's output is seen even when its line
    lies in the elided middle of the capped text.
    """
    executions, elapsed, finding = _parse_fuzz_output(
        invocation["stdout"], invocation.get("stderr", ""),
        invocation.get("marker_finding"))
    if elapsed <= 0.0:
        elapsed = float(invocation.get("wall_elapsed", 0.0))
    # Infrastructure failures (timeout, spawn failure) take precedence
    # over any marker text: libFuzzer prints "ERROR: libFuzzer" on a
    # timeout, which must classify as an infrastructure failure with zero
    # crashes and zero sanitizer findings, not as a crash.
    if invocation["returncode"] == -1:
        stderr = invocation.get("stderr", "")
        if stderr.startswith("timed out:"):
            return executions, elapsed, "timed out: fuzz invocation exceeded its time cap"
        if "spawn failed:" in stderr:
            return executions, elapsed, "spawn failed: fuzz target could not be launched"
    if finding:
        return executions, elapsed, finding
    if invocation["returncode"] != 0:
        return executions, elapsed, (
            f"fuzz run failed with exit code {invocation['returncode']}")
    if executions == 0 and elapsed <= 0.0:
        return executions, elapsed, (
            "fuzz run produced no statistics; build may have failed")
    return executions, elapsed, None


def _startup_corpus_size(corpus_dir: Path) -> int:
    """Count seed inputs in the target corpus directory (0 when absent).

    libFuzzer replays every startup-corpus seed plus the empty-input
    callback before mutation begins; those executions are launch
    overhead, not mutation budget, and must not count toward the
    required-executions floor.
    """
    if not corpus_dir.is_dir():
        return 0
    return sum(bool(entry.is_file())
           for entry in corpus_dir.iterdir())


def _soak_invocation_schedule(
    seed: int,
    seconds_remaining: int,
    runs_remaining: int,
    startup_overhead: int,
    continuation_spent: float,
    deadline: float | None = None,
) -> tuple[list[str], int, str | None]:
    """Return (flags, time_cap, failure) for the next soak invocation.

    The soak-time floor is chased first, WITHOUT a runs cap: when both caps
    are present, libFuzzer stops at whichever trips first, and for fast
    targets the runs cap ends the invocation in about a second.  Those short
    bursts can then cancel out against the startup-corpus replay deduction,
    leaving runs_remaining -- and therefore the command -- unchanged across
    invocations: a fixed point that exhausts MAX_FUZZ_INVOCATIONS with
    neither floor met (observed live: every blocking target failed this
    way).

    Once the time floor is met, the executions floor is chased with the runs
    cap, bounded by the remaining continuation budget; the requested cap
    carries the startup-replay overhead so the mutation budget actually
    delivered still covers runs_remaining whichever executions the counter
    includes.  Sub-second leftovers must not produce -max_total_time=0
    (libFuzzer reads 0 as no time limit), so they fail instead.

    The shared job deadline, when present, bounds the invocation cap; a
    passed deadline fails the target immediately.
    """
    cap_limit = None
    if deadline is not None:
        cap_limit = int(deadline - time.monotonic())
        if cap_limit < 1:
            return [], 0, ("fuzz job budget exhausted before the floors "
                           "were met")
    if seconds_remaining > 0:
        time_cap = seconds_remaining
        if cap_limit is not None:
            time_cap = min(time_cap, cap_limit)
        flags = [f"-max_total_time={time_cap}", f"-seed={seed}",
                 "-print_final_stats=1"]
        return flags, time_cap, None
    remaining_budget = TIME_CONTINUATION_BUDGET - continuation_spent
    # The invocation's subprocess allowance (cap plus margin) must fit the
    # remaining continuation budget, so the cap reserves the margin.  A
    # remainder too small to host another invocation with its margin fails
    # the target instead of scheduling a doomed one.
    cap_max = int(remaining_budget) - INVOCATION_TIMEOUT_MARGIN
    if remaining_budget < 1 or cap_max < 1:
        return [], 0, ("executions floor not reached within the "
                       "continuation budget")
    time_cap = min(TIME_CONTINUATION_CEILING, cap_max)
    if cap_limit is not None:
        time_cap = min(time_cap, cap_limit)
    flags = [f"-runs={runs_remaining + startup_overhead}",
             f"-max_total_time={time_cap}",
             f"-seed={seed}", "-print_final_stats=1"]
    return flags, time_cap, None


def _soak_invocation_timeout(time_cap: int) -> int:
    """The subprocess timeout for one soak invocation.

    The fuzzer's own cap plus the startup/replay/shutdown margin.  The
    chase scheduler already reserves this margin inside the continuation
    budget when it sizes the cap, so the allowance always exceeds the cap
    (a cap without room for the margin is never scheduled) and never
    exceeds the remaining budget.
    """
    return time_cap + INVOCATION_TIMEOUT_MARGIN
def _account_soak_invocation(
    invocation: dict, startup_overhead: int, seconds_remaining: int
) -> tuple[int, float, float, str | None]:
    """Credit one invocation: (executions, elapsed, wall charge, failure).

    The empty-input callback and the startup-corpus replay do not count
    toward the mutation budget: they are launch overhead repeated on every
    invocation, and startup_overhead is captured immediately before the
    invocation so corpus growth is included in the deduction.  The
    continuation budget is a strict wall-clock bound, so chase invocations
    are charged their measured wall time, not the parsed fuzz time.
    """
    executions, elapsed, failure = _soak_outcome(invocation)
    executions = max(0, executions - startup_overhead)
    charge = 0.0
    if seconds_remaining == 0:
        charge = float(invocation.get("wall_elapsed") or elapsed)
    return executions, elapsed, charge, failure


def _run_target_soak(target: str, seed: int, required_executions: int,
                     required_seconds: int, log_path: Path,
                     deadline: float | None = None) -> dict:
    """Run libFuzzer until both floors are met or a finding terminates the run.

    libFuzzer stops at whichever of -runs / -max_total_time it hits first;
    when only one floor is met, the remaining floor is chased with follow-up
    invocations until both are satisfied.
    """
    validated_target = validate_filename_strict(target, purpose=FUZZ_TARGET_LABEL)
    total_executions = 0
    total_elapsed = 0.0
    continuation_spent = 0.0
    log_parts = []
    failure = None
    target_corpus_dir = CORPUS_ROOT / validated_target
    for _index in range(MAX_FUZZ_INVOCATIONS):
        runs_remaining = max(0, required_executions - total_executions)
        seconds_remaining = max(
            0, int(math.ceil(required_seconds - total_elapsed)))
        if runs_remaining == 0 and seconds_remaining == 0:
            break
        # Measure the startup corpus immediately before THIS invocation:
        # libFuzzer replays every seed present at launch, so an earlier
        # invocation that added corpus files increases THIS invocation's
        # startup-replay overhead.  Re-measuring here (instead of once
        # before the loop) keeps both the deduction and the runs request
        # accurate.
        startup_overhead = _startup_corpus_size(target_corpus_dir) + 1
        flags, time_cap, schedule_failure = _soak_invocation_schedule(
            seed, seconds_remaining, runs_remaining, startup_overhead,
            continuation_spent, deadline,
        )
        if schedule_failure is not None:
            failure = schedule_failure
            break
        invocation = _invoke_fuzz(
            validated_target, flags,
            timeout=_soak_invocation_timeout(time_cap))
        log_parts.append(invocation["stdout"] + "\n" + invocation["stderr"])
        executions, elapsed, charge, failure = _account_soak_invocation(
            invocation, startup_overhead, seconds_remaining)
        total_executions += executions
        total_elapsed += elapsed
        continuation_spent += charge
        if failure:
            break
        if (total_executions >= required_executions
                and total_elapsed >= required_seconds):
            break
    else:
        failure = (f"threshold not reached within "
                   f"{MAX_FUZZ_INVOCATIONS} invocations")

    validated_log_path = validate_write_path_within_root(
        log_path, REPO_ROOT, purpose="fuzz raw log"
    )
    validated_log_path.parent.mkdir(parents=True, exist_ok=True)
    validated_log_path.write_text("\n".join(log_parts), encoding="utf-8")
    crashes, sanitizer_findings = _classify_finding(failure)
    status = "fail" if failure else "pass"
    return {
        "target": validated_target,
        "seed": seed,
        "elapsed_seconds_total": round(total_elapsed, 3),
        "executions_total": total_executions,
        "crashes": crashes,
        "sanitizer_findings": sanitizer_findings,
        "corpus_dir": str(CORPUS_ROOT / validated_target),
        "seed_path": "",
        "raw_log_ref": str(validated_log_path.relative_to(REPO_ROOT)),
        "status": status,
        "failure_reason": failure,
    }


def _skipped_record(entry: dict, reason: str) -> dict:
    """Return a per-target record entry for a target that was not run."""
    return {
        "target": entry["name"],
        "seed": entry["seed"],
        "elapsed_seconds_total": 0,
        "executions_total": 0,
        "crashes": 0,
        "sanitizer_findings": 0,
        "corpus_dir": str(CORPUS_ROOT / entry["name"]),
        "seed_path": "",
        "raw_log_ref": "",
        "status": "skipped",
        "skip_reason": reason,
    }


def _run_target_record(entry: dict, seed_path: str,
                       deadline: float | None = None) -> dict:
    """Run one blocking target and build its qualification record entry."""
    required_seconds = int(entry["required_minutes"] * 60)
    required_executions = int(entry["required_executions"])
    log_dir = _default_artifact_paths()["log_dir"]
    log_path = REPO_ROOT / log_dir / f"{entry['name']}.log"
    record = _run_target_soak(entry["name"], int(entry["seed"]),
                              required_executions, required_seconds, log_path,
                              deadline=deadline)
    record["seed_path"] = seed_path
    return record


def _compose_record(candidate_sha: str, blocking_names: set[str],
                    per_target: list[dict], started_at: str,
                    toolchain_identity: dict | None) -> dict:
    """Assemble the top-level qualification record from per-target entries."""
    blocking_entries = [entry for entry in per_target
                        if entry["target"] in blocking_names]
    failures = [entry for entry in blocking_entries
                if entry["status"] == "fail"]
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_sha": candidate_sha,
        "run_id": _run_id_from(started_at),
        "started_at": started_at,
        "finished_at": _utc_now(),
        "toolchain_identity": toolchain_identity,
        "per_target": per_target,
        "blocking_pass": not failures,
        "blocking_failures": [entry["target"] for entry in failures],
    }


def _atomic_write_record(path: Path, record: dict) -> None:
    """Write a complete JSON record beside its destination, then replace it."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(record, temporary, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _write_record(record: dict, args) -> Path:
    """Persist the qualification record at its one canonical artifact path."""
    canonical_record = _default_artifact_paths()["record"]
    requested = args.output or args.record or canonical_record
    requested_path = Path(requested)
    if not requested_path.is_absolute():
        # Relative inputs (including the DEFAULT_RECORD default) resolve
        # against the repository root, never the current working directory.
        requested_path = REPO_ROOT / requested_path
    requested_output = requested_path.resolve()
    expected_output = (REPO_ROOT / canonical_record).resolve()
    if requested_output != expected_output:
        raise ValueError(
            "Output path is fixed to "
            f"'{canonical_record}' for {RECORD_OUTPUT_LABEL}"
        )
    safe_name = validate_filename_strict(
        expected_output.name, purpose=RECORD_OUTPUT_LABEL
    )
    candidate_output = REPO_ROOT / Path(canonical_record).parent / safe_name
    resolved_candidate = candidate_output.resolve(strict=False)
    resolved_root = REPO_ROOT.resolve(strict=False)
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError(
            f"Refusing to write outside repository root: {resolved_candidate}"
        )
    validated_path = validate_write_path_within_root(
        resolved_candidate, REPO_ROOT, purpose=RECORD_OUTPUT_LABEL
    )
    validated_path.parent.mkdir(parents=True, exist_ok=True)
    # NOSONAR suppression for pythonsecurity:S2083: the fixed output root and
    # strict filename validation prevent CLI-selected targets.
    _atomic_write_record(validated_path, record)  # NOSONAR
    # SONAR_NOTE(S2083): Filename is allowlisted and the path is built from
    # the trusted generated-output root, so CLI input cannot select a target.
    return validated_path


def _print_target(record: dict) -> None:
    """Print one per-target PASS/FAIL/SKIP line to stdout."""
    status = record["status"]
    line = f"  [{status.upper()}] {record['target']}"
    if status == "pass":
        line += (f": {record['elapsed_seconds_total']}s elapsed, "
                 f"{record['executions_total']} execs")
    elif status == "skipped":
        line += f": {record['skip_reason']}"
    else:
        line += f": {record.get('failure_reason', 'failed')}"
    print(line)


def _handle_cargo_missing(args, candidate_sha: str,
                          targets: list[dict]) -> int:
    """Handle an unavailable pinned Cargo toolchain, honoring skip policy."""
    allow = args.allow_skip_fuzz or os.environ.get(SKIP_ENV) == "1"
    if not allow:
        print("ERROR: pinned fuzz Cargo toolchain unavailable; "
              "pass --allow-skip-fuzz or "
              f"set {SKIP_ENV}=1 to skip fuzz qualification", file=sys.stderr)
        return 1
    started_at = _utc_now()
    skip_reason = f"pinned fuzz toolchain unavailable ({SKIP_ENV}=1)"
    blocking_names = {entry["name"] for entry in targets if entry["blocking"]}
    per_target = [_skipped_record(entry, skip_reason) for entry in targets]
    record = _compose_record(candidate_sha, blocking_names, per_target,
                             started_at, None)
    record["blocking_pass"] = False
    record["skip_reason"] = skip_reason
    out_path = _write_record(record, args)
    for entry in per_target:
        _print_target(entry)
    print(f"WARNING: fuzz qualification skipped ({SKIP_ENV}=1); record "
          f"written to {out_path}", file=sys.stderr)
    return 0


def _handle_toolchain_identity_failure(args, candidate_sha: str,
                                       targets: list[dict],
                                       reason: str) -> int:
    """Persist a failed qualification when the pinned identity drifts."""
    started_at = _utc_now()
    blocking_names = {entry["name"] for entry in targets if entry["blocking"]}
    per_target = []
    for entry in targets:
        if entry["name"] in blocking_names:
            per_target.append({
                "target": entry["name"],
                "status": "fail",
                "failure_reason": "pinned fuzz toolchain identity failed",
            })
        else:
            per_target.append(_skipped_record(
                entry, "toolchain identity failed before fuzzing"))
    record = _compose_record(candidate_sha, blocking_names, per_target,
                             started_at, None)
    record["toolchain_error"] = "pinned fuzz toolchain identity failed"
    out_path = _write_record(record, args)
    for entry in per_target:
        _print_target(entry)
    print("ERROR: pinned fuzz toolchain identity verification failed: "
          f"{reason}; record written to {out_path}", file=sys.stderr)
    return 1


def _worker_queue(entries: list[dict], worker_count: int) -> list[list[dict]]:
    """Round-robin the entries into per-worker queues."""
    queues: list[list[dict]] = [[] for _ in range(worker_count)]
    for index, entry in enumerate(entries):
        queues[index % worker_count].append(entry)
    return queues


def _run_queue(
    queue: list[dict],
    seeds: dict,
    deadline: float,
    records: dict[str, dict],
    lock: threading.Lock,
    errors: list[BaseException],
    stop: threading.Event,
) -> None:
    """Run one worker's queue serially, recording errors and stopping peers.

    An interpreter-level exit (KeyboardInterrupt/SystemExit) is recorded
    for the parent's join-time re-raise and re-raised in-thread so the
    runtime keeps seeing it; any other exception is recorded and stops the
    sibling queues at their next boundary.
    """
    try:
        for entry in queue:
            if stop.is_set():
                return
            record = _run_target_record(
                entry, seeds[entry["name"]]["seed_path"], deadline=deadline)
            with lock:
                records[entry["name"]] = record
    except (KeyboardInterrupt, SystemExit) as exc:
        # Interpreter-level exits are recorded for the parent's join-time
        # re-raise and signal the siblings to stop; the re-raise here
        # keeps the exit visible to the runtime instead of swallowing it
        # inside the worker thread.
        with lock:
            errors.append(exc)
        stop.set()
        raise
    except Exception as exc:
        with lock:
            errors.append(exc)
        stop.set()


def _raise_worker_errors(errors: list[BaseException]) -> None:
    """Re-raise the first worker error after reporting every later one."""
    if not errors:
        return
    for extra in errors[1:]:
        print(f"WARNING: additional worker error: {extra!r}",
              file=sys.stderr)
    raise errors[0]


def _stop_and_join_workers(
    threads: list[threading.Thread], stop: threading.Event
) -> None:
    """Cancel active work and give attempted workers a bounded cleanup join."""
    stop.set()
    _FUZZ_CANCEL_REQUESTED.set()
    _cancel_active_fuzz_processes()
    for thread in threads:
        try:
            thread.join(_INTERRUPT_JOIN_GRACE_SECONDS)
        except RuntimeError:
            # A thread whose start itself was interrupted may never have
            # reached the started state, in which case join is invalid.
            continue


def _join_workers(threads: list[threading.Thread],
                  stop: threading.Event) -> None:
    """Join the worker threads, stopping siblings on an interrupt.

    A KeyboardInterrupt raised while the main thread waits here would
    otherwise propagate with the siblings still running: they would keep
    draining the fuzz envelope (and their in-flight invocations) after
    the gate has effectively been abandoned.  The interrupt is turned
    into the same cooperative stop the workers honor at their queue
    boundaries, the workers are given a bounded grace to unwind, and the
    interrupt is re-raised so the process still exits as interrupted.
    """
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        _stop_and_join_workers(threads, stop)
        raise


def _start_and_join_workers(
    threads: list[threading.Thread], stop: threading.Event
) -> None:
    """Start the pool, cleaning every attempted thread if startup aborts."""
    attempted: list[threading.Thread] = []
    try:
        for thread in threads:
            attempted.append(thread)
            thread.start()
    except BaseException:
        _stop_and_join_workers(attempted, stop)
        raise
    _join_workers(threads, stop)


def _run_blocking_targets(entries: list[dict], seeds: dict,
                          deadline: float) -> dict[str, dict]:
    """Run the blocking targets on a small worker pool.

    Each worker owns a disjoint queue of targets (round-robin over the
    manifest order) and runs its queue serially; the pool overlaps the slow
    decode target's executions chase with the fast targets' soaks, which a
    strictly serial schedule cannot fit into the shared job envelope at the
    measured CI execution rate.  The shared deadline bounds the phase, and
    an interrupted or failing worker stops its siblings at the next queue
    boundary instead of letting them drain the whole envelope; a
    KeyboardInterrupt raised while this function joins the workers sets the
    same stop event before re-raising, so an external stop also ends the
    queued entries.  All worker errors are reported, not just the first:
    records are returned keyed by target name, and any error re-raises the
    first exception after the join.
    """
    with _ACTIVE_FUZZ_PROCESSES_LOCK:
        if _ACTIVE_FUZZ_PROCESSES:
            raise RuntimeError("cannot start fuzz workers while a process group is active")
        _FUZZ_CANCEL_REQUESTED.clear()
    queues = _worker_queue(entries, TARGET_WORKER_COUNT)
    records: dict[str, dict] = {}
    lock = threading.Lock()
    errors: list[BaseException] = []
    stop = threading.Event()

    threads = [
        threading.Thread(
            target=_run_queue,
            args=(queue, seeds, deadline, records, lock, errors, stop),
            name=f"fuzz-worker-{index}",
        )
        for index, queue in enumerate(queues)
    ]
    _start_and_join_workers(threads, stop)
    _raise_worker_errors(errors)
    return records


def run_real_gate(args) -> int:
    """Run every blocking fuzz target and persist the qualification record."""
    manifest = load_json(args.manifest, BLOCKING_FUZZ_TARGET_MANIFEST_LABEL)
    targets = validate_target_manifest(manifest)
    blocking_names = {entry["name"] for entry in targets if entry["blocking"]}
    if not blocking_names:
        raise ValueError(f"{BLOCKING_FUZZ_TARGET_MANIFEST_LABEL} contains no "
                         "blocking targets")
    corpus_data = load_json(args.corpus_manifest, "corpus-seed manifest")
    seeds = validate_corpus_seeds(corpus_data, manifest["candidate_sha"],
                                  blocking_names)
    if getattr(args, "git_head", False):
        actual_head = _git_head_sha()
        if manifest["candidate_sha"] != actual_head:
            raise ValueError(
                "stale-digest: blocking fuzz manifest candidate_sha "
                f"{manifest['candidate_sha']} != git HEAD {actual_head}"
            )
    if not _cargo_fuzz_available():
        return _handle_cargo_missing(args, manifest["candidate_sha"], targets)

    try:
        toolchain_identity = _collect_fuzz_toolchain_identity()
    except ValueError as exc:
        return _handle_toolchain_identity_failure(
            args, manifest["candidate_sha"], targets, str(exc))

    started_at = _utc_now()
    fuzz_deadline = time.monotonic() + FUZZ_JOB_BUDGET
    blocking_entries = [entry for entry in targets
                        if entry["name"] in blocking_names]
    records_by_name = _run_blocking_targets(blocking_entries, seeds,
                                            fuzz_deadline)
    per_target = []
    for entry in targets:
        if entry["name"] in blocking_names:
            record = records_by_name[entry["name"]]
        else:
            record = _skipped_record(entry, "blocking=false (policy)")
        per_target.append(record)
        _print_target(record)
    record = _compose_record(manifest["candidate_sha"], blocking_names,
                             per_target, started_at, toolchain_identity)
    out_path = _write_record(record, args)
    if record["blocking_pass"]:
        print(f"PASS: fuzz qualification complete; record written to "
              f"{out_path}")
        return 0
    print("FAIL: blocking fuzz targets not qualified: "
          + ", ".join(record["blocking_failures"]))
    return 1


def _per_target_reasons(entry, index: int) -> list[str]:
    """Return fixture-mode reasons for one per-target record entry."""
    if not isinstance(entry, dict):
        return [f"malformed: per_target[{index}] must be an object"]
    reasons = []
    reasons.extend(
        f"missing-observation: per_target[{index}] missing {field}"
        for field in OBSERVATION_FIELDS
        if field not in entry
    )
    reasons.extend(
        f"malformed: per_target[{index}] missing {field}"
        for field in PER_TARGET_IDENTITY_FIELDS
        if field not in entry
    )
    return reasons


def _blocking_entry_reasons(spec: dict, entry: dict | None) -> list[str]:
    """Return status and threshold reasons for one blocking manifest entry."""
    name = spec["name"]
    if entry is None:
        return [f"blocking-pending: no record for blocking target {name}"]
    if entry.get("status") != "pass":
        return [f"blocking-pending: blocking target {name} status is "
                f"{entry.get('status')!r}"]
    for field in ("crashes", "sanitizer_findings"):
        value = entry.get(field)
        if type(value) is not int:
            return [f"malformed: {name} {field} must be an integer"]
        if value != 0:
            return [f"blocking-pending: blocking target {name} reports "
                    f"{field}={value}"]
    elapsed = entry.get("elapsed_seconds_total")
    executions = entry.get("executions_total")
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed):
        return [f"missing-observation: {name} elapsed_seconds_total "
                f"must be finite numeric"]
    if type(executions) is not int:
        return [f"missing-observation: {name} executions_total "
                f"not an integer"]
    reasons = []
    required_seconds = int(spec["required_minutes"] * 60)
    required_executions = int(spec["required_executions"])
    if elapsed < required_seconds:
        reasons.append(f"below-threshold: {name} elapsed_seconds_total "
                       f"{elapsed} < {required_seconds}")
    if executions < required_executions:
        reasons.append(f"below-threshold: {name} executions_total "
                       f"{executions} < {required_executions}")
    return reasons


def _blocking_set_reasons(record: dict, manifest: dict) -> list[str]:
    """Return reasons for missing, non-pass, or below-threshold blocking runs."""
    reasons = []
    by_name = {
        entry.get("target"): entry
        for entry in record.get("per_target", [])
        if isinstance(entry, dict)
    }
    for spec in manifest["targets"]:
        if spec["blocking"]:
            reasons.extend(_blocking_entry_reasons(
                spec, by_name.get(spec["name"])))
    return reasons


def validate_record(record: dict, manifest: dict) -> list[str]:
    """Validate a qualification record against manifest threshold semantics."""
    reasons = []
    if record.get("schema_version") != SCHEMA_VERSION:
        reasons.append(f"malformed: record schema_version "
                       f"{record.get('schema_version')!r} != {SCHEMA_VERSION!r}")
    if error := _check_candidate_sha(record.get("candidate_sha"), "record"):
        reasons.append(error)
    elif record["candidate_sha"] != manifest["candidate_sha"]:
        reasons.append("stale-digest: record candidate sha mismatch with "
                       "manifest")
    reasons.extend(validate_toolchain_identity(
        record.get("toolchain_identity")))
    per_target = record.get("per_target")
    if not isinstance(per_target, list):
        reasons.append("malformed: record per_target must be an array")
        return reasons
    for index, entry in enumerate(per_target):
        reasons.extend(_per_target_reasons(entry, index))
    reasons.extend(_blocking_set_reasons(record, manifest))
    return reasons


def run_fixture_gate(args) -> int:
    """Validate a pre-made qualification record against the manifest."""
    manifest = load_json(args.manifest, BLOCKING_FUZZ_TARGET_MANIFEST_LABEL)
    validate_target_manifest(manifest)
    if not args.record_input:
        raise ValueError("malformed: --record-input is required in "
                         "fixture mode")
    record = load_json(args.record_input, "fuzz-qualification record")
    if reasons := validate_record(record, manifest):
        for reason in reasons:
            print(f"ERROR: {reason}", file=sys.stderr)
        return 1
    print(f"PASS: fuzz qualification record {args.record_input} validated")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the fuzz qualification gate and return the process exit code.

    ``argv`` holds the CLI flags without the program name; when omitted the
    process ``sys.argv`` is used.
    """
    args = build_arg_parser().parse_args(argv)
    try:
        defaults = (
            _default_artifact_paths()
            if args.mode == "real" or args.manifest is None
            else {}
        )
        if args.manifest is None:
            args.manifest = str(REPO_ROOT / defaults["manifest"])
        if args.mode == "real":
            if args.corpus_manifest is None:
                args.corpus_manifest = str(
                    REPO_ROOT / defaults["corpus_manifest"]
                )
            if args.record is None:
                args.record = defaults["record"]
        if args.mode == "fixture":
            return run_fixture_gate(args)
        return run_real_gate(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
