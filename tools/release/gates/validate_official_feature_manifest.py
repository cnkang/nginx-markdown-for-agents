#!/usr/bin/env python3
"""Validate the official build feature manifest.

Checks that `artifacts/release/0.9.2/official-build-feature-manifest.json`
is exactly the three-key object `{"incremental": true, "streaming": true,
"prune_noise_regions": true}`, that the Cargo default features agree with
the manifest, and that no consumer uses the forbidden Cargo feature names
`pruning` or `brotli`.

Consumers must use the manifest's digest rather than re-declaring feature
values; this gate is the drift check for that contract.

Exit codes:
    0 - manifest valid and consistent
    1 - manifest missing, malformed, or inconsistent
"""

import hashlib
import json
import pathlib
import sys
import tomllib
from argparse import ArgumentParser

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
MANIFEST_PATH = (
    REPO_ROOT / "artifacts" / "release" / "0.9.2" / "official-build-feature-manifest.json"
)
CARGO_TOML_PATH = REPO_ROOT / "components" / "rust-converter" / "Cargo.toml"

EXPECTED = {"incremental": True, "streaming": True, "prune_noise_regions": True}
FORBIDDEN_FEATURE_NAMES = {"pruning", "brotli"}


def manifest_digest(manifest: dict) -> str:
    """Compute the canonical digest of the manifest object."""
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def check_cargo_features(cargo_toml: str, failures: list) -> None:
    """Verify Cargo default features agree with the manifest and no
    forbidden feature name is used."""
    try:
        cargo = tomllib.loads(cargo_toml)
    except tomllib.TOMLDecodeError as exc:
        failures.append(f"Cargo.toml is not valid TOML: {exc}")
        return
    features = cargo.get("features")
    if not isinstance(features, dict):
        failures.append("Cargo.toml has no [features] table")
        return
    defaults = features.get("default")
    if not isinstance(defaults, list) or not all(
        isinstance(feature, str) for feature in defaults
    ):
        failures.append("Cargo.toml [features].default must be an array of strings")
        return
    for feature in EXPECTED:
        if feature not in defaults:
            failures.append(
                f"Cargo default features do not include manifest feature {feature!r}"
            )
    for name in FORBIDDEN_FEATURE_NAMES:
        if name in features:
            failures.append(f"forbidden Cargo feature name {name!r} is used")


def load_cargo_toml(failures: list) -> str:
    """Load Cargo.toml or record a failure."""
    if not CARGO_TOML_PATH.is_file():
        failures.append(f"Cargo.toml missing: {CARGO_TOML_PATH}")
        return ""
    try:
        return CARGO_TOML_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        failures.append(f"Cargo.toml unreadable: {exc}")
        return ""


def write_manifest() -> int:
    """Create the ignored build artifact after checking its source inputs."""
    failures: list[str] = []
    check_cargo_features(load_cargo_toml(failures), failures)
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1

    try:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(
            json.dumps(EXPECTED, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        print(f"ERROR: feature manifest could not be written: {exc}", file=sys.stderr)
        return 1

    print(f"Generated official build feature manifest: {MANIFEST_PATH}")
    return 0


def main(argv=None) -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Create the ignored build artifact before validating it.",
    )
    args = parser.parse_args(argv)

    if args.write and write_manifest() != 0:
        return 1

    failures = []

    if not MANIFEST_PATH.is_file():
        print(f"ERROR: feature manifest missing: {MANIFEST_PATH}", file=sys.stderr)
        return 1

    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: feature manifest unreadable: {exc}", file=sys.stderr)
        return 1

    if manifest != EXPECTED:
        failures.append(
            f"manifest keys/values mismatch: expected {EXPECTED}, got {manifest}"
        )

    check_cargo_features(load_cargo_toml(failures), failures)

    digest = manifest_digest(manifest)
    print(f"feature manifest digest: {digest}")

    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1

    print("PASS: official build feature manifest is valid and consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
