#!/usr/bin/env python3
"""Regression tests for THIRD-PARTY-NOTICES dependency version checks."""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import tempfile
import textwrap
import unittest
import unittest.mock as mock
from pathlib import Path

import tools.ci.check_third_party_notices as checker


class ThirdPartyNoticesTests(unittest.TestCase):
    """Exercise resolved-version and workspace-lock validation."""

    def setUp(self) -> None:
        """Create an isolated repository-shaped fixture."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.cargo_toml = self.root / "Cargo.toml"
        self.cargo_lock = self.root / "Cargo.lock"
        self.notices = self.root / "THIRD-PARTY-NOTICES"
        self._write_runtime_fixture(
            notice_regex_version="1.13.0",
            dev_dep_entry="7. proptest 1.11.0\n",
        )

    def tearDown(self) -> None:
        """Remove the isolated fixture."""
        self.temp_dir.cleanup()

    def _write_runtime_fixture(
        self, *, notice_regex_version: str, dev_dep_entry: str | None = None
    ) -> None:
        """Write a minimal converter manifest, lock file, and notices file.

        ``dev_dep_entry`` is the optional NOTICE entry for the fixture's
        direct dev dependency, so a test can leave it out or pin a wrong
        version.
        """
        self.cargo_toml.write_text(
            textwrap.dedent(
                """\
                [package]
                name = "converter-fixture"
                version = "0.1.0"

                [dependencies]
                markup5ever_rcdom = "0.39"
                regex = "1.10"

                [dev-dependencies]
                proptest = "1.11"
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
                name = "proptest"
                version = "1.11.0"

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
            )
            + (dev_dep_entry or ""),
            encoding="utf-8",
        )

    def _run_checker(
        self,
        sub_manifests: list[Path] | None = None,
        sub_locks: list[Path] | None = None,
        cross_lock_allow: dict[str, dict[str, set[str]]] | None = None,
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
            mock.patch.object(
                checker,
                "CROSS_LOCK_ALLOWED_DIVERGENCES",
                cross_lock_allow or {},
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            return checker.main(), output.getvalue()

    def _write_sub_lock(self, relative: str, body: str) -> Path:
        """Write one sub-workspace lock under the fixture root."""
        lock = self.root / relative
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(textwrap.dedent(body), encoding="utf-8")
        return lock

    def test_stale_resolved_version_fails(self) -> None:
        """A NOTICE entry must match the exact Cargo.lock version."""
        self._write_runtime_fixture(
            notice_regex_version="1.12.4",
            dev_dep_entry="7. proptest 1.11.0\n",
        )

        result, output = self._run_checker()

        self.assertEqual(result, 1)
        self.assertIn("regex", output)
        self.assertIn("1.13.0", output)

    def test_missing_dev_dependency_entry_fails(self) -> None:
        """A direct [dev-dependencies] crate needs its own NOTICE entry.

        The notices preamble states that development-only crates are listed,
        so a missing dev-dependency entry is drift, not an accepted omission.
        """
        self._write_runtime_fixture(notice_regex_version="1.13.0")

        result, output = self._run_checker()

        self.assertEqual(result, 1)
        self.assertIn("Rust dev dependency", output)
        self.assertIn("proptest", output)
        self.assertIn("1.11.0", output)

    def test_stale_dev_dependency_version_fails(self) -> None:
        """A dev-dependency entry must carry the resolved lock version."""
        self._write_runtime_fixture(
            notice_regex_version="1.13.0",
            dev_dep_entry="7. proptest 1.10.0\n",
        )

        result, output = self._run_checker()

        self.assertEqual(result, 1)
        self.assertIn("Rust dev dependency", output)
        self.assertIn("proptest", output)

    def test_dev_dependency_entry_matching_the_lock_passes(self) -> None:
        """A correct dev-dependency entry keeps the check green."""
        self._write_runtime_fixture(
            notice_regex_version="1.13.0",
            dev_dep_entry="7. proptest 1.11.0\n",
        )

        result, output = self._run_checker()

        self.assertEqual(result, 0)
        self.assertIn("coverage check passed", output)

    def test_dev_dependency_crate_also_in_dependencies_is_not_duplicated(
        self,
    ) -> None:
        """A crate in both sections is validated once, as a runtime dependency."""
        self.cargo_toml.write_text(
            textwrap.dedent(
                """\
                [package]
                name = "converter-fixture"
                version = "0.1.0"

                [dependencies]
                markup5ever_rcdom = "0.39"
                regex = "1.10"
                proptest = "1.11"

                [dev-dependencies]
                proptest = "1.11"
                """
            ),
            encoding="utf-8",
        )

        direct = checker.parse_rust_direct_deps(self.cargo_toml)
        dev = checker.parse_rust_dev_deps(self.cargo_toml)
        dev_only = [name for name in dev if name not in direct]

        self.assertIn(
            "proptest", direct, "fixture must declare proptest in [dependencies]"
        )
        self.assertIn("proptest", dev)
        self.assertNotIn(
            "proptest",
            dev_only,
            "a both-sections crate is validated once, as a runtime dependency",
        )
        self.assertNotIn(
            "regex", dev, "the dev section must not read the runtime block"
        )

    @unittest.skipUnless(
        checker.resolve_approved_executable("cargo"),
        "cargo is required to compare an e2e lock against its manifest",
    )
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

    @unittest.skipUnless(
        checker.resolve_approved_executable("cargo"),
        "cargo is required to compare an e2e lock against its manifest",
    )
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
        if shutil.which("cargo") is None:
            self.skipTest("cargo is not available in this environment")
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
                fixture-dep = { path = "../../fixture-dep" }

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

    # ------------------------------------------------------------------
    # Cross-workspace shared-package version consistency
    # ------------------------------------------------------------------

    # `regex` is the one shared package the fixture baseline lock resolves
    # (1.13.0), so a sub-lock on any other regex version is drift.
    _DRIFT_SUB_LOCK = """\
        version = 4

        [[package]]
        name = "fuzz-fixture"
        version = "0.0.0"

        [[package]]
        name = "regex"
        version = "1.12.4"

        [[package]]
        name = "libfuzzer-sys"
        version = "0.4.0"
        """

    def test_cross_lock_shared_version_drift_fails(self) -> None:
        """A sub-lock on a different shared-crate version than the baseline fails.

        This is the rc9 drift shape: the fuzz lock resolved its own older copy
        of a crate the production archive also links, so the fuzz targets
        exercised different code than the shipped artifact.
        """
        sub_lock = self._write_sub_lock("fuzz/Cargo.lock", self._DRIFT_SUB_LOCK)

        result, output = self._run_checker(sub_locks=[sub_lock])

        self.assertEqual(result, 1)
        self.assertIn("cross-workspace version drift", output)
        self.assertIn("regex", output)
        self.assertIn("1.12.4", output)  # the stale sub-side version
        self.assertIn("1.13.0", output)  # the baseline version it must match
        self.assertIn("fuzz/Cargo.lock", output)

    def test_cross_lock_matching_shared_version_passes(self) -> None:
        """A sub-lock agreeing with the baseline on shared crates passes."""
        sub_lock = self._write_sub_lock(
            "fuzz/Cargo.lock",
            """\
            version = 4

            [[package]]
            name = "regex"
            version = "1.13.0"

            [[package]]
            name = "libfuzzer-sys"
            version = "0.4.0"
            """,
        )

        result, output = self._run_checker(sub_locks=[sub_lock])

        self.assertEqual(result, 0, output)
        self.assertIn("coverage check passed", output)

    def test_cross_lock_workspace_unique_packages_are_ignored(self) -> None:
        """A crate the baseline never resolves is not a shared-package drift."""
        sub_lock = self._write_sub_lock(
            "fuzz/Cargo.lock",
            """\
            version = 4

            [[package]]
            name = "regex"
            version = "1.13.0"

            [[package]]
            name = "libfuzzer-sys"
            version = "0.4.0"

            [[package]]
            name = "arbitrary"
            version = "1.4.0"
            """,
        )

        result, output = self._run_checker(sub_locks=[sub_lock])

        self.assertEqual(result, 0, output)
        self.assertIn("coverage check passed", output)

    def test_cross_lock_allowlisted_exact_version_passes(self) -> None:
        """A recorded divergence naming the exact stale version still passes."""
        sub_lock = self._write_sub_lock("fuzz/Cargo.lock", self._DRIFT_SUB_LOCK)

        result, output = self._run_checker(
            sub_locks=[sub_lock],
            cross_lock_allow={"fuzz/Cargo.lock": {"regex": {"1.12.4"}}},
        )

        self.assertEqual(result, 0, output)
        self.assertIn("coverage check passed", output)

    def test_cross_lock_allowlist_does_not_absorb_a_different_version(self) -> None:
        """The allowance names one exact version, so another drift still fails.

        An allowlist keyed only by crate name would silently bless whatever
        that sub-lock later resolves; keying it by version means an unrelated
        regex version still trips the check.
        """
        sub_lock = self._write_sub_lock(
            "fuzz/Cargo.lock",
            """\
            version = 4

            [[package]]
            name = "regex"
            version = "1.12.9"
            """,
        )

        result, output = self._run_checker(
            sub_locks=[sub_lock],
            cross_lock_allow={"fuzz/Cargo.lock": {"regex": {"1.12.4"}}},
        )

        self.assertEqual(result, 1)
        self.assertIn("cross-workspace version drift", output)
        self.assertIn("1.12.9", output)

    def test_cross_lock_baseline_missing_is_reported(self) -> None:
        """An unreadable baseline lock fails closed instead of skipping."""
        sub_lock = self._write_sub_lock("fuzz/Cargo.lock", self._DRIFT_SUB_LOCK)
        missing_baseline = self.root / "nope" / "Cargo.lock"

        issues = checker.collect_cross_lock_version_issues(
            missing_baseline, [sub_lock], {}
        )

        self.assertTrue(issues)
        self.assertIn("cannot read baseline Cargo.lock", issues[0])

    def test_cross_lock_reports_every_stale_package(self) -> None:
        """All drifting shared packages in one lock are reported, not just the first."""
        sub_lock = self._write_sub_lock(
            "fuzz/Cargo.lock",
            """\
            version = 4

            [[package]]
            name = "regex"
            version = "1.12.0"

            [[package]]
            name = "proptest"
            version = "1.10.0"

            [[package]]
            name = "markup5ever"
            version = "0.39.0"
            """,
        )

        issues = checker.collect_cross_lock_version_issues(
            self.cargo_lock, [sub_lock], {}
        )

        joined = "\n".join(issues)
        # regex 1.12.0 and proptest 1.10.0 drift; markup5ever matches at 0.39.0.
        self.assertIn("regex", joined)
        self.assertIn("proptest", joined)
        self.assertNotIn("markup5ever", joined)
        self.assertNotIn("regex-syntax", joined)

    def test_cross_lock_divergence_checker_signal_is_off_by_default(self) -> None:
        """Without the allowlist, an existing drift is a hard failure.

        Guards the shipped configuration: the real CROSS_LOCK_ALLOWED_DIVERGENCES
        must not accidentally cover the fuzz lock, whose drift this change
        removed (the fuzz lock is the one lock the fix actually rebuilt).
        """
        self.assertNotIn(
            "components/rust-converter/fuzz/Cargo.lock",
            checker.CROSS_LOCK_ALLOWED_DIVERGENCES,
            "the fuzz lock was rebuilt against the baseline; it must not stay exempt",
        )


if __name__ == "__main__":
    unittest.main()
