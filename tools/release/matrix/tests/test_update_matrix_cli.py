"""CLI and doc-table focused tests for update_matrix."""

import json
import os
import sys
from urllib.error import URLError

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import tools.release.matrix.update_matrix as um
from tools.release.matrix.update_matrix import (
    DOC_MARKER_BEGIN,
    DOC_MARKER_END,
    compute_matrix,
    main,
    parse_args,
    update_doc_table,
)

from tools.release.matrix.tests.test_update_matrix import (
    _archs,
    _matrix_entry_with_managed_by,
    _nginx_version,
    _os_types,
    _unique_versions,
    _allow_repo_writes,
    write_matrix,
)

# ---------------------------------------------------------------------------
# Helper for doc-marker tests
# ---------------------------------------------------------------------------


def _make_doc_with_markers(before: str, after: str) -> str:
    """Build a document containing marker-bounded placeholder table content.

    Parameters:
        before (str): Text to place before the doc markers.
        after (str): Text to place after the doc markers.

    Returns:
        str: The composed document with placeholder content between markers.
    """
    return f"{before}\n{DOC_MARKER_BEGIN}\nold table content\n{DOC_MARKER_END}\n{after}"


def _parse_doc_table_tuples(new_content: str) -> set[tuple[str, str, str, str]]:
    """Extract data tuples from the generated doc table between markers."""
    begin_idx = new_content.index(DOC_MARKER_BEGIN) + len(DOC_MARKER_BEGIN)
    end_idx = new_content.index(DOC_MARKER_END)
    table_rows = new_content[begin_idx:end_idx].strip().split("\n")
    data_rows = [row for row in table_rows if row.startswith("|") and "---" not in row]
    if data_rows:
        data_rows = data_rows[1:]  # Skip table header row

    parsed: set[tuple[str, str, str, str]] = set()
    for row in data_rows:
        cells = [cell.strip() for cell in row.split("|") if cell.strip()]
        if len(cells) == 4:
            parsed.add((cells[0], cells[1], cells[2], cells[3]))
    return parsed


def _expected_doc_table_tuples(entries: list[dict]) -> set[tuple[str, str, str, str]]:
    """Build expected tuples from matrix entries using display-tier formatting."""
    expected: set[tuple[str, str, str, str]] = set()
    for entry in entries:
        tier = entry["support_tier"].replace("_", " ").title()
        expected.add((entry["nginx"], entry["os_type"], entry["arch"], tier))
    return expected


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
def test_property5_schema_version_preservation(
    tmp_path, monkeypatch, schema_ver, entries
):
    """For any valid release-matrix.json with a schema_version field, after running
    write_matrix, the output schema_version shall equal the input schema_version.

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
    assert (
        parsed["schema_version"] == schema_ver
    ), f"schema_version changed: expected {schema_ver!r}, got {parsed['schema_version']!r}"


# ---------------------------------------------------------------------------
# Property 7 — Doc Table Reflects Matrix
# ---------------------------------------------------------------------------


@given(
    entries=st.lists(
        st.fixed_dictionaries(
            {
                "nginx": _nginx_version,
                "os_type": _os_types,
                "arch": _archs,
                "support_tier": st.sampled_from(["full", "source_only"]),
            }
        ),
        min_size=0,
        max_size=10,
    ),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property7_doc_table_reflects_matrix(tmp_path, entries):
    """For any set of matrix entries, the generated Platform Compatibility Matrix table
    shall contain exactly the same set of (nginx, os_type, arch, tier) tuples as the
    matrix entries.

    **Validates: Requirements 3.1**
    """
    doc_content = _make_doc_with_markers("# Header", "Footer text")
    doc_path = tmp_path / "INSTALLATION.md"
    doc_path.write_text(doc_content)

    new_content = update_doc_table(doc_path, entries)

    parsed_tuples = _parse_doc_table_tuples(new_content)
    expected_tuples = _expected_doc_table_tuples(entries)

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
        st.fixed_dictionaries(
            {
                "nginx": _nginx_version,
                "os_type": _os_types,
                "arch": _archs,
                "support_tier": st.just("full"),
            }
        ),
        min_size=0,
        max_size=5,
    ),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property8_doc_surrounding_content_preservation(
    tmp_path, before, after, entries
):
    """For any INSTALLATION.md document containing the markers, updating the table shall
    not modify any content outside the marker boundaries (content before the BEGIN
    marker and content after the END marker).

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


def _build_mock_html(versions: list[str]) -> str:
    """Generate a minimal HTML document containing download links for the provided nginx
    versions.

    Parameters:
        versions (list[str]): Version strings to include. Each version yields
            one anchor pointing to "/download/nginx-<version>.tar.gz" in input
            order.

    Returns:
        html (str): A small HTML document with an <h4> header and one <a> link per version.
    """
    links = "".join(
        f'<a href="/download/nginx-{v}.tar.gz">nginx-{v}</a>' for v in versions
    )
    return f"<html><body><h4>Mainline version</h4>{links}</body></html>"


# Strategy: 1-3 unique supported versions >= 1.24.0 so they pass
# the MIN_SUPPORTED filter. We keep the set small to keep file I/O fast.
_supported_version_for_p9 = st.builds(
    lambda minor, patch: f"1.{minor}.{patch}",
    st.integers(min_value=24, max_value=30),
    st.integers(min_value=0, max_value=20),
)

_supported_versions_list = st.lists(
    _supported_version_for_p9, min_size=1, max_size=3
).map(
    lambda vs: list(dict.fromkeys(vs))
)  # deduplicate


# ---------------------------------------------------------------------------
# Property 9 — Read-Only Modes Do Not Modify Files
# ---------------------------------------------------------------------------


@given(versions=_supported_versions_list)
@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property9_read_only_modes_do_not_modify_files(tmp_path, versions, monkeypatch):
    """For any input state (including a pre-existing matrix-diff.json), running the
    Matrix_Updater with ``--dry-run`` or ``--check-only`` shall leave all files
    (release-matrix.json, INSTALLATION.md, and matrix-diff.json) byte-identical to their
    state before invocation.

    **Validates: Requirements 4.10, 5.1, 6.6**
    """
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
        "matrix": compute_matrix(["1.24.0"], ["glibc", "musl"], ["x86_64", "aarch64"]),
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
    diff_content = (
        json.dumps({"added_versions": ["1.99.0"], "removed_versions": []}) + "\n"
    )
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
    # --- Run --dry-run ----------------------------------------------------
    main(["--dry-run"])

    assert (
        matrix_path.read_bytes() == matrix_before
    ), "--dry-run modified release-matrix.json"
    assert doc_path.read_bytes() == doc_before, "--dry-run modified INSTALLATION.md"
    assert diff_path.read_bytes() == diff_before, "--dry-run modified matrix-diff.json"

    # --- Run --check-only -------------------------------------------------
    main(["--check-only"])

    assert (
        matrix_path.read_bytes() == matrix_before
    ), "--check-only modified release-matrix.json"
    assert doc_path.read_bytes() == doc_before, "--check-only modified INSTALLATION.md"
    assert (
        diff_path.read_bytes() == diff_before
    ), "--check-only modified matrix-diff.json"


# ---------------------------------------------------------------------------
# Property 10 — Idempotent Matrix Computation
# ---------------------------------------------------------------------------


@given(versions=_unique_versions)
@settings(max_examples=200)
def test_property10_idempotent_matrix_computation(versions):
    """Asserts that computing the release matrix twice with the same nginx versions
    yields identical results.

    Verifies both byte-identical JSON serialization (stable ordering and formatting) and
    structural equality between the two outputs.
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


def _build_entries_for_versions(versions_in_matrix: list[str]) -> list[dict]:
    """Build auto-managed matrix entries for each version/platform combination."""
    entries = []
    for version in versions_in_matrix:
        for os_type in ["glibc", "musl"]:
            entries.extend(
                {
                    "nginx": version,
                    "os_type": os_type,
                    "arch": arch,
                    "support_tier": "full",
                }
                for arch in ["x86_64", "aarch64"]
            )
    return entries


def _build_installation_doc(entries: list[dict]) -> str:
    """Render INSTALLATION.md content with markers and a matching matrix table."""
    table_rows = []
    for entry in entries:
        tier = entry["support_tier"].replace("_", " ").title()
        table_rows.append(
            f"| {entry['nginx']} | {entry['os_type']} | {entry['arch']} | {tier} |"
        )
    return (
        "# Installation Guide\n"
        "Some intro text.\n"
        f"{DOC_MARKER_BEGIN}\n"
        "| NGINX Version | OS Type | Architecture | Support Tier |\n"
        "|---------------|---------|--------------|--------------|\n"
        + "\n".join(table_rows)
        + "\n"
        f"{DOC_MARKER_END}\n"
        "Footer content.\n"
    )


def _setup_cli_env(
    tmp_path,
    versions_in_matrix,
    monkeypatch,
    *,
    html_versions=None,
):
    """Create a complete temporary CLI test environment and monkeypatch module-level
    paths and download behavior for main()/CLI tests.

    Creates these files under tmp_path:
    - release-matrix.json with auto-managed matrix entries for each version in
      versions_in_matrix (glibc/musl x x86_64/aarch64).
    - INSTALLATION.md containing document markers and a table that matches the matrix entries.
    - install.sh containing MIN_SUPPORTED_NGINX_VERSION="1.24.0".
    - matrix-diff.json is not created by default (path is returned for tests that expect it).

    Monkeypatches update_matrix path constants (MATRIX_PATH, DOC_PATH,
    DIFF_PATH, INSTALL_SCRIPT_PATH, REPO_ROOT) to tmp_path and replaces
    fetch_download_page with deterministic HTML.

    Parameters:
        tmp_path: Temporary filesystem path object where test files will be created.
        versions_in_matrix: Iterable of nginx version strings to include in
            the generated release-matrix.json.
        monkeypatch: pytest monkeypatch fixture used to set attributes on the update_matrix module.
        html_versions (optional): nginx versions included in mocked HTML from
            fetch_download_page. Defaults to versions_in_matrix.

    Returns:
        tuple: (matrix_path, doc_path, diff_path) path objects for created
            matrix/doc files and the diff file path.
    """
    if html_versions is None:
        html_versions = versions_in_matrix
    matrix_path = tmp_path / "release-matrix.json"
    doc_path = tmp_path / "INSTALLATION.md"
    diff_path = tmp_path / "matrix-diff.json"
    install_path = tmp_path / "install.sh"

    install_path.write_text('MIN_SUPPORTED_NGINX_VERSION="1.24.0"\n')

    entries = _build_entries_for_versions(versions_in_matrix)
    matrix_data = {
        "schema_version": "1.0.0",
        "updated_at": "2025-07-14T00:00:00Z",
        "matrix": entries,
    }
    matrix_path.write_text(json.dumps(matrix_data, indent=2) + "\n")
    doc_path.write_text(_build_installation_doc(entries))

    # Monkeypatch paths and fetch
    monkeypatch.setattr(um, "MATRIX_PATH", matrix_path)
    monkeypatch.setattr(um, "DOC_PATH", doc_path)
    monkeypatch.setattr(um, "DIFF_PATH", diff_path)
    monkeypatch.setattr(um, "INSTALL_SCRIPT_PATH", install_path)
    monkeypatch.setattr(um, "REPO_ROOT", tmp_path)

    mock_html = _build_mock_html(html_versions)
    monkeypatch.setattr(um, "fetch_download_page", lambda _url: mock_html)
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
        tmp_path,
        versions,
        monkeypatch,
        html_versions=versions,
    )

    exit_code = main(["--check-only"])
    assert exit_code == 0


def test_cli_check_only_fresh_with_latest_official_versions(tmp_path, monkeypatch):
    """--check-only stays green when the matrix includes the latest stable and mainline releases."""
    versions = ["1.24.0", "1.26.3", "1.28.3", "1.29.8", "1.30.0"]
    _setup_cli_env(
        tmp_path,
        versions,
        monkeypatch,
        html_versions=versions,
    )

    exit_code = main(["--check-only"])
    assert exit_code == 0


def test_cli_check_only_ignores_release_asset_versions_not_on_download_page(
    tmp_path, monkeypatch
):
    """Release-asset noise should not affect the matrix when nginx.org is unchanged."""
    existing = ["1.24.0", "1.26.3", "1.28.3", "1.29.8"]

    _setup_cli_env(
        tmp_path,
        existing,
        monkeypatch,
        html_versions=existing,
    )

    exit_code = main(["--check-only"])
    assert exit_code == 0


def test_cli_check_only_stale_exit_2(tmp_path, monkeypatch):
    """Verify the CLI exits with status code 2 when the local release matrix is stale
    because newer versions exist on nginx.org."""
    existing = ["1.24.0"]
    from_nginx = ["1.24.0", "1.26.3"]  # 1.26.3 missing from matrix

    _setup_cli_env(
        tmp_path,
        existing,
        monkeypatch,
        html_versions=from_nginx,
    )

    exit_code = main(["--check-only"])
    assert exit_code == 2


def test_cli_check_only_error_exit_1(tmp_path, monkeypatch):
    """--check-only returns 1 on scraping/parsing error."""
    versions = ["1.24.0"]
    _setup_cli_env(
        tmp_path,
        versions,
        monkeypatch,
    )

    # Make fetch raise a URLError to simulate network failure
    def raise_url_error(_url=None):
        """Raise a URLError indicating the network is down.

        Raises:
            URLError: Always raised with the message "network down".
        """
        raise URLError("network down")

    monkeypatch.setattr(um, "fetch_download_page", raise_url_error)

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
    )

    exit_code = main([])
    assert exit_code == 0
    assert diff_path.exists()

    diff_data = json.loads(diff_path.read_text())
    assert "added_versions" in diff_data
    assert "removed_versions" in diff_data
    assert "1.26.3" in diff_data["added_versions"]
    assert not diff_data["removed_versions"]


def test_cli_diff_json_removed_versions(tmp_path, monkeypatch):
    """matrix-diff.json correctly reports removed versions."""
    existing = ["1.24.0", "1.26.3"]
    from_nginx = ["1.26.3"]  # 1.24.0 dropped from nginx.org

    _, _, diff_path = _setup_cli_env(
        tmp_path,
        existing,
        monkeypatch,
        html_versions=from_nginx,
    )

    # The default min is 1.24.0, so 1.26.3 passes. 1.24.0 disappears from
    # the latest nginx.org download page, so it gets removed from the desired matrix.
    exit_code = main([])
    assert exit_code == 0
    assert diff_path.exists()

    diff_data = json.loads(diff_path.read_text())
    assert "1.24.0" in diff_data["removed_versions"]
    assert not diff_data["added_versions"]


# ---------------------------------------------------------------------------
# 4. Test crash-safe write rollback on simulated failure
# ---------------------------------------------------------------------------


def test_cli_rollback_on_doc_write_failure(tmp_path, monkeypatch):
    """Ensure the matrix file is restored when updating the documentation fails.

    Simulates a failure during the documentation file rename and asserts that the CLI
    exits with code 1, the original matrix file content is restored from backup, and no
    temporary documentation files remain.
    """
    existing = ["1.24.0"]
    from_nginx = ["1.24.0", "1.26.3"]

    matrix_path, _, _ = _setup_cli_env(
        tmp_path,
        existing,
        monkeypatch,
        html_versions=from_nginx,
    )

    matrix_before = matrix_path.read_text()

    # Let the matrix write succeed, but make the doc temp-file write fail.
    # We intercept os.replace: allow the first call (matrix rename) but
    # fail the second call (doc rename).
    original_replace = os.replace
    call_count = [0]

    def selective_replace(src, dst):
        """Replace the destination path with the source path, simulating a failure on
        the second call.

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
