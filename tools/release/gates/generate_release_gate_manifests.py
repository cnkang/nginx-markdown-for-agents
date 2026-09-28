#!/usr/bin/env python3
"""Materialize the candidate-bound inputs consumed by the release gate.

The release workflow starts from a clean checkout.  Candidate-bound manifests
therefore have to be generated from tracked policy/scope inputs and the
artifacts downloaded by that workflow; they cannot be treated as pre-existing
working-tree state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath

# Sibling module import: this script is invoked as
# `python3 tools/release/gates/...py` from the repo root, so its own
# directory is not on sys.path.  Add the repo root and the gates
# directory so the sibling generate_soak_scenario_manifest import below
# resolves (its own lib.* imports need <repo>/tools on sys.path).
REPO_ROOT = Path(__file__).resolve().parents[3]
_GATES_DIR = Path(__file__).resolve().parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "tools"), str(_GATES_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_soak_scenario_manifest import build_manifest  # noqa: E402
from tools.release.gates.validate_fuzz_qualification import (  # noqa: E402
    SCHEMA_VERSION as FUZZ_QUALIFICATION_SCHEMA_VERSION,
    FUZZ_JOB_BUDGET,
    MAX_FUZZ_TARGET_EXECUTIONS,
    validate_toolchain_identity,
)
from tools.lib.executable_validation import (  # noqa: E402
    resolve_approved_executable,
)

_CARGO_MANIFEST = REPO_ROOT / "components" / "rust-converter" / "Cargo.toml"
FUZZ_CORPUS_RELATIVE_ROOT = PurePosixPath(
    "components/rust-converter/fuzz/corpus")


def _release_version() -> str:
    """Read and validate the active release version from Cargo metadata."""
    try:
        document = tomllib.loads(_CARGO_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"unable to read active release version: {exc}") from exc
    package = document.get("package")
    version = package.get("version") if isinstance(package, dict) else None
    if not isinstance(version, str) or re.fullmatch(
        _SEMVER_PATTERN, version
    ) is None:
        raise ValueError("Cargo package version must be MAJOR.MINOR.PATCH")
    return version


_SEMVER_PATTERN = r"\d+\.\d+\.\d+"

FUZZ_QUALIFICATION_RECORD_NAME = "fuzz-qualification-record.json"
SOAK_QUALIFICATION_RECORD_NAME = "soak-qualification-record.json"


@lru_cache(maxsize=1)
def _release_state() -> tuple[str, Path, Path]:
    """Return (release_version, artifact_root, output_root).

    Resolved lazily (and once per process) so an invalid or unreadable
    Cargo version surfaces from ``main()``'s ERROR-prefixed handler with
    a failure exit code instead of an import-time traceback.
    """
    version = _release_version()
    artifact_root = Path("artifacts") / "release" / version
    return version, artifact_root, REPO_ROOT / artifact_root


def release_version() -> str:
    """Active release version, resolved on first use."""
    return _release_state()[0]


def _release_artifact_ref(filename: str) -> str:
    """Return a repository-relative path under the active release directory."""
    return (_release_state()[1] / filename).as_posix()

ABI_HEADER = REPO_ROOT / "components" / "rust-converter" / "include" / "markdown_converter.h"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FINAL_EVIDENCE_SCHEMA = "schemas/final-evidence-manifest.schema.json"
OBSERVATION_STATE_SCHEMA = "schemas/observation-state.schema.json"
SHORT_SOAK_SCOPE = "release/scope/short-soak-scope.json"
CANONICAL_PERF_ENV = "release/performance/canonical-environment.json"


def _feature_manifest_path() -> Path:
    """Path of the official-build feature manifest for the active release."""
    return _release_state()[2] / "official-build-feature-manifest.json"


# Static tracked inputs; the versioned official-build feature manifest path
# is appended lazily via _tracked_release_inputs() so a bad Cargo version
# surfaces from main() rather than at import time.
_STATIC_RELEASE_INPUTS = (
    "docs/releases/release-matrix.json",
    "schemas/release-matrix.schema.json",
    FINAL_EVIDENCE_SCHEMA,
    OBSERVATION_STATE_SCHEMA,
    "release/signing-policy.json",
    "release/provenance-policy.json",
    "release/scope/sanitizer-support-matrix.json",
    "release/scope/fuzz-scope.json",
    "release/scope/corpus-scope.json",
    SHORT_SOAK_SCOPE,
    CANONICAL_PERF_ENV,
    "components/rust-converter/include/markdown_converter.h",
)


@lru_cache(maxsize=1)
def _tracked_release_inputs() -> tuple[str, ...]:
    """Tracked release inputs including the versioned feature-manifest path."""
    return (
        _release_artifact_ref("official-build-feature-manifest.json"),
    ) + _STATIC_RELEASE_INPUTS




def _utc_now() -> str:
    """Return a stable ISO-8601 UTC timestamp for generated records."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_digest(path: Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return _sha256_bytes(canonical.encode("utf-8"))


def _git(args: list[str]) -> str:
    git = resolve_approved_executable("git")
    if git is None:
        raise ValueError("approved git executable is unavailable")
    try:
        result = subprocess.run(
            [git, *args],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"unable to resolve git metadata: {exc}") from exc
    if result.returncode != 0:
        raise ValueError(
            f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _candidate_sha(requested: str | None) -> str:
    actual = _git(["rev-parse", "HEAD"])
    if not SHA_RE.fullmatch(actual):
        raise ValueError("git HEAD is not a full lowercase commit SHA")
    if requested is not None and requested != actual:
        raise ValueError(
            f"requested candidate SHA {requested} does not equal git HEAD {actual}"
        )
    return actual


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _load_json(relative_path: str) -> dict:
    path = REPO_ROOT / relative_path
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read {relative_path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{relative_path} must contain a JSON object")
    return value


def _source_tree_digest() -> str:
    git = resolve_approved_executable("git")
    if git is None:
        raise ValueError("approved git executable is unavailable")
    try:
        result = subprocess.run(
            [git, "ls-tree", "-r", "-z", "HEAD"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"unable to hash the source tree: {exc}") from exc
    if result.returncode != 0:
        raise ValueError("git ls-tree failed while hashing the source tree")
    return _sha256_bytes(result.stdout)


def _branch_name() -> str:
    branch = _git(["branch", "--show-current"])
    if branch:
        return branch
    ref_name = os.environ.get("GITHUB_REF_NAME", "")
    return ref_name or "detached-release-candidate"


def build_candidate_manifest(candidate_sha: str, created_at: str) -> dict:
    """Build a candidate manifest using only clean-checkout inputs."""
    for relative_path in _tracked_release_inputs():
        if not (REPO_ROOT / relative_path).is_file():
            raise ValueError(f"required tracked release input is missing: {relative_path}")

    input_digests = {
        path: _sha256_file(REPO_ROOT / path) for path in _tracked_release_inputs()
    }
    feature_digest = _canonical_digest(_feature_manifest_path())
    matrix_digest = _sha256_file(REPO_ROOT / "docs/releases/release-matrix.json")
    ffi_digest = _sha256_file(ABI_HEADER)
    # The canonical performance environment (NGINX version, runner, rust
    # toolchain, identity contract) is a tracked release input whose digest
    # is folded into input_digests; short-soak-scope.json is a different
    # artifact.
    performance_digest = input_digests[CANONICAL_PERF_ENV]
    return {
        "schema_version": "release.candidate-sha-manifest.v1",
        "candidate_sha": candidate_sha,
        "branch": _branch_name(),
        "source_tree_digest": _source_tree_digest(),
        "frozen_at": created_at,
        "required_inputs": list(_tracked_release_inputs()),
        "input_digests": input_digests,
        "feature_manifest_digest": feature_digest,
        "final_ffi_freeze_digest": ffi_digest,
        "canonical_performance_environment_digest": performance_digest,
        "release_matrix_digest": matrix_digest,
        "evidence_schema_digests": {
            FINAL_EVIDENCE_SCHEMA: input_digests[FINAL_EVIDENCE_SCHEMA],
            OBSERVATION_STATE_SCHEMA: input_digests[OBSERVATION_STATE_SCHEMA],
        },
        "freeze_rule": (
            "Candidate-bound evidence is valid only for this exact checkout "
            "and its tracked release policy inputs."
        ),
        "git_tree_sha": _git(["rev-parse", "HEAD^{tree}"]),
    }


def _fuzz_targets() -> list[str]:
    scope = _load_json("release/scope/fuzz-scope.json")
    if scope.get("schema_version") != "release.scope.fuzz.v1":
        raise ValueError("unexpected fuzz scope schema_version")
    targets = scope.get("targets")
    if not isinstance(targets, list) or not targets or not all(
        isinstance(target, str) and target for target in targets
    ):
        raise ValueError("fuzz scope targets must be a non-empty string array")
    if len(set(targets)) != len(targets):
        raise ValueError("fuzz scope contains duplicate targets")
    return targets


def _seed_for_target(target: str) -> tuple[str, str]:
    corpus_dir = REPO_ROOT / "components" / "rust-converter" / "fuzz" / "corpus" / target
    candidates = sorted(
        path for path in corpus_dir.glob("basic.*") if path.is_file()
    )
    if not candidates:
        raise ValueError(f"missing basic corpus seed for fuzz target {target}")
    seed = candidates[0]
    return seed.relative_to(REPO_ROOT).as_posix(), _sha256_file(seed)


def build_fuzz_manifests(candidate_sha: str, created_at: str) -> tuple[dict, dict]:
    """Build the blocking target and candidate corpus manifests."""
    targets = _fuzz_targets()
    target_entries = []
    seed_entries = []
    for target in targets:
        seed_path, digest = _seed_for_target(target)
        target_entries.append({
            "name": target,
            "seed": 12345,
            "required_minutes": 15,
            "required_executions": 100000,
            "blocking": True,
        })
        seed_entries.append({
            "target": target,
            "seed_path": seed_path,
            "digest": digest,
        })
    return (
        {
            "schema_version": "release.blocking-fuzz-target-manifest.v1",
            "candidate_sha": candidate_sha,
            "created_at": created_at,
            "targets": target_entries,
            "threshold_reference": (
                "Requirement 18 Wave-6 qualification thresholds: at least "
                "15 minutes or 100,000 executions per blocking target, "
                "whichever is later, with a fixed recorded seed"
            ),
        },
        {
            "schema_version": "release.corpus-seed.v1",
            "candidate_sha": candidate_sha,
            "created_at": created_at,
            "seeds": seed_entries,
        },
    )


def build_artifact_index(candidate_sha: str, created_at: str, artifact_root: Path) -> dict:
    """
    Build an artifact index that binds each downloaded DEB or RPM artifact to a release candidate.

    Parameters:
        candidate_sha (str): Full Git SHA identifying the release candidate.
        created_at (str): Timestamp recorded in the generated index.
        artifact_root (Path): Repository-contained directory containing downloaded artifacts.

    Returns:
        dict: Artifact index containing each artifact's relative path, type, SHA-256 digest, candidate identity, ABI version, and verification metadata.

    Raises:
        ValueError: If the artifact directory is outside the repository, contains no DEB or RPM artifacts, includes symlinks or paths escaping the repository, has an inconsistent artifact type, or the ABI version cannot be read.
    """
    try:
        artifact_root = artifact_root.resolve()
        artifact_root.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("artifact root must remain inside the repository") from exc
    files = sorted(
        path for path in artifact_root.rglob("*")
        if path.is_file() and path.suffix in {".deb", ".rpm"}
    )
    if not files:
        raise ValueError(f"no DEB/RPM artifacts found under {artifact_root}")
    feature_digest = _canonical_digest(_feature_manifest_path())
    abi_text = ABI_HEADER.read_text(encoding="utf-8")
    match = re.search(r"#define\s+MARKDOWN_ABI_VERSION\s+(\d+)", abi_text)
    if match is None:
        raise ValueError("MARKDOWN_ABI_VERSION is missing from the ABI header")
    abi_version = int(match.group(1))
    artifacts = []
    for path in files:
        # Reject symlinked artifacts outright: path.is_file() follows
        # symlinks, so a .deb symlink could point at a different file and
        # the index would declare one type while hashing another's bytes.
        if path.is_symlink():
            raise ValueError(f"artifact path must not be a symlink: {path}")
        # Resolve each matched artifact before hashing or deriving its
        # artifact_id: a symlink inside the artifact root could point
        # outside REPO_ROOT, and hashing or recording the un-resolved
        # path would bind bytes that do not belong to the repository.
        try:
            resolved = path.resolve()
            resolved.relative_to(REPO_ROOT.resolve())
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"artifact path escapes the repository: {path}"
            ) from exc
        if resolved.suffix != path.suffix:
            raise ValueError(
                f"artifact type does not match resolved path: {path}"
            )
        relative = resolved.relative_to(REPO_ROOT).as_posix()
        artifact_type = path.suffix[1:]
        relative_id = relative.replace("/", "__")
        artifacts.append({
            "artifact_type": artifact_type,
            "release_matrix_row_id": f"downloaded-{artifact_type}-{relative_id}",
            "artifact_id": relative,
            "candidate_sha": candidate_sha,
            "artifact_sha256": _sha256_file(resolved),
            "feature_manifest_digest": feature_digest,
            "abi_version": abi_version,
            "verification_status": "pass",
            "producer_run_id": f"release-gate-{candidate_sha[:12]}",
        })
    return {
        "schema_version": "release.candidate-artifact-index.v1",
        "candidate_sha": candidate_sha,
        "created_at": created_at,
        "producer_run_id": f"release-gate-{candidate_sha[:12]}",
        "artifacts": artifacts,
    }


def _record_value(path: Path, field: str = "status"):
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value.get(field) if isinstance(value, dict) else None


def _safe_fuzz_reference(value: object) -> PurePosixPath | None:
    """Accept only canonical repository-relative POSIX references."""
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        return None
    return path


def _resolve_fuzz_reference(
    value: PurePosixPath, expected_kind: str
) -> Path | None:
    """Resolve a fuzz reference while rejecting symlinks and root escapes."""
    repo_root = REPO_ROOT.resolve()
    candidate = repo_root.joinpath(*value.parts)
    current = repo_root
    try:
        for part in value.parts:
            current = current / part
            if current.is_symlink():
                return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repo_root)
    except (OSError, RuntimeError, ValueError):
        return None
    if expected_kind == "directory" and not resolved.is_dir():
        return None
    if expected_kind == "file" and not resolved.is_file():
        return None
    return resolved


def _repo_relative_fuzz_reference(path: Path) -> PurePosixPath | None:
    """Accept an existing non-symlink path beneath the checked-out tree."""
    try:
        value = path.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return None
    relative = _safe_fuzz_reference(value)
    if relative is None or _resolve_fuzz_reference(relative, "file") is None:
        return None
    return relative


def _load_candidate_fuzz_seed_manifest(
    path: Path, candidate_sha: str
) -> dict | None:
    """Load a seed manifest only when its path and candidate binding hold."""
    if _repo_relative_fuzz_reference(path) is None:
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        isinstance(manifest, dict)
        and manifest.get("schema_version") == "release.corpus-seed.v1"
        and manifest.get("candidate_sha") == candidate_sha
        and isinstance(manifest.get("seeds"), list)
    ):
        return manifest
    return None


def _candidate_seed_entry(
    entry: object, target_specs: dict[str, dict]
) -> tuple[str, PurePosixPath, str] | None:
    """Validate one target name, seed path and digest declaration."""
    if not isinstance(entry, dict):
        return None
    target = entry.get("target")
    seed_path = _safe_fuzz_reference(entry.get("seed_path"))
    digest = entry.get("digest")
    if (
        not isinstance(target, str)
        or target not in target_specs
        or seed_path is None
        or seed_path.parent != FUZZ_CORPUS_RELATIVE_ROOT / target
        or not seed_path.name.startswith("basic.")
        or not isinstance(digest, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
    ):
        return None
    return target, seed_path, digest


def _candidate_seed_file_matches(path: PurePosixPath, digest: str) -> bool:
    """Check that the candidate seed is a contained file with its digest."""
    resolved_seed = _resolve_fuzz_reference(path, "file")
    if resolved_seed is None:
        return False
    try:
        return _sha256_file(resolved_seed) == digest
    except OSError:
        return False


def _candidate_fuzz_seed_paths(
    manifest_path: Path,
    candidate_sha: str,
    target_contract: tuple[dict[str, dict], set[str]],
) -> dict[str, PurePosixPath] | None:
    """Validate the candidate-bound seed manifest and each seed file digest."""
    manifest = _load_candidate_fuzz_seed_manifest(
        manifest_path, candidate_sha)
    if manifest is None:
        return None
    target_specs, _blocking_names = target_contract
    seed_paths: dict[str, PurePosixPath] = {}
    for entry in manifest["seeds"]:
        parsed = _candidate_seed_entry(entry, target_specs)
        if parsed is None:
            return None
        target, seed_path, digest = parsed
        if target in seed_paths or not _candidate_seed_file_matches(
                seed_path, digest):
            return None
        seed_paths[target] = seed_path
    return seed_paths if set(seed_paths) == set(target_specs) else None


def _fuzz_record_paths_match(
    entry: dict, target: str, seed_path: PurePosixPath
) -> bool:
    """Bind record references to this target's candidate corpus and log."""
    corpus_path = _safe_fuzz_reference(entry.get("corpus_dir"))
    expected_corpus = FUZZ_CORPUS_RELATIVE_ROOT / target
    if corpus_path is None or corpus_path != expected_corpus:
        return False
    if _resolve_fuzz_reference(corpus_path, "directory") is None:
        return False
    record_seed = _safe_fuzz_reference(entry.get("seed_path"))
    if record_seed != seed_path:
        return False
    if _resolve_fuzz_reference(seed_path, "file") is None:
        return False
    log_path = _safe_fuzz_reference(entry.get("raw_log_ref"))
    expected_log = (
        _release_state()[1] / "fuzz-logs" / f"{target}.log"
    )
    if log_path is None or log_path.as_posix() != expected_log.as_posix():
        return False
    return _resolve_fuzz_reference(log_path, "file") is not None


def _fuzz_record_passes(path: Path, candidate_sha: str) -> bool:
    """Accept only a passing, candidate-bound record with pinned provenance."""
    if _repo_relative_fuzz_reference(path) is None:
        return False
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        manifest_path = path.parent / "blocking-fuzz-target-manifest.json"
        if _repo_relative_fuzz_reference(manifest_path) is None:
            return False
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    target_contract = _manifest_fuzz_target_names(manifest, candidate_sha)
    if target_contract is None:
        return False
    seed_paths = _candidate_fuzz_seed_paths(
        path.parent / "corpus-seed-manifest.json", candidate_sha,
        target_contract)
    if seed_paths is None:
        return False
    return (
        isinstance(record, dict)
        and record.get("schema_version") == FUZZ_QUALIFICATION_SCHEMA_VERSION
        and record.get("candidate_sha") == candidate_sha
        and record.get("blocking_pass") is True
        and not validate_toolchain_identity(record.get("toolchain_identity"))
        and _validate_fuzz_record_per_target(
            record, target_contract, seed_paths)
    )


def _manifest_target_spec_valid(spec: object, seen_names: set[str]) -> bool:
    if not isinstance(spec, dict):
        return False
    name = spec.get("name")
    minutes = spec.get("required_minutes")
    executions = spec.get("required_executions")
    return (
        isinstance(name, str)
        and bool(name)
        and name == name.strip()
        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is not None
        and type(spec.get("seed")) is int
        and type(spec.get("blocking")) is bool
        and name not in seen_names
        and not isinstance(minutes, bool)
        and isinstance(minutes, (int, float))
        and (not isinstance(minutes, float) or math.isfinite(minutes))
        and 0 < minutes <= FUZZ_JOB_BUDGET / 60
        and type(executions) is int
        and executions > 0
        and executions <= MAX_FUZZ_TARGET_EXECUTIONS
    )


def _manifest_fuzz_target_names(
    manifest: object, candidate_sha: str
) -> tuple[dict[str, dict], set[str]] | None:
    """Validate target identities and preserve each blocking threshold."""
    if not isinstance(manifest, dict):
        return None
    if (
        manifest.get("schema_version")
        != "release.blocking-fuzz-target-manifest.v1"
        or manifest.get("candidate_sha") != candidate_sha
    ):
        return None
    targets = manifest.get("targets")
    if not isinstance(targets, list) or not targets:
        return None
    specs: dict[str, dict] = {}
    blocking_names: set[str] = set()
    for spec in targets:
        if not _manifest_target_spec_valid(spec, set(specs)):
            return None
        name = spec["name"]
        specs[name] = spec
        if spec["blocking"]:
            blocking_names.add(name)
    return (specs, blocking_names) if blocking_names else None


def _fuzz_observations_meet_threshold(entry: dict, spec: dict) -> bool:
    """Require observed work, duration and zero sanitizer/crash findings."""
    elapsed = entry.get("elapsed_seconds_total")
    executions = entry.get("executions_total")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or (isinstance(elapsed, float) and not math.isfinite(elapsed))
        or elapsed < 0
        or elapsed > FUZZ_JOB_BUDGET
        or type(executions) is not int
        or executions > MAX_FUZZ_TARGET_EXECUTIONS
    ):
        return False
    return (
        elapsed >= int(spec["required_minutes"] * 60)
        and executions >= spec["required_executions"]
        and type(entry.get("crashes")) is int
        and entry["crashes"] == 0
        and type(entry.get("sanitizer_findings")) is int
        and entry["sanitizer_findings"] == 0
    )


def _fuzz_target_record_identity_matches(entry: dict, spec: dict) -> bool:
    if type(entry.get("seed")) is not int or entry["seed"] != spec["seed"]:
        return False
    return all(
        isinstance(entry.get(field), str) and bool(entry[field])
        for field in ("corpus_dir", "seed_path", "raw_log_ref")
    )


def _index_fuzz_target_entries(
    per_target: object, target_specs: dict[str, dict]
) -> dict[str, dict] | None:
    if not isinstance(per_target, list):
        return None
    by_name: dict[str, dict] = {}
    for entry in per_target:
        if not isinstance(entry, dict):
            return None
        name = entry.get("target")
        if not isinstance(name, str) or name not in target_specs or name in by_name:
            return None
        by_name[name] = entry
    return by_name


def _blocking_fuzz_target_passes(
    entry: dict | None,
    name: str,
    spec: dict,
    seed_path: PurePosixPath | None,
) -> bool:
    return (
        entry is not None
        and seed_path is not None
        and entry.get("status") == "pass"
        and _fuzz_target_record_identity_matches(entry, spec)
        and _fuzz_observations_meet_threshold(entry, spec)
        and _fuzz_record_paths_match(entry, name, seed_path)
    )


def _validate_fuzz_record_per_target(
    record: dict,
    target_contract: tuple[dict[str, dict], set[str]],
    seed_paths: dict[str, PurePosixPath],
) -> bool:
    """Require unique target observations and thresholds for blocking runs."""
    target_specs, blocking_names = target_contract
    by_name = _index_fuzz_target_entries(record.get("per_target"), target_specs)
    if by_name is None:
        return False
    return all(
        _blocking_fuzz_target_passes(
            by_name.get(name), name, target_specs[name], seed_paths.get(name))
        for name in blocking_names
    )


def _soak_record_passes(path: Path, candidate_sha: str) -> bool:
    """Accept only a passing soak artifact bound to the candidate SHA."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return (
        isinstance(record, dict)
        and record.get("schema_version") == "release.soak-qualification.v1"
        and record.get("candidate_sha") == candidate_sha
        and record.get("status") == "pass"
    )


def build_final_evidence(candidate_sha: str, generated_at: str) -> tuple[dict, dict]:
    """Build transparent evidence for this job and its separate CI jobs."""
    root = _release_state()[2]
    fuzz_pass = _fuzz_record_passes(
        root / FUZZ_QUALIFICATION_RECORD_NAME, candidate_sha)
    soak_pass = _soak_record_passes(
        root / SOAK_QUALIFICATION_RECORD_NAME, candidate_sha
    )
    # The blocking performance evidence is produced by the release-gate job's
    # `make release-perf-evidence-blocking BASELINE_VERSION=092` step, which
    # writes perf/reports/evidence-092.json. This is the sole performance
    # report consumed by final evidence generation.
    performance_path = (
        REPO_ROOT / "perf" / "reports" / "evidence-092.json"
    )
    performance_pass = False
    if performance_path.is_file():
        try:
            performance_report = json.loads(
                performance_path.read_text(encoding="utf-8")
            )
            performance_pass = (
                isinstance(performance_report, dict)
                and performance_report.get("verdict") == "PASS"
            )
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            performance_pass = False

    entries = [
        {
            "domain": "coverage",
            "blocking": False,
            "status": "skip",
            "artifact_ref": "workflow:coverage is supplied by CI quality jobs",
            "justification": "Coverage is executed by the required CI quality workflow, not the package release job.",
            "policy_reference": "release/provenance-policy.json#verification_commands",
        },
        {
            "domain": "performance",
            "blocking": True,
            "status": "pass" if performance_pass else "fail",
            "artifact_ref": "perf/reports/evidence-092.json",
        },
        {
            "domain": "fuzz",
            "blocking": True,
            "status": "pass" if fuzz_pass is True else "fail",
            "artifact_ref": _release_artifact_ref(FUZZ_QUALIFICATION_RECORD_NAME),
        },
        {
            "domain": "soak",
            "blocking": True,
            "status": "pass" if soak_pass else "fail",
            "artifact_ref": _release_artifact_ref(SOAK_QUALIFICATION_RECORD_NAME),
        },
        {
            "domain": "security",
            "blocking": False,
            "status": "skip",
            "artifact_ref": "workflow:required security checks on candidate SHA",
            "justification": "SAST and dependency checks are required upstream checks on the same candidate SHA.",
            "policy_reference": "release/provenance-policy.json#security_scan",
        },
        {
            "domain": "signature",
            "blocking": False,
            "status": "skip",
            "artifact_ref": "workflow:integrity-signing job",
            "justification": "Signing is performed by the protected release-signing job after this gate.",
            "policy_reference": "release/signing-policy.json",
        },
        {
            "domain": "provenance",
            "blocking": False,
            "status": "skip",
            "artifact_ref": "workflow:artifact attestation jobs",
            "justification": "Attestations are emitted by the protected publication jobs after this gate.",
            "policy_reference": "release/provenance-policy.json",
        },
        {
            "domain": "documentation",
            "blocking": False,
            "status": "skip",
            "artifact_ref": ".github/workflows/ci.yml (docs-check job)",
            "justification": "Documentation synchronization is enforced by make docs-check in CI, not by this gate; the check is not executed here and must not report pass.",
            "policy_reference": "docs/harness/rules/documentation-quality.md",
        },
    ]
    blocking_statuses = [entry["status"] for entry in entries if entry["blocking"]]
    final_status = "pass" if all(status == "pass" for status in blocking_statuses) else "fail"
    evidence = {
        "schema_version": 1,
        "candidate_sha": candidate_sha,
        "evidence_schema_digest": _sha256_file(
            REPO_ROOT / FINAL_EVIDENCE_SCHEMA
        ),
        "observation_schema_digest": _sha256_file(
            REPO_ROOT / OBSERVATION_STATE_SCHEMA
        ),
        "generated_at": generated_at,
        "entries": entries,
        "run_status": final_status,
        "residual_risk": [],
    }
    observation = {
        "schema_version": 1,
        "candidate_sha": candidate_sha,
        "phase": "phase_1_candidate_qualification",
        "phase_started_at": generated_at,
        "observation_plan_reference": "Requirement 18 criterion 6",
        "domains": {
            "coverage": {
                "aggregate_pct": 0.0,
                "critical_path_pct": 0.0,
                "report_ref": "workflow:coverage is supplied by CI quality jobs",
            },
            "performance": {
                "per_scenario_budgets": [],
                "baseline_ref": "perf/reports/evidence-092.json",
            },
            "fuzz": {
                "blocking_targets": [],
                "campaign_ref": _release_artifact_ref(FUZZ_QUALIFICATION_RECORD_NAME),
            },
            "soak": {
                "runs": [],
                "soak_ref": _release_artifact_ref(SOAK_QUALIFICATION_RECORD_NAME),
            },
            "security": {
                "sast_status": "accepted_residual",
                "dependency_status": "accepted_residual",
                "scan_report_ref": "workflow:required security checks on candidate SHA",
            },
            "signature": {
                "verified_artifact_count": 0,
                "signature_index_ref": "workflow:integrity-signing job",
            },
            "provenance": {
                "attestation_count": 0,
                "provenance_index_ref": "workflow:artifact attestation jobs",
            },
            "documentation": {
                "open_items": 0,
                "audit_ref": FINAL_EVIDENCE_SCHEMA,
            },
            "residual-risk": {"records": []},
        },
    }
    return evidence, observation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("inputs", "artifact", "final", "all"), default="all")
    parser.add_argument("--candidate-sha", default=None)
    parser.add_argument("--artifact-root", default="dist")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        candidate_sha = _candidate_sha(args.candidate_sha)
        created_at = _utc_now()
        output_root = _release_state()[2]
        if args.phase in {"inputs", "all"}:
            _write_json(output_root / "release-candidate-sha-manifest.json",
                        build_candidate_manifest(candidate_sha, created_at))
            blocking, corpus = build_fuzz_manifests(candidate_sha, created_at)
            _write_json(output_root / "blocking-fuzz-target-manifest.json", blocking)
            _write_json(output_root / "corpus-seed-manifest.json", corpus)

            soak_scope_path = REPO_ROOT / SHORT_SOAK_SCOPE
            soak_scope = _load_json(SHORT_SOAK_SCOPE)
            _write_json(
                output_root / "short-soak-scenario-manifest.json",
                build_manifest(soak_scope, candidate_sha, soak_scope_path, created_at),
            )
        if args.phase in {"artifact", "all"}:
            artifact_root = (REPO_ROOT / args.artifact_root).resolve()
            _write_json(
                output_root / "candidate-release-artifact-index.json",
                build_artifact_index(candidate_sha, created_at, artifact_root),
            )
        if args.phase in {"final", "all"}:
            evidence, observation = build_final_evidence(candidate_sha, created_at)
            _write_json(output_root / "final-evidence-manifest.json", evidence)
            _write_json(output_root / "observation-state.json", observation)
    except (OSError, ValueError, ImportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: generated release gate manifests ({args.phase})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
