#!/usr/bin/env python3
"""
detect_baseline_hand_edit.py — Rule 61 extension (release-integrity).

Performance baselines are finalizer output, never hand-edited data.
The 2026-08-20/21 cluster produced ~15 commits churning candidate-bound
evidence (36a9ad45, 0847c287, f68109ac, 2f8fc276, 3bd22d47, f49920c6,
19340b31, d7012e42 reverted by cf6aea8e, e8d24878) plus one direct edit
of a derived field (8c899644).  This gate enforces the binding lifecycle:

Full-audit mode (default over perf/baselines):
  1. every finalized baseline carries all six baseline_policy provenance
     fields (Rule 61 table), fail closed otherwise;
  2. module_benchmark.git_commit equals baseline_policy.source_git_commit;
  3. source_git_commit is a full 40-hex SHA-1 present in repository
     history (existence check skipped in shallow clones);
  4. recomputed SHA-256 of the retained raw artifact matches
     source_artifact_sha256;
  5. measurement_timestamp is ISO-8601 with explicit UTC offset.

Changed-file mode (--changed PATH...):
  a finalized baseline JSON may only change together with its retained
  raw inputs (*-raw.json or *-raw-probes/) in the same change set.
  A finalized JSON that moves alone indicates a hand edit; regenerate it
  with tools/perf/finalize_module_baseline.py instead.

Usage:
    python3 tools/harness/detect_baseline_hand_edit.py [--changed PATH ...]

Exit codes: 0 clean, 1 violations found.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = REPO_ROOT / "perf" / "baselines"

REQUIRED_POLICY_FIELDS = (
    "source_git_commit",
    "source_run",
    "source_artifact",
    "source_artifact_sha256",
    "measurement_timestamp",
    "normalization",
)

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)


def is_finalized_baseline(path):
    name = path.name
    return (
        path.is_file()
        and name.startswith("module-baseline-")
        and name.endswith(".json")
        and "-raw" not in name
    )


def repo_is_shallow():
    """True when this checkout is a shallow clone."""
    marker = REPO_ROOT / ".git" / "shallow"
    if marker.exists():
        return True
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def repo_commit_exists(sha):
    """Return True (present), False (absent), or None (cannot determine).

    None is returned only when the checkout is shallow and the object is
    missing: the absence may be a clone artifact rather than evidence of a
    broken provenance binding.  Callers must surface that distinction as an
    explicit SKIP instead of silently passing — a full clone and a shallow
    clone must reach the same verdict for the same baseline.
    """
    try:
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
    except OSError:
        return True
    if result.returncode != 0 and repo_is_shallow():
        return None
    return result.returncode == 0


def _verify_artifact_digest(label, policy, findings):
    artifact = str(policy.get("source_artifact", ""))
    digest = str(policy.get("source_artifact_sha256", ""))
    if not artifact:
        return
    if artifact.startswith("/") or ".." in Path(artifact).parts:
        findings.append(
            f"{label}: source_artifact must be repo-relative: {artifact!r}"
        )
        return
    # Rule 33: containment must hold for the canonical target after
    # symlink resolution, not only for the literal path.
    root = REPO_ROOT.resolve()
    artifact_path = (REPO_ROOT / artifact).resolve()
    if not artifact_path.is_relative_to(root):
        findings.append(
            f"{label}: source_artifact resolves outside the repository "
            f"root: {artifact!r}"
        )
        return
    if not artifact_path.is_file():
        findings.append(f"{label}: retained raw artifact missing: {artifact}")
        return
    if not digest:
        return
    actual = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    if actual != digest:
        findings.append(
            f"{label}: source_artifact_sha256 mismatch for {artifact} "
            f"(declared {digest[:12]}, actual {actual[:12]}); regenerate "
            f"via tools/perf/finalize_module_baseline.py"
        )


def _baseline_label(path):
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except (OSError, ValueError):
        return path.name


def _check_policy_fields(label, policy, findings):
    if policy.get("type") == "verbatim_import":
        return
    for field in REQUIRED_POLICY_FIELDS:
        value = policy.get(field)
        if value is None or (isinstance(value, str)
                             and not value.strip()):
            findings.append(
                f"{label}: baseline_policy.{field} missing/empty"
            )


def _check_commit_and_timestamp(label, doc, policy, findings):
    sha = str(policy.get("source_git_commit", ""))
    if sha and not FULL_SHA_RE.match(sha):
        findings.append(
            f"{label}: source_git_commit must be a full 40-hex SHA, got {sha!r}"
        )
    elif sha:
        exists = repo_commit_exists(sha)
        if exists is None:
            # Shallow clone cannot decide: SKIP loudly AND fail closed.
            # An indeterminate provenance check must not exit clean — a
            # shallow checkout would otherwise accept metadata a full
            # clone rejects (verdict must match across clone topologies).
            print(
                f"SKIP {label}: source_git_commit {sha} not present in this "
                f"shallow clone; provenance existence check deferred to a "
                f"full clone (verdict must match across clone topologies)",
                file=sys.stderr,
            )
            findings.append(
                f"{label}: source_git_commit {sha} unverifiable in this "
                f"shallow clone (re-run the check from a full clone)"
            )
        elif not exists:
            findings.append(f"{label}: source_git_commit {sha} not in history")

    benchmark = doc.get("module_benchmark", {})
    benchmark_commit = str(benchmark.get("git_commit", ""))
    if (
        benchmark_commit
        and sha
        and not sha.startswith(benchmark_commit)
        and not benchmark_commit.startswith(sha)
    ):
        findings.append(
            f"{label}: module_benchmark.git_commit ({benchmark_commit}) does "
            f"not match baseline_policy.source_git_commit ({sha[:12]})"
        )

    timestamp = str(
        policy.get("measurement_timestamp") or benchmark.get("timestamp") or ""
    )
    if timestamp and not UTC_TIMESTAMP_RE.match(timestamp):
        findings.append(
            f"{label}: measurement timestamp not ISO-8601 UTC: {timestamp!r}"
        )


def audit_baseline(path, findings):
    label = _baseline_label(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        findings.append(f"{label}: unreadable or invalid JSON: {exc}")
        return

    policy = doc.get("baseline_policy")
    if not isinstance(policy, dict):
        findings.append(f"{label}: missing baseline_policy object")
        return

    _check_policy_fields(label, policy, findings)
    _check_commit_and_timestamp(label, doc, policy, findings)
    _verify_artifact_digest(label, policy, findings)


def _classify_changed_path(raw_path, changed_finalized, changed_raw_names,
                           changed_probe_stems):
    """Sort one changed path into finalized / raw-file / probe-dir buckets."""
    path = Path(raw_path)
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if is_finalized_baseline(resolved):
        changed_finalized.append(resolved)
        return
    if "module-baseline-" in path.name and "-raw" in path.name:
        changed_raw_names.add(path.name)
        return
    _collect_probe_stem(path, changed_probe_stems)


def _collect_probe_stem(path, changed_probe_stems):
    """Record the baseline stem when `path` is a raw-probe artifact.

    Probe artifacts live inside a `<stem>-raw-probes/` directory and carry
    their own scenario names (e.g. plain-small.json), so the DIRECTORY path
    — not the file name — carries the marker.  Match any path segment for
    `-raw-probes` so a probe file counts as its stem's raw input.  The
    previous implementation only matched `-raw` FILE names, so a legitimate
    regeneration touching only probe files was rejected with a misleading
    "hand edits are forbidden" message.
    """
    for i, seg in enumerate(path.parts):
        if seg.endswith("-raw-probes"):
            stem = seg[: -len("-raw-probes")]
            in_baseline_dir = (
                i > 0 and path.parts[i - 1] == "perf"
                and "baselines" in path.parts
            )
            if stem.startswith("module-baseline-") or in_baseline_dir:
                changed_probe_stems.add(stem)
            return


def check_changed(paths, findings):
    changed_finalized = []
    changed_raw_names = set()
    changed_probe_stems = set()
    for raw_path in paths:
        _classify_changed_path(raw_path, changed_finalized,
                               changed_raw_names, changed_probe_stems)

    for baseline_path in changed_finalized:
        stem = baseline_path.name[: -len(".json")]
        raw_touched = any(
            name.startswith(f"{stem}-raw") for name in changed_raw_names
        ) or stem in changed_probe_stems
        if not raw_touched:
            findings.append(
                f"{baseline_path.name}: finalized baseline changed without "
                f"its raw inputs (*-raw.json / *-raw-probes/) in the same "
                f"change set; hand edits are forbidden — regenerate with "
                f"tools/perf/finalize_module_baseline.py"
            )


def main():
    parser = argparse.ArgumentParser(
        description="Rule 61 baseline binding-lifecycle check"
    )
    parser.add_argument(
        "--changed",
        nargs="*",
        default=None,
        metavar="PATH",
        help="changed-file mode: verify these paths follow the lifecycle",
    )
    args = parser.parse_args()

    findings = []

    if args.changed is not None:
        check_changed(args.changed, findings)
    else:
        if not BASELINE_DIR.is_dir():
            print(f"ERROR baseline directory missing: {BASELINE_DIR}",
                  file=sys.stderr)
            return 2
        for path in sorted(BASELINE_DIR.iterdir()):
            if is_finalized_baseline(path):
                audit_baseline(path, findings)

    for finding in findings:
        print(f"VIOLATION {finding}", file=sys.stderr)
    audited = (
        len(args.changed) if args.changed is not None
        else len(list(BASELINE_DIR.glob("module-baseline-*.json")))
    )
    print(f"=== baseline hand-edit check: {audited} path(s) considered, "
          f"{len(findings)} violation(s) ===", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
