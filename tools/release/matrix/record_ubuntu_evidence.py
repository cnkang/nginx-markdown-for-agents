#!/usr/bin/env python3
"""Compose one Ubuntu-native compatibility evidence record.

The build script writes ``ubuntu-native-build.json`` and the verifier writes
``ledger.json``.  This composer merges them into the evidence contract, adds
the build test class the verifier does not produce, and recomputes the totals
from the test ledger instead of copying declared counts.

Usage:
    python3 tools/release/matrix/record_ubuntu_evidence.py \
        --build dist/ubuntu-native/ubuntu-native-build.json \
        --ledger dist/ubuntu-native/ubuntu-native-ledger.json \
        --out docs/evidence/0.9.3/compat/ubuntu-2404-1.24.0-amd64.json \
        [--source-sha SHA] [--verified-by ci:release-packages/ubuntu-native] \
        [--workflow-run-url URL]

Exit codes:
    0  Record written and contract-valid
    1  Missing input or contract violation
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(SCRIPT_DIR))
from lib.path_validation import (  # noqa: E402
    validate_read_path,
    validate_write_path_within_root,
)
from evidence_contract import validate_record  # noqa: E402

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_VERIFIER_TESTS = ("load", "request_markdown", "request_passthrough", "lifecycle")


class ComposeError(RuntimeError):
    """Raised when a record cannot be composed from the build inputs."""


def _git_head(repo_root: pathlib.Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    sha = result.stdout.strip()
    if result.returncode != 0 or not _COMMIT_RE.match(sha):
        raise ComposeError("cannot resolve the source commit for the build")
    return sha


def _read(path: pathlib.Path, purpose: str) -> dict[str, Any]:
    payload = json.loads(validate_read_path(path, purpose=purpose).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ComposeError(f"{purpose} must be a JSON object")
    return payload


def _check_agreement(build: dict[str, Any], ledger: dict[str, Any]) -> None:
    """Raise when the build record and the ledger describe different targets."""
    for field in ("nginx_version", "nginx_package_revision", "arch", "distro"):
        if build.get(field) != ledger.get(field):
            raise ComposeError(f"build and ledger disagree on {field}")
    if build.get("module_sha256") != ledger.get("module_sha256"):
        raise ComposeError("ledger module digest does not match the built module")


def _reject_reported_build_failure(ledger: dict[str, Any]) -> None:
    """Refuse a ledger that reports a failed build.

    The composer supplies the build class, so a reported failure would
    otherwise disappear when the list is filtered.
    """
    reported = [
        test for test in ledger.get("tests", []) if test.get("name") == "build"
    ]
    if any(test.get("status") != "PASS" for test in reported):
        raise ComposeError(
            "ledger reports a non-passing build test; refusing to compose"
        )


def _test_ledger(ledger: dict[str, Any], module_sha: str) -> tuple[list[dict], dict]:
    """Return the merged test list and its recomputed totals."""
    _reject_reported_build_failure(ledger)
    tests = [test for test in ledger.get("tests", []) if test.get("name") != "build"]
    present = {test.get("name") for test in tests}
    missing = [name for name in _VERIFIER_TESTS if name not in present]
    if missing:
        raise ComposeError(f"ledger is missing required test classes: {missing}")
    if any(test.get("status") != "PASS" for test in tests):
        raise ComposeError("ledger contains a non-passing test; refusing to compose")
    tests.insert(
        0,
        {"name": "build", "status": "PASS", "detail": f"module sha256 {module_sha[:16]}"},
    )
    counts = {
        status: sum(1 for test in tests if test["status"] == status)
        for status in ("PASS", "FAIL", "SKIP")
    }
    return tests, counts


def compose(
    *,
    build_path: pathlib.Path,
    ledger_path: pathlib.Path,
    verified_by: str,
    workflow_run_url: str,
    repo_root: pathlib.Path = REPO_ROOT,
    source_sha: str | None = None,
) -> dict[str, Any]:
    """Return a contract-ready record for one Ubuntu-native target."""
    build = _read(build_path, "Ubuntu-native build information")
    ledger = _read(ledger_path, "Ubuntu-native verification ledger")
    _check_agreement(build, ledger)
    tests, counts = _test_ledger(ledger, str(build.get("module_sha256", "")))

    record = dict(build)
    record.update(ledger)
    record["tests"] = tests
    record["pass"] = counts["PASS"]
    record["fail"] = counts["FAIL"]
    record["skip"] = counts["SKIP"]
    record["target_identity"] = (
        f"ubuntu-default-repo:{build['nginx_package_revision']}:"
        f"{build['distro']}:{build['arch']}"
    )
    commit = source_sha if source_sha is not None else _git_head(repo_root)
    if not _COMMIT_RE.match(commit):
        raise ComposeError("source SHA must be 40 lowercase hex characters")
    record["commit_sha"] = commit
    record["workflow_run_url"] = workflow_run_url
    record["verified_by"] = verified_by
    record["date"] = _today()
    return record


def _today() -> str:
    import datetime

    return datetime.date.today().isoformat()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=pathlib.Path, required=True)
    parser.add_argument("--ledger", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--source-sha", default=None)
    parser.add_argument("--verified-by", default="local:release-agent")
    parser.add_argument("--workflow-run-url", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        record = compose(
            build_path=args.build,
            ledger_path=args.ledger,
            verified_by=args.verified_by,
            workflow_run_url=args.workflow_run_url,
            source_sha=args.source_sha,
        )
    except (OSError, KeyError, ComposeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    errors = validate_record(record)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1

    try:
        target = validate_write_path_within_root(args.out, root=REPO_ROOT)
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    # NOSONAR suppression for pythonsecurity:S2083: the path above passed
    # validate_write_path_within_root against the repository root, which
    # rejects a '..' component and any target that resolves outside the tree.
    # CLI input therefore cannot select a write outside the checkout.
    target.write_text(  # NOSONAR
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
