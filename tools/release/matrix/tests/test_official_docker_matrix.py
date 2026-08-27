"""Regression tests for official Docker image reference normalization."""

from tools.release.matrix.official_docker_matrix import _resolve_docker_entry


def _entry(**overrides):
    entry = {
        "nginx_version": "1.31.4",
        "os": "alpine3.24",
        "libc": "musl",
        "arch": "amd64",
        "image_ref": "nginx:1.31.4-alpine3.24",
        "image_digest": "sha256:" + "0" * 64,
    }
    entry.update(overrides)
    return entry


def test_alpine_reference_includes_declared_release():
    row = _resolve_docker_entry(_entry())
    assert row["image_ref"] == "nginx:1.31.4-alpine3.24"


def test_debian_reference_remains_unqualified():
    row = _resolve_docker_entry(
        _entry(
            os="debian12",
            libc="glibc",
            image_ref="nginx:1.31.4",
        )
    )
    assert row["image_ref"] == "nginx:1.31.4"
