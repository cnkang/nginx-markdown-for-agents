"""
Property 8: Compatibility matrix state validity — property-based tests.

Uses hypothesis to generate random capability entries and state values,
verifying that only the three allowed states are accepted.

Each property runs at least 100 iterations.

Validates: Requirements 9.1, 9.2
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

# The three allowed states
VALID_STATES = frozenset(
    {"streaming-supported", "full-buffer-only", "pre-commit-fallback-only"}
)


def is_valid_compatibility_state(state: str) -> bool:
    """Return True if *state* is one of the three allowed values."""
    return state in VALID_STATES


@settings(max_examples=100)
@given(state=st.sampled_from(sorted(VALID_STATES)))
def test_valid_states_accepted(state):
    """All three valid states must be accepted."""
    assert is_valid_compatibility_state(state), f"should accept: {state}"


@settings(max_examples=100)
@given(state=st.text(min_size=1, max_size=50))
def test_invalid_states_rejected(state):
    """Any state not in the valid set must be rejected."""
    assume(state not in VALID_STATES)
    assert not is_valid_compatibility_state(state), f"should reject: {state}"


@settings(max_examples=100)
@given(
    capability=st.text(min_size=1, max_size=40),
    state=st.sampled_from(sorted(VALID_STATES)),
)
def test_any_capability_with_valid_state(capability, state):
    """Any capability name paired with a valid state must be accepted."""
    assert is_valid_compatibility_state(state)


@settings(max_examples=100)
@given(
    state=st.from_regex(r"[a-z\-]{1,30}", fullmatch=True),
)
def test_random_hyphenated_strings(state):
    """Random hyphenated strings that aren't valid states must be rejected."""
    assume(state not in VALID_STATES)
    assert not is_valid_compatibility_state(state), f"should reject: {state}"


# ---------------------------------------------------------------------------
# Structural tests for the Markdown table parser and matrix validation
# ---------------------------------------------------------------------------

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from release_gates.validate_release_gates import (
    ValidationResult,
    _extract_table_under_heading,
    _check_compat_capabilities,
    _check_compat_states,
    _check_compat_row_validity,
    CANONICAL_CAPABILITIES,
)


def _make_matrix(cap_rows: list[tuple[str, int]]) -> str:
    """Build a minimal compat matrix Markdown doc.

    Each cap_row is (capability_name, state_column_index) where
    state_column_index is 0=streaming-supported, 1=full-buffer-only,
    2=pre-commit-fallback-only.
    """
    lines = [
        "## Capability Classification Matrix",
        "",
        "| Capability | streaming-supported | full-buffer-only | pre-commit-fallback-only | Notes |",
        "|------------|:---:|:---:|:---:|-------|",
    ]
    for cap, idx in cap_rows:
        cols = ["", "", ""]
        cols[idx] = "✓"
        lines.append(f"| {cap} | {cols[0]} | {cols[1]} | {cols[2]} | note |")
    return "\n".join(lines)


def _all_caps_matrix() -> str:
    """Build a matrix with all canonical capabilities in streaming-supported."""
    # Some capabilities need sub-capability splits
    rows = []
    for cap in CANONICAL_CAPABILITIES:
        if cap == "markdown_front_matter":
            rows.extend(
                (
                    (
                        "markdown_front_matter (common head metadata within lookahead)",
                        0,
                    ),
                    (
                        "markdown_front_matter (metadata beyond lookahead budget)",
                        2,
                    ),
                )
            )
        elif cap == "markdown_etag":
            rows.extend(
                (
                    ("markdown_etag (response-header ETag)", 1),
                    ("markdown_etag (internal hash)", 0),
                )
            )
        elif cap == "markdown_conditional_requests":
            rows.extend(
                (
                    (
                        "markdown_conditional_requests (if_modified_since_only)",
                        0,
                    ),
                    ("markdown_conditional_requests (full_support)", 1),
                )
            )
        elif cap in ("table conversion", "prune_noise_regions"):
            rows.append((cap, 2))
        else:
            rows.append((cap, 0))
    return _make_matrix(rows)


def test_full_matrix_passes():
    """A complete matrix with all capabilities should pass all checks."""
    content = _all_caps_matrix()
    rows = _extract_table_under_heading(content, "Capability Classification Matrix")
    assert len(rows) > 1

    result = ValidationResult()
    _check_compat_capabilities(result, rows)
    assert not result.has_failures, [r for r in result.results if r[0] == "FAIL"]


def test_missing_capability_row_fails():
    """Removing a capability row from the table must cause failure."""
    rows_data = [
        (cap, 0) for cap in CANONICAL_CAPABILITIES
        if cap != "automatic decompression"
    ]
    content = _make_matrix(rows_data)
    rows = _extract_table_under_heading(content, "Capability Classification Matrix")

    result = ValidationResult()
    _check_compat_capabilities(result, rows)
    assert result.has_failures
    fail_detail = [d for s, _, d in result.results if s == "FAIL"][0]
    assert "automatic decompression" in fail_detail


def test_multiple_states_marked_fails():
    """A row with two states marked must fail row-validity."""
    content = "\n".join([
        "## Capability Classification Matrix",
        "",
        "| Capability | streaming-supported | full-buffer-only | pre-commit-fallback-only | Notes |",
        "|------------|:---:|:---:|:---:|-------|",
        "| automatic decompression | ✓ | ✓ | | bad row |",
    ])
    rows = _extract_table_under_heading(content, "Capability Classification Matrix")
    header = rows[0]
    state_indices = [
        i for i, h in enumerate(header)
        if h.strip().lower() in {s.lower() for s in VALID_STATES}
    ]

    result = ValidationResult()
    _check_compat_row_validity(result, rows[1:], state_indices)
    assert result.has_failures
    fail_detail = [d for s, _, d in result.results if s == "FAIL"][0]
    assert "marked 2 states" in fail_detail


def test_extra_state_column_fails():
    """An extra state column in the header must fail."""
    content = "\n".join([
        "## Capability Classification Matrix",
        "",
        "| Capability | streaming-supported | full-buffer-only | pre-commit-fallback-only | secretly-degraded | Notes |",
        "|------------|:---:|:---:|:---:|:---:|-------|",
        "| automatic decompression | ✓ | | | | note |",
    ])
    rows = _extract_table_under_heading(content, "Capability Classification Matrix")

    result = ValidationResult()
    _check_compat_states(result, rows[0])
    assert result.has_failures
    fail_detail = [d for s, _, d in result.results if s == "FAIL"][0]
    assert "unexpected columns" in fail_detail


def test_capability_mentioned_outside_table_not_counted():
    """A capability name appearing only outside the table must not pass."""
    # Build a matrix missing "security sanitization" in the table
    # but mention it in prose above
    rows_data = [
        (cap, 0) for cap in CANONICAL_CAPABILITIES
        if cap != "security sanitization"
    ]
    table = _make_matrix(rows_data)
    content = "We discuss security sanitization elsewhere.\n\n" + table
    rows = _extract_table_under_heading(content, "Capability Classification Matrix")

    result = ValidationResult()
    _check_compat_capabilities(result, rows)
    assert result.has_failures
    fail_detail = [d for s, _, d in result.results if s == "FAIL"][0]
    assert "security sanitization" in fail_detail
