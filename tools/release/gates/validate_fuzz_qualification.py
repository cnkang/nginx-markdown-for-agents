#!/usr/bin/env python3
"""Fuzz qualification gate validator.

Real mode runs every blocking fuzz target with ``cargo +nightly fuzz``
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
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from lib.path_validation import (  # noqa: E402
    validate_filename_strict,
    validate_read_path,
    validate_write_path_within_root,
)
from lib.executable_validation import resolve_approved_executable  # noqa: E402

SCHEMA_VERSION = "release.fuzz-qualification.v1"
DEFAULT_MANIFEST = "artifacts/release/0.9.2/blocking-fuzz-target-manifest.json"
DEFAULT_CORPUS_MANIFEST = "artifacts/release/0.9.2/corpus-seed-manifest.json"
DEFAULT_RECORD = "artifacts/release/0.9.2/fuzz-qualification-record.json"
DEFAULT_LOG_DIR = "artifacts/release/0.9.2/fuzz-logs"
CORPUS_ROOT = REPO_ROOT / "components" / "rust-converter" / "fuzz" / "corpus"
FUZZ_CRATE_DIR = REPO_ROOT / "components" / "rust-converter"

SKIP_ENV = "RELEASE_GATE_ALLOW_SKIP_FUZZ"
TIME_CONTINUATION_CEILING = 3600
MAX_FUZZ_INVOCATIONS = 8
INVOCATION_TIMEOUT_MARGIN = 900
BLOCKING_FUZZ_TARGET_MANIFEST_LABEL = "blocking-fuzz-target manifest"
FUZZ_TARGET_LABEL = "fuzz target"
RECORD_OUTPUT_LABEL = "fuzz qualification record"
RECORD_OUTPUT_ROOT = Path("artifacts/release/0.9.2")

CANDIDATE_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
STAT_EXECS_PATTERN = re.compile(r"stat::number_of_executed_units:\s*(\d+)")
STAT_ELAPSED_PATTERN = re.compile(r"stat::elapsed_seconds:\s*([\d.]+)")
DONE_RUNS_PATTERN = re.compile(r"Done\s+(\d+)\s+runs?\s+in\s+([\d.]+)\s+second")
FAILURE_MARKER_PATTERN = re.compile(
    r"ERROR: libFuzzer|==ERROR: AddressSanitizer|SUMMARY: AddressSanitizer"
    r"|SUMMARY: UndefinedBehaviorSanitizer|runtime error:")

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
    parser.add_argument("--manifest",
                        default=str(REPO_ROOT / DEFAULT_MANIFEST))
    parser.add_argument("--corpus-manifest",
                        default=str(REPO_ROOT / DEFAULT_CORPUS_MANIFEST))
    parser.add_argument("--record", default=DEFAULT_RECORD)
    parser.add_argument("--record-input",
                        help="fixture mode: qualification record to validate")
    parser.add_argument(
        "--output",
        help=(
            "real mode: compatibility option; output is always written to "
            "the canonical DEFAULT_RECORD path, so the supplied path must "
            "equal DEFAULT_RECORD (validated by _write_record)"
        ),
    )
    parser.add_argument("--allow-skip-fuzz", action="store_true",
                        help="exit 0 when cargo +nightly is unavailable")
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
    if non_empty and not value:
        return False
    if positive and value <= 0:
        return False
    return True


def _validate_target_entry(entry, index: int) -> str | None:
    """Return an error string when a manifest target entry is malformed."""
    if not isinstance(entry, dict):
        return f"malformed: targets[{index}] must be an object"
    missing = [field for field in REQUIRED_TARGET_FIELDS if field not in entry]
    if missing:
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
    error = _check_candidate_sha(
        data.get("candidate_sha"), BLOCKING_FUZZ_TARGET_MANIFEST_LABEL
    )
    if error:
        raise ValueError(error)
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("malformed: manifest targets must be a non-empty array")
    errors = []
    for index, entry in enumerate(targets):
        error = _validate_target_entry(entry, index)
        if error:
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


def _validate_seed_entry(entry, index: int) -> str | None:
    """Return an error string when a seed manifest entry is malformed."""
    if not isinstance(entry, dict):
        return f"malformed: seeds[{index}] must be an object"
    missing = [field for field in REQUIRED_SEED_FIELDS if field not in entry]
    if missing:
        return (f"malformed: seeds[{index}] missing fields: "
                + ", ".join(missing))
    for field in REQUIRED_SEED_FIELDS:
        if _validate_scalar(entry[field], str, False, True):
            continue
        return (f"malformed: seeds[{index}].{field} must be "
                f"a non-empty string")
    return None


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
        error = _validate_seed_entry(entry, index)
        if error:
            raise ValueError(error)
        target = entry["target"]
        if target in by_target:
            raise ValueError(f"duplicate corpus seed target: {target!r}")
        by_target[target] = entry
    missing_seeds = sorted(blocking_names - set(by_target))
    if missing_seeds:
        raise ValueError("blocking targets missing corpus seed entries: "
                         + ", ".join(missing_seeds))
    for name in blocking_names:
        error = _validate_seed_path(by_target[name]["seed_path"], name)
        if error:
            raise ValueError(error)
    return by_target


def _cargo_fuzz_available() -> bool:
    """Return whether the cargo +nightly toolchain can be invoked."""
    cargo = resolve_approved_executable("cargo")
    if cargo is None:
        return False
    try:
        result = subprocess.run(
            [cargo, "+nightly", "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _invoke_fuzz(target: str, flags: list[str], timeout: int) -> dict:
    """Run one cargo fuzz invocation, returning status and captured output."""
    cargo = resolve_approved_executable("cargo")
    if cargo is None:
        return {"returncode": -1, "stdout": "",
                "stderr": "spawn failed: cargo not found"}
    validated_target = validate_filename_strict(target, purpose=FUZZ_TARGET_LABEL)
    command = [
        cargo, "+nightly", "fuzz", "run", validated_target, "--", *flags
    ]
    started = time.monotonic()
    try:
        result = subprocess.run(
            command, cwd=FUZZ_CRATE_DIR, capture_output=True, text=True,
            timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return {"returncode": -1, "stdout": stdout,
                "stderr": f"timed out: {exc}\n{stderr}"}
    except OSError as exc:
        return {"returncode": -1, "stdout": "", "stderr": f"spawn failed: {exc}"}
    return {"returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
            "wall_elapsed": time.monotonic() - started}


def _marker_line(combined: str, start: int) -> str:
    """Return the full line containing a failure marker."""
    line_start = combined.rfind("\n", 0, start) + 1
    line_end = combined.find("\n", start)
    if line_end < 0:
        line_end = len(combined)
    return combined[line_start:line_end].strip()


def _parse_fuzz_output(stdout: str, stderr: str) -> tuple[int, float, str | None]:
    """Extract executed units, elapsed seconds, and the first failure marker."""
    combined = stdout + "\n" + stderr
    match = STAT_EXECS_PATTERN.search(combined)
    executions = int(match.group(1)) if match else 0
    match = STAT_ELAPSED_PATTERN.search(combined)
    elapsed = float(match.group(1)) if match else 0.0
    if elapsed <= 0.0:
        match = DONE_RUNS_PATTERN.search(combined)
        if match:
            elapsed = float(match.group(2))
    marker = FAILURE_MARKER_PATTERN.search(combined)
    finding = None
    if marker:
        finding = f"{marker.group(0)}: {_marker_line(combined, marker.start())}"
    return executions, elapsed, finding


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
    )):
        return 0, 0
    if any(marker in finding for marker in (
            "AddressSanitizer", "UndefinedBehaviorSanitizer", "runtime error:")):
        return 0, 1
    return 1, 0


def _soak_outcome(invocation: dict) -> tuple[int, float, str | None]:
    """Return (executions, elapsed, failure) for one fuzz invocation."""
    executions, elapsed, finding = _parse_fuzz_output(
        invocation["stdout"], invocation["stderr"])
    if elapsed <= 0.0:
        elapsed = float(invocation.get("wall_elapsed", 0.0))
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
    return sum(1 for entry in corpus_dir.iterdir() if entry.is_file())


def _run_target_soak(target: str, seed: int, required_executions: int,
                     required_seconds: int, log_path: Path) -> dict:
    """Run libFuzzer until both floors are met or a finding terminates the run.

    libFuzzer stops at whichever of -runs / -max_total_time it hits first;
    when only one floor is met, the remaining floor is chased with follow-up
    invocations until both are satisfied.
    """
    validated_target = validate_filename_strict(target, purpose=FUZZ_TARGET_LABEL)
    total_executions = 0
    total_elapsed = 0.0
    log_parts = []
    failure = None
    target_corpus_dir = CORPUS_ROOT / validated_target
    for _index in range(MAX_FUZZ_INVOCATIONS):
        runs_remaining = max(0, required_executions - total_executions)
        seconds_remaining = max(
            0, int(math.ceil(required_seconds - total_elapsed)))
        if runs_remaining == 0 and seconds_remaining == 0:
            break
        time_cap = (seconds_remaining if seconds_remaining > 0
                    else TIME_CONTINUATION_CEILING)
        flags = [f"-max_total_time={time_cap}", f"-seed={seed}",
                 "-print_final_stats=1"]
        if runs_remaining > 0:
            flags.insert(0, f"-runs={runs_remaining}")
        # Measure the startup corpus immediately before THIS invocation:
        # libFuzzer replays every seed present at launch, so an earlier
        # invocation that added corpus files increases THIS invocation's
        # startup-replay overhead.  Re-measuring here (instead of once
        # before the loop) keeps the deduction accurate (tools P3-4).
        startup_overhead = _startup_corpus_size(target_corpus_dir) + 1
        invocation = _invoke_fuzz(
            validated_target, flags, timeout=time_cap + INVOCATION_TIMEOUT_MARGIN)
        log_parts.append(invocation["stdout"] + "\n" + invocation["stderr"])
        executions, elapsed, failure = _soak_outcome(invocation)
        # Exclude the empty-input callback and the startup-corpus replay
        # from the mutation-budget accounting: they are launch overhead
        # repeated on every invocation, not new executions.  startup_overhead
        # was captured before the loop (tools P3-4) so corpus growth during
        # the run does not inflate the deduction.
        executions = max(0, executions - startup_overhead)
        total_executions += executions
        total_elapsed += elapsed
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


def _run_target_record(entry: dict, seed_path: str) -> dict:
    """Run one blocking target and build its qualification record entry."""
    required_seconds = int(entry["required_minutes"] * 60)
    required_executions = int(entry["required_executions"])
    log_path = REPO_ROOT / DEFAULT_LOG_DIR / f"{entry['name']}.log"
    record = _run_target_soak(entry["name"], int(entry["seed"]),
                              required_executions, required_seconds, log_path)
    record["seed_path"] = seed_path
    return record


def _compose_record(candidate_sha: str, blocking_names: set[str],
                    per_target: list[dict], started_at: str) -> dict:
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
        "per_target": per_target,
        "blocking_pass": len(failures) == 0,
        "blocking_failures": [entry["target"] for entry in failures],
    }


def _write_record(record: dict, args) -> Path:
    """Persist the qualification record at its one canonical artifact path."""
    requested = args.output if args.output else args.record
    requested_path = Path(requested)
    if not requested_path.is_absolute():
        # Relative inputs (including the DEFAULT_RECORD default) resolve
        # against the repository root, never the current working directory.
        requested_path = REPO_ROOT / requested_path
    requested_output = requested_path.resolve()
    expected_output = (REPO_ROOT / DEFAULT_RECORD).resolve()
    if requested_output != expected_output:
        raise ValueError(
            "Output path is fixed to "
            f"'{DEFAULT_RECORD}' for {RECORD_OUTPUT_LABEL}"
        )
    safe_name = validate_filename_strict(
        expected_output.name, purpose=RECORD_OUTPUT_LABEL
    )
    candidate_output = REPO_ROOT / RECORD_OUTPUT_ROOT / safe_name
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
    validated_path.write_text(  # NOSONAR
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
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
    """Handle an unavailable cargo +nightly, honoring the skip contract."""
    allow = args.allow_skip_fuzz or os.environ.get(SKIP_ENV) == "1"
    if not allow:
        print("ERROR: cargo +nightly unavailable; pass --allow-skip-fuzz or "
              f"set {SKIP_ENV}=1 to skip fuzz qualification", file=sys.stderr)
        return 1
    started_at = _utc_now()
    skip_reason = f"cargo +nightly unavailable ({SKIP_ENV}=1)"
    blocking_names = {entry["name"] for entry in targets if entry["blocking"]}
    per_target = [_skipped_record(entry, skip_reason) for entry in targets]
    record = _compose_record(candidate_sha, blocking_names, per_target,
                             started_at)
    record["blocking_pass"] = False
    record["skip_reason"] = skip_reason
    out_path = _write_record(record, args)
    for entry in per_target:
        _print_target(entry)
    print(f"WARNING: fuzz qualification skipped ({SKIP_ENV}=1); record "
          f"written to {out_path}", file=sys.stderr)
    return 0


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

    started_at = _utc_now()
    per_target = []
    for entry in targets:
        if entry["name"] in blocking_names:
            record = _run_target_record(entry, seeds[entry["name"]]["seed_path"])
        else:
            record = _skipped_record(entry, "blocking=false (policy)")
        per_target.append(record)
        _print_target(record)
    record = _compose_record(manifest["candidate_sha"], blocking_names,
                             per_target, started_at)
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
    for field in OBSERVATION_FIELDS:
        if field not in entry:
            reasons.append(f"missing-observation: per_target[{index}] "
                           f"missing {field}")
    for field in PER_TARGET_IDENTITY_FIELDS:
        if field not in entry:
            reasons.append(f"malformed: per_target[{index}] missing {field}")
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
    by_name = {entry.get("target"): entry for entry in record["per_target"]}
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
    error = _check_candidate_sha(record.get("candidate_sha"), "record")
    if error:
        reasons.append(error)
    elif record["candidate_sha"] != manifest["candidate_sha"]:
        reasons.append("stale-digest: record candidate sha mismatch with "
                       "manifest")
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
    reasons = validate_record(record, manifest)
    if reasons:
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
        if args.mode == "fixture":
            return run_fixture_gate(args)
        return run_real_gate(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
