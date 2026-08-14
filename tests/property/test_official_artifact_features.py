# Feature: official release artifact feature-set consistency
"""Property-based tests for official release artifact feature-set consistency
(Property 27).

For any official release artifact (DEB, RPM, container, Homebrew), the
compiled module SHALL use the same fixed feature set (incremental,
streaming, prune_noise_regions all enabled). Custom source builds MAY
disable features but SHALL expose their capability set through build_info
diagnostics. No CI matrix tests all feature-flag combinations.

This model encodes the official feature manifest contract as a single
source of truth that must stay in sync with the Cargo default features,
the official-build-feature-manifest.json artifact, and every official
artifact producer workflow (release-packages, release-deb, release-rpm,
nightly-perf module baseline). It does NOT link against C code.

Validates: Requirements 14.2, 15.16

Run:
    python3 -m pytest tests/property/test_official_artifact_features.py -v
"""

from __future__ import annotations

import json
import pathlib
import re
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

OFFICIAL_FEATURES = frozenset({"incremental", "streaming", "prune_noise_regions"})


def _resolve_manifest_path() -> pathlib.Path:
    """Resolve the official-build-feature-manifest.json under artifacts/release.

    The release version directory is not hardcoded: pick the highest semver
    version directory that ships the manifest, so bumping the release version
    does not silently point this test at a stale artifact.
    """
    release_dir = REPO_ROOT / "artifacts" / "release"
    manifests = list(release_dir.glob("*/official-build-feature-manifest.json"))
    assert manifests, (
        "no official-build-feature-manifest.json under artifacts/release/"
    )

    def _version_key(path: pathlib.Path) -> tuple[int, ...]:
        return tuple(int(part) for part in re.findall(r"\d+", path.parent.name))

    return sorted(manifests, key=_version_key, reverse=True)[0]


CARGO_TOML_PATH = REPO_ROOT / "components" / "rust-converter" / "Cargo.toml"

WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# Official artifact producer workflows. Every one of these SHALL build the
# module with the same fixed feature set.
OFFICIAL_PRODUCER_WORKFLOWS = (
    "release-packages.yml",
    "release-deb.yml",
    "release-rpm.yml",
    "nightly-perf.yml",
)

# Non-official (custom/supplemental) build surfaces. They MAY omit or
# disable features, but if they reference a feature list they must not
# disable a feature the official set requires.
CUSTOM_BUILD_WORKFLOWS = (
    "release-binaries.yml",
    "macos-smoke.yml",
    "real-nginx-ims.yml",
    "official-nginx-docker.yml",
)


def _load_manifest() -> dict:
    return json.loads(_resolve_manifest_path().read_text(encoding="utf-8"))


def _cargo_default_features() -> set[str]:
    text = CARGO_TOML_PATH.read_text(encoding="utf-8")
    match = re.search(r"^default\s*=\s*\[([^\]]*)\]", text, re.MULTILINE)
    assert match is not None, "Cargo.toml must declare a default feature list"
    return {name.strip().strip('"') for name in match.group(1).split(",") if name.strip()}


def _workflow_feature_assignments(path: pathlib.Path) -> list[str]:
    """Extract RUST_FEATURES assignment values from a workflow file.

    Each assignment is captured in full (including spaces) and normalized
    to a sorted, comma-joined feature list, so equivalent assignments that
    differ only in spacing or order compare equal.
    """
    text = path.read_text(encoding="utf-8")
    return [
        ",".join(
            sorted(f.strip() for f in match.split(",") if f.strip())
        )
        for match in re.findall(
            r"RUST_FEATURES\s*:\s*['\"]?([A-Za-z0-9_, ]+)['\"]?",
            text,
        )
        if match.strip()
    ]


def _workflow_feature_flags(path: pathlib.Path) -> list[set[str]]:
    """Extract `--features X` or `--no-default-features` usage."""
    text = path.read_text(encoding="utf-8")
    no_default = "no-default-features" in text
    flags = re.findall(
        r"--features(?:=|\s+)[\"']?([A-Za-z0-9_,]+)[\"']?",
        text,
    )
    parsed = [set(f.split(",")) for f in flags]
    if no_default:
        parsed.append(set())
    return parsed


def test_official_feature_manifest_is_exact_three_key_object() -> None:
    """The official build feature manifest is exactly {incremental: true,
    streaming: true, prune_noise_regions: true}."""
    manifest = _load_manifest()
    assert manifest == {"incremental": True, "streaming": True, "prune_noise_regions": True}


def test_cargo_default_features_match_official_manifest() -> None:
    """Cargo default features equal the official feature set exactly."""
    manifest = _load_manifest()
    assert _cargo_default_features() == set(manifest.keys()) == OFFICIAL_FEATURES


@pytest.mark.parametrize("workflow", OFFICIAL_PRODUCER_WORKFLOWS)
def test_official_producer_uses_fixed_feature_set(workflow: str) -> None:
    """Every official artifact producer builds with the same fixed feature
    set; no producer may add, omit, or disable a feature."""
    path = WORKFLOW_DIR / workflow
    if not path.is_file():
        pytest.skip(f"{workflow} not present in this checkout")
    feature_sets = _workflow_feature_flags(path)
    if not feature_sets:
        return  # producer relies on Cargo defaults, which the other tests pin
    for features in feature_sets:
        assert features == set(OFFICIAL_FEATURES), (
            f"{workflow} must use exactly the official feature set "
            f"{sorted(OFFICIAL_FEATURES)}, got {sorted(features)}"
        )


@pytest.mark.parametrize("workflow", OFFICIAL_PRODUCER_WORKFLOWS)
def test_official_producer_no_feature_flag_matrix(workflow: str) -> None:
    """No official producer workflow runs a feature-flag combination
    matrix; a single fixed feature set is used."""
    path = WORKFLOW_DIR / workflow
    if not path.is_file():
        pytest.skip(f"{workflow} not present in this checkout")
    feature_sets = _workflow_feature_flags(path)
    assert len(set(frozenset(s) for s in feature_sets)) <= 1, (
        f"{workflow} varies feature flags across jobs/matrix entries"
    )


@pytest.mark.parametrize("workflow", OFFICIAL_PRODUCER_WORKFLOWS)
def test_official_producer_feature_assignments_consistent(workflow: str) -> None:
    """RUST_FEATURES env assignments in official producers are consistent
    across the file (no per-matrix divergence)."""
    path = WORKFLOW_DIR / workflow
    if not path.is_file():
        pytest.skip(f"{workflow} not present in this checkout")
    assignments = _workflow_feature_assignments(path)
    assert len(set(assignments)) <= 1, (
        f"{workflow} has divergent RUST_FEATURES assignments: {assignments}"
    )
    for assignment in assignments:
        assert set(assignment.split(",")) == set(OFFICIAL_FEATURES), (
            f"{workflow} must set RUST_FEATURES to exactly "
            f"{sorted(OFFICIAL_FEATURES)}, got {assignment!r}"
        )


def test_custom_builds_never_disable_required_feature_via_no_default() -> None:
    """Custom build surfaces must not use --no-default-features (which would
    disable the official set)."""
    for workflow in CUSTOM_BUILD_WORKFLOWS:
        path = WORKFLOW_DIR / workflow
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "no-default-features" not in text, (
            f"{workflow} disables default features, breaking the official set"
        )


@pytest.mark.parametrize("workflow", OFFICIAL_PRODUCER_WORKFLOWS)
def test_official_producer_does_not_disable_required_features(workflow: str) -> None:
    """Every explicit feature list contains the complete official set."""
    path = WORKFLOW_DIR / workflow
    if not path.is_file():
        pytest.skip(f"{workflow} not present in this checkout")
    text = path.read_text(encoding="utf-8")
    assert "no-default-features" not in text
    for features in _workflow_feature_flags(path):
        assert features == set(OFFICIAL_FEATURES), (
            f"{workflow} declares a feature subset: {sorted(features)}"
        )
