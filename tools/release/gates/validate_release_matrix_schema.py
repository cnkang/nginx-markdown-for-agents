#!/usr/bin/env python3
"""Release-matrix schema drift gate.

Validates that:
1. `schemas/release-matrix.schema.json` exists, is valid JSON, declares
   `schema_version` 1, the canonical top-level `entries`, and the canonical
   entry keys (nginx_version, os, libc, target, artifact_type,
   feature_manifest_digest, abi_version).
2. The single normalization entry point
   (`tools/release/matrix/normalize_matrix.py`) enforces the fail-closed
   rules against fixture inputs:
   - legacy top-level `matrix` accepted when `entries` is absent
   - simultaneous `entries` + `matrix` rejected
   - unknown top-level/entry keys rejected
   - alias/canonical disagreement rejected
   - entries without a resolvable canonical identity rejected
3. The schema digest and producer/consumer record are reported for the
   Artifact Registry Contract.

Exit codes:
    0 - schema valid and normalization entry point behaves per contract
    1 - any check failed
"""

import hashlib
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "schemas" / "release-matrix.schema.json"
NORMALIZE_PATH = REPO_ROOT / "tools" / "release" / "matrix" / "normalize_matrix.py"

CANONICAL_KEYS = {
    "nginx_version",
    "os",
    "libc",
    "target",
    "artifact_type",
    "feature_manifest_digest",
    "abi_version",
}


def schema_digest() -> str:
    raw = SCHEMA_PATH.read_bytes()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def run_normalization(doc: dict) -> tuple[bool, str]:
    """Run the normalization entry point as a subprocess over a JSON doc."""
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(NORMALIZE_PATH), "-"],
        input=json.dumps(doc),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return True, proc.stdout
    return False, proc.stderr.strip()


def check_schema_shape(schema: dict, failures: list) -> None:
    """Validate the schema declares the canonical contract."""
    schema_version_prop = schema.get("properties", {}).get("schema_version", {})
    if schema_version_prop.get("const") != 1:
        failures.append("schema must declare schema_version const 1")
    if "entries" not in schema.get("properties", {}):
        failures.append("schema must declare the canonical top-level 'entries'")

    entry_props = schema.get("$defs", {}).get("entry", {}).get("properties", {})
    for key in CANONICAL_KEYS:
        if key not in entry_props:
            failures.append(f"schema entry must declare canonical key {key!r}")


def check_positive_cases(cases: list, failures: list) -> None:
    """Run positive normalization fixtures and validate canonical output."""
    for name, doc in cases:
        ok, output = run_normalization(doc)
        if not ok:
            failures.append(f"positive case {name!r} failed: {output}")
            continue
        normalized = json.loads(output)
        if "entries" not in normalized:
            failures.append(f"positive case {name!r} lost the entries array")
            continue
        for entry in normalized["entries"]:
            for key in ("nginx_version", "os", "libc", "target", "artifact_type"):
                if key not in entry:
                    failures.append(
                        f"positive case {name!r} entry missing canonical key {key!r}"
                    )


def check_negative_cases(cases: list, failures: list) -> None:
    """Run negative normalization fixtures; every case must fail closed."""
    for name, doc in cases:
        ok, _ = run_normalization(doc)
        if ok:
            failures.append(f"negative case {name!r} must fail closed")


def main() -> int:
    failures = []

    if not SCHEMA_PATH.is_file():
        print(f"ERROR: schema missing: {SCHEMA_PATH}", file=sys.stderr)
        return 1

    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: schema unreadable: {exc}", file=sys.stderr)
        return 1

    check_schema_shape(schema, failures)
    check_positive_cases(positive_cases(), failures)
    check_negative_cases(negative_cases(), failures)

    digest = schema_digest()
    print(f"release-matrix schema digest: {digest}")
    print("producer: checked-in release matrix schema")
    print("consumers: documentation, matrix tools, and release workflows")

    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1

    print("PASS: release-matrix schema contract and normalization entry point are valid")
    return 0


def positive_cases() -> list:
    """Positive normalization fixtures (canonical, legacy alias, metadata
    drop)."""
    return [
        ("canonical entries", {"schema_version": 1, "entries": [canonical_entry()]}),
        (
            "legacy matrix alias accepted",
            {
                "schema_version": 1,
                "matrix": [
                    {
                        "nginx": "1.26.3",
                        "os_type": "linux",
                        "libc": "glibc",
                        "arch": "x86_64-unknown-linux-gnu",
                        "artifact_type": "rpm",
                    }
                ],
            },
        ),
        (
            "canonical keys with legacy metadata dropped",
            {
                "schema_version": 1,
                "entries": [
                    {
                        **canonical_entry(),
                        "nginx_channel": "stable",
                        "support_tier": "full",
                    }
                ],
            },
        ),
    ]


def negative_cases() -> list:
    """Negative normalization fixtures; every case must fail closed."""
    return [
        ("simultaneous entries and matrix", {
            "schema_version": 1,
            "entries": [canonical_entry()],
            "matrix": [canonical_entry()],
        }),
        ("unknown top-level key", {
            "schema_version": 1,
            "entries": [canonical_entry()],
            "bogus": True,
        }),
        ("unknown entry key", {
            "schema_version": 1,
            "entries": [{**canonical_entry(), "surprise": 1}],
        }),
        ("alias/canonical disagreement", {
            "schema_version": 1,
            "entries": [
                {**canonical_entry(), "nginx": "1.28.0"}
            ],
        }),
        ("missing canonical identity", {
            "schema_version": 1,
            "entries": [{"artifact_type": "deb"}],
        }),
    ]


def canonical_entry() -> dict:
    """A canonical matrix entry fixture."""
    return {
        "nginx_version": "1.26.3",
        "os": "linux",
        "libc": "glibc",
        "target": "x86_64-unknown-linux-gnu",
        "artifact_type": "deb",
        "feature_manifest_digest": "sha256:" + "0" * 64,
        "abi_version": 2,
    }


if __name__ == "__main__":
    sys.exit(main())
