"""Unit tests for tools/release/matrix/validate_doc_matrix_sync.py."""

import json
import sys
from pathlib import Path


_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.release.matrix.validate_doc_matrix_sync import compare_matrices, parse_doc_matrix, load_matrix_entries


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


def test_tier_normalization_source_only_variants(tmp_path):
    """Regression: 'Source Only' (docs) and 'source_only' (JSON) must be treated as equal."""
    matrix_path = tmp_path / "release-matrix.json"
    matrix_path.write_text(
        json.dumps(
            {
                "matrix": [
                    {"nginx": "1.26.3", "os_type": "musl", "arch": "aarch64", "support_tier": "source_only"},
                ]
            }
        ),
        encoding="utf-8",
    )

    json_entries = load_matrix_entries(matrix_path)

    # Exercise parse_doc_matrix with the human-facing "Source Only" tier
    doc_path = tmp_path / "INSTALLATION.md"
    doc_path.write_text(
        "\n".join(
            [
                "## Platform Compatibility Matrix",
                "| NGINX Version | OS Type | Architecture | Support Tier |",
                "|---------------|---------|--------------|--------------|",
                "| 1.26.3 | musl | aarch64 | Source Only |",
            ]
        ),
        encoding="utf-8",
    )
    doc_entries = parse_doc_matrix(doc_path)

    diffs = compare_matrices(json_entries, doc_entries)
    assert not diffs, f"Expected no differences but got: {diffs}"
