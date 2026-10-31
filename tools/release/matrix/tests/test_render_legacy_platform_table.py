"""Tests for the legacy platform-table renderer."""

from __future__ import annotations

import json

import pytest

from tools.release.matrix import render_legacy_platform_table as renderer


def _matrix(*entries):
    return {"schema_version": 1, "entries": list(entries)}


def _module(version: str, tier: str, **overrides):
    entry = {
        "nginx_version": version,
        "os": "linux",
        "libc": "glibc",
        "arch": "arm64",
        "artifact_type": "dynamic-module",
        "support_tier": tier,
    }
    entry.update(overrides)
    return entry


def _four_pairs(version: str, tier: str):
    return [
        _module(version, tier, libc=libc, arch=arch)
        for libc, arch in (
            ("glibc", "arm64"),
            ("glibc", "amd64"),
            ("musl", "arm64"),
            ("musl", "amd64"),
        )
    ]


def test_one_row_per_libc_and_architecture_pair():
    rows = renderer.legacy_entries(_matrix(*_four_pairs("1.30.6", "supported")))

    assert len(rows) == 4
    assert {(r["os_type"], r["arch"]) for r in rows} == {
        ("glibc", "aarch64"),
        ("glibc", "x86_64"),
        ("musl", "aarch64"),
        ("musl", "x86_64"),
    }
    assert {r["support_tier"] for r in rows} == {"supported"}


def test_rows_outside_the_linux_module_family_are_ignored():
    rows = renderer.legacy_entries(
        _matrix(
            _module("1.30.6", "supported"),
            _module("1.30.5", "supported", artifact_type="deb-package"),
            _module("1.31.8", "best-effort", os="debian12"),
        )
    )

    assert {r["nginx"] for r in rows} == {"1.30.6"}


def test_a_version_with_one_architecture_does_not_invent_the_others():
    """The table must describe the matrix, not a fixed cross-product."""
    rows = renderer.legacy_entries(
        _matrix(_module("1.30.6", "supported", arch="arm64"))
    )

    assert len(rows) == 1
    assert rows[0]["arch"] == "aarch64"


def test_a_duplicate_matrix_row_is_rejected():
    matrix = _matrix(
        _module("1.30.6", "supported"),
        _module("1.30.6", "supported"),
    )

    with pytest.raises(ValueError, match="duplicate"):
        renderer.legacy_entries(matrix)


def test_renderer_rewrites_the_marker_block(tmp_path, monkeypatch):
    doc = tmp_path / "INSTALLATION.md"
    doc.write_text(
        "intro\n"
        f"{renderer.DOC_MARKER_BEGIN}\n"
        "| old | table |\n"
        f"{renderer.DOC_MARKER_END}\n"
        "tail\n",
        encoding="utf-8",
    )
    matrix = tmp_path / "matrix.json"
    matrix.write_text(
        json.dumps(_matrix(*_four_pairs("1.30.6", "supported"))), encoding="utf-8"
    )
    monkeypatch.setattr(renderer, "DOC_PATH", doc)
    monkeypatch.setattr(renderer, "MATRIX_PATH", matrix)
    monkeypatch.setattr(renderer, "REPO_ROOT", tmp_path)

    assert renderer.main([]) == 0
    body = doc.read_text(encoding="utf-8")
    assert "| 1.30.6 | glibc | aarch64 | Supported |" in body
    assert "| old | table |" not in body
    assert body.startswith("intro\n")
    assert body.endswith("tail\n")

    assert renderer.main(["--check"]) == 0


def test_check_reports_a_stale_block(tmp_path, monkeypatch):
    doc = tmp_path / "INSTALLATION.md"
    doc.write_text(
        "intro\n"
        f"{renderer.DOC_MARKER_BEGIN}\n"
        "| 1.30.6 | glibc | aarch64 | Best-Effort |\n"
        f"{renderer.DOC_MARKER_END}\n",
        encoding="utf-8",
    )
    matrix = tmp_path / "matrix.json"
    matrix.write_text(
        json.dumps(_matrix(*_four_pairs("1.30.6", "supported"))), encoding="utf-8"
    )
    monkeypatch.setattr(renderer, "DOC_PATH", doc)
    monkeypatch.setattr(renderer, "MATRIX_PATH", matrix)
    monkeypatch.setattr(renderer, "REPO_ROOT", tmp_path)

    assert renderer.main(["--check"]) == 2
    # --check must not modify the file.
    assert "Best-Effort" in doc.read_text(encoding="utf-8")
