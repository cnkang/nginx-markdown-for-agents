#!/usr/bin/env python3
"""Tests for generate-release-manifest.py and validate-release-manifest.py.

Run with:
    python3 packaging/scripts/test_release_manifest.py

Uses only local fixtures — no network access required.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
GENERATE_SCRIPT = SCRIPTS_DIR / "generate-release-manifest.py"
VALIDATE_SCRIPT = SCRIPTS_DIR / "validate-release-manifest.py"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_deb(name: str, content: bytes = b"fake-deb-content") -> bytes:
    return content


def make_rpm(name: str, content: bytes = b"fake-rpm-content") -> bytes:
    return content


class TestGenerateManifest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.artifact_dir = Path(self.tmpdir) / "artifacts"
        self.artifact_dir.mkdir()
        self.output = Path(self.tmpdir) / "output" / "release-manifest.json"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_package(self, filename: str, content: bytes = b"fake-pkg") -> str:
        path = self.artifact_dir / filename
        path.write_bytes(content)
        return sha256_bytes(content)

    def test_single_deb(self):
        expected_sha = self._write_package(
            "nginx-module-markdown-for-agents_0.8.3_nginx-1.28.0_amd64.deb"
        )
        result = subprocess.run(
            [
                sys.executable, str(GENERATE_SCRIPT),
                "-d", str(self.artifact_dir),
                "-o", str(self.output),
                "--version", "0.8.3",
                "--tag", "v0.8.3",
                "--commit", "abc1234def5678",
                "--repo", "cnkang/nginx-markdown-for-agents",
            ],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        manifest = json.loads(self.output.read_text())
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["project"], "nginx-markdown-for-agents")
        self.assertEqual(manifest["version"], "0.8.3")
        self.assertEqual(manifest["git"]["tag"], "v0.8.3")
        self.assertEqual(manifest["git"]["commit"], "abc1234def5678")
        self.assertEqual(len(manifest["packages"]), 1)
        self.assertEqual(manifest["packages"][0]["sha256"], expected_sha)
        self.assertEqual(manifest["integrity"]["checksums"], "SHA256SUMS")
        self.assertEqual(manifest["integrity"]["signature"], "SHA256SUMS.asc")

    def test_deb_and_rpm(self):
        self._write_package(
            "nginx-module-markdown-for-agents_0.8.3_nginx-1.28.0_amd64.deb"
        )
        self._write_package(
            "nginx-module-markdown-for-agents_0.8.3_nginx-1.28.0_arm64.deb"
        )
        self._write_package(
            "nginx-module-markdown-for-agents-0.8.3-nginx1.28.0-1.x86_64.rpm"
        )
        self._write_package(
            "nginx-module-markdown-for-agents-0.8.3-nginx1.28.0-1.aarch64.rpm"
        )

        result = subprocess.run(
            [
                sys.executable, str(GENERATE_SCRIPT),
                "-d", str(self.artifact_dir),
                "-o", str(self.output),
                "--version", "0.8.3",
                "--tag", "v0.8.3",
                "--commit", "abc1234",
                "--repo", "cnkang/nginx-markdown-for-agents",
            ],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        manifest = json.loads(self.output.read_text())
        self.assertEqual(len(manifest["packages"]), 4)

        # Packages must be globally sorted by filename (validator requirement)
        filenames = [p["filename"] for p in manifest["packages"]]
        self.assertEqual(filenames, sorted(filenames),
                         "Packages not globally sorted by filename")

        # Check RPM arch normalization
        rpm_pkgs = [p for p in manifest["packages"] if p["format"] == "rpm"]
        for pkg in rpm_pkgs:
            self.assertIn(pkg["arch"], ("amd64", "arm64"))
            self.assertIn(pkg["rpm_arch"], ("x86_64", "aarch64"))

    def test_no_packages_fails(self):
        result = subprocess.run(
            [
                sys.executable, str(GENERATE_SCRIPT),
                "-d", str(self.artifact_dir),
                "-o", str(self.output),
                "--version", "0.8.3",
                "--repo", "cnkang/nginx-markdown-for-agents",
            ],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_invalid_filename_fails(self):
        (self.artifact_dir / "bad-file.deb").write_bytes(b"x")
        result = subprocess.run(
            [
                sys.executable, str(GENERATE_SCRIPT),
                "-d", str(self.artifact_dir),
                "-o", str(self.output),
                "--version", "0.8.3",
                "--repo", "cnkang/nginx-markdown-for-agents",
            ],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_no_source(self):
        self._write_package(
            "nginx-module-markdown-for-agents_0.8.3_nginx-1.28.0_amd64.deb"
        )
        result = subprocess.run(
            [
                sys.executable, str(GENERATE_SCRIPT),
                "-d", str(self.artifact_dir),
                "-o", str(self.output),
                "--version", "0.8.3",
                "--repo", "cnkang/nginx-markdown-for-agents",
                "--no-source",
            ],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(self.output.read_text())
        self.assertFalse(manifest["source"]["available"])

    def test_deterministic_output(self):
        self._write_package(
            "nginx-module-markdown-for-agents_0.8.3_nginx-1.28.0_amd64.deb"
        )
        args = [
            sys.executable, str(GENERATE_SCRIPT),
            "-d", str(self.artifact_dir),
            "-o", str(self.output),
            "--version", "0.8.3",
            "--commit", "deadbeef",
            "--repo", "cnkang/nginx-markdown-for-agents",
        ]
        subprocess.run(args, capture_output=True, text=True)
        out1 = self.output.read_text()
        subprocess.run(args, capture_output=True, text=True)
        out2 = self.output.read_text()
        self.assertEqual(out1, out2)

    def test_empty_string_args_normalized(self):
        """Empty --version and --tag should be normalized to None."""
        self._write_package(
            "nginx-module-markdown-for-agents_0.8.3_nginx-1.28.0_amd64.deb"
        )
        result = subprocess.run(
            [
                sys.executable, str(GENERATE_SCRIPT),
                "-d", str(self.artifact_dir),
                "-o", str(self.output),
                "--version", "",
                "--tag", "",
                "--no-source",
            ],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(self.output.read_text())
        self.assertEqual(manifest["version"], "0.8.3")
        self.assertIsNone(manifest["git"]["tag"])

    def test_nontag_dispatch_no_invented_tag(self):
        """Non-tag dispatch must not synthesize git.tag = v{version}."""
        self._write_package(
            "nginx-module-markdown-for-agents_0.8.3_nginx-1.28.0_amd64.deb"
        )
        result = subprocess.run(
            [
                sys.executable, str(GENERATE_SCRIPT),
                "-d", str(self.artifact_dir),
                "-o", str(self.output),
                "--version", "0.8.3",
                "--no-source",
                "--ref-type", "branch",
            ],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(self.output.read_text())
        self.assertIsNone(manifest["git"]["tag"],
                          "Non-tag dispatch invented git.tag")
        self.assertEqual(manifest["workflow"]["ref_type"], "branch")


class TestValidateManifest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.artifact_dir = Path(self.tmpdir) / "artifacts"
        self.artifact_dir.mkdir()
        self.manifest_path = Path(self.tmpdir) / "release-manifest.json"
        self.sha256sums_path = Path(self.tmpdir) / "SHA256SUMS"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_valid_manifest(self, packages: list[dict] | None = None) -> dict:
        if packages is None:
            fname = "nginx-module-markdown-for-agents_0.8.3_nginx-1.28.0_amd64.deb"
            path = self.artifact_dir / fname
            path.write_bytes(b"fake-content")
            packages = [
                {
                    "filename": fname,
                    "format": "deb",
                    "version": "0.8.3",
                    "nginx_version": "1.28.0",
                    "arch": "amd64",
                    "sha256": sha256_bytes(b"fake-content"),
                }
            ]
        manifest = {
            "schema_version": 1,
            "project": "nginx-markdown-for-agents",
            "version": "0.8.3",
            "git": {
                "repository": "cnkang/nginx-markdown-for-agents",
                "tag": "v0.8.3",
                "commit": "abc1234def567890",
            },
            "source": {
                "archive_url": "https://github.com/cnkang/nginx-markdown-for-agents/archive/refs/tags/v0.8.3.tar.gz",
                "sha256": "a" * 64,
                "available": True,
            },
            "packages": packages,
            "integrity": {
                "checksums": "SHA256SUMS",
                "signature": "SHA256SUMS.asc",
                "signature_type": "gpg-detached-ascii-armored",
                "signed_file": "SHA256SUMS",
            },
            "workflow": {
                "provider": "github-actions",
                "workflow": "release-packages.yml",
            },
        }
        self.manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest

    def _make_sha256sums(self, entries: list[str] | None = None):
        if entries is None:
            entries = []
            for f in sorted(self.artifact_dir.iterdir()):
                sha = sha256_bytes(f.read_bytes())
                entries.append(f"{sha}  {f.name}")
        self.sha256sums_path.write_text("\n".join(entries) + "\n")

    def _validate(self, **kwargs) -> list[str]:
        args = [
            sys.executable, str(VALIDATE_SCRIPT),
            "-m", str(self.manifest_path),
            "-d", str(self.artifact_dir),
        ]
        if self.sha256sums_path.exists():
            args.extend(["--sha256sums", str(self.sha256sums_path)])
        for k, v in kwargs.items():
            args.extend([f"--{k.replace('_', '-')}", str(v)])
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            # Extract errors from stderr
            errors = [
                line.strip().lstrip("✗ ")
                for line in result.stderr.strip().splitlines()
                if line.strip().startswith("✗")
            ]
            return errors
        return []

    def test_valid_manifest_passes(self):
        self._make_valid_manifest()
        # SHA256SUMS must include both packages and release-manifest.json
        entries = []
        for f in sorted(self.artifact_dir.iterdir()):
            entries.append(f"{sha256_bytes(f.read_bytes())}  {f.name}")
        # Write manifest to artifact dir so it gets included
        (self.artifact_dir / "release-manifest.json").write_text(
            self.manifest_path.read_text()
        )
        entries.append(
            f"{sha256_bytes((self.artifact_dir / 'release-manifest.json').read_bytes())}  release-manifest.json"
        )
        self.sha256sums_path.write_text("\n".join(entries) + "\n")
        errors = self._validate()
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")

    def test_missing_top_level_key(self):
        self._make_valid_manifest()
        manifest = json.loads(self.manifest_path.read_text())
        del manifest["packages"]
        self.manifest_path.write_text(json.dumps(manifest, indent=2))
        errors = self._validate()
        self.assertTrue(len(errors) > 0)

    def test_version_mismatch(self):
        self._make_valid_manifest()
        self._make_sha256sums()
        errors = self._validate(version="9.9.9")
        self.assertTrue(any("mismatch" in e.lower() for e in errors))

    def test_sha256_mismatch(self):
        fname = "nginx-module-markdown-for-agents_0.8.3_nginx-1.28.0_amd64.deb"
        path = self.artifact_dir / fname
        path.write_bytes(b"real-content")
        manifest = self._make_valid_manifest([
            {
                "filename": fname,
                "format": "deb",
                "version": "0.8.3",
                "nginx_version": "1.28.0",
                "arch": "amd64",
                "sha256": "0" * 64,  # wrong hash
            }
        ])
        errors = self._validate()
        self.assertTrue(any("sha256 mismatch" in e.lower() for e in errors))

    def test_placeholder_detection(self):
        self._make_valid_manifest()
        manifest = json.loads(self.manifest_path.read_text())
        manifest["version"] = "PLACEHOLDER_VERSION"
        self.manifest_path.write_text(json.dumps(manifest, indent=2))
        errors = self._validate()
        self.assertTrue(any("placeholder" in e.lower() for e in errors))

    def test_manifest_not_in_sha256sums(self):
        self._make_valid_manifest()
        self.sha256sums_path.write_text("abc123  some-other-file.deb\n")
        errors = self._validate()
        self.assertTrue(any("release-manifest.json" in e and "sha256sums" in e.lower() for e in errors))

    def test_nontag_no_source_validates(self):
        """Non-tag manifest with --no-source should pass validation."""
        fname = "nginx-module-markdown-for-agents_0.8.3_nginx-1.28.0_amd64.deb"
        path = self.artifact_dir / fname
        path.write_bytes(b"fake-content")
        manifest = {
            "schema_version": 1,
            "project": "nginx-markdown-for-agents",
            "version": "0.8.3",
            "git": {
                "repository": "cnkang/nginx-markdown-for-agents",
                "commit": "deadbeef12345678",
            },
            "source": {"available": False},
            "packages": [
                {
                    "filename": fname,
                    "format": "deb",
                    "version": "0.8.3",
                    "nginx_version": "1.28.0",
                    "arch": "amd64",
                    "sha256": sha256_bytes(b"fake-content"),
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
                "ref_type": "branch",
            },
        }
        self.manifest_path.write_text(json.dumps(manifest, indent=2))
        (self.artifact_dir / "release-manifest.json").write_text(
            self.manifest_path.read_text()
        )
        entries = []
        for f in sorted(self.artifact_dir.iterdir()):
            entries.append(f"{sha256_bytes(f.read_bytes())}  {f.name}")
        self.sha256sums_path.write_text("\n".join(entries) + "\n")
        errors = self._validate()
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")

    def test_tag_missing_source_sha_fails(self):
        """Tag release without source.sha256 should fail validation."""
        fname = "nginx-module-markdown-for-agents_0.8.3_nginx-1.28.0_amd64.deb"
        path = self.artifact_dir / fname
        path.write_bytes(b"fake-content")
        manifest = {
            "schema_version": 1,
            "project": "nginx-markdown-for-agents",
            "version": "0.8.3",
            "git": {
                "repository": "cnkang/nginx-markdown-for-agents",
                "tag": "v0.8.3",
                "commit": "deadbeef12345678",
            },
            "source": {
                "available": True,
                "archive_url": "https://github.com/cnkang/nginx-markdown-for-agents/archive/refs/tags/v0.8.3.tar.gz",
                # sha256 missing
            },
            "packages": [
                {
                    "filename": fname,
                    "format": "deb",
                    "version": "0.8.3",
                    "nginx_version": "1.28.0",
                    "arch": "amd64",
                    "sha256": sha256_bytes(b"fake-content"),
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
        self.manifest_path.write_text(json.dumps(manifest, indent=2))
        errors = self._validate()
        self.assertTrue(any("source.sha256" in e for e in errors),
                        f"Expected source.sha256 error, got: {errors}")

    def test_tag_valid_source_sha_passes(self):
        """Tag release with valid source.sha256 should pass validation."""
        fname = "nginx-module-markdown-for-agents_0.8.3_nginx-1.28.0_amd64.deb"
        path = self.artifact_dir / fname
        path.write_bytes(b"fake-content")
        manifest = {
            "schema_version": 1,
            "project": "nginx-markdown-for-agents",
            "version": "0.8.3",
            "git": {
                "repository": "cnkang/nginx-markdown-for-agents",
                "tag": "v0.8.3",
                "commit": "deadbeef12345678",
            },
            "source": {
                "available": True,
                "archive_url": "https://github.com/cnkang/nginx-markdown-for-agents/archive/refs/tags/v0.8.3.tar.gz",
                "sha256": "a" * 64,
            },
            "packages": [
                {
                    "filename": fname,
                    "format": "deb",
                    "version": "0.8.3",
                    "nginx_version": "1.28.0",
                    "arch": "amd64",
                    "sha256": sha256_bytes(b"fake-content"),
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
        self.manifest_path.write_text(json.dumps(manifest, indent=2))
        errors = self._validate()
        self.assertEqual(errors, [], f"Unexpected errors: {errors}")


    def test_filename_parent_directory_traversal(self):
        """Filename with ../ prefix must be rejected."""
        self._make_valid_manifest([
            {
                "filename": "../evil.deb",
                "format": "deb",
                "version": "0.8.3",
                "nginx_version": "1.28.0",
                "arch": "amd64",
                "sha256": "0" * 64,
            }
        ])
        errors = self._validate()
        self.assertTrue(
            any("filename escapes artifact directory" in e for e in errors),
            f"Expected path traversal error, got: {errors}",
        )

    def test_filename_nested_traversal(self):
        """Nested ../ must be rejected (subdir/../../evil.deb)."""
        self._make_valid_manifest([
            {
                "filename": "subdir/../../evil.deb",
                "format": "deb",
                "version": "0.8.3",
                "nginx_version": "1.28.0",
                "arch": "amd64",
                "sha256": "0" * 64,
            }
        ])
        errors = self._validate()
        self.assertTrue(
            any("filename escapes artifact directory" in e for e in errors),
            f"Expected path traversal error, got: {errors}",
        )

    def test_filename_absolute_path(self):
        """Absolute path filename must be rejected."""
        self._make_valid_manifest([
            {
                "filename": "/etc/evil.deb",
                "format": "deb",
                "version": "0.8.3",
                "nginx_version": "1.28.0",
                "arch": "amd64",
                "sha256": "0" * 64,
            }
        ])
        errors = self._validate()
        self.assertTrue(
            any("filename escapes artifact directory" in e for e in errors),
            f"Expected path traversal error, got: {errors}",
        )


if __name__ == "__main__":
    unittest.main()
