#!/usr/bin/env python3
"""Validate the official build feature manifest.

Checks that `artifacts/release/0.9.2/official-build-feature-manifest.json`
is exactly the two-key object `{"streaming": true,
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
import importlib.util
import json
import pathlib
import sys
import tomllib
from argparse import ArgumentParser
from collections.abc import Iterator

def _load_release_version() -> str:
    """Load the release artifact version without a hardcoded fallback."""
    try:
        from tools.release.matrix.normalize_matrix import RELEASE_VERSION
    except ImportError:
        norm_path = pathlib.Path(__file__).resolve().parents[1] / "matrix" / "normalize_matrix.py"
        norm_spec = importlib.util.spec_from_file_location(
            "normalize_matrix_standalone", str(norm_path)
        )
        if norm_spec is None or norm_spec.loader is None:
            raise ImportError(f"cannot load release matrix normalizer: {norm_path}")
        norm_module = importlib.util.module_from_spec(norm_spec)
        norm_spec.loader.exec_module(norm_module)
        RELEASE_VERSION = getattr(norm_module, "RELEASE_VERSION", None)
    if not isinstance(RELEASE_VERSION, str) or not RELEASE_VERSION:
        raise ValueError("release matrix normalizer does not export RELEASE_VERSION")
    return RELEASE_VERSION


try:
    RELEASE_VERSION = _load_release_version()
    RELEASE_VERSION_ERROR = None
except Exception as exc:  # fail closed in main() with a structured error
    RELEASE_VERSION = None
    RELEASE_VERSION_ERROR = str(exc)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

def _manifest_path() -> pathlib.Path:
    if RELEASE_VERSION is None:
        raise RuntimeError(
            f"release version is unavailable: {RELEASE_VERSION_ERROR}"
        )
    return REPO_ROOT / "artifacts" / "release" / RELEASE_VERSION / "official-build-feature-manifest.json"

def _cargo_toml_path() -> pathlib.Path:
    return REPO_ROOT / "components" / "rust-converter" / "Cargo.toml"

EXPECTED = {"streaming": True, "prune_noise_regions": True}
FORBIDDEN_FEATURE_NAMES = {"pruning", "brotli"}


def manifest_digest(manifest: dict) -> str:
    """Compute the canonical digest of the manifest object."""
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def check_cargo_features(cargo_toml: str, failures: list) -> None:
    """Verify Cargo default features agree with the manifest and no
    forbidden feature name is used by a package feature consumer."""
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
    unexpected = sorted(set(defaults) - set(EXPECTED))
    for feature in unexpected:
        failures.append(
            f"Cargo default feature {feature!r} is not declared by the manifest"
        )
    for name in FORBIDDEN_FEATURE_NAMES:
        if name in features:
            failures.append(f"forbidden Cargo feature name {name!r} is used")

    _check_feature_consumers(cargo, failures)


def _forbidden_feature_consumers(
    value: object, path: tuple[str, ...] = ()
) -> Iterator[tuple[str, str]]:
    """Yield forbidden feature names and their parsed TOML locations."""
    if isinstance(value, dict):
        yield from _forbidden_feature_dict(value, path)
    elif isinstance(value, list):
        yield from _forbidden_feature_list(value, path)


def _forbidden_feature_table(
    table: dict, path: tuple[str, ...]
) -> Iterator[tuple[str, str]]:
    """Walk every list-valued child of a `[features]` table.

    Feature names vary, so each list-valued child is inspected with its
    full path; nested dict children recurse.
    """
    for sub_key, sub_child in table.items():
        sub_path = (*path, str(sub_key))
        if isinstance(sub_child, list):
            yield from _forbidden_feature_values(sub_child, sub_path)
        elif isinstance(sub_child, dict):
            yield from _forbidden_feature_dict(sub_child, sub_path)


def _forbidden_feature_dict(
    value: dict, path: tuple[str, ...]
) -> Iterator[tuple[str, str]]:
    """Walk a parsed TOML table for feature consumers."""
    for key, child in value.items():
        child_path = (*path, str(key))
        if key == "features":
            # A `[features]` table (or `features = [...]` array) may carry
            # any number of list-valued feature declarations.  Inspect every
            # list-valued child with its full path, regardless of the
            # feature name, so a forbidden name inside an arbitrary feature
            # array is still caught.
            if isinstance(child, list):
                yield from _forbidden_feature_values(child, child_path)
            elif isinstance(child, dict):
                yield from _forbidden_feature_table(child, child_path)
            continue
        if isinstance(child, dict) and key in ("features", "options"):
            yield from _forbidden_feature_dict(child, child_path)
            continue
        yield from _forbidden_feature_consumers(child, child_path)


def _forbidden_feature_list(
    value: list, path: tuple[str, ...]
) -> Iterator[tuple[str, str]]:
    """Walk a parsed TOML array for nested feature consumers."""
    for index, child in enumerate(value):
        yield from _forbidden_feature_consumers(child, (*path, str(index)))


def _normalize_feature_token(feature: str) -> tuple[str, list[str]]:
    """Normalize one Cargo feature token into (bare_name, extra_segments).

    Strips the optional-dependency prefix (`dep:`), splits slash-delimited
    dependency/feature tokens so both segments are checked, and strips the
    weak-dependency suffix (`?`) from EACH slash-delimited segment (not
    only from the whole token) so `dep:brotli`, `brotli?`,
    `brotli?/std`, `dependency/brotli`, and `dep:brotli/foo` all match the
    bare names.
    """
    normalized = feature
    if normalized.startswith("dep:"):
        normalized = normalized[4:]
    segments = normalized.split("/") if "/" in normalized else []
    if segments:
        stripped = [seg[:-1] if seg.endswith("?") else seg for seg in segments]
        return stripped[0], stripped[1:]
    if normalized.endswith("?"):
        normalized = normalized[:-1]
    return normalized, []


def _forbidden_feature_values(
    values: list, path: tuple[str, ...]
) -> Iterator[tuple[object, str]]:
    """Yield forbidden values from one Cargo ``features`` array."""
    location = ".".join(path)
    for feature in values:
        if not isinstance(feature, str):
            # TOML accepts arrays containing mixed scalar types.  A malformed
            # feature request must fail closed instead of reaching string
            # normalization and raising an unhandled AttributeError.
            yield f"<non-string:{type(feature).__name__}> {feature!r}", location
            continue
        # Both the dependency segment and the feature segment are
        # checked: a forbidden dependency name must not slip through as
        # the prefix of a slash-delimited token.
        normalized, extra_segments = _normalize_feature_token(feature)
        for segment in extra_segments:
            if segment in FORBIDDEN_FEATURE_NAMES:
                yield feature, location
        if normalized in FORBIDDEN_FEATURE_NAMES:
            yield feature, location


def _check_feature_consumers(value: object, failures: list) -> None:
    """Reject forbidden names in every Cargo ``features = [...]`` consumer.

    Checking only the package ``[features]`` table misses dependency feature
    requests such as ``some-parser = { features = ["brotli"] }``.  Walk the
    parsed TOML tree so target-specific and build/dev dependency tables use
    the same allowlist as the default feature contract.
    """
    for feature, location in _forbidden_feature_consumers(value):
        failures.append(
            f"forbidden Cargo feature name {feature!r} is used at {location}"
        )


def load_cargo_toml(failures: list) -> str:
    """Load Cargo.toml or record a failure."""
    cargo_path = _cargo_toml_path()
    if not cargo_path.is_file():
        failures.append(f"Cargo.toml missing: {cargo_path}")
        return ""
    try:
        return cargo_path.read_text(encoding="utf-8")
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
        _manifest_path().parent.mkdir(parents=True, exist_ok=True)
        _manifest_path().write_text(
            json.dumps(EXPECTED, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        print(f"ERROR: feature manifest could not be written: {exc}", file=sys.stderr)
        return 1

    print(f"Generated official build feature manifest: {_manifest_path()}")
    return 0


def main(argv=None) -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Create the ignored build artifact before validating it.",
    )
    args = parser.parse_args(argv)

    if RELEASE_VERSION is None:
        print(
            f"ERROR: cannot resolve release artifact version: {RELEASE_VERSION_ERROR}",
            file=sys.stderr,
        )
        return 1

    if args.write and write_manifest() != 0:
        return 1

    failures = []

    if not _manifest_path().is_file():
        print(f"ERROR: feature manifest missing: {_manifest_path()}", file=sys.stderr)
        return 1

    try:
        manifest = json.loads(_manifest_path().read_text(encoding="utf-8"))
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
