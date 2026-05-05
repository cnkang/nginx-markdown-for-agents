"""Tests for tools/sonar/lcov_to_sonar_xml.py."""

from __future__ import annotations

from pathlib import Path

from tools.sonar.lcov_to_sonar_xml import _resolve_to_workspace_path


def test_resolve_workspace_native_path(tmp_path: Path) -> None:
    """Keep in-workspace paths unchanged."""
    workspace = tmp_path
    file_path = workspace / "components" / "nginx-module" / "src" / "a.c"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("int x;\n", encoding="utf-8")

    resolved = _resolve_to_workspace_path(str(file_path), str(workspace))
    assert resolved == str(file_path)


def test_resolve_tmp_build_mirrored_suffix(tmp_path: Path) -> None:
    """Map temporary build roots back into workspace mirrored paths."""
    workspace = tmp_path
    target = workspace / "components" / "nginx-module" / "src" / "b.c"
    target.parent.mkdir(parents=True)
    target.write_text("int y;\n", encoding="utf-8")

    fake_tmp = (
        "/tmp/nginx-coverage-build.ABCD/"
        "components/nginx-module/src/b.c"
    )
    resolved = _resolve_to_workspace_path(fake_tmp, str(workspace))
    assert resolved == str(target)


def test_resolve_outside_workspace_returns_none(tmp_path: Path) -> None:
    """Ignore external files that cannot be mapped into workspace."""
    resolved = _resolve_to_workspace_path("/tmp/unrelated/outside.c", str(tmp_path))
    assert resolved is None
