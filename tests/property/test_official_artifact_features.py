# Feature: official release artifact feature-set consistency
"""Property-based tests for official release artifact feature-set consistency
(Property 27).

For any official release artifact (DEB, RPM, container, Homebrew), the
compiled module SHALL use the same fixed feature set (streaming,
prune_noise_regions all enabled). Custom source builds MAY
disable features but SHALL expose their capability set through build_info
diagnostics. No CI matrix tests all feature-flag combinations.

This model encodes the official feature manifest contract as a single
source of truth that must stay in sync with the Cargo default features,
the official-build-feature-manifest.json artifact, and every official
artifact producer workflow (release-packages, release-rpm, nightly-perf
module baseline). It does NOT link against C code.

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

OFFICIAL_FEATURES = frozenset({"streaming", "prune_noise_regions"})


def _resolve_manifest_path() -> pathlib.Path:
    """Resolve the official-build-feature-manifest.json under artifacts/release.

    The release version directory is not hardcoded: pick the highest semver
    version directory that ships the manifest, so bumping the release version
    does not silently point this test at a stale artifact.
    """
    repo_root = REPO_ROOT.resolve()
    release_dir = (repo_root / "artifacts" / "release").resolve()
    manifests = [path.resolve() for path in release_dir.glob(
        "*/official-build-feature-manifest.json"
    )]
    manifests = [
        path for path in manifests
        if path.is_relative_to(release_dir) and path.is_file()
    ]
    assert manifests, (
        "no official-build-feature-manifest.json under artifacts/release/"
    )

    def _version_key(path: pathlib.Path) -> tuple:
        name = path.parent.name
        # Validate the directory name as SemVer (2.0.0) before using it in
        # sorting or artifact selection: reject names such as "backup-2027",
        # "1.2.3.4", or "01.2.3" that are not valid release versions.
        # Numeric identifiers (major/minor/patch and numeric prerelease
        # identifiers) must not carry leading zeros.
        if not re.fullmatch(
            r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
            r"(?:-(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
            r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*)?"
            r"(?:\+[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*)?",
            name,
        ):
            raise ValueError(
                f"release directory name is not a valid SemVer: {name!r}"
            )
        base, separator, pre = name.partition("-")
        main = tuple(int(part) for part in re.findall(r"\d+", base))
        if not separator:
            # Stable release: ranks above every prerelease of the same
            # main version (SemVer precedence).
            return (main, 1, ())
        # SemVer prerelease ordering: dot-separated identifiers compared
        # left to right; numeric identifiers rank lower than non-numeric
        # ones at the same position and compare numerically; missing
        # identifiers sort before present ones.  The directory name uses
        # a single "-" separator (for example 0.9.2-beta.1), so the
        # prerelease string is the whole suffix.
        pre_idents = pre.split(".")
        key_idents = []
        for ident in pre_idents:
            if ident.isdigit():
                # Numeric identifiers have LOWER precedence than
                # non-numeric ones at the same position, so the numeric
                # marker (0) sorts below the non-numeric marker (1) under
                # reverse=True.
                key_idents.append((0, int(ident), ""))
            else:
                key_idents.append((1, 0, ident))
        # reverse=True sorting: a shorter identifier list (fewer dots)
        # sorts after a longer one when all shared identifiers are equal,
        # matching SemVer's "larger set of pre-release fields has a
        # higher precedence".
        return (main, 0, key_idents, len(pre_idents))

    return sorted(manifests, key=_version_key, reverse=True)[0]


CARGO_TOML_PATH = REPO_ROOT / "components" / "rust-converter" / "Cargo.toml"

WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# Official artifact producer workflows. Every one of these SHALL build the
# module with the same fixed feature set.
OFFICIAL_PRODUCER_WORKFLOWS = (
    "release-packages.yml",
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
    manifest_path = _resolve_manifest_path().resolve()
    assert manifest_path.is_file(), f"manifest is not a regular file: {manifest_path}"
    return json.loads(manifest_path.read_text(encoding="utf-8"))


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


def _workflow_feature_sentinels(path: pathlib.Path) -> dict[str, bool]:
    """Return Cargo feature-mode switches separately from feature lists."""
    text = path.read_text(encoding="utf-8")
    return {
        "all_features": bool(re.search(r"--all-features(?:\s|$)", text)),
        "no_default_features": bool(
            re.search(r"--no-default-features(?:\s|$)", text)
        ),
    }


def _workflow_feature_flags(path: pathlib.Path) -> list[set[str]]:
    """Extract only explicit `--features X` lists.

    ``--all-features`` and ``--no-default-features`` are modes, not feature
    sets. Keeping them out of this list prevents a sentinel from looking like
    a second matrix entry or an empty explicit declaration.
    """
    text = path.read_text(encoding="utf-8")
    flags = re.findall(
        r"--features(?:=|\s+)[\"']?([A-Za-z0-9_,][A-Za-z0-9_, ]*)[\"']?",
        text,
    )
    parsed = [
        {f.strip() for f in re.split(r"[,\s]+", flag) if f.strip()}
        for flag in flags
    ]
    return parsed


def test_official_feature_manifest_is_exact_two_key_object() -> None:
    """The official build feature manifest contains only active features."""
    manifest = _load_manifest()
    assert manifest == {"streaming": True, "prune_noise_regions": True}


def test_cargo_default_features_match_official_manifest() -> None:
    """Cargo default features equal the official feature set exactly."""
    manifest = _load_manifest()
    assert _cargo_default_features() == set(manifest.keys()) == OFFICIAL_FEATURES


@pytest.mark.parametrize("workflow", OFFICIAL_PRODUCER_WORKFLOWS)
def test_official_producer_exposes_feature_declaration(workflow: str) -> None:
    """Every official artifact producer must expose at least one feature
    declaration or assignment before its contents are validated.  A producer
    with no feature signal would silently skip the fixed-feature-set check
    and could drift from the official feature set unnoticed."""
    path = WORKFLOW_DIR / workflow
    assert path.is_file(), f"{workflow} must exist in this checkout"
    flags = _workflow_feature_flags(path)
    assignments = _workflow_feature_assignments(path)
    sentinels = _workflow_feature_sentinels(path)
    assert flags or assignments or any(sentinels.values()), (
        f"{workflow} must declare a feature set (--features/--all-features/"
        f"--no-default-features or RUST_FEATURES) so the official "
        f"feature-set contract can be validated"
    )


@pytest.mark.parametrize("workflow", OFFICIAL_PRODUCER_WORKFLOWS)
def test_official_producer_uses_fixed_feature_set(workflow: str) -> None:
    """Every official artifact producer builds with the same fixed feature
    set; no producer may add, omit, or disable a feature."""
    path = WORKFLOW_DIR / workflow
    assert path.is_file(), f"{workflow} must exist in this checkout"
    feature_sets = _workflow_feature_flags(path)
    sentinels = _workflow_feature_sentinels(path)
    assert not sentinels["all_features"], (
        f"{workflow} must not use --all-features for an official build"
    )
    assert not sentinels["no_default_features"], (
        f"{workflow} must not disable Cargo defaults for an official build"
    )
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
    assert path.is_file(), f"{workflow} must exist in this checkout"
    feature_sets = _workflow_feature_flags(path)
    assert len(set(frozenset(s) for s in feature_sets)) <= 1, (
        f"{workflow} varies feature flags across jobs/matrix entries"
    )


@pytest.mark.parametrize("workflow", OFFICIAL_PRODUCER_WORKFLOWS)
def test_official_producer_feature_assignments_consistent(workflow: str) -> None:
    """RUST_FEATURES env assignments in official producers are consistent
    across the file (no per-matrix divergence)."""
    path = WORKFLOW_DIR / workflow
    assert path.is_file(), f"{workflow} must exist in this checkout"
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
    """Custom build surfaces that use --no-default-features must still
    declare the complete official feature set explicitly."""
    for workflow in CUSTOM_BUILD_WORKFLOWS:
        path = WORKFLOW_DIR / workflow
        if not path.is_file():
            continue
        sentinels = _workflow_feature_sentinels(path)
        if not sentinels["no_default_features"]:
            continue
        flags = _workflow_feature_flags(path)
        explicit = [s for s in flags if s]
        assert any(s == set(OFFICIAL_FEATURES) for s in explicit), (
            f"{workflow} uses --no-default-features without the full "
            f"official feature set {sorted(OFFICIAL_FEATURES)}"
        )


@pytest.mark.parametrize("workflow", OFFICIAL_PRODUCER_WORKFLOWS)
def test_official_producer_does_not_disable_required_features(workflow: str) -> None:
    """Every explicit feature list contains the complete official set."""
    path = WORKFLOW_DIR / workflow
    assert path.is_file(), f"{workflow} must exist in this checkout"
    sentinels = _workflow_feature_sentinels(path)
    assert not sentinels["no_default_features"]
    assert not sentinels["all_features"]
    for features in _workflow_feature_flags(path):
        assert features == set(OFFICIAL_FEATURES), (
            f"{workflow} declares a feature subset: {sorted(features)}"
        )


def test_feature_flag_parser_separates_cargo_sentinels(tmp_path: pathlib.Path) -> None:
    """Cargo mode switches must not masquerade as explicit feature sets."""
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        "run: cargo build --no-default-features "
        "--features streaming,prune_noise_regions "
        "--all-features\n",
        encoding="utf-8",
    )

    assert _workflow_feature_flags(workflow) == [set(OFFICIAL_FEATURES)]
    assert _workflow_feature_sentinels(workflow) == {
        "all_features": True,
        "no_default_features": True,
    }
