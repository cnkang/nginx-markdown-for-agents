"""Property-based tests for tools/release/matrix/update_matrix.py.

Tests for the automated nginx release matrix updater.
"""


import itertools
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

import json
import sys
from pathlib import Path

# Ensure the package root is on sys.path so the test can be invoked from
# either the repository root or from tools/release/.
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.release.matrix.update_matrix import (
    classify_version,
    version_tuple,
    compute_matrix,
    merge_matrix,
    diff_matrix,
    parse_release_module_versions,
    write_matrix,
    update_doc_table,
    parse_args,
    main,
    DOC_MARKER_BEGIN,
    DOC_MARKER_END,
    _entry_sort_key,
    _resolve_repo_write_path,
)

from tools.release.matrix.update_matrix import filter_versions

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_nginx_version = st.builds(
    "1.{}.{}".format,
    st.integers(min_value=0, max_value=99),
    st.integers(min_value=0, max_value=99),
)


# ---------------------------------------------------------------------------
# Property 1 — Version Classification Correctness
# ---------------------------------------------------------------------------


@given(minor=st.integers(min_value=0, max_value=99), patch=st.integers(min_value=0, max_value=99))
@settings(max_examples=200)
def test_version_classification_correctness(minor, patch):
    """classify_version returns 'stable' for even minor, 'mainline' for odd.

    **Validates: Requirements 1.2**
    """
    version = f"1.{minor}.{patch}"
    result = classify_version(version)
    if minor % 2 == 0:
        assert result == "stable", f"Expected 'stable' for even minor {minor}, got '{result}'"
    else:
        assert result == "mainline", f"Expected 'mainline' for odd minor {minor}, got '{result}'"


# ---------------------------------------------------------------------------
# Property 2 — Version Filtering Completeness
# ---------------------------------------------------------------------------


import pytest

@pytest.mark.skipif(filter_versions is None, reason="filter_versions not yet implemented (Task 1.4)")
@given(
    versions=st.lists(_nginx_version, min_size=0, max_size=30),
    min_version=_nginx_version,
)
@settings(max_examples=200)
def test_version_filtering_completeness(versions, min_version):
    """
    Asserts that filter_versions returns only versions greater than or equal to min_version and that every input version meeting that threshold appears in the output.
    
    Parameters:
        versions (list[str]): Candidate version strings to filter.
        min_version (str): Minimum version string; versions less than this value must be excluded.
    """
    filtered = filter_versions(versions, min_version)
    min_tuple = version_tuple(min_version)

    # Every version in the output must be >= min_version
    for v in filtered:
        t = version_tuple(v)
        assert t >= min_tuple, f"Filtered version {v} is below min_version {min_version}"

    # No qualifying version from the input should be missing from the output
    filtered_set = set(filtered)
    for v in versions:
        t = version_tuple(v)
        if t >= min_tuple:
            assert v in filtered_set, f"Qualifying version {v} was excluded from filtered output"


# ---------------------------------------------------------------------------
# Import parse_nginx_versions for unit tests
# ---------------------------------------------------------------------------

from tools.release.matrix.update_matrix import parse_nginx_versions

# ---------------------------------------------------------------------------
# Unit Tests — HTML Parsing (parse_nginx_versions)
# ---------------------------------------------------------------------------

# Realistic HTML snippet modelled on the nginx.org/en/download.html structure.
# Contains mainline, stable, and legacy download links.
_REALISTIC_HTML = """\
<!DOCTYPE html>
<html>
<head><title>nginx: download</title></head>
<body>
<h4>Mainline version</h4>
<table>
<tr>
  <td><a href="/download/nginx-1.27.4.tar.gz">nginx-1.27.4</a></td>
  <td><a href="/download/nginx-1.27.4.zip">nginx/Windows-1.27.4</a></td>
</tr>
</table>

<h4>Stable version</h4>
<table>
<tr>
  <td><a href="/download/nginx-1.26.3.tar.gz">nginx-1.26.3</a></td>
  <td><a href="/download/nginx-1.26.3.zip">nginx/Windows-1.26.3</a></td>
</tr>
</table>

<h4>Legacy versions</h4>
<table>
<tr>
  <td><a href="/download/nginx-1.24.0.tar.gz">nginx-1.24.0</a></td>
  <td><a href="/download/nginx-1.24.0.zip">nginx/Windows-1.24.0</a></td>
</tr>
<tr>
  <td><a href="/download/nginx-1.22.1.tar.gz">nginx-1.22.1</a></td>
  <td><a href="/download/nginx-1.22.1.zip">nginx/Windows-1.22.1</a></td>
</tr>
</table>
</body>
</html>
"""


def test_parse_realistic_html():
    """parse_nginx_versions extracts all versions from a realistic page."""
    versions = parse_nginx_versions(_REALISTIC_HTML)
    assert set(versions) == {"1.27.4", "1.26.3", "1.24.0", "1.22.1"}


def test_parse_empty_html():
    """Empty HTML yields zero versions."""
    assert parse_nginx_versions("") == []


def test_parse_no_matching_links():
    """HTML with no download links matching the pattern yields zero versions."""
    html = "<html><body><a href='/other/file.tar.gz'>nothing</a></body></html>"
    assert parse_nginx_versions(html) == []


def test_parse_deduplication():
    """Duplicate version links are deduplicated, preserving first-seen order."""
    html = (
        '<a href="/download/nginx-1.26.3.tar.gz">link1</a>'
        '<a href="/download/nginx-1.24.0.tar.gz">link2</a>'
        '<a href="/download/nginx-1.26.3.tar.gz">link3</a>'
        '<a href="/download/nginx-1.24.0.tar.gz">link4</a>'
    )
    versions = parse_nginx_versions(html)
    assert versions == ["1.26.3", "1.24.0"]


def test_parse_release_module_versions_extracts_assets():
    """Only markdown module tarballs should contribute release versions."""
    release_json = json.dumps(
        {
            "assets": [
                {"name": "nginx-version-groups.json"},
                {"name": "ngx_http_markdown_filter_module-1.28.3-glibc-x86_64.tar.gz"},
                {"name": "ngx_http_markdown_filter_module-1.29.8-musl-aarch64.tar.gz"},
                {"name": "README.txt"},
            ]
        }
    )

    assert parse_release_module_versions(release_json) == {"1.28.3", "1.29.8"}


def test_parse_release_module_versions_ignores_nonconforming_assets():
    """Malformed asset names must not be misclassified as release versions."""
    release_json = json.dumps(
        {
            "assets": [
                {"name": "ngx_http_markdown_filter_module-1.29.8-glibc-x86_64.zip"},
                {"name": "ngx_http_markdown_filter_module-1.29.8-glibc.tar.gz"},
                {"name": "ngx_http_markdown_filter_module-1.29.8-glibc-x86_64.tar.gz"},
                {"name": "ngx_http_markdown_filter_module-mainline-glibc-x86_64.tar.gz"},
                {"name": "README.txt"},
            ]
        }
    )

    assert parse_release_module_versions(release_json) == {"1.29.8"}


@pytest.mark.parametrize("version", ["1", "1x26x3", "mainline"])
def test_classify_version_rejects_malformed_strings(version):
    """Malformed versions raise ValueError instead of crashing with IndexError."""
    with pytest.raises(ValueError):
        classify_version(version)


# ---------------------------------------------------------------------------
# Import load_matrix for unit tests
# ---------------------------------------------------------------------------

from tools.release.matrix.update_matrix import load_matrix

# ---------------------------------------------------------------------------
# Unit Tests — load_matrix
# ---------------------------------------------------------------------------


def test_load_matrix_valid(tmp_path):
    """load_matrix returns (data, auto_entries, manual_entries) for valid JSON."""
    matrix_data = {
        "schema_version": "1.0.0",
        "updated_at": "2025-07-14T00:00:00Z",
        "matrix": [
            {"nginx": "1.26.3", "os_type": "glibc", "arch": "x86_64", "support_tier": "full"},
            {"nginx": "1.24.0", "os_type": "glibc", "arch": "x86_64", "support_tier": "source_only", "managed_by": "manual"},
        ],
    }
    p = tmp_path / "release-matrix.json"
    p.write_text(json.dumps(matrix_data))

    data, auto_entries, manual_entries = load_matrix(p)

    assert data["schema_version"] == "1.0.0"
    assert len(auto_entries) == 1
    assert auto_entries[0]["nginx"] == "1.26.3"
    assert len(manual_entries) == 1
    assert manual_entries[0]["nginx"] == "1.24.0"
    assert manual_entries[0]["managed_by"] == "manual"


def test_load_matrix_auto_explicit(tmp_path):
    """Entries with managed_by: 'auto' are treated as auto-managed."""
    matrix_data = {
        "schema_version": "1.0.0",
        "matrix": [
            {"nginx": "1.26.3", "os_type": "glibc", "arch": "x86_64", "support_tier": "full", "managed_by": "auto"},
        ],
    }
    p = tmp_path / "release-matrix.json"
    p.write_text(json.dumps(matrix_data))

    _data, auto_entries, manual_entries = load_matrix(p)

    assert len(auto_entries) == 1
    assert len(manual_entries) == 0


def test_load_matrix_no_managed_by(tmp_path):
    """Entries without managed_by field are treated as auto-managed."""
    matrix_data = {
        "schema_version": "1.0.0",
        "matrix": [
            {"nginx": "1.28.0", "os_type": "musl", "arch": "aarch64", "support_tier": "full"},
        ],
    }
    p = tmp_path / "release-matrix.json"
    p.write_text(json.dumps(matrix_data))

    _data, auto_entries, manual_entries = load_matrix(p)

    assert len(auto_entries) == 1
    assert len(manual_entries) == 0


def test_load_matrix_invalid_json(tmp_path):
    """Invalid JSON causes sys.exit(1)."""
    p = tmp_path / "release-matrix.json"
    p.write_text("{not valid json")

    with pytest.raises(SystemExit) as exc_info:
        load_matrix(p)
    assert exc_info.value.code == 1


def test_load_matrix_missing_matrix_key(tmp_path):
    """JSON without 'matrix' key causes sys.exit(1)."""
    p = tmp_path / "release-matrix.json"
    p.write_text(json.dumps({"schema_version": "1.0.0"}))

    with pytest.raises(SystemExit) as exc_info:
        load_matrix(p)
    assert exc_info.value.code == 1


def test_load_matrix_duplicate_manual_keys(tmp_path):
    """Duplicate (nginx, os_type, arch) among manual entries causes sys.exit(1)."""
    matrix_data = {
        "schema_version": "1.0.0",
        "matrix": [
            {"nginx": "1.22.1", "os_type": "glibc", "arch": "x86_64", "support_tier": "source_only", "managed_by": "manual"},
            {"nginx": "1.22.1", "os_type": "glibc", "arch": "x86_64", "support_tier": "full", "managed_by": "manual"},
        ],
    }
    p = tmp_path / "release-matrix.json"
    p.write_text(json.dumps(matrix_data))

    with pytest.raises(SystemExit) as exc_info:
        load_matrix(p)
    assert exc_info.value.code == 1


def test_load_matrix_manual_entry_missing_required_keys(tmp_path, capsys):
    """Manual entries without nginx/os_type/arch fail validation cleanly."""
    matrix_data = {
        "schema_version": "1.0.0",
        "matrix": [
            {"nginx": "1.22.1", "arch": "x86_64", "support_tier": "full", "managed_by": "manual"},
        ],
    }
    p = tmp_path / "release-matrix.json"
    p.write_text(json.dumps(matrix_data))

    with pytest.raises(SystemExit) as exc_info:
        load_matrix(p)

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "missing required keys" in captured.err
    assert "os_type" in captured.err


def test_resolve_repo_write_path_rejects_paths_outside_repo(tmp_path, monkeypatch):
    """Write helpers reject paths that escape the repository root."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    import tools.release.matrix.update_matrix as um

    monkeypatch.setattr(um, "REPO_ROOT", repo_root)

    with pytest.raises(ValueError, match="outside repository root"):
        _resolve_repo_write_path(tmp_path / "outside.json")


def _allow_repo_writes(monkeypatch, repo_root: Path) -> None:
    """
    Point the updater at a temporary repository root for tests by setting its REPO_ROOT.
    
    Parameters:
        monkeypatch (pytest.MonkeyPatch): The pytest monkeypatch fixture used to set attributes.
        repo_root (Path): Path to the temporary repository root that tests should use for file writes.
    """
    repo_root.mkdir(parents=True, exist_ok=True)

    import tools.release.matrix.update_matrix as um

    monkeypatch.setattr(um, "REPO_ROOT", repo_root)


def test_load_matrix_preserves_full_data(tmp_path):
    """load_matrix preserves all top-level keys in the returned data dict."""
    matrix_data = {
        "schema_version": "1.0.0",
        "updated_at": "2025-07-14T00:00:00Z",
        "support_tiers": {"full": "desc"},
        "matrix": [
            {"nginx": "1.26.3", "os_type": "glibc", "arch": "x86_64", "support_tier": "full"},
        ],
    }
    p = tmp_path / "release-matrix.json"
    p.write_text(json.dumps(matrix_data))

    data, _, _ = load_matrix(p)

    assert data["schema_version"] == "1.0.0"
    assert data["updated_at"] == "2025-07-14T00:00:00Z"
    assert data["support_tiers"] == {"full": "desc"}


def test_load_matrix_file_not_found(tmp_path):
    """
    Asserts that calling load_matrix on a nonexistent path causes the process to exit with status code 1.
    
    Raises:
        SystemExit: with exit code 1 when the target file is not found.
    """
    p = tmp_path / "nonexistent.json"

    with pytest.raises(SystemExit) as exc_info:
        load_matrix(p)
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Strategies for matrix computation / diffing property tests
# ---------------------------------------------------------------------------

_os_types = st.sampled_from(["glibc", "musl"])
_archs = st.sampled_from(["x86_64", "aarch64"])

_unique_versions = (
    st.lists(_nginx_version, min_size=0, max_size=10)
    .map(lambda vs: list(dict.fromkeys(vs)))
)

_matrix_entry_with_managed_by = st.fixed_dictionaries(
    {
        "nginx": _nginx_version,
        "os_type": _os_types,
        "arch": _archs,
        "support_tier": st.just("full"),
    },
    optional={"managed_by": st.just("manual")},
)


# ---------------------------------------------------------------------------
# Property 3 — Matrix Cross-Product Completeness
# ---------------------------------------------------------------------------


@given(versions=_unique_versions)
@settings(max_examples=200)
def test_property3_matrix_cross_product_completeness(versions):
    """
    Verifies the release matrix contains the complete cross-product of provided versions, OS types, and architectures.
    
    Asserts the matrix has exactly len(versions) × 2 × 2 entries (for the two OS types and two architectures used in the test), that each (nginx, os_type, arch) tuple appears exactly once, and that every entry's support_tier is "full".
    
    Parameters:
        versions (list[str]): Sequence of nginx version strings to generate the matrix for.
    """
    os_types = ["glibc", "musl"]
    archs = ["x86_64", "aarch64"]
    matrix = compute_matrix(versions, os_types, archs)

    expected_count = len(versions) * len(os_types) * len(archs)
    assert len(matrix) == expected_count, (
        f"Expected {expected_count} entries, got {len(matrix)}"
    )

    # Every combination appears exactly once
    seen: set[tuple[str, str, str]] = set()
    for entry in matrix:
        key = (entry["nginx"], entry["os_type"], entry["arch"])
        assert key not in seen, f"Duplicate entry for {key}"
        seen.add(key)
        assert entry["support_tier"] == "full", (
            f"Expected support_tier 'full', got '{entry['support_tier']}'"
        )

    # Every expected combination is present
    for v in versions:
        for os_type, arch in itertools.product(os_types, archs):
            assert (v, os_type, arch) in seen, (
                f"Missing entry for ({v}, {os_type}, {arch})"
            )


# ---------------------------------------------------------------------------
# Property 4 — Matrix Diff Precision
# ---------------------------------------------------------------------------


@given(
    current_versions=_unique_versions,
    desired_versions=_unique_versions,
)
@settings(max_examples=200)
def test_property4_matrix_diff_precision(current_versions, desired_versions):
    """diff_matrix reports added = desired - current and removed = current - desired
    with exact set-difference precision.

    **Validates: Requirements 2.3, 2.4**
    """
    os_types = ["glibc", "musl"]
    archs = ["x86_64", "aarch64"]

    current_auto = compute_matrix(current_versions, os_types, archs)
    desired_auto = compute_matrix(desired_versions, os_types, archs)

    diff = diff_matrix(current_auto, desired_auto)

    current_set = set(current_versions)
    desired_set = set(desired_versions)

    expected_added = desired_set - current_set
    expected_removed = current_set - desired_set

    assert set(diff.added_versions) == expected_added, (
        f"Added mismatch: got {diff.added_versions}, expected {expected_added}"
    )
    assert set(diff.removed_versions) == expected_removed, (
        f"Removed mismatch: got {diff.removed_versions}, expected {expected_removed}"
    )
    assert diff.has_changes == bool(expected_added or expected_removed)


# ---------------------------------------------------------------------------
# Property 6 — Matrix Entry Sorting
# ---------------------------------------------------------------------------


@given(versions=_unique_versions)
@settings(max_examples=200)
def test_property6_matrix_entry_sorting(versions):
    """Entries from compute_matrix are sorted by version tuple ascending,
    then os_type alphabetical, then arch alphabetical.

    **Validates: Requirements 2.8, 3.3**
    """
    os_types = ["glibc", "musl"]
    archs = ["x86_64", "aarch64"]
    matrix = compute_matrix(versions, os_types, archs)

    for i in range(1, len(matrix)):
        prev_key = _entry_sort_key(matrix[i - 1])
        curr_key = _entry_sort_key(matrix[i])
        assert prev_key <= curr_key, (
            f"Sorting violation at index {i}: {prev_key} > {curr_key}"
        )


# ---------------------------------------------------------------------------
# Property 11 — Pin_Entry Preservation
# ---------------------------------------------------------------------------


@given(
    auto_versions=_unique_versions,
    manual_entries=st.lists(
        st.fixed_dictionaries({
            "nginx": _nginx_version,
            "os_type": _os_types,
            "arch": _archs,
            "support_tier": st.sampled_from(["full", "source_only"]),
            "managed_by": st.just("manual"),
        }),
        min_size=0,
        max_size=5,
    ),
)
@settings(max_examples=200)
def test_property11_pin_entry_preservation(auto_versions, manual_entries):
    """
    Verify merge_matrix preserves every manual Pin_Entry in the merged matrix.
    
    Asserts that for each manual entry there is exactly one merged entry with the same (nginx, os_type, arch), that it remains marked as managed_by "manual", and that its support_tier is unchanged.
    """
    # Deduplicate manual entries by key to avoid invalid input
    unique_manual: dict[tuple[str, str, str], dict] = {}
    for e in manual_entries:
        key = (e["nginx"], e["os_type"], e["arch"])
        unique_manual[key] = e
    manual_deduped = list(unique_manual.values())

    os_types = ["glibc", "musl"]
    archs = ["x86_64", "aarch64"]
    auto_entries = compute_matrix(auto_versions, os_types, archs)

    merged = merge_matrix(auto_entries, manual_deduped)

    # Every manual entry must appear in the merged output
    for manual_e in manual_deduped:
        key = (manual_e["nginx"], manual_e["os_type"], manual_e["arch"])
        matching = [
            e for e in merged
            if (e["nginx"], e["os_type"], e["arch"]) == key
        ]
        assert len(matching) == 1, (
            f"Expected exactly 1 entry for manual key {key}, found {len(matching)}"
        )
        assert matching[0]["managed_by"] == "manual", (
            f"Manual entry for {key} was not preserved"
        )
        assert matching[0]["support_tier"] == manual_e["support_tier"], (
            f"Support tier changed for manual entry {key}"
        )


# ---------------------------------------------------------------------------
# Property 12 — Key Uniqueness After Merge
# ---------------------------------------------------------------------------


@given(
    auto_versions=_unique_versions,
    manual_entries=st.lists(
        st.fixed_dictionaries({
            "nginx": _nginx_version,
            "os_type": _os_types,
            "arch": _archs,
            "support_tier": st.sampled_from(["full", "source_only"]),
            "managed_by": st.just("manual"),
        }),
        min_size=0,
        max_size=5,
    ),
)
@settings(max_examples=200)
def test_property12_key_uniqueness_after_merge(auto_versions, manual_entries):
    """The merged matrix contains at most one entry per (nginx, os_type, arch) key.
    When a manual entry and an auto entry share the same key, only the manual
    entry appears.

    **Validates: Requirements 2.5, 2.8, 2.11**
    """
    # Deduplicate manual entries by key to avoid invalid input
    unique_manual: dict[tuple[str, str, str], dict] = {}
    for e in manual_entries:
        key = (e["nginx"], e["os_type"], e["arch"])
        unique_manual[key] = e
    manual_deduped = list(unique_manual.values())

    os_types = ["glibc", "musl"]
    archs = ["x86_64", "aarch64"]
    auto_entries = compute_matrix(auto_versions, os_types, archs)

    merged = merge_matrix(auto_entries, manual_deduped)

    # Check key uniqueness
    seen_keys: set[tuple[str, str, str]] = set()
    for entry in merged:
        key = (entry["nginx"], entry["os_type"], entry["arch"])
        assert key not in seen_keys, f"Duplicate key in merged matrix: {key}"
        seen_keys.add(key)

    # When manual and auto share a key, manual wins
    manual_keys = {(e["nginx"], e["os_type"], e["arch"]) for e in manual_deduped}
    for entry in merged:
        key = (entry["nginx"], entry["os_type"], entry["arch"])
        if key in manual_keys:
            assert entry.get("managed_by") == "manual", (
                f"Key {key} collides with manual entry but auto entry was kept"
            )


# ---------------------------------------------------------------------------
# Unit test — entry-level diff detection
# ---------------------------------------------------------------------------


def test_diff_matrix_entry_level_change():
    """diff_matrix detects entry-level changes even when version sets are identical.

    When the same versions exist in both current and desired but individual
    entries differ (e.g., different support_tier or missing/extra platform
    entries), has_changes should be True even though added_versions and
    removed_versions are both empty.
    """
    # Same version, but different support_tier on one entry
    current = [
        {"nginx": "1.24.0", "os_type": "glibc", "arch": "x86_64", "support_tier": "full"},
        {"nginx": "1.24.0", "os_type": "musl", "arch": "x86_64", "support_tier": "full"},
    ]
    desired = [
        {"nginx": "1.24.0", "os_type": "glibc", "arch": "x86_64", "support_tier": "full"},
        {"nginx": "1.24.0", "os_type": "musl", "arch": "x86_64", "support_tier": "source_only"},
    ]

    diff = diff_matrix(current, desired)

    # Version sets are identical — no version-level additions/removals
    assert diff.added_versions == []
    assert diff.removed_versions == []
    # But entry-level change detected
    assert diff.has_changes is True


def test_diff_matrix_missing_platform_entry():
    """diff_matrix detects when a platform entry is added or removed for an
    existing version (sparse matrix change)."""
    current = [
        {"nginx": "1.24.0", "os_type": "glibc", "arch": "x86_64", "support_tier": "full"},
    ]
    desired = [
        {"nginx": "1.24.0", "os_type": "glibc", "arch": "x86_64", "support_tier": "full"},
        {"nginx": "1.24.0", "os_type": "glibc", "arch": "aarch64", "support_tier": "full"},
    ]

    diff = diff_matrix(current, desired)

    # Same version set
    assert diff.added_versions == []
    assert diff.removed_versions == []
    # But new platform entry detected
    assert diff.has_changes is True


# ---------------------------------------------------------------------------
# Unit tests — write_matrix
# ---------------------------------------------------------------------------


def test_write_matrix_basic(tmp_path, monkeypatch):
    """write_matrix writes formatted JSON with 2-space indent and trailing newline."""
    _allow_repo_writes(monkeypatch, tmp_path)
    target = tmp_path / "release-matrix.json"
    data = {
        "schema_version": "1.0.0",
        "updated_at": "2025-07-14T00:00:00Z",
        "matrix": [
            {"nginx": "1.26.3", "os_type": "glibc", "arch": "x86_64", "support_tier": "full"},
        ],
    }
    write_matrix(target, data)

    content = target.read_text()
    assert content.endswith("\n")
    parsed = json.loads(content)
    assert parsed == data
    # Verify 2-space indentation
    assert '  "schema_version"' in content


def test_write_matrix_preserves_all_fields(tmp_path, monkeypatch):
    """write_matrix preserves schema_version, updated_at, support_tiers, and matrix."""
    _allow_repo_writes(monkeypatch, tmp_path)
    target = tmp_path / "release-matrix.json"
    data = {
        "schema_version": "2.0.0",
        "updated_at": "2025-01-01T12:00:00Z",
        "support_tiers": {"full": "Prebuilt binary", "source_only": "Build from source"},
        "matrix": [],
    }
    write_matrix(target, data)

    parsed = json.loads(target.read_text())
    assert parsed["schema_version"] == "2.0.0"
    assert parsed["updated_at"] == "2025-01-01T12:00:00Z"
    assert parsed["support_tiers"] == data["support_tiers"]
    assert parsed["matrix"] == []


def test_write_matrix_overwrites_existing(tmp_path, monkeypatch):
    """write_matrix replaces an existing file atomically."""
    _allow_repo_writes(monkeypatch, tmp_path)
    target = tmp_path / "release-matrix.json"
    target.write_text('{"old": true}\n')

    data = {"schema_version": "1.0.0", "matrix": []}
    write_matrix(target, data)

    parsed = json.loads(target.read_text())
    assert "old" not in parsed
    assert parsed["schema_version"] == "1.0.0"


def test_write_matrix_no_temp_file_on_success(tmp_path, monkeypatch):
    """After a successful write, no .tmp file should remain."""
    _allow_repo_writes(monkeypatch, tmp_path)
    target = tmp_path / "release-matrix.json"
    data = {"schema_version": "1.0.0", "matrix": []}
    write_matrix(target, data)

    tmp_file = tmp_path / "release-matrix.json.tmp"
    assert not tmp_file.exists()


def test_write_matrix_cleans_up_temp_on_failure(tmp_path, monkeypatch):
    """On write failure, the temp file is cleaned up."""
    import os as _os

    _allow_repo_writes(monkeypatch, tmp_path)
    target = tmp_path / "release-matrix.json"
    tmp_file = target.with_suffix(f"{target.suffix}.tmp")

    # Make os.replace raise an OSError to simulate a rename failure
    def failing_replace(src, dst):
        """
        Simulate a file rename failure by always raising an OSError.
        
        This test helper unconditionally raises an OSError to emulate a failing os.replace/rename
        operation when attempting to move a file from `src` to `dst`.
        
        Parameters:
            src (str | Path): Path to the source file attempted to be moved.
            dst (str | Path): Path to the destination location.
        
        Raises:
            OSError: Always raised to indicate a simulated rename failure.
        """
        raise OSError("simulated rename failure")

    monkeypatch.setattr("os.replace", failing_replace)

    raised = False
    try:
        write_matrix(target, {"schema_version": "1.0.0", "matrix": []})
    except OSError:
        raised = True

    assert raised, "Expected an OSError from write_matrix"
    assert not tmp_file.exists(), "Temp file should be cleaned up after failure"


# ---------------------------------------------------------------------------
# Helper for doc-marker tests
# ---------------------------------------------------------------------------


def _make_doc_with_markers(before: str, after: str) -> str:
    """
    Builds a document string containing the documentation markers and a placeholder table between provided surrounding content.
    
    Parameters:
        before (str): Text to place before the doc markers.
        after (str): Text to place after the doc markers.
    
    Returns:
        str: The composed document with DOC_MARKER_BEGIN, the literal "old table content", and DOC_MARKER_END inserted between `before` and `after`, separated by newlines.
    """
    return f"{before}\n{DOC_MARKER_BEGIN}\nold table content\n{DOC_MARKER_END}\n{after}"


# ---------------------------------------------------------------------------
# Strategies for file writing / doc table property tests
# ---------------------------------------------------------------------------

_schema_version = st.from_regex(r"[0-9]+\.[0-9]+\.[0-9]+", fullmatch=True)

_surrounding_text = st.text(min_size=0, max_size=200).filter(
    lambda t: DOC_MARKER_BEGIN not in t and DOC_MARKER_END not in t and "\r" not in t
)


# ---------------------------------------------------------------------------
# Property 5 — Schema Version Preservation
# ---------------------------------------------------------------------------


@given(
    schema_ver=_schema_version,
    entries=st.lists(_matrix_entry_with_managed_by, min_size=0, max_size=5),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property5_schema_version_preservation(tmp_path, monkeypatch, schema_ver, entries):
    """For any valid release-matrix.json with a schema_version field,
    after running write_matrix, the output schema_version shall equal
    the input schema_version.

    **Validates: Requirements 2.6**
    """
    data = {
        "schema_version": schema_ver,
        "updated_at": "2025-07-14T00:00:00Z",
        "matrix": entries,
    }
    _allow_repo_writes(monkeypatch, tmp_path)
    target = tmp_path / "release-matrix.json"
    write_matrix(target, data)

    parsed = json.loads(target.read_text())
    assert parsed["schema_version"] == schema_ver, (
        f"schema_version changed: expected {schema_ver!r}, got {parsed['schema_version']!r}"
    )


# ---------------------------------------------------------------------------
# Property 7 — Doc Table Reflects Matrix
# ---------------------------------------------------------------------------


@given(
    entries=st.lists(
        st.fixed_dictionaries({
            "nginx": _nginx_version,
            "os_type": _os_types,
            "arch": _archs,
            "support_tier": st.sampled_from(["full", "source_only"]),
        }),
        min_size=0,
        max_size=10,
    ),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property7_doc_table_reflects_matrix(tmp_path, entries):
    """For any set of matrix entries, the generated Platform Compatibility
    Matrix table shall contain exactly the same set of (nginx, os_type,
    arch, tier) tuples as the matrix entries.

    **Validates: Requirements 3.1**
    """
    doc_content = _make_doc_with_markers("# Header", "Footer text")
    doc_path = tmp_path / "INSTALLATION.md"
    doc_path.write_text(doc_content)

    new_content = update_doc_table(doc_path, entries)

    # Extract table rows between markers
    begin_idx = new_content.index(DOC_MARKER_BEGIN) + len(DOC_MARKER_BEGIN)
    end_idx = new_content.index(DOC_MARKER_END)
    table_section = new_content[begin_idx:end_idx].strip()

    # Parse table rows (skip header + separator lines)
    rows = table_section.split("\n")
    data_rows = [r for r in rows if r.startswith("|") and "---" not in r]
    # First data_row is the header line (NGINX Version | OS Type | ...)
    if data_rows:
        data_rows = data_rows[1:]  # skip header

    parsed_tuples: set[tuple[str, str, str, str]] = set()
    for row in data_rows:
        cells = [c.strip() for c in row.split("|") if c.strip()]
        if len(cells) == 4:
            parsed_tuples.add((cells[0], cells[1], cells[2], cells[3]))

    # Build expected tuples from entries (tier is title-cased in the table)
    expected_tuples: set[tuple[str, str, str, str]] = set()
    for e in entries:
        tier = e["support_tier"].replace("_", " ").title()
        expected_tuples.add((e["nginx"], e["os_type"], e["arch"], tier))

    assert parsed_tuples == expected_tuples, (
        f"Table tuples mismatch.\n"
        f"  In table but not expected: {parsed_tuples - expected_tuples}\n"
        f"  Expected but not in table: {expected_tuples - parsed_tuples}"
    )


# ---------------------------------------------------------------------------
# Property 8 — Doc Surrounding Content Preservation
# ---------------------------------------------------------------------------


@given(
    before=_surrounding_text,
    after=_surrounding_text,
    entries=st.lists(
        st.fixed_dictionaries({
            "nginx": _nginx_version,
            "os_type": _os_types,
            "arch": _archs,
            "support_tier": st.just("full"),
        }),
        min_size=0,
        max_size=5,
    ),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property8_doc_surrounding_content_preservation(tmp_path, before, after, entries):
    """For any INSTALLATION.md document containing the markers, updating
    the table shall not modify any content outside the marker boundaries
    (content before the BEGIN marker and content after the END marker).

    **Validates: Requirements 3.2, 3.5**
    """
    doc_content = _make_doc_with_markers(before, after)
    doc_path = tmp_path / "INSTALLATION.md"
    doc_path.write_text(doc_content)

    new_content = update_doc_table(doc_path, entries)

    # Content before the BEGIN marker must be preserved
    begin_idx = new_content.index(DOC_MARKER_BEGIN)
    actual_before = new_content[:begin_idx]
    expected_before = before + "\n"
    assert actual_before == expected_before, (
        f"Content before BEGIN marker was modified.\n"
        f"  Expected: {expected_before!r}\n"
        f"  Got:      {actual_before!r}"
    )

    # Content after the END marker must be preserved
    end_marker_end = new_content.index(DOC_MARKER_END) + len(DOC_MARKER_END)
    actual_after = new_content[end_marker_end:]
    expected_after = "\n" + after
    assert actual_after == expected_after, (
        f"Content after END marker was modified.\n"
        f"  Expected: {expected_after!r}\n"
        f"  Got:      {actual_after!r}"
    )


# ---------------------------------------------------------------------------
# Unit tests — CLI argument parsing (Task 6.1)
# ---------------------------------------------------------------------------


class TestParseArgs:
    """Tests for ``parse_args`` CLI argument handling."""

    def test_no_flags_defaults(self):
        """Normal mode: both flags are False when no arguments given."""
        args = parse_args([])
        assert args.dry_run is False
        assert args.check_only is False

    def test_dry_run_flag(self):
        """``--dry-run`` sets dry_run=True, check_only=False."""
        args = parse_args(["--dry-run"])
        assert args.dry_run is True
        assert args.check_only is False

    def test_check_only_flag(self):
        """``--check-only`` sets check_only=True, dry_run=False."""
        args = parse_args(["--check-only"])
        assert args.check_only is True
        assert args.dry_run is False

    def test_mutual_exclusion(self):
        """``--dry-run`` and ``--check-only`` cannot be used together."""
        import pytest
        with pytest.raises(SystemExit):
            parse_args(["--dry-run", "--check-only"])

    def test_argv_none_uses_sys_argv(self, monkeypatch):
        """When argv is None, argparse reads from sys.argv."""
        monkeypatch.setattr(sys, "argv", ["update_matrix.py", "--dry-run"])
        args = parse_args(None)
        assert args.dry_run is True
        assert args.check_only is False


# ---------------------------------------------------------------------------
# Strategies for Property 9 — Read-Only Modes
# ---------------------------------------------------------------------------

# Controlled HTML that yields known supported versions for the mock fetch.
# Uses the same link pattern that parse_nginx_versions expects.
_MOCK_HTML_TEMPLATE = (
    '<h4>Stable version</h4>'
    '<a href="/download/nginx-{v}.tar.gz">nginx-{v}</a>'
)


def _build_mock_html(versions: list[str]) -> str:
    """
    Generate a minimal HTML document containing download links for the provided nginx versions.
    
    Parameters:
        versions (list[str]): Version strings to include; each version produces an anchor linking to "/download/nginx-<version>.tar.gz". Order of `versions` is preserved.
    
    Returns:
        html (str): A small HTML document with an <h4> header and one <a> link per version.
    """
    links = "".join(
        f'<a href="/download/nginx-{v}.tar.gz">nginx-{v}</a>' for v in versions
    )
    return f"<html><body><h4>Mainline version</h4>{links}</body></html>"


# Strategy: 1–3 unique supported versions >= 1.24.0 so they pass
# the MIN_SUPPORTED filter. We keep the set small to keep file I/O fast.
_supported_version_for_p9 = st.builds(
    "1.{}.{}".format,
    st.integers(min_value=24, max_value=30),
    st.integers(min_value=0, max_value=20),
)

_supported_versions_list = (
    st.lists(_supported_version_for_p9, min_size=1, max_size=3)
    .map(lambda vs: list(dict.fromkeys(vs)))  # deduplicate
)


# ---------------------------------------------------------------------------
# Property 9 — Read-Only Modes Do Not Modify Files
# ---------------------------------------------------------------------------


@given(versions=_supported_versions_list)
@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property9_read_only_modes_do_not_modify_files(tmp_path, versions, monkeypatch):
    """For any input state (including a pre-existing matrix-diff.json),
    running the Matrix_Updater with ``--dry-run`` or ``--check-only`` shall
    leave all files (release-matrix.json, INSTALLATION.md, and
    matrix-diff.json) byte-identical to their state before invocation.

    **Validates: Requirements 4.10, 5.1, 6.6**
    """
    import tools.release.matrix.update_matrix as um

    # --- Set up temporary files -------------------------------------------
    matrix_path = tmp_path / "release-matrix.json"
    doc_path = tmp_path / "INSTALLATION.md"
    diff_path = tmp_path / "matrix-diff.json"
    install_path = tmp_path / "install.sh"

    # install.sh with MIN_SUPPORTED_NGINX_VERSION
    install_path.write_text('MIN_SUPPORTED_NGINX_VERSION="1.24.0"\n')

    # A valid release-matrix.json (may differ from what nginx.org returns,
    # so the updater will detect changes — that's fine, we just want to
    # verify it doesn't write anything).
    matrix_data = {
        "schema_version": "1.0.0",
        "updated_at": "2025-07-14T00:00:00Z",
        "matrix": [
            {"nginx": "1.24.0", "os_type": "glibc", "arch": "x86_64", "support_tier": "full"},
            {"nginx": "1.24.0", "os_type": "glibc", "arch": "aarch64", "support_tier": "full"},
            {"nginx": "1.24.0", "os_type": "musl", "arch": "x86_64", "support_tier": "full"},
            {"nginx": "1.24.0", "os_type": "musl", "arch": "aarch64", "support_tier": "full"},
        ],
    }
    matrix_path.write_text(json.dumps(matrix_data, indent=2) + "\n")

    # INSTALLATION.md with markers
    doc_content = (
        "# Installation Guide\n"
        "Some intro text.\n"
        f"{DOC_MARKER_BEGIN}\n"
        "| NGINX Version | OS Type | Architecture | Support Tier |\n"
        "|---------------|---------|--------------|--------------|\n"
        "| 1.24.0 | glibc | x86_64 | Full |\n"
        f"{DOC_MARKER_END}\n"
        "Footer content.\n"
    )
    doc_path.write_text(doc_content)

    # Pre-existing matrix-diff.json (should survive read-only modes)
    diff_content = json.dumps({"added_versions": ["1.99.0"], "removed_versions": []}) + "\n"
    diff_path.write_text(diff_content)

    # --- Snapshot file contents before invocation -------------------------
    matrix_before = matrix_path.read_bytes()
    doc_before = doc_path.read_bytes()
    diff_before = diff_path.read_bytes()

    # --- Monkeypatch module-level constants and fetch ---------------------
    monkeypatch.setattr(um, "MATRIX_PATH", matrix_path)
    monkeypatch.setattr(um, "DOC_PATH", doc_path)
    monkeypatch.setattr(um, "DIFF_PATH", diff_path)
    monkeypatch.setattr(um, "INSTALL_SCRIPT_PATH", install_path)

    mock_html = _build_mock_html(versions)
    monkeypatch.setattr(um, "fetch_download_page", lambda _url: mock_html)
    mock_release_json = json.dumps(
        {
            "assets": [
                {
                    "name": f"ngx_http_markdown_filter_module-{v}-glibc-x86_64.tar.gz"
                }
                for v in versions
            ]
        }
    )
    monkeypatch.setattr(um, "fetch_release_json", lambda _url=None: mock_release_json)

    # --- Run --dry-run ----------------------------------------------------
    main(["--dry-run"])

    assert matrix_path.read_bytes() == matrix_before, (
        "--dry-run modified release-matrix.json"
    )
    assert doc_path.read_bytes() == doc_before, (
        "--dry-run modified INSTALLATION.md"
    )
    assert diff_path.read_bytes() == diff_before, (
        "--dry-run modified matrix-diff.json"
    )

    # --- Run --check-only -------------------------------------------------
    main(["--check-only"])

    assert matrix_path.read_bytes() == matrix_before, (
        "--check-only modified release-matrix.json"
    )
    assert doc_path.read_bytes() == doc_before, (
        "--check-only modified INSTALLATION.md"
    )
    assert diff_path.read_bytes() == diff_before, (
        "--check-only modified matrix-diff.json"
    )


# ---------------------------------------------------------------------------
# Property 10 — Idempotent Matrix Computation
# ---------------------------------------------------------------------------


@given(versions=_unique_versions)
@settings(max_examples=200)
def test_property10_idempotent_matrix_computation(versions):
    """
    Asserts that computing the release matrix twice with the same nginx versions yields identical results.
    
    Verifies both byte-identical JSON serialization (stable ordering and formatting) and structural equality between the two outputs.
    """
    os_types = ["glibc", "musl"]
    archs = ["x86_64", "aarch64"]

    first = compute_matrix(versions, os_types, archs)
    second = compute_matrix(versions, os_types, archs)

    # Byte-identical: serialise both to JSON and compare
    first_json = json.dumps(first, indent=2, sort_keys=True)
    second_json = json.dumps(second, indent=2, sort_keys=True)

    assert first_json == second_json, (
        "compute_matrix is not idempotent — two calls with the same input "
        "produced different output"
    )

    # Also verify structural equality
    assert first == second


# ---------------------------------------------------------------------------
# Unit Tests — CLI and orchestration (Task 6.4)
# ---------------------------------------------------------------------------

import tools.release.matrix.update_matrix as um
from tools.release.matrix.update_matrix import write_diff_file, MatrixDiff


def _setup_cli_env(
    tmp_path,
    versions_in_matrix,
    monkeypatch,
    *,
    html_versions=None,
    release_versions=None,
):
    """
    Create a complete temporary CLI test environment and monkeypatch module-level paths and download behavior for main()/CLI tests.
    
    Creates these files under tmp_path:
    - release-matrix.json populated with auto-managed matrix entries for each version in versions_in_matrix (glibc/musl × x86_64/aarch64).
    - INSTALLATION.md containing document markers and a table that matches the matrix entries.
    - install.sh containing MIN_SUPPORTED_NGINX_VERSION="1.24.0".
    - matrix-diff.json is not created by default (path is returned for tests that expect it).
    
    Monkeypatches the update_matrix module's path constants (MATRIX_PATH, DOC_PATH, DIFF_PATH, INSTALL_SCRIPT_PATH, REPO_ROOT) to point into tmp_path and replaces fetch_download_page / fetch_release_json with deterministic fixtures.
    
    Parameters:
        tmp_path: Temporary filesystem path object where test files will be created.
        versions_in_matrix: Iterable of nginx version strings to include in the generated release-matrix.json.
        monkeypatch: pytest monkeypatch fixture used to set attributes on the update_matrix module.
        html_versions (optional): Iterable of nginx version strings to include in the mocked HTML returned by fetch_download_page.
            If omitted, html_versions defaults to versions_in_matrix.
        release_versions (optional): Iterable of nginx version strings to include in the mocked latest GitHub release JSON.
            If omitted, release_versions defaults to versions_in_matrix.
    
    Returns:
        tuple: (matrix_path, doc_path, diff_path) Path objects for the created matrix file, documentation file, and diff file path respectively.
    """
    if html_versions is None:
        html_versions = versions_in_matrix
    if release_versions is None:
        release_versions = versions_in_matrix

    matrix_path = tmp_path / "release-matrix.json"
    doc_path = tmp_path / "INSTALLATION.md"
    diff_path = tmp_path / "matrix-diff.json"
    install_path = tmp_path / "install.sh"

    install_path.write_text('MIN_SUPPORTED_NGINX_VERSION="1.24.0"\n')

    # Build matrix entries for the given versions
    entries = []
    for v in versions_in_matrix:
        for os_type in ["glibc", "musl"]:
            entries.extend(
                {
                    "nginx": v,
                    "os_type": os_type,
                    "arch": arch,
                    "support_tier": "full",
                }
                for arch in ["x86_64", "aarch64"]
            )
    matrix_data = {
        "schema_version": "1.0.0",
        "updated_at": "2025-07-14T00:00:00Z",
        "matrix": entries,
    }
    matrix_path.write_text(json.dumps(matrix_data, indent=2) + "\n")

    # Build doc with markers and a table matching the matrix
    table_rows = []
    for e in entries:
        tier = e["support_tier"].replace("_", " ").title()
        table_rows.append(f"| {e['nginx']} | {e['os_type']} | {e['arch']} | {tier} |")

    doc_content = (
        "# Installation Guide\n"
        "Some intro text.\n"
        f"{DOC_MARKER_BEGIN}\n"
        "| NGINX Version | OS Type | Architecture | Support Tier |\n"
        "|---------------|---------|--------------|--------------|\n"
        + "\n".join(table_rows) + "\n"
        f"{DOC_MARKER_END}\n"
        "Footer content.\n"
    )
    doc_path.write_text(doc_content)

    # Monkeypatch paths and fetch
    monkeypatch.setattr(um, "MATRIX_PATH", matrix_path)
    monkeypatch.setattr(um, "DOC_PATH", doc_path)
    monkeypatch.setattr(um, "DIFF_PATH", diff_path)
    monkeypatch.setattr(um, "INSTALL_SCRIPT_PATH", install_path)
    monkeypatch.setattr(um, "REPO_ROOT", tmp_path)

    mock_html = _build_mock_html(html_versions)
    monkeypatch.setattr(um, "fetch_download_page", lambda _url: mock_html)
    mock_release_json = json.dumps(
        {
            "assets": [
                {
                    "name": f"ngx_http_markdown_filter_module-{v}-glibc-x86_64.tar.gz"
                }
                for v in release_versions
            ]
        }
    )
    monkeypatch.setattr(um, "fetch_release_json", lambda _url=None: mock_release_json)

    return matrix_path, doc_path, diff_path


# ---------------------------------------------------------------------------
# 1. Test --dry-run prints changes but writes no files
# ---------------------------------------------------------------------------


def test_cli_dry_run_no_file_writes(tmp_path, monkeypatch, capsys):
    """--dry-run prints changes to stdout but does not modify any files."""
    existing = ["1.24.0", "1.26.3"]
    from_nginx = ["1.24.0", "1.26.3", "1.28.0"]  # 1.28.0 is new

    matrix_path, doc_path, diff_path = _setup_cli_env(
        tmp_path,
        existing,
        monkeypatch,
        html_versions=from_nginx,
        release_versions=from_nginx,
    )

    matrix_before = matrix_path.read_bytes()
    doc_before = doc_path.read_bytes()

    exit_code = main(["--dry-run"])

    assert exit_code == 0
    # Files must be untouched
    assert matrix_path.read_bytes() == matrix_before
    assert doc_path.read_bytes() == doc_before
    assert not diff_path.exists()

    # Stdout should mention the dry-run and the new version
    captured = capsys.readouterr()
    assert "Dry-run" in captured.out or "dry-run" in captured.out.lower()
    assert "1.28.0" in captured.out


# ---------------------------------------------------------------------------
# 2. Test --check-only returns correct exit codes
# ---------------------------------------------------------------------------


def test_cli_check_only_fresh_exit_0(tmp_path, monkeypatch):
    """--check-only returns 0 when matrix matches nginx.org (fresh)."""
    versions = ["1.24.0", "1.26.3"]
    _setup_cli_env(
        tmp_path, versions, monkeypatch, html_versions=versions,
    )

    exit_code = main(["--check-only"])
    assert exit_code == 0


def test_cli_check_only_ignores_upstream_only_versions_without_release_assets(
    tmp_path, monkeypatch
):
    """Upstream-only versions should not enter the matrix until release assets exist."""
    existing = ["1.24.0", "1.26.3", "1.28.3", "1.29.8"]
    upstream = existing + ["1.30.0"]

    _setup_cli_env(
        tmp_path,
        existing,
        monkeypatch,
        html_versions=upstream,
        release_versions=existing,
    )

    exit_code = main(["--check-only"])
    assert exit_code == 0


def test_cli_check_only_stale_exit_2(tmp_path, monkeypatch):
    """
    Verify the CLI exits with status code 2 when the local release matrix is stale because newer versions exist upstream.
    """
    existing = ["1.24.0"]
    from_nginx = ["1.24.0", "1.26.3"]  # 1.26.3 missing from matrix

    _setup_cli_env(
        tmp_path,
        existing,
        monkeypatch,
        html_versions=from_nginx,
        release_versions=from_nginx,
    )

    exit_code = main(["--check-only"])
    assert exit_code == 2


def test_cli_check_only_error_exit_1(tmp_path, monkeypatch):
    """--check-only returns 1 on scraping/parsing error."""
    versions = ["1.24.0"]
    _setup_cli_env(
        tmp_path, versions, monkeypatch,
    )

    # Make fetch raise a URLError to simulate network failure
    from urllib.error import URLError
    def raise_url_error(_url=None):
        """
        Raise a URLError indicating the network is down.
        
        Raises:
            URLError: Always raised with the message "network down".
        """
        raise URLError("network down")
    monkeypatch.setattr(um, "fetch_release_json", raise_url_error)

    exit_code = main(["--check-only"])
    assert exit_code == 1


# ---------------------------------------------------------------------------
# 3. Test matrix-diff.json output structure
# ---------------------------------------------------------------------------


def test_cli_diff_json_structure(tmp_path, monkeypatch):
    """Normal mode writes matrix-diff.json with correct added/removed structure."""
    existing = ["1.24.0"]
    from_nginx = ["1.24.0", "1.26.3"]  # 1.26.3 is new

    _, _, diff_path = _setup_cli_env(
        tmp_path,
        existing,
        monkeypatch,
        html_versions=from_nginx,
        release_versions=from_nginx,
    )

    exit_code = main([])
    assert exit_code == 0
    assert diff_path.exists()

    diff_data = json.loads(diff_path.read_text())
    assert "added_versions" in diff_data
    assert "removed_versions" in diff_data
    assert "1.26.3" in diff_data["added_versions"]
    assert diff_data["removed_versions"] == []


def test_cli_diff_json_removed_versions(tmp_path, monkeypatch):
    """matrix-diff.json correctly reports removed versions."""
    existing = ["1.24.0", "1.26.3"]
    from_nginx = ["1.26.3"]  # 1.24.0 dropped from nginx.org

    _, _, diff_path = _setup_cli_env(
        tmp_path,
        existing,
        monkeypatch,
        html_versions=from_nginx,
        release_versions=from_nginx,
    )

    # The default min is 1.24.0, so 1.26.3 passes. 1.24.0 disappears from
    # the latest release asset set, so it gets removed from the desired matrix.
    exit_code = main([])
    assert exit_code == 0
    assert diff_path.exists()

    diff_data = json.loads(diff_path.read_text())
    assert "1.24.0" in diff_data["removed_versions"]
    assert diff_data["added_versions"] == []


# ---------------------------------------------------------------------------
# 4. Test crash-safe write rollback on simulated failure
# ---------------------------------------------------------------------------


def test_cli_rollback_on_doc_write_failure(tmp_path, monkeypatch):
    """
    Ensure the matrix file is restored when updating the documentation fails.
    
    Simulates a failure during the documentation file rename and asserts that the CLI exits with code 1, the original matrix file content is restored from backup, and no temporary documentation files remain.
    """
    existing = ["1.24.0"]
    from_nginx = ["1.24.0", "1.26.3"]

    matrix_path, _, _ = _setup_cli_env(
        tmp_path,
        existing,
        monkeypatch,
        html_versions=from_nginx,
        release_versions=from_nginx,
    )

    matrix_before = matrix_path.read_text()

    # Let the matrix write succeed, but make the doc temp-file write fail.
    # We intercept os.replace: allow the first call (matrix rename) but
    # fail the second call (doc rename).
    import os as _os
    original_replace = _os.replace
    call_count = [0]

    def selective_replace(src, dst):
        """
        Replace the destination path with the source path, simulating a failure on the second call.
        
        Parameters:
            src (str | pathlib.Path): Source path to use for replacement.
            dst (str | pathlib.Path): Destination path to be replaced.
        
        Returns:
            The result of the underlying replace operation (typically `None`).
        
        Raises:
            OSError: Raised on the second invocation to simulate a rename failure.
        
        Notes:
            Increments the shared counter `call_count[0]` on every invocation as a side effect.
        """
        call_count[0] += 1
        if call_count[0] == 2:
            raise OSError("simulated doc rename failure")
        return original_replace(src, dst)

    monkeypatch.setattr("os.replace", selective_replace)

    exit_code = main([])
    assert exit_code == 1

    # Matrix should be restored to its original content
    assert matrix_path.read_text() == matrix_before

    # No leftover temp files
    assert not (tmp_path / "INSTALLATION.md.tmp").exists()


# ---------------------------------------------------------------------------
# 5. Test no-change scenario
# ---------------------------------------------------------------------------


def test_cli_no_change_exit_0(tmp_path, monkeypatch, capsys):
    """When matrix is already up to date, exit 0 with informational message."""
    versions = ["1.24.0", "1.26.3"]
    matrix_path, doc_path, diff_path = _setup_cli_env(
        tmp_path,
        versions,
        monkeypatch,
        html_versions=versions,
        release_versions=versions,
    )

    matrix_before = matrix_path.read_bytes()
    doc_before = doc_path.read_bytes()

    exit_code = main([])
    assert exit_code == 0

    # No files modified
    assert matrix_path.read_bytes() == matrix_before
    assert doc_path.read_bytes() == doc_before
    assert not diff_path.exists()

    # Informational message printed
    captured = capsys.readouterr()
    assert "no changes" in captured.out.lower() or "up to date" in captured.out.lower()


# ---------------------------------------------------------------------------
# 6. Test version addition/removal logging to stdout
# ---------------------------------------------------------------------------


def test_cli_version_logging(tmp_path, monkeypatch, capsys):
    """Each version addition and removal is logged to stdout."""
    existing = ["1.24.0", "1.26.3"]
    from_nginx = ["1.26.3", "1.28.0"]  # add 1.28.0, remove 1.24.0

    _setup_cli_env(
        tmp_path,
        existing,
        monkeypatch,
        html_versions=from_nginx,
        release_versions=from_nginx,
    )

    exit_code = main([])
    assert exit_code == 0

    captured = capsys.readouterr()
    # Addition logged
    assert "1.28.0" in captured.out
    assert "adding" in captured.out.lower() or "add" in captured.out.lower()
    # Removal logged
    assert "1.24.0" in captured.out
    assert "removing" in captured.out.lower() or "remove" in captured.out.lower()
