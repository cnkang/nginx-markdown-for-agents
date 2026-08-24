"""Unit tests for tools/release/matrix/validate_doc_matrix_sync.py."""

import json
import sys
from pathlib import Path


_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.release.matrix.validate_doc_matrix_sync import (  # noqa: E402 - path bootstrap
    compare_matrices,
    parse_doc_matrix,
    load_matrix_entries,
)


def test_parse_doc_matrix_extracts_rows_from_matrix_section(tmp_path):
    doc_path = tmp_path / "INSTALLATION.md"
    doc_path.write_text(
        "\n".join(
            [
                "# Intro",
                "",
                "## Platform Compatibility Matrix",
                "| NGINX Version | OS Type | Architecture | Support Tier |",
                "|---------------|---------|--------------|--------------|",
                "| 1.26.3 | glibc | x86_64 | Full |",
                "| 1.26.3 | musl | aarch64 | Source Only |",
                "",
                "## Next Section",
            ]
        ),
        encoding="utf-8",
    )

    assert parse_doc_matrix(doc_path) == [
        ("1.26.3", "glibc", "x86_64", "full"),
        ("1.26.3", "musl", "aarch64", "source_only"),
    ]


def test_parse_doc_matrix_ignores_malformed_rows(tmp_path):
    """
    Ensures parse_doc_matrix ignores malformed Markdown table rows and returns only well-formed entries.

    Writes an INSTALLATION.md containing a "Platform Compatibility Matrix" table with malformed rows (extra columns and missing columns) and asserts that parse_doc_matrix yields a single normalized entry for the valid row.
    """
    doc_path = tmp_path / "INSTALLATION.md"
    doc_path.write_text(
        "\n".join(
            [
                "## Platform Compatibility Matrix",
                "| NGINX Version | OS Type | Architecture | Support Tier |",
                "|---------------|---------|--------------|--------------|",
                "| 1.26.3 | glibc | x86_64 | Full | extra |",
                "| 1.26.3 | glibc | x86_64 |",
                "| 1.26.3 | glibc | x86_64 | Full |",
            ]
        ),
        encoding="utf-8",
    )

    assert parse_doc_matrix(doc_path) == [
        ("1.26.3", "glibc", "x86_64", "full"),
    ]


def test_compare_matrices_reports_missing_and_tier_mismatches():
    json_entries = [
        ("1.26.3", "glibc", "x86_64", "full"),
        ("1.26.3", "musl", "aarch64", "source_only"),
    ]
    doc_entries = [
        ("1.26.3", "glibc", "x86_64", "source_only"),
        ("1.28.0", "glibc", "x86_64", "full"),
    ]

    diffs = compare_matrices(json_entries, doc_entries)

    expected_diffs = {
        "In JSON but missing from doc: nginx=1.26.3 os_type=musl arch=aarch64 tier=source_only",
        "In doc but missing from JSON: nginx=1.28.0 os_type=glibc arch=x86_64 tier=full",
        "Tier mismatch for nginx=1.26.3 os_type=glibc arch=x86_64: JSON=full, doc=source_only",
    }
    assert set(diffs) == expected_diffs, f"Unexpected diffs: {diffs}"


def test_tier_normalization_supported_display_label(tmp_path):
    """Regression: the legacy "Full" table label maps to canonical supported."""
    matrix_path = tmp_path / "release-matrix.json"
    matrix_path.write_text(
        json.dumps(
            {
                "entries": [
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

    json_entries = load_matrix_entries(matrix_path)

    # Exercise parse_doc_matrix with the historical human-facing "Full" tier.
    doc_path = tmp_path / "INSTALLATION.md"
    doc_path.write_text(
        "\n".join(
            [
                "## Platform Compatibility Matrix",
                "| NGINX Version | OS Type | Architecture | Support Tier |",
                "|---------------|---------|--------------|--------------|",
                "| 1.26.3 | musl | aarch64 | Full |",
            ]
        ),
        encoding="utf-8",
    )
    doc_entries = parse_doc_matrix(doc_path)

    diffs = compare_matrices(json_entries, doc_entries)
    assert not diffs, f"Expected no differences but got: {diffs}"


def test_load_matrix_entries_retains_source_only_row(tmp_path):
    """Best-effort source support remains comparable with Source Only docs."""
    matrix_path = tmp_path / "release-matrix.json"
    matrix_path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "nginx_version": "1.26.3",
                        "libc": "n/a",
                        "arch": "any",
                        "artifact_type": "source",
                        "support_tier": "best-effort",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    assert load_matrix_entries(matrix_path) == [
        ("1.26.3", "n/a", "any", "source_only"),
    ]


def test_load_matrix_entries_omits_redundant_source_only_row(tmp_path):
    """A source fallback row is hidden when binary coverage exists."""
    matrix_path = tmp_path / "release-matrix.json"
    matrix_path.write_text(
        json.dumps(
            {
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
                        "libc": "n/a",
                        "arch": "any",
                        "artifact_type": "source",
                        "support_tier": "best-effort",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    assert load_matrix_entries(matrix_path) == [
        ("1.26.3", "glibc", "x86_64", "full"),
    ]
