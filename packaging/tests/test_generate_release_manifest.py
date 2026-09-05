"""Regression tests for release-manifest artifact discovery filtering."""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "packaging" / "scripts" / "generate-release-manifest.py"
SPEC = importlib.util.spec_from_file_location("generate_release_manifest", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


def test_discovery_skips_non_matching_tarballs(tmp_path: Path) -> None:
    """Only module tarballs matching TARBALL_PATTERN enter the manifest.

    A stray source archive must not reach parse_package (which fail-closes
    with SystemExit on unparseable names).
    """
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    module_tarball = (
        "ngx_http_markdown_filter_module-1.28.3-glibc-x86_64.tar.gz"
    )
    (artifact_dir / module_tarball).write_bytes(b"module")
    (artifact_dir / "source-archive-0.9.2.tar.gz").write_bytes(b"source")

    manifest = GENERATOR.build_manifest(
        artifact_dir,
        version="0.9.2",
        tag=None,
        commit="a" * 40,
        run_id="1",
        run_number="1",
        ref="refs/heads/main",
        ref_type="branch",
        repo="example/repository",
        source_url=None,
        source_sha=None,
        no_source=True,
    )

    assert [p["filename"] for p in manifest["packages"]] == [module_tarball]
