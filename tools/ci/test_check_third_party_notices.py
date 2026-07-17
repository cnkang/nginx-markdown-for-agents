#!/usr/bin/env python3
"""Regression tests for THIRD-PARTY-NOTICES dependency version checks."""

from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from tools.ci import check_third_party_notices as checker


class ThirdPartyNoticesTests(unittest.TestCase):
    """Exercise resolved-version and workspace-lock validation."""

    def setUp(self) -> None:
        """Create an isolated repository-shaped fixture."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.cargo_toml = self.root / "Cargo.toml"
        self.cargo_lock = self.root / "Cargo.lock"
        self.notices = self.root / "THIRD-PARTY-NOTICES"
        self._write_runtime_fixture(notice_regex_version="1.13.0")

    def tearDown(self) -> None:
        """Remove the isolated fixture."""
        self.temp_dir.cleanup()

    def _write_runtime_fixture(self, *, notice_regex_version: str) -> None:
        """Write a minimal converter manifest, lock file, and notices file."""
        self.cargo_toml.write_text(
            textwrap.dedent(
                """\
                [package]
                name = "converter-fixture"
                version = "0.1.0"

                [dependencies]
                markup5ever_rcdom = "0.39"
                regex = "1.10"
                """
            ),
            encoding="utf-8",
        )
        self.cargo_lock.write_text(
            textwrap.dedent(
                """\
                version = 4

                [[package]]
                name = "converter-fixture"
                version = "0.1.0"
                dependencies = [
                 "markup5ever_rcdom",
                 "regex",
                ]

                [[package]]
                name = "markup5ever"
                version = "0.39.0"

                [[package]]
                name = "markup5ever_rcdom"
                version = "0.39.0+unofficial"
                dependencies = [
                 "markup5ever",
                ]

                [[package]]
                name = "regex"
                version = "1.13.0"
                """
            ),
            encoding="utf-8",
        )
        self.notices.write_text(
            textwrap.dedent(
                f"""\
                1. NGINX 1.29.4
                2. zlib 1.3.1
                3. Brotli 1.2.0
                4. markup5ever 0.39.0
                5. markup5ever_rcdom 0.39.0+unofficial
                6. regex {notice_regex_version}
                """
            ),
            encoding="utf-8",
        )

    def _run_checker(
        self,
        sub_manifests: list[Path] | None = None,
        sub_locks: list[Path] | None = None,
    ) -> tuple[int, str]:
        """Run the checker against this test's fixture paths."""
        with (
            mock.patch.object(checker, "ROOT", self.root),
            mock.patch.object(checker, "CARGO_TOML", self.cargo_toml),
            mock.patch.object(checker, "CARGO_LOCK", self.cargo_lock, create=True),
            mock.patch.object(checker, "NOTICES_PATH", self.notices),
            mock.patch.object(
                checker,
                "SUB_WORKSPACE_CARGO_TOMLS",
                sub_manifests or [],
            ),
            mock.patch.object(
                checker,
                "SUB_WORKSPACE_CARGO_LOCKS",
                sub_locks or [],
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            return checker.main(), output.getvalue()

    def test_stale_resolved_version_fails(self) -> None:
        """A NOTICE entry must match the exact Cargo.lock version."""
        self._write_runtime_fixture(notice_regex_version="1.12.4")

        result, output = self._run_checker()

        self.assertEqual(result, 1)
        self.assertIn("regex", output)
        self.assertIn("1.13.0", output)

    def test_missing_e2e_lock_fails(self) -> None:
        """A checked-in e2e manifest without its lock file is a gate failure."""
        e2e_dir = self.root / "tools" / "e2e-harness"
        e2e_dir.mkdir(parents=True)
        (e2e_dir / "src").mkdir()
        (e2e_dir / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        e2e_manifest = e2e_dir / "Cargo.toml"
        e2e_manifest.write_text(
            '[package]\nname = "e2e-fixture"\nversion = "0.1.0"\n\n[workspace]\n',
            encoding="utf-8",
        )

        result, output = self._run_checker(
            sub_manifests=[e2e_manifest],
            sub_locks=[e2e_dir / "Cargo.lock"],
        )

        self.assertEqual(result, 1)
        self.assertIn("Cargo.lock missing", output)

    def test_stale_e2e_lock_fails(self) -> None:
        """An e2e lock that Cargo would update must fail the locked check."""
        e2e_dir = self.root / "tools" / "e2e-harness"
        e2e_dir.mkdir(parents=True)
        (e2e_dir / "src").mkdir()
        (e2e_dir / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        e2e_manifest = e2e_dir / "Cargo.toml"
        e2e_manifest.write_text(
            '[package]\nname = "e2e-fixture"\nversion = "0.1.0"\n\n[workspace]\n',
            encoding="utf-8",
        )
        subprocess.run(
            ["cargo", "generate-lockfile", "--manifest-path", str(e2e_manifest)],
            check=True,
            capture_output=True,
            text=True,
        )
        dependency_dir = self.root / "fixture-dep"
        dependency_dir.mkdir()
        (dependency_dir / "src").mkdir()
        (dependency_dir / "src" / "lib.rs").write_text("pub fn value() {}\n", encoding="utf-8")
        (dependency_dir / "Cargo.toml").write_text(
            '[package]\nname = "fixture-dep"\nversion = "0.1.0"\n\n[workspace]\n',
            encoding="utf-8",
        )
        e2e_manifest.write_text(
            textwrap.dedent(
                """\
                [package]
                name = "e2e-fixture"
                version = "0.1.0"

                [dependencies]
                fixture-dep = { path = "../../../fixture-dep" }

                [workspace]
                """
            ),
            encoding="utf-8",
        )

        result, output = self._run_checker(
            sub_manifests=[e2e_manifest],
            sub_locks=[e2e_dir / "Cargo.lock"],
        )

        self.assertEqual(result, 1)
        self.assertIn("Cargo.lock is stale", output)

    def test_exact_versions_and_fresh_locks_pass(self) -> None:
        """Exact NOTICE versions with no sub-workspace errors pass."""
        result, output = self._run_checker()

        self.assertEqual(result, 0)
        self.assertIn("coverage check passed", output)


if __name__ == "__main__":
    unittest.main()
