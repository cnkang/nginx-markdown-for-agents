"""Unit tests for tools/release/completeness_check.py."""

import json
import tempfile
from pathlib import Path

import pytest

from tools.release.completeness_check import (
    check_completeness,
    collect_artifacts,
    expected_artifact_name,
    format_missing,
    load_matrix,
    main,
)


# -- helpers ------------------------------------------------------------------

SAMPLE_ENTRY = {"nginx": "1.24.0", "os_type": "glibc", "arch": "x86_64", "support_tier": "full"}
SAMPLE_FILENAME = "ngx_http_markdown_filter_module-1.24.0-glibc-x86_64.tar.gz"


def _write_matrix(tmp: Path, entries: list[dict], *, schema_version: str = "1.0.0") -> Path:
    """
    Create a minimal release-matrix.json in the given directory and return its path.
    
    Parameters:
    	tmp (Path): Directory where release-matrix.json will be written.
    	entries (list[dict]): List of matrix entry objects to include under the "matrix" key.
    	schema_version (str): Schema version to write into the file (defaults to "1.0.0").
    
    Returns:
    	Path: Path to the written release-matrix.json file.
    """
    p = tmp / "release-matrix.json"
    p.write_text(json.dumps({
        "schema_version": schema_version,
        "support_tiers": {"full": "desc", "source_only": "desc"},
        "matrix": entries,
    }))
    return p


# -- expected_artifact_name ---------------------------------------------------

def test_expected_artifact_name():
    assert expected_artifact_name(SAMPLE_ENTRY) == SAMPLE_FILENAME


def test_expected_artifact_name_musl_aarch64():
    entry = {"nginx": "1.28.0", "os_type": "musl", "arch": "aarch64"}
    assert expected_artifact_name(entry) == (
        "ngx_http_markdown_filter_module-1.28.0-musl-aarch64.tar.gz"
    )


def test_expected_artifact_name_missing_required_keys():
    with pytest.raises(KeyError, match="missing required keys: os_type"):
        expected_artifact_name({"nginx": "1.28.0", "arch": "aarch64"})


# -- load_matrix --------------------------------------------------------------

def test_load_matrix_filters_full_tier(tmp_path):
    entries = [
        {"nginx": "1.24.0", "os_type": "glibc", "arch": "x86_64", "support_tier": "full"},
        {"nginx": "1.24.0", "os_type": "glibc", "arch": "x86_64", "support_tier": "source_only"},
    ]
    p = _write_matrix(tmp_path, entries)
    result = load_matrix(str(p))
    assert len(result) == 1
    assert result[0]["support_tier"] == "full"


def test_load_matrix_empty(tmp_path):
    p = _write_matrix(tmp_path, [])
    assert load_matrix(str(p)) == []


# -- collect_artifacts (directory mode) ---------------------------------------

def test_collect_artifacts_directory(tmp_path):
    (tmp_path / SAMPLE_FILENAME).touch()
    (tmp_path / "unrelated.txt").touch()
    result = collect_artifacts(str(tmp_path))
    assert result == {SAMPLE_FILENAME}


def test_collect_artifacts_directory_empty(tmp_path):
    assert collect_artifacts(str(tmp_path)) == set()


# -- collect_artifacts (file list mode) ---------------------------------------

def test_collect_artifacts_file_list(tmp_path):
    listing = tmp_path / "artifacts.txt"
    listing.write_text(f"{SAMPLE_FILENAME}\nother-1.0.0.tar.gz\n\n")
    result = collect_artifacts(str(listing))
    assert result == {SAMPLE_FILENAME, "other-1.0.0.tar.gz"}


def test_collect_artifacts_file_list_blank_lines(tmp_path):
    listing = tmp_path / "artifacts.txt"
    listing.write_text("\n\n\n")
    assert collect_artifacts(str(listing)) == set()


# -- check_completeness ------------------------------------------------------

def test_check_completeness_all_present():
    entries = [SAMPLE_ENTRY]
    assert check_completeness(entries, {SAMPLE_FILENAME}) == []


def test_check_completeness_missing():
    entries = [SAMPLE_ENTRY]
    missing = check_completeness(entries, set())
    assert len(missing) == 1
    assert missing[0] == (SAMPLE_ENTRY, SAMPLE_FILENAME)


# -- format_missing -----------------------------------------------------------

def test_format_missing_output():
    missing = [(SAMPLE_ENTRY, SAMPLE_FILENAME)]
    output = format_missing(missing)
    assert "Missing 1 artifact(s):" in output
    assert SAMPLE_FILENAME in output
    assert "nginx=1.24.0" in output


# -- main (integration) -------------------------------------------------------

def test_main_exit_0_directory(tmp_path):
    entries = [SAMPLE_ENTRY]
    matrix_path = _write_matrix(tmp_path, entries)
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / SAMPLE_FILENAME).touch()

    rc = main(["--matrix", str(matrix_path), "--artifacts", str(artifact_dir)])
    assert rc == 0


def test_main_exit_1_missing(tmp_path, capsys):
    entries = [SAMPLE_ENTRY]
    matrix_path = _write_matrix(tmp_path, entries)
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    # No artifacts created

    rc = main(["--matrix", str(matrix_path), "--artifacts", str(artifact_dir)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Missing 1 artifact(s):" in captured.err


def test_main_exit_0_file_list(tmp_path):
    entries = [SAMPLE_ENTRY]
    matrix_path = _write_matrix(tmp_path, entries)
    listing = tmp_path / "list.txt"
    listing.write_text(SAMPLE_FILENAME + "\n")

    rc = main(["--matrix", str(matrix_path), "--artifacts", str(listing)])
    assert rc == 0
