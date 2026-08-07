"""Regression tests for tools/release/matrix/validate_release_matrix.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.release.matrix import validate_release_matrix as validator


@pytest.fixture
def fake_matrix_root(tmp_path: Path) -> Path:
    """Materialize the repo surface the validator reads under tmp_path."""
    root = tmp_path
    (root / "docs/releases").mkdir(parents=True)
    (root / "schemas").mkdir(parents=True)
    (root / "tools/release/matrix").mkdir(parents=True)
    (root / "artifacts/release/0.9.2").mkdir(parents=True)
    (root / "components/rust-converter/include").mkdir(parents=True)
    (root / "schemas/release-matrix.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "required": ["schema_version", "entries"],
                "properties": {
                    "schema_version": {"const": 1},
                    "entries": {"type": "array", "items": {"type": "object"}},
                },
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    (root / "artifacts/release/0.9.2/official-build-feature-manifest.json").write_text(
        json.dumps({"streaming": True, "incremental": True, "prune_noise_regions": True}),
        encoding="utf-8",
    )
    (root / "components/rust-converter/include/markdown_converter.h").write_text(
        "#define MARKDOWN_ABI_VERSION 2\n",
        encoding="utf-8",
    )
    return root


def canonical_entry() -> dict:
    digest = "sha256:" + "9d" * 32
    return {
        "nginx_version": "1.26.3",
        "os": "debian12",
        "libc": "glibc",
        "target": "x86_64-unknown-linux-gnu",
        "artifact_type": "deb",
        "feature_manifest_digest": digest,
        "abi_version": 2,
    }


def write_matrix(root: Path, doc: dict) -> None:
    (root / "docs/releases/release-matrix.json").write_text(
        json.dumps(doc), encoding="utf-8"
    )


def expected_digest() -> str:
    manifest = {"streaming": True, "incremental": True, "prune_noise_regions": True}
    import hashlib

    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_valid_matrix_passes(monkeypatch: pytest.MonkeyPatch, fake_matrix_root: Path) -> None:
    """A canonical, bound matrix must pass all checks."""
    for path_attr in (
        "MATRIX_PATH",
        "SCHEMA_PATH",
        "FEATURE_MANIFEST_PATH",
        "ABI_HEADER_PATH",
    ):
        monkeypatch.setattr(
            validator, path_attr, fake_matrix_root / getattr(validator, path_attr).relative_to(validator.REPO_ROOT)
        )
    # NORMALIZE_PATH stays real: the subprocess entry point is the shared
    # repository tool, which reads whatever MATRIX_PATH the validator passes.
    monkeypatch.setattr(
        validator, "NORMALIZE_PATH", validator.REPO_ROOT / "tools/release/matrix/normalize_matrix.py"
    )
    write_matrix(
        fake_matrix_root,
        {"schema_version": 1, "entries": [{**canonical_entry(), "feature_manifest_digest": expected_digest()}]},
    )
    assert validator.main() == 0


def test_alias_usage_fails(
    monkeypatch: pytest.MonkeyPatch, fake_matrix_root: Path
) -> None:
    """Legacy alias keys in the canonical doc must fail closed."""
    for path_attr in (
        "MATRIX_PATH",
        "SCHEMA_PATH",
        "FEATURE_MANIFEST_PATH",
        "ABI_HEADER_PATH",
    ):
        monkeypatch.setattr(
            validator, path_attr, fake_matrix_root / getattr(validator, path_attr).relative_to(validator.REPO_ROOT)
        )
    # NORMALIZE_PATH stays real: the subprocess entry point is the shared
    # repository tool, which reads whatever MATRIX_PATH the validator passes.
    monkeypatch.setattr(
        validator, "NORMALIZE_PATH", validator.REPO_ROOT / "tools/release/matrix/normalize_matrix.py"
    )
    doc = {
        "schema_version": 1,
        "entries": [
            {
                "nginx": "1.26.3",
                "os_type": "linux",
                "libc": "glibc",
                "arch": "x86_64-unknown-linux-gnu",
                "artifact_type": "deb",
                "feature_manifest_digest": expected_digest(),
                "abi_version": 2,
            }
        ],
    }
    write_matrix(fake_matrix_root, doc)
    with pytest.raises(SystemExit, match="legacy alias"):
        validator.main()


def test_stale_feature_digest_fails(
    monkeypatch: pytest.MonkeyPatch, fake_matrix_root: Path
) -> None:
    """A stale feature-manifest digest must fail closed."""
    for path_attr in (
        "MATRIX_PATH",
        "SCHEMA_PATH",
        "FEATURE_MANIFEST_PATH",
        "ABI_HEADER_PATH",
    ):
        monkeypatch.setattr(
            validator, path_attr, fake_matrix_root / getattr(validator, path_attr).relative_to(validator.REPO_ROOT)
        )
    # NORMALIZE_PATH stays real: the subprocess entry point is the shared
    # repository tool, which reads whatever MATRIX_PATH the validator passes.
    monkeypatch.setattr(
        validator, "NORMALIZE_PATH", validator.REPO_ROOT / "tools/release/matrix/normalize_matrix.py"
    )
    write_matrix(
        fake_matrix_root,
        {
            "schema_version": 1,
            "entries": [
                {
                    **canonical_entry(),
                    "feature_manifest_digest": "sha256:" + "0" * 64,
                }
            ],
        },
    )
    with pytest.raises(SystemExit, match="binding drift"):
        validator.main()


def test_mismatched_abi_fails(
    monkeypatch: pytest.MonkeyPatch, fake_matrix_root: Path
) -> None:
    """A mismatched ABI version must fail closed."""
    for path_attr in (
        "MATRIX_PATH",
        "SCHEMA_PATH",
        "FEATURE_MANIFEST_PATH",
        "ABI_HEADER_PATH",
    ):
        monkeypatch.setattr(
            validator, path_attr, fake_matrix_root / getattr(validator, path_attr).relative_to(validator.REPO_ROOT)
        )
    # NORMALIZE_PATH stays real: the subprocess entry point is the shared
    # repository tool, which reads whatever MATRIX_PATH the validator passes.
    monkeypatch.setattr(
        validator, "NORMALIZE_PATH", validator.REPO_ROOT / "tools/release/matrix/normalize_matrix.py"
    )
    write_matrix(
        fake_matrix_root,
        {
            "schema_version": 1,
            "entries": [{**canonical_entry(), "abi_version": 1}],
        },
    )
    with pytest.raises(SystemExit, match="binding drift"):
        validator.main()


def test_missing_matrix_fails(
    monkeypatch: pytest.MonkeyPatch, fake_matrix_root: Path
) -> None:
    """A missing matrix document must fail closed."""
    for path_attr in (
        "MATRIX_PATH",
        "SCHEMA_PATH",
        "FEATURE_MANIFEST_PATH",
        "ABI_HEADER_PATH",
    ):
        monkeypatch.setattr(
            validator, path_attr, fake_matrix_root / getattr(validator, path_attr).relative_to(validator.REPO_ROOT)
        )
    # NORMALIZE_PATH stays real: the subprocess entry point is the shared
    # repository tool, which reads whatever MATRIX_PATH the validator passes.
    monkeypatch.setattr(
        validator, "NORMALIZE_PATH", validator.REPO_ROOT / "tools/release/matrix/normalize_matrix.py"
    )
    assert validator.main() == 1


def test_feature_manifest_digest_convention() -> None:
    """The digest must use the canonical-content convention (sorted keys)."""
    digest = validator.feature_manifest_digest()
    assert digest.startswith("sha256:")
    assert len(digest) == 7 + 64
