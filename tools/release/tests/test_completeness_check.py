"""Property-based tests for tools/release/completeness_check.py.

**Validates: Requirements 11.4, 11.5**

Property 8: Release matrix completeness check correctness — generate random
matrices and artifact lists, verify that missing-detection is precise.
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

import sys
from pathlib import Path

# Ensure the package root is on sys.path so the test can be invoked from
# either the repository root or from tools/release/.
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.release.completeness_check import check_completeness, expected_artifact_name


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_nginx_version = st.builds(
    "1.{}.{}".format,
    st.integers(min_value=0, max_value=99),
    st.integers(min_value=0, max_value=99),
)

_os_type = st.sampled_from(["glibc", "musl"])
_arch = st.sampled_from(["x86_64", "aarch64"])

_matrix_entry = st.fixed_dictionaries(
    {
        "nginx": _nginx_version,
        "os_type": _os_type,
        "arch": _arch,
        "support_tier": st.just("full"),
    }
)

# A list of matrix entries with unique (nginx, os_type, arch) combinations.
_matrix = (
    st.lists(_matrix_entry, min_size=0, max_size=30)
    .map(
        lambda entries: list(
            {(e["nginx"], e["os_type"], e["arch"]): e for e in entries}.values()
        )
    )
)


def _expected_names(entries):
    """
    Compute the expected artifact filenames for the given matrix entries.
    
    Parameters:
        entries (Iterable[dict]): An iterable of release matrix entry objects (dicts with keys like
            "nginx", "os_type", "arch", "support_tier") for which artifact names should be derived.
    
    Returns:
        set: A set of expected artifact filename strings, one per entry.
    """
    return {expected_artifact_name(e) for e in entries}


# ---------------------------------------------------------------------------
# Property 8 — completeness check correctness
# ---------------------------------------------------------------------------


@given(data=st.data())
@settings(max_examples=200)
def test_missing_is_exact_set_difference(data):
    """The missing set equals expected minus actual (set-difference precision).

    **Validates: Requirements 11.4, 11.5**
    """
    entries = data.draw(_matrix, label="matrix_entries")
    all_names = _expected_names(entries)

    # Draw a random subset of the expected names to simulate partial availability
    present = data.draw(
        st.frozensets(st.sampled_from(sorted(all_names)) if all_names else st.nothing()),
        label="present_artifacts",
    )

    missing = check_completeness(entries, set(present))
    missing_names = {name for _, name in missing}

    assert missing_names == all_names - present


@given(entries=_matrix)
@settings(max_examples=200)
def test_all_present_returns_empty(entries):
    """When every expected artifact exists, the result must be empty.

    **Validates: Requirements 11.4, 11.5**
    """
    all_names = _expected_names(entries)
    assert check_completeness(entries, all_names) == []


@given(entries=_matrix)
@settings(max_examples=200)
def test_none_present_reports_all_missing(entries):
    """When no artifacts exist, every matrix entry must be reported missing.

    **Validates: Requirements 11.4, 11.5**
    """
    missing = check_completeness(entries, set())
    assert len(missing) == len(entries)
    missing_names = {name for _, name in missing}
    assert missing_names == _expected_names(entries)


@given(entries=_matrix)
@settings(max_examples=200)
def test_missing_count_bounded_by_matrix_size(entries):
    """The number of missing artifacts can never exceed the matrix size.

    **Validates: Requirements 11.4, 11.5**
    """
    # Use an empty artifact set (worst case)
    missing = check_completeness(entries, set())
    assert len(missing) <= len(entries)
