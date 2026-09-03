"""Regression tests for the install.sh compatibility projection."""

from __future__ import annotations

import json
from pathlib import Path

from tools.release.matrix import validate_matrix_install_consistency as validator


def test_load_matrix_projects_normalized_target_alias(tmp_path: Path) -> None:
    """Compatibility arch aliases survive normalization into install rows."""
    matrix_path = tmp_path / "release-matrix.json"
    matrix_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "entries": [
                    {
                        "nginx_version": "1.26.3",
                        "libc": "glibc",
                        "arch": "amd64",
                        "artifact_type": "dynamic-module",
                        "support_tier": "supported",
                    },
                    {
                        "nginx_version": "1.26.3",
                        "libc": "musl",
                        "arch": "arm64",
                        "artifact_type": "dynamic-module",
                        "support_tier": "supported",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    rows = validator.load_matrix(matrix_path)

    assert {(row["os_type"], row["arch"]) for row in rows} == {
        ("glibc", "x86_64"),
        ("musl", "aarch64"),
    }


def test_validate_reports_none_arch_without_sorting_error() -> None:
    """Malformed rows must produce validation errors, not a mixed-type sort crash."""
    matrix = [
        {"os_type": "glibc", "arch": "x86_64", "support_tier": "full"},
        {"os_type": "glibc", "arch": None, "support_tier": "full"},
    ]
    install_info = {
        "supported_architectures": {"x86_64"},
        "asset_name_template": validator.EXPECTED_ASSET_TEMPLATE,
        "detectable_os_types": {"glibc"},
        "detectable_archs": {"x86_64"},
    }

    errors = validator.validate(matrix, install_info)

    assert any("unrecognized arch 'None'" in error for error in errors)
