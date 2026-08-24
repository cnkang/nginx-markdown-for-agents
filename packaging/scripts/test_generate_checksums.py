#!/usr/bin/env python3
"""Regression tests for generate-checksums.sh path and format contracts."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("generate-checksums.sh")


class TestGenerateChecksums(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.artifact_dir = Path(self.tempdir.name) / "artifacts"
        self.artifact_dir.mkdir()
        (self.artifact_dir / "package.deb").write_bytes(b"package")
        (self.artifact_dir / "release-manifest.json").write_bytes(b"manifest")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_script(self, output: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT), "-d", str(self.artifact_dir), "-o", output],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_valid_output_is_generated_inside_artifact_directory(self) -> None:
        if shutil.which("sha256sum") is None:
            self.skipTest("sha256sum is not installed")

        result = self.run_script("SHA256SUMS")

        self.assertEqual(result.returncode, 0, result.stderr)
        checksums = self.artifact_dir / "SHA256SUMS"
        self.assertTrue(checksums.is_file())
        self.assertEqual(len(checksums.read_text(encoding="utf-8").splitlines()), 2)

    def test_rejects_escaping_and_artifact_output_names(self) -> None:
        for output in ("../foo", "/tmp/foo", "release-manifest.json", "package.deb"):
            with self.subTest(output=output):
                result = self.run_script(output)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    (self.artifact_dir / "package.deb").read_bytes(), b"package"
                )
                self.assertEqual(
                    (self.artifact_dir / "release-manifest.json").read_bytes(),
                    b"manifest",
                )


if __name__ == "__main__":
    unittest.main()
