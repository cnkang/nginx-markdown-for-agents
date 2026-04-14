#!/usr/bin/env python3
"""Regression tests for harness_route helper logic."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from harness_route import _find_repo_root, _load_manifest, _match_path, _parse_status_output


def test_find_repo_root_from_repo_file() -> None:
    repo_root = _find_repo_root(Path(__file__))
    assert (repo_root / "AGENTS.md").exists()


def test_find_repo_root_fails_without_agents() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(SystemExit, match="cannot locate repository root"):
            _find_repo_root(Path(tmpdir))


def test_load_manifest_validates_required_keys() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = Path(tmpdir) / "manifest.json"
        manifest_path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        with pytest.raises(SystemExit, match="missing required keys"):
            _load_manifest(manifest_path)


def test_load_manifest_validates_types() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = Path(tmpdir) / "manifest.json"
        manifest_path.write_text(
            json.dumps({"risk_packs": "not_a_list", "verification_families": {}}),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit, match="'risk_packs' must be a list"):
            _load_manifest(manifest_path)


def test_match_path_directory_glob_has_boundary() -> None:
    assert _match_path(
        "components/nginx-module/src/foo.c", "components/nginx-module/**"
    )
    assert not _match_path(
        "components/nginx-module-extra/src/foo.c", "components/nginx-module/**"
    )


def test_parse_status_output_keeps_old_and_new_on_rename() -> None:
    output = "R  old/path.c -> new/path.c\n M docs/harness/README.md\n"
    assert _parse_status_output(output) == [
        "old/path.c",
        "new/path.c",
        "docs/harness/README.md",
    ]

