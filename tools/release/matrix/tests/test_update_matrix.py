"""Property-based tests for tools/release/matrix/update_matrix.py.

Tests for the automated nginx release matrix updater.
"""

import itertools
import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import tools.release.matrix.update_matrix as um
from tools.release.matrix.update_matrix import (
    _entry_sort_key,
    _resolve_repo_write_path,
    classify_version,
    compute_matrix,
    diff_matrix,
    filter_versions,
    load_matrix,
    merge_matrix,
    parse_release_module_versions,
    parse_nginx_versions,
    version_tuple,
    write_matrix,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_nginx_version = st.builds(
    lambda minor, patch: f"1.{minor}.{patch}",
    st.integers(min_value=0, max_value=99),
    st.integers(min_value=0, max_value=99),
)


# ---------------------------------------------------------------------------
# Property 1 — Version Classification Correctness
# ---------------------------------------------------------------------------


@given(
    minor=st.integers(min_value=0, max_value=99),
    patch=st.integers(min_value=0, max_value=99),
)
@settings(max_examples=200)
def test_version_classification_correctness(minor, patch):
    """classify_version returns 'stable' for even minor, 'mainline' for odd.

    **Validates: Requirements 1.2**
    """
    version = f"1.{minor}.{patch}"
    result = classify_version(version)
    if minor % 2 == 0:
        assert (
            result == "stable"
        ), f"Expected 'stable' for even minor {minor}, got '{result}'"
    else:
        assert (
            result == "mainline"
        ), f"Expected 'mainline' for odd minor {minor}, got '{result}'"


# ---------------------------------------------------------------------------
# Property 2 — Version Filtering Completeness
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    filter_versions is None, reason="filter_versions not yet implemented (Task 1.4)"
)
@given(
    versions=st.lists(_nginx_version, min_size=0, max_size=30),
    min_version=_nginx_version,
)
@settings(max_examples=200)
def test_version_filtering_completeness(versions, min_version):
    """Asserts that filter_versions returns only versions greater than or equal to
    min_version and that every input version meeting that threshold appears in the
    output.

    Parameters:
        versions (list[str]): Candidate version strings to filter.
        min_version (str): Minimum version string; versions less than this value must be excluded.
    """
    filtered = filter_versions(versions, min_version)
    min_tuple = version_tuple(min_version)

    # Every version in the output must be >= min_version
    for v in filtered:
        t = version_tuple(v)
        assert (
            t >= min_tuple
        ), f"Filtered version {v} is below min_version {min_version}"

    # No qualifying version from the input should be missing from the output
    filtered_set = set(filtered)
    for v in versions:
        t = version_tuple(v)
        if t >= min_tuple:
            assert (
                v in filtered_set
            ), f"Qualifying version {v} was excluded from filtered output"


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
    assert not parse_nginx_versions("")


def test_parse_no_matching_links():
    """HTML with no download links matching the pattern yields zero versions."""
    html = "<html><body><a href='/other/file.tar.gz'>nothing</a></body></html>"
    assert not parse_nginx_versions(html)


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
                {
                    "name": "ngx_http_markdown_filter_module-mainline-glibc-x86_64.tar.gz"
                },
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


def test_fetch_download_page_rejects_non_https_url(monkeypatch):
    """fetch_download_page should reject non-HTTPS URLs before network I/O."""

    def _unexpected_urlopen(*_args, **_kwargs):
        raise AssertionError("urlopen should not be called for rejected URLs")

    monkeypatch.setattr(um, "urlopen", _unexpected_urlopen)
    with pytest.raises(ValueError, match="unsupported URL scheme"):
        um.fetch_download_page("http://nginx.org/en/download.html")


def test_fetch_download_page_rejects_untrusted_host(monkeypatch):
    """fetch_download_page should reject hosts outside the allowlist."""

    def _unexpected_urlopen(*_args, **_kwargs):
        raise AssertionError("urlopen should not be called for rejected URLs")

    monkeypatch.setattr(um, "urlopen", _unexpected_urlopen)
    with pytest.raises(ValueError, match="untrusted URL host"):
        um.fetch_download_page("https://example.com/en/download.html")


def test_fetch_release_json_rejects_untrusted_host(monkeypatch):
    """fetch_release_json should reject custom URLs outside api.github.com."""

    def _unexpected_urlopen(*_args, **_kwargs):
        raise AssertionError("urlopen should not be called for rejected URLs")

    monkeypatch.setattr(um, "urlopen", _unexpected_urlopen)
    with pytest.raises(ValueError, match="untrusted URL host"):
        um.fetch_release_json("https://evil.example/releases/latest")


# ---------------------------------------------------------------------------
# Unit Tests — load_matrix
# ---------------------------------------------------------------------------


def test_load_matrix_valid(tmp_path):
    """load_matrix returns (data, auto_entries, manual_entries) for valid JSON."""
    matrix_data = {
        "schema_version": "1.0.0",
        "updated_at": "2025-07-14T00:00:00Z",
        "matrix": [
            {
                "nginx": "1.26.3",
                "os_type": "glibc",
                "arch": "x86_64",
                "support_tier": "full",
            },
            {
                "nginx": "1.24.0",
                "os_type": "glibc",
                "arch": "x86_64",
                "support_tier": "source_only",
                "managed_by": "manual",
            },
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
            {
                "nginx": "1.26.3",
                "os_type": "glibc",
                "arch": "x86_64",
                "support_tier": "full",
                "managed_by": "auto",
            },
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
            {
                "nginx": "1.28.0",
                "os_type": "musl",
                "arch": "aarch64",
                "support_tier": "full",
            },
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
            {
                "nginx": "1.22.1",
                "os_type": "glibc",
                "arch": "x86_64",
                "support_tier": "source_only",
                "managed_by": "manual",
            },
            {
                "nginx": "1.22.1",
                "os_type": "glibc",
                "arch": "x86_64",
                "support_tier": "full",
                "managed_by": "manual",
            },
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
            {
                "nginx": "1.22.1",
                "arch": "x86_64",
                "support_tier": "full",
                "managed_by": "manual",
            },
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

    monkeypatch.setattr(um, "REPO_ROOT", repo_root)

    with pytest.raises(ValueError, match="outside repository root"):
        _resolve_repo_write_path(tmp_path / "outside.json")


def _allow_repo_writes(monkeypatch, repo_root: Path) -> None:
    """Point the updater at a temporary repository root for tests by setting its
    REPO_ROOT.

    Parameters:
        monkeypatch (pytest.MonkeyPatch): The pytest monkeypatch fixture used to set attributes.
        repo_root (Path): Path to the temporary repository root used for
            file writes in tests.
    """
    repo_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(um, "REPO_ROOT", repo_root)


def test_load_matrix_preserves_full_data(tmp_path):
    """load_matrix preserves all top-level keys in the returned data dict."""
    matrix_data = {
        "schema_version": "1.0.0",
        "updated_at": "2025-07-14T00:00:00Z",
        "support_tiers": {"full": "desc"},
        "matrix": [
            {
                "nginx": "1.26.3",
                "os_type": "glibc",
                "arch": "x86_64",
                "support_tier": "full",
            },
        ],
    }
    p = tmp_path / "release-matrix.json"
    p.write_text(json.dumps(matrix_data))

    data, _, _ = load_matrix(p)

    assert data["schema_version"] == "1.0.0"
    assert data["updated_at"] == "2025-07-14T00:00:00Z"
    assert data["support_tiers"] == {"full": "desc"}


def test_load_matrix_file_not_found(tmp_path):
    """Asserts that calling load_matrix on a nonexistent path causes the process to exit
    with status code 1.

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

_unique_versions = st.lists(_nginx_version, min_size=0, max_size=10).map(
    lambda vs: list(dict.fromkeys(vs))
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
    """Verifies the release matrix contains the complete cross-product of provided
    versions, OS types, and architectures.

    Asserts the matrix has exactly len(versions) × 2 × 2 entries for the two
    OS types and two architectures used in the test, each
    (nginx, os_type, arch) tuple appears exactly once, and every entry has
    support_tier "full".

    Parameters:
        versions (list[str]): Sequence of nginx version strings to generate the matrix for.
    """
    os_types = ["glibc", "musl"]
    archs = ["x86_64", "aarch64"]
    matrix = compute_matrix(versions, os_types, archs)

    expected_count = len(versions) * len(os_types) * len(archs)
    assert (
        len(matrix) == expected_count
    ), f"Expected {expected_count} entries, got {len(matrix)}"

    # Every combination appears exactly once
    seen: set[tuple[str, str, str]] = set()
    for entry in matrix:
        key = (entry["nginx"], entry["os_type"], entry["arch"])
        assert key not in seen, f"Duplicate entry for {key}"
        seen.add(key)
        assert (
            entry["support_tier"] == "full"
        ), f"Expected support_tier 'full', got '{entry['support_tier']}'"

    # Every expected combination is present
    for v in versions:
        for os_type, arch in itertools.product(os_types, archs):
            assert (
                v,
                os_type,
                arch,
            ) in seen, f"Missing entry for ({v}, {os_type}, {arch})"


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

    assert (
        set(diff.added_versions) == expected_added
    ), f"Added mismatch: got {diff.added_versions}, expected {expected_added}"
    assert (
        set(diff.removed_versions) == expected_removed
    ), f"Removed mismatch: got {diff.removed_versions}, expected {expected_removed}"
    assert diff.has_changes == bool(expected_added or expected_removed)


# ---------------------------------------------------------------------------
# Property 6 — Matrix Entry Sorting
# ---------------------------------------------------------------------------


@given(versions=_unique_versions)
@settings(max_examples=200)
def test_property6_matrix_entry_sorting(versions):
    """Entries from compute_matrix are sorted by version tuple ascending, then os_type
    alphabetical, then arch alphabetical.

    **Validates: Requirements 2.8, 3.3**
    """
    os_types = ["glibc", "musl"]
    archs = ["x86_64", "aarch64"]
    matrix = compute_matrix(versions, os_types, archs)

    for i in range(1, len(matrix)):
        prev_key = _entry_sort_key(matrix[i - 1])
        curr_key = _entry_sort_key(matrix[i])
        assert (
            prev_key <= curr_key
        ), f"Sorting violation at index {i}: {prev_key} > {curr_key}"


# ---------------------------------------------------------------------------
# Property 11 — Pin_Entry Preservation
# ---------------------------------------------------------------------------


@given(
    auto_versions=_unique_versions,
    manual_entries=st.lists(
        st.fixed_dictionaries(
            {
                "nginx": _nginx_version,
                "os_type": _os_types,
                "arch": _archs,
                "support_tier": st.sampled_from(["full", "source_only"]),
                "managed_by": st.just("manual"),
            }
        ),
        min_size=0,
        max_size=5,
    ),
)
@settings(max_examples=200)
def test_property11_pin_entry_preservation(auto_versions, manual_entries):
    """Verify merge_matrix preserves every manual Pin_Entry in the merged matrix.

    Asserts that for each manual entry there is exactly one merged entry with the same
    (nginx, os_type, arch), that it remains marked as managed_by "manual", and that its
    support_tier is unchanged.
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
        matching = [e for e in merged if (e["nginx"], e["os_type"], e["arch"]) == key]
        assert (
            len(matching) == 1
        ), f"Expected exactly 1 entry for manual key {key}, found {len(matching)}"
        assert (
            matching[0]["managed_by"] == "manual"
        ), f"Manual entry for {key} was not preserved"
        assert (
            matching[0]["support_tier"] == manual_e["support_tier"]
        ), f"Support tier changed for manual entry {key}"


# ---------------------------------------------------------------------------
# Property 12 — Key Uniqueness After Merge
# ---------------------------------------------------------------------------


@given(
    auto_versions=_unique_versions,
    manual_entries=st.lists(
        st.fixed_dictionaries(
            {
                "nginx": _nginx_version,
                "os_type": _os_types,
                "arch": _archs,
                "support_tier": st.sampled_from(["full", "source_only"]),
                "managed_by": st.just("manual"),
            }
        ),
        min_size=0,
        max_size=5,
    ),
)
@settings(max_examples=200)
def test_property12_key_uniqueness_after_merge(auto_versions, manual_entries):
    """The merged matrix contains at most one entry per (nginx, os_type, arch) key. When
    a manual entry and an auto entry share the same key, only the manual entry appears.

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
            assert (
                entry.get("managed_by") == "manual"
            ), f"Key {key} collides with manual entry but auto entry was kept"


# ---------------------------------------------------------------------------
# Unit test — entry-level diff detection
# ---------------------------------------------------------------------------


def test_diff_matrix_entry_level_change():
    """diff_matrix detects entry-level changes even when version sets are identical.

    When the same versions exist in both current and desired but individual entries
    differ (e.g., different support_tier or missing/extra platform entries), has_changes
    should be True even though added_versions and removed_versions are both empty.
    """
    # Same version, but different support_tier on one entry
    current = [
        {
            "nginx": "1.24.0",
            "os_type": "glibc",
            "arch": "x86_64",
            "support_tier": "full",
        },
        {
            "nginx": "1.24.0",
            "os_type": "musl",
            "arch": "x86_64",
            "support_tier": "full",
        },
    ]
    desired = [
        {
            "nginx": "1.24.0",
            "os_type": "glibc",
            "arch": "x86_64",
            "support_tier": "full",
        },
        {
            "nginx": "1.24.0",
            "os_type": "musl",
            "arch": "x86_64",
            "support_tier": "source_only",
        },
    ]

    diff = diff_matrix(current, desired)

    # Version sets are identical — no version-level additions/removals
    assert not diff.added_versions
    assert not diff.removed_versions
    # But entry-level change detected
    assert diff.has_changes is True


def test_diff_matrix_missing_platform_entry():
    """diff_matrix detects when a platform entry is added or removed for an existing
    version (sparse matrix change)."""
    current = [
        {
            "nginx": "1.24.0",
            "os_type": "glibc",
            "arch": "x86_64",
            "support_tier": "full",
        },
    ]
    desired = [
        {
            "nginx": "1.24.0",
            "os_type": "glibc",
            "arch": "x86_64",
            "support_tier": "full",
        },
        {
            "nginx": "1.24.0",
            "os_type": "glibc",
            "arch": "aarch64",
            "support_tier": "full",
        },
    ]

    diff = diff_matrix(current, desired)

    # Same version set
    assert not diff.added_versions
    assert not diff.removed_versions
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
            {
                "nginx": "1.26.3",
                "os_type": "glibc",
                "arch": "x86_64",
                "support_tier": "full",
            },
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
        "support_tiers": {
            "full": "Prebuilt binary",
            "source_only": "Build from source",
        },
        "matrix": [],
    }
    write_matrix(target, data)

    parsed = json.loads(target.read_text())
    assert parsed["schema_version"] == "2.0.0"
    assert parsed["updated_at"] == "2025-01-01T12:00:00Z"
    assert parsed["support_tiers"] == data["support_tiers"]
    assert not parsed["matrix"]


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
    _allow_repo_writes(monkeypatch, tmp_path)
    target = tmp_path / "release-matrix.json"
    tmp_file = target.with_suffix(f"{target.suffix}.tmp")

    # Make os.replace raise an OSError to simulate a rename failure
    def failing_replace(src, dst):
        """Simulate a file rename failure by always raising an OSError.

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
