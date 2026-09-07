"""Regression tests for malformed release-manifest package entries."""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "packaging" / "scripts" / "validate-release-manifest.py"
SPEC = importlib.util.spec_from_file_location("validate_release_manifest", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def test_non_object_package_entry_fails_closed_without_crashing(tmp_path: Path) -> None:
    """A malformed package entry produces a validation error, not an exception."""
    manifest_path = tmp_path / "release-manifest.json"
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    manifest_path.write_text(
        '{"schema_version": 1, '
        '"project": "nginx-markdown-for-agents", '
        '"version": "0.9.2", '
        '"git": {"repository": "example/repository", '
        '"commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}, '
        '"packages": [null], '
        '"integrity": {"checksums": "SHA256SUMS", '
        '"signature": null, "signature_available": false, '
        '"signature_type": null, "signed_file": null}, '
        '"workflow": {"ref_type": "workflow_dispatch"}}',
        encoding="utf-8",
    )

    errors = VALIDATOR.validate_manifest(
        manifest_path, artifact_dir, None, expected_version=None
    )

    assert errors == ["packages[0]: package must be an object"]


def test_dynamic_module_non_string_libc_or_arch_rejected(tmp_path: Path) -> None:
    """A dynamic-module entry with a non-string libc or arch fails closed."""
    manifest_path = tmp_path / "release-manifest.json"
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    manifest_path.write_text(
        '{"schema_version": 1, '
        '"project": "nginx-markdown-for-agents", '
        '"version": "0.9.2", '
        '"git": {"repository": "example/repository", '
        '"commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}, '
        '"packages": ['
        '{"filename": "nginx-markdown-module-1.26.3-x86_64.tar.gz", '
        '"format": "dynamic-module", "nginx_version": "1.26.3", '
        '"libc": null, "arch": 64, "sha256": "'
        + "a" * 64 + '"}], '
        '"integrity": {"checksums": "SHA256SUMS", '
        '"signature": null, "signature_available": false, '
        '"signature_type": null, "signed_file": null}, '
        '"workflow": {"ref_type": "workflow_dispatch"}}',
        encoding="utf-8",
    )

    errors = VALIDATOR.validate_manifest(
        manifest_path, artifact_dir, None, expected_version=None
    )

    assert "packages[0]: libc must be a string, got: NoneType" in errors
    assert "packages[0]: arch must be a string, got: int" in errors
