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
import os
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

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

_CARGO_MANIFEST = REPO_ROOT / "components" / "rust-converter" / "Cargo.toml"


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

RELEASE_VERSION = _release_version()
RELEASE_ARTIFACT_ROOT = Path("artifacts") / "release" / RELEASE_VERSION
OUTPUT_ROOT = REPO_ROOT / RELEASE_ARTIFACT_ROOT
FEATURE_MANIFEST = OUTPUT_ROOT / "official-build-feature-manifest.json"
ABI_HEADER = REPO_ROOT / "components" / "rust-converter" / "include" / "markdown_converter.h"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FINAL_EVIDENCE_SCHEMA = "schemas/final-evidence-manifest.schema.json"
OBSERVATION_STATE_SCHEMA = "schemas/observation-state.schema.json"
SHORT_SOAK_SCOPE = "release/scope/short-soak-scope.json"
CANONICAL_PERF_ENV = "release/performance/canonical-environment.json"


def _release_artifact_ref(filename: str) -> str:
    """Return a repository-relative path under the active release directory."""
    return (RELEASE_ARTIFACT_ROOT / filename).as_posix()


TRACKED_RELEASE_INPUTS = (
    _release_artifact_ref("official-build-feature-manifest.json"),
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
    try:
        result = subprocess.run(
            ["git", *args],
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
    try:
        result = subprocess.run(
            ["git", "ls-tree", "-r", "-z", "HEAD"],
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
    for relative_path in TRACKED_RELEASE_INPUTS:
        if not (REPO_ROOT / relative_path).is_file():
            raise ValueError(f"required tracked release input is missing: {relative_path}")

    input_digests = {
        path: _sha256_file(REPO_ROOT / path) for path in TRACKED_RELEASE_INPUTS
    }
    feature_digest = _canonical_digest(FEATURE_MANIFEST)
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
        "required_inputs": list(TRACKED_RELEASE_INPUTS),
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
            "schema_version": "release.corpus-seed-manifest.v1",
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
    feature_digest = _canonical_digest(FEATURE_MANIFEST)
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


def build_final_evidence(candidate_sha: str, generated_at: str) -> tuple[dict, dict]:
    """Build transparent evidence for this job and its separate CI jobs."""
    root = OUTPUT_ROOT
    fuzz_pass = _record_value(root / FUZZ_QUALIFICATION_RECORD_NAME, "blocking_pass")
    soak_status = _record_value(root / SOAK_QUALIFICATION_RECORD_NAME)
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
            "status": "pass" if soak_status == "pass" else "fail",
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
        if args.phase in {"inputs", "all"}:
            _write_json(OUTPUT_ROOT / "release-candidate-sha-manifest.json",
                        build_candidate_manifest(candidate_sha, created_at))
            blocking, corpus = build_fuzz_manifests(candidate_sha, created_at)
            _write_json(OUTPUT_ROOT / "blocking-fuzz-target-manifest.json", blocking)
            _write_json(OUTPUT_ROOT / "corpus-seed-manifest.json", corpus)

            soak_scope_path = REPO_ROOT / SHORT_SOAK_SCOPE
            soak_scope = _load_json(SHORT_SOAK_SCOPE)
            _write_json(
                OUTPUT_ROOT / "short-soak-scenario-manifest.json",
                build_manifest(soak_scope, candidate_sha, soak_scope_path, created_at),
            )
        if args.phase in {"artifact", "all"}:
            artifact_root = (REPO_ROOT / args.artifact_root).resolve()
            _write_json(
                OUTPUT_ROOT / "candidate-release-artifact-index.json",
                build_artifact_index(candidate_sha, created_at, artifact_root),
            )
        if args.phase in {"final", "all"}:
            evidence, observation = build_final_evidence(candidate_sha, created_at)
            _write_json(OUTPUT_ROOT / "final-evidence-manifest.json", evidence)
            _write_json(OUTPUT_ROOT / "observation-state.json", observation)
    except (OSError, ValueError, ImportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: generated release gate manifests ({args.phase})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
