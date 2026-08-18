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
