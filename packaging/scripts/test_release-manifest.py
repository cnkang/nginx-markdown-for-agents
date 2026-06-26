#!/usr/bin/env python3
"""Tests for generate-release-manifest.py and validate-release-manifest.py.

Run:
    python3 packaging/scripts/test_release-manifest.py
    # or
    python3 -m pytest packaging/scripts/test_release-manifest.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
GENERATE = SCRIPT_DIR / "generate-release-manifest.py"
VALIDATE = SCRIPT_DIR / "validate-release-manifest.py"


def _write_deb(tmp: Path, name: str, content: str = "deb") -> Path:
    p = tmp / name
    p.write_text(content)
    return p


def _write_rpm(tmp: Path, name: str, content: str = "rpm") -> Path:
    p = tmp / name
    p.write_text(content)
    return p


def _run_generate(tmp: Path, extra_args: list[str] | None = None) -> dict:
    """Run generate-release-manifest.py and return the parsed manifest."""
    args = [
        sys.executable, str(GENERATE),
        "-d", str(tmp),
        "-o", str(tmp / "release-manifest.json"),
    ]
    if extra_args:
        args.extend(extra_args)
    subprocess.run(args, check=True, capture_output=True, text=True)
    return json.loads((tmp / "release-manifest.json").read_text())


def _run_validate(tmp: Path, sha256sums: bool = True) -> subprocess.CompletedProcess:
    """Run validate-release-manifest.py and return the result."""
    args = [
        sys.executable, str(VALIDATE),
        "-m", str(tmp / "release-manifest.json"),
        "-d", str(tmp),
    ]
    if sha256sums:
        args.extend(["--sha256sums", str(tmp / "SHA256SUMS")])
    return subprocess.run(args, capture_output=True, text=True)


def _generate_sha256sums(tmp: Path) -> None:
    """Generate SHA256SUMS in the temp directory."""
    subprocess.run(
        ["bash", str(SCRIPT_DIR / "generate-checksums.sh"), "-d", str(tmp)],
        check=True, capture_output=True, text=True,
    )


# ---------------------------------------------------------------------------
# Test 1: Empty string CLI args are normalized safely
# ---------------------------------------------------------------------------

def test_empty_string_args_normalized():
    """Empty --version and --tag should be normalized to None, falling back
    to package filename detection."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_deb(tmp, "nginx-module-markdown-for-agents_0.8.3_nginx-1.27.4_amd64.deb")
        manifest = _run_generate(tmp, [
            "--version", "",
            "--tag", "",
            "--no-source",
        ])
        # Version should be detected from filename, not empty string
        assert manifest["version"] == "0.8.3", f"Expected 0.8.3, got {manifest['version']}"
        # Tag should be None (not "v" or empty string)
        assert manifest["git"]["tag"] is None, f"Expected None, got {manifest['git']['tag']!r}"


# ---------------------------------------------------------------------------
# Test 2: Non-tag dispatch does not invent git.tag
# ---------------------------------------------------------------------------

def test_nontag_dispatch_no_invented_tag():
    """workflow_dispatch should not synthesize git.tag = v{version}."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_deb(tmp, "nginx-module-markdown-for-agents_0.8.3_nginx-1.27.4_amd64.deb")
        manifest = _run_generate(tmp, [
            "--version", "0.8.3",
            "--no-source",
            "--ref-type", "branch",
        ])
        assert manifest["git"]["tag"] is None, (
            f"Non-tag dispatch invented tag: {manifest['git']['tag']!r}"
        )
        assert manifest["workflow"]["ref_type"] == "branch"


# ---------------------------------------------------------------------------
# Test 3: Non-tag dispatch with --no-source validates successfully
# ---------------------------------------------------------------------------

def test_nontag_no_source_validates():
    """Non-tag manifest with --no-source should pass validation."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_deb(tmp, "nginx-module-markdown-for-agents_0.8.3_nginx-1.27.4_amd64.deb")
        _run_generate(tmp, [
            "--version", "0.8.3",
            "--no-source",
            "--ref-type", "branch",
        ])
        _generate_sha256sums(tmp)
        result = _run_validate(tmp)
        assert result.returncode == 0, (
            f"Validation failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Test 4: Tag manifest with missing source.sha256 fails validation
# ---------------------------------------------------------------------------

def test_tag_missing_source_sha_fails():
    """Tag release manifest without source.sha256 should fail validation."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_deb(tmp, "nginx-module-markdown-for-agents_0.8.3_nginx-1.27.4_amd64.deb")

        # Manually write a manifest with tag but no source.sha256
        manifest = {
            "schema_version": 1,
            "project": "nginx-markdown-for-agents",
            "version": "0.8.3",
            "git": {
                "repository": "cnkang/nginx-markdown-for-agents",
                "tag": "v0.8.3",
                "commit": "abc1234",
            },
            "source": {
                "available": True,
                "archive_url": "https://github.com/cnkang/nginx-markdown-for-agents/archive/refs/tags/v0.8.3.tar.gz",
                # sha256 missing
            },
            "packages": [
                {
                    "filename": "nginx-module-markdown-for-agents_0.8.3_nginx-1.27.4_amd64.deb",
                    "format": "deb",
                    "version": "0.8.3",
                    "nginx_version": "1.27.4",
                    "arch": "amd64",
                    "sha256": "a" * 64,
                }
            ],
            "integrity": {
                "checksums": "SHA256SUMS",
                "signature": "SHA256SUMS.asc",
                "signature_type": "gpg-detached-ascii-armored",
                "signed_file": "SHA256SUMS",
            },
            "workflow": {
                "provider": "github-actions",
                "workflow": "release-packages.yml",
                "ref_type": "tag",
            },
        }
        (tmp / "release-manifest.json").write_text(json.dumps(manifest, indent=2))

        result = _run_validate(tmp, sha256sums=False)
        assert result.returncode != 0, "Validation should fail for missing source.sha256"
        assert "source.sha256" in result.stderr, (
            f"Expected source.sha256 error, got: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Test 5: Tag manifest with valid source.sha256 passes validation
# ---------------------------------------------------------------------------

def test_tag_valid_source_sha_passes():
    """Tag release manifest with valid source.sha256 should pass validation."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_deb(tmp, "nginx-module-markdown-for-agents_0.8.3_nginx-1.27.4_amd64.deb")
        # Compute actual SHA of the deb file for the manifest
        import hashlib
        deb_path = tmp / "nginx-module-markdown-for-agents_0.8.3_nginx-1.27.4_amd64.deb"
        actual_deb_sha = hashlib.sha256(deb_path.read_bytes()).hexdigest()
        valid_sha = "a" * 64
        manifest = {
            "schema_version": 1,
            "project": "nginx-markdown-for-agents",
            "version": "0.8.3",
            "git": {
                "repository": "cnkang/nginx-markdown-for-agents",
                "tag": "v0.8.3",
                "commit": "deadbeef1234567890abcdef1234567890abcdef",
            },
            "source": {
                "available": True,
                "archive_url": "https://github.com/cnkang/nginx-markdown-for-agents/archive/refs/tags/v0.8.3.tar.gz",
                "sha256": valid_sha,
            },
            "packages": [
                {
                    "filename": "nginx-module-markdown-for-agents_0.8.3_nginx-1.27.4_amd64.deb",
                    "format": "deb",
                    "version": "0.8.3",
                    "nginx_version": "1.27.4",
                    "arch": "amd64",
                    "sha256": actual_deb_sha,
                }
            ],
            "integrity": {
                "checksums": "SHA256SUMS",
                "signature": "SHA256SUMS.asc",
                "signature_type": "gpg-detached-ascii-armored",
                "signed_file": "SHA256SUMS",
            },
            "workflow": {
                "provider": "github-actions",
                "workflow": "release-packages.yml",
                "ref_type": "tag",
            },
        }
        (tmp / "release-manifest.json").write_text(json.dumps(manifest, indent=2))

        result = _run_validate(tmp, sha256sums=False)
        assert result.returncode == 0, (
            f"Validation failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    tests = [
        test_empty_string_args_normalized,
        test_nontag_dispatch_no_invented_tag,
        test_nontag_no_source_validates,
        test_tag_missing_source_sha_fails,
        test_tag_valid_source_sha_passes,
    ]
    passed = 0
    failed = 0
    for test in tests:
        name = test.__name__
        try:
            test()
            print(f"  ✓ {name}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
