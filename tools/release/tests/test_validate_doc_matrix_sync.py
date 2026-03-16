"""Unit tests for tools/release/validate_doc_matrix_sync.py."""

import json
import sys
from pathlib import Path


_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.release.validate_doc_matrix_sync import compare_matrices, parse_doc_matrix


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
        ("1.26.3", "musl", "aarch64", "source only"),
    ]


def test_parse_doc_matrix_ignores_malformed_rows(tmp_path):
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
        ("1.26.3", "glibc", "x86_64", "source only"),
        ("1.28.0", "glibc", "x86_64", "full"),
    ]

    diffs = compare_matrices(json_entries, doc_entries)

    assert any("In JSON but missing from doc" in diff for diff in diffs)
    assert any("In doc but missing from JSON" in diff for diff in diffs)
    assert any("Tier mismatch" in diff for diff in diffs)
