from __future__ import annotations

from tools.release.gates.validate_release_gates import (
    ValidationResult,
    check_compat_capabilities,
    check_compat_row_validity,
    check_compat_states,
    extract_table_under_heading,
)


def test_single_classification_column_matrix_passes():
    content = "\n".join(
        [
            "## Capability Classification Matrix",
            "",
            "| # | Capability | Classification | Notes |",
            "|---|-----------|---------------|-------|",
            "| 1 | automatic decompression | `streaming-supported` | note |",
            "| 2 | charset detection / transcoding | `streaming-supported` | note |",
            "| 3 | security sanitization | `streaming-supported` | note |",
            "| 4 | deterministic output | `streaming-supported` | note |",
            "| 5 | `markdown_timeout` | `streaming-supported` | note |",
            "| 6 | `markdown_max_size` | `streaming-supported` | note |",
            "| 7 | `markdown_token_estimate` | `streaming-supported` | note |",
            "| 8 | `markdown_front_matter` (common head metadata within lookahead) | `streaming-supported` | note |",
            "| 9 | `markdown_front_matter` (metadata beyond lookahead budget) | `pre-commit-fallback-only` | note |",
            "| 10 | `markdown_etag` (response-header ETag) | `full-buffer-only` | note |",
            "| 11 | `markdown_etag` (internal hash) | `streaming-supported` | note |",
            "| 12 | `markdown_conditional_requests` (`if_modified_since_only`) | `streaming-supported` (conditional) | note |",
            "| 13 | `markdown_conditional_requests` (`full_support`) | `full-buffer-only` | note |",
            "| 14 | authenticated request policy / cache-control | `streaming-supported` (conditional) | note |",
            "| 15 | decision logs / reason codes / metrics | `streaming-supported` | note |",
            "| 16 | table conversion | `pre-commit-fallback-only` | note |",
            "| 17 | `prune_noise_regions` | `pre-commit-fallback-only` | note |",
            "| 18 | `markdown_on_wildcard` | `streaming-supported` | note |",
        ]
    )
    rows = extract_table_under_heading(content, "Capability Classification Matrix")
    result = ValidationResult()
    check_compat_capabilities(result, rows)
    state_indices, capability_idx = check_compat_states(result, rows[0])
    check_compat_row_validity(result, rows[1:], state_indices, capability_idx)
    assert not result.has_failures, [r for r in result.results if r[0] == "FAIL"]


def test_single_classification_column_rejects_invalid_state():
    content = "\n".join(
        [
            "## Capability Classification Matrix",
            "",
            "| # | Capability | Classification | Notes |",
            "|---|-----------|---------------|-------|",
            "| 1 | automatic decompression | `secretly-degraded` | note |",
        ]
    )
    rows = extract_table_under_heading(content, "Capability Classification Matrix")
    result = ValidationResult()
    state_indices, capability_idx = check_compat_states(result, rows[0])
    check_compat_row_validity(result, rows[1:], state_indices, capability_idx)
    assert result.has_failures
    fail_detail = next(d for s, _, d in result.results if s == "FAIL")
    assert "invalid state" in fail_detail


def test_single_classification_column_rejects_missing_state_cell():
    content = "\n".join(
        [
            "## Capability Classification Matrix",
            "",
            "| # | Capability | Classification | Notes |",
            "|---|-----------|---------------|-------|",
            "| 1 | automatic decompression |  | note |",
        ]
    )
    rows = extract_table_under_heading(content, "Capability Classification Matrix")
    result = ValidationResult()
    state_indices, capability_idx = check_compat_states(result, rows[0])
    check_compat_row_validity(result, rows[1:], state_indices, capability_idx)
    assert result.has_failures
    fail_detail = next(d for s, _, d in result.results if s == "FAIL")
    assert "missing classification" in fail_detail
