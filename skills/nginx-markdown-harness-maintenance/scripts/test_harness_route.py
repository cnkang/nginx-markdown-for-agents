#!/usr/bin/env python3
"""Regression tests for harness_route helper logic."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

import harness_route
from harness_route import (
    _find_repo_root,
    _git_diff_files,
    _git_status_files,
    _load_manifest,
    _match_path,
    _normalize_files,
    _pack_matches,
    _parse_status_output,
    _verification_plan,
)


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


def test_load_manifest_validates_risk_pack_shape() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = Path(tmpdir) / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "risk_packs": [{}],
                    "verification_families": {},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit, match=r"risk_packs\[0\] missing keys"):
            _load_manifest(manifest_path)


def test_load_manifest_validates_verification_family_key_shape() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = Path(tmpdir) / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "risk_packs": [],
                    "verification_families": {"": {"phase": "cheap-blocker"}},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(
            SystemExit,
            match="verification_families keys must be non-empty strings",
        ):
            _load_manifest(manifest_path)


@pytest.mark.parametrize(
    ("pack", "error_match"),
    [
        (
            {
                "id": "",
                "doc": "docs/harness/risk-packs/example.md",
                "paths": ["docs/**"],
                "keywords": ["docs"],
                "verification_families": ["harness-sync"],
            },
            r"risk_packs\[0\]\.id must be a non-empty string",
        ),
        (
            {
                "id": "docs-tooling-drift",
                "doc": 42,
                "paths": ["docs/**"],
                "keywords": ["docs"],
                "verification_families": ["harness-sync"],
            },
            r"risk_packs\[0\]\.doc must be a non-empty string",
        ),
        (
            {
                "id": "docs-tooling-drift",
                "doc": "docs/harness/risk-packs/example.md",
                "paths": "docs/**",
                "keywords": ["docs"],
                "verification_families": ["harness-sync"],
            },
            r"risk_packs\[0\]\.paths must be a list",
        ),
    ],
)
def test_load_manifest_validates_risk_pack_field_types(
    pack: dict,
    error_match: str,
) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = Path(tmpdir) / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "risk_packs": [pack],
                    "verification_families": {},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit, match=error_match):
            _load_manifest(manifest_path)


@pytest.mark.parametrize(
    ("paths", "keywords", "families", "error_match"),
    [
        (
            ["docs/**", ""],
            ["docs"],
            ["harness-sync"],
            r"risk_packs\[0\]\.paths\[1\] must be a non-empty string",
        ),
        (
            ["docs/**"],
            ["docs", ""],
            ["harness-sync"],
            r"risk_packs\[0\]\.keywords\[1\] must be a non-empty string",
        ),
        (
            ["docs/**"],
            ["docs"],
            [""],
            r"risk_packs\[0\]\.verification_families\[0\] must be a non-empty string",
        ),
        (
            ["docs/**"],
            ["docs"],
            ["unknown-family"],
            r"risk_packs\[0\]\.verification_families contains unknown family 'unknown-family'",
        ),
    ],
)
def test_load_manifest_validates_risk_pack_entry_values(
    paths: list[str],
    keywords: list[str],
    families: list[str],
    error_match: str,
) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = Path(tmpdir) / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "risk_packs": [
                        {
                            "id": "docs-tooling-drift",
                            "doc": "docs/harness/risk-packs/example.md",
                            "paths": paths,
                            "keywords": keywords,
                            "verification_families": families,
                        }
                    ],
                    "verification_families": {
                        "harness-sync": {"phase": "cheap-blocker", "commands": []}
                    },
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit, match=error_match):
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


def test_parse_status_output_skips_deleted_entries() -> None:
    output = (
        "D  removed/from-index.c\n"
        " D removed/from-worktree.c\n"
        "R  old/path.c -> new/path.c\n"
        "A  added/new-file.c\n"
    )
    assert _parse_status_output(output) == [
        "old/path.c",
        "new/path.c",
        "added/new-file.c",
    ]


def test_git_diff_files_uses_delete_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_check_output(cmd, cwd, stderr, text):
        captured["cmd"] = cmd
        return "docs/harness/README.md\n"

    monkeypatch.setattr(harness_route.subprocess, "check_output", fake_check_output)
    files = _git_diff_files(None)
    assert files == ["docs/harness/README.md"]
    assert "--diff-filter=d" in captured["cmd"]


def test_git_status_files_expands_directory_with_nested_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_check_output(cmd, cwd, stderr, text):
        if cmd[:2] == ["git", "status"]:
            return "?? some_dir\n"
        if cmd[:2] == ["git", "ls-files"]:
            assert cmd[-1] == "some_dir"
            return "some_dir/file1.c\nsome_dir/nested/file2.c\n"
        raise AssertionError(f"unexpected command: {cmd}")

    def fake_is_dir(path: Path) -> bool:
        return path.as_posix().endswith("/some_dir")

    monkeypatch.setattr(harness_route.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(Path, "is_dir", fake_is_dir)

    assert _git_status_files() == [
        "some_dir/file1.c",
        "some_dir/nested/file2.c",
    ]


def test_git_status_files_directory_without_nested_files_falls_back_to_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_check_output(cmd, cwd, stderr, text):
        if cmd[:2] == ["git", "status"]:
            return "?? some_dir\n"
        if cmd[:2] == ["git", "ls-files"]:
            return ""
        raise AssertionError(f"unexpected command: {cmd}")

    def fake_is_dir(path: Path) -> bool:
        return path.as_posix().endswith("/some_dir")

    monkeypatch.setattr(harness_route.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(Path, "is_dir", fake_is_dir)

    assert _git_status_files() == ["some_dir"]


def test_git_status_files_directory_ls_files_error_falls_back_to_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_check_output(cmd, cwd, stderr, text):
        if cmd[:2] == ["git", "status"]:
            return "?? some_dir\n"
        if cmd[:2] == ["git", "ls-files"]:
            raise harness_route.subprocess.CalledProcessError(1, cmd, output="boom")
        raise AssertionError(f"unexpected command: {cmd}")

    def fake_is_dir(path: Path) -> bool:
        return path.as_posix().endswith("/some_dir")

    monkeypatch.setattr(harness_route.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(Path, "is_dir", fake_is_dir)

    assert _git_status_files() == ["some_dir"]


def test_normalize_files_splits_commas_and_normalizes_backslashes() -> None:
    raw = ["a/b.py, c\\d.py", "  e/f.py  "]
    assert _normalize_files(raw) == ["a/b.py", "c/d.py", "e/f.py"]


def test_pack_matches_scores_path_hits_higher_than_keywords() -> None:
    pack = {
        "id": "docs-tooling-drift",
        "doc": "docs/harness/risk-packs/docs-tooling-drift.md",
        "paths": ["docs/**"],
        "keywords": ["docs", "drift"],
        "verification_families": ["harness-sync"],
    }
    files = ["docs/harness/README.md", "components/x.c"]
    match = _pack_matches(pack, files, "drift in docs")
    assert match is not None
    assert match["id"] == "docs-tooling-drift"
    assert match["path_hits"] == ["docs/harness/README.md"]
    assert "docs" in match["keyword_hits"]
    assert match["score"] == 4


def test_pack_matches_score_uses_unique_hits() -> None:
    pack = {
        "id": "docs-tooling-drift",
        "doc": "docs/harness/risk-packs/docs-tooling-drift.md",
        "paths": ["docs/**", "docs/**"],
        "keywords": ["docs", "docs"],
        "verification_families": ["harness-sync"],
    }
    match = _pack_matches(pack, ["docs/harness/README.md"], "docs docs")
    assert match is not None
    assert match["path_hits"] == ["docs/harness/README.md"]
    assert match["keyword_hits"] == ["docs"]
    assert match["score"] == 3


def test_pack_matches_returns_none_without_any_hit() -> None:
    pack = {
        "id": "runtime-streaming",
        "doc": "docs/harness/risk-packs/runtime-streaming.md",
        "paths": ["components/nginx-module/**"],
        "keywords": ["NGX_AGAIN"],
        "verification_families": ["nginx-streaming"],
    }
    assert _pack_matches(pack, ["docs/README.md"], "unrelated text") is None


def test_pack_matches_avoids_substring_keyword_false_positive() -> None:
    pack = {
        "id": "docs-tooling-drift",
        "doc": "docs/harness/risk-packs/docs-tooling-drift.md",
        "paths": [],
        "keywords": ["docs"],
        "verification_families": ["harness-sync"],
    }
    assert _pack_matches(pack, ["tools/mydocsify/check.py"], "unrelated") is None


def test_verification_plan_dedupes_and_sorts_by_phase() -> None:
    manifest = {
        "verification_families": {
            "release-quality": {
                "phase": "umbrella",
                "commands": ["make harness-check-full"],
            },
            "harness-sync": {
                "phase": "cheap-blocker",
                "commands": ["make harness-check"],
            },
            "docs-tooling": {
                "phase": "cheap-blocker",
                "commands": ["make docs-check"],
            },
            "ffi-boundary": {
                "phase": "focused-semantic",
                "commands": ["make test-rust"],
            },
        }
    }
    result = _verification_plan(
        manifest,
        [
            "release-quality",
            "docs-tooling",
            "unknown-family",
            "harness-sync",
            "ffi-boundary",
            "harness-sync",
        ],
    )
    plan = result["plan"]
    assert [item["family"] for item in plan] == [
        "docs-tooling",
        "harness-sync",
        "ffi-boundary",
        "release-quality",
    ]
    assert result["unknown_verification_families"] == ["unknown-family"]
