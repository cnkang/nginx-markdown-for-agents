#!/usr/bin/env python3
"""Regression tests for the dependency-surface sync tool."""

from __future__ import annotations

import contextlib
import io
import tempfile
import textwrap
import unittest
from pathlib import Path

from tools.ci.sync_dependency_surfaces import (
    SyncPlan,
    build_cargo_update_command,
    describe_changes,
    lock_name_variants,
    plan_notices_rewrites,
    run_sync,
)
from tools.ci.sync_dependency_surfaces import LockUpdate

# Minimal baseline lock: every package the fixture locks and notices touch.
BASELINE_LOCK = textwrap.dedent(
    """\
    [[package]]
    name = "brotli"
    version = "9.1.1"

    [[package]]
    name = "brotli-decompressor"
    version = "6.3.1"

    [[package]]
    name = "encoding_rs"
    version = "0.8.44"

    [[package]]
    name = "regex"
    version = "1.13.0"

    [[package]]
    name = "toml"
    version = "1.1.6+spec-1.1.0"
    """
)

# Fuzz-shaped sub lock: one stale shared package plus a workspace-only crate.
FUZZ_LOCK = textwrap.dedent(
    """\
    [[package]]
    name = "brotli-decompressor"
    version = "6.3.0"

    [[package]]
    name = "libfuzzer-sys"
    version = "0.15.1"

    [[package]]
    name = "regex"
    version = "1.13.0"
    """
)

# e2e-shaped sub lock: the stale shared version is a documented divergence.
E2E_LOCK = textwrap.dedent(
    """\
    [[package]]
    name = "brotli"
    version = "8.0.4"

    [[package]]
    name = "regex"
    version = "1.13.0"
    """
)

NOTICES = textwrap.dedent(
    """\
    THIRD-PARTY SOFTWARE NOTICES

    ================================================================================

    Section A: C / NGINX Module Dependencies

    ================================================================================

    3.  Brotli 1.2.1 (official bundle)
        https://github.com/google/brotli

    --------------------------------------------------------------------------------

    ================================================================================

    Section B: Rust Conversion Engine Dependencies

    ================================================================================

    4.  html5ever 0.40.1
    9.  regex 1.13.0
    10. encoding_rs 0.8.43
    12. brotli 9.1.1 (Rust crate)
    13. brotli-decompressor 6.3.0 (Rust crate)
    18. toml 1.1.5+spec-1.1.0 (development/test dependency only)

    ================================================================================

    Section C: Build-Time Tool Dependencies

    ================================================================================

    19. cbindgen 0.29.4
    """
)


class SyncDependencySurfacesTests(unittest.TestCase):
    """Exercise planning, check mode, and apply mode end to end."""

    def setUp(self) -> None:
        """Create an isolated repository-shaped fixture."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.baseline_lock = self.root / "Cargo.lock"
        self.notices_path = self.root / "THIRD-PARTY-NOTICES"
        self.fuzz_toml = self.root / "fuzz" / "Cargo.toml"
        self.fuzz_lock = self.root / "fuzz" / "Cargo.lock"
        self.e2e_toml = self.root / "e2e" / "Cargo.toml"
        self.e2e_lock = self.root / "e2e" / "Cargo.lock"

        self.baseline_lock.write_text(BASELINE_LOCK, encoding="utf-8")
        self.notices_path.write_text(NOTICES, encoding="utf-8")
        for toml_path, name in (
            (self.fuzz_toml, "fuzz-fixture"),
            (self.e2e_toml, "e2e-fixture"),
        ):
            toml_path.parent.mkdir(parents=True, exist_ok=True)
            toml_path.write_text(
                f'[package]\nname = "{name}"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )
        self.fuzz_lock.write_text(FUZZ_LOCK, encoding="utf-8")
        self.e2e_lock.write_text(E2E_LOCK, encoding="utf-8")
        self.sub_workspaces = [
            (self.fuzz_toml, self.fuzz_lock),
            (self.e2e_toml, self.e2e_lock),
        ]

    def tearDown(self) -> None:
        """Remove the isolated fixture."""
        self.temp_dir.cleanup()

    def _run_sync(self, *, check_only: bool, cargo: str | None = None) -> int:
        """Run the tool against the fixture and capture stdout."""
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = run_sync(
                check_only=check_only,
                baseline_lock=self.baseline_lock,
                sub_workspaces=self.sub_workspaces,
                allowed=self.allowed,
                notices_path=self.notices_path,
                cargo=cargo,
            )
        self.output = stdout.getvalue()
        return code

    def _write_cascade_stub(
        self, replacements: list[tuple[str, str]]
    ) -> tuple[Path, Path]:
        """Write a cargo stub that rewrites the fuzz lock on every call.

        Models cargo moving a shared transitive dependency as a side effect of
        updating another package: the plan records the pre-cascade versions,
        and every stub invocation applies the recorded rewrite.
        """
        command_log = self.root / "cargo-commands.log"
        stub = self.root / "stub-cargo"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            f"with pathlib.Path({str(command_log)!r}).open("
            "'a', encoding='utf-8') as log:\n"
            "    log.write(' '.join(sys.argv[1:]) + '\\n')\n"
            f"lock = pathlib.Path({str(self.fuzz_lock)!r})\n"
            "text = lock.read_text(encoding='utf-8')\n"
            f"for old, new in {replacements!r}:\n"
            "    text = text.replace(old, new)\n"
            "lock.write_text(text, encoding='utf-8')\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        return stub, command_log

    def _use_cascade_fixture(
        self, fuzz_lock: str, notices: str
    ) -> None:
        """Install a baseline/fuzz/e2e/notices set for cascade tests."""
        self.allowed = {str(self.e2e_lock): {"brotli": {"8.0.4"}}}
        self.baseline_lock.write_text(
            '[[package]]\nname = "brotli"\nversion = "9.1.1"\n'
            '\n[[package]]\nname = "regex"\nversion = "1.13.2"\n'
            '\n[[package]]\nname = "regex-automata"\nversion = "0.4.19"\n',
            encoding="utf-8",
        )
        self.fuzz_lock.write_text(fuzz_lock, encoding="utf-8")
        self.e2e_lock.write_text(
            '[[package]]\nname = "brotli"\nversion = "8.0.4"\n'
            '\n[[package]]\nname = "regex"\nversion = "1.13.2"\n',
            encoding="utf-8",
        )
        self.notices_path.write_text(notices, encoding="utf-8")

    def test_apply_skips_package_moved_by_a_transitive_cascade(self) -> None:
        """A package an earlier update already moved is not re-run as a failure."""
        self._use_cascade_fixture(
            '[[package]]\nname = "regex"\nversion = "1.13.1"\n'
            '\n[[package]]\nname = "regex-automata"\nversion = "0.4.16"\n',
            "Section B: crates\n\n9.  regex 1.13.2\n"
            "12. brotli 9.1.1 (Rust crate)\n",
        )
        stub, command_log = self._write_cascade_stub(
            [('"1.13.1"', '"1.13.2"'), ('"0.4.16"', '"0.4.19"')]
        )

        exit_code = self._run_sync(check_only=False, cargo=str(stub))

        self.assertEqual(exit_code, 0)
        commands = command_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(commands), 1)
        self.assertIn("regex@1.13.1", commands[0])
        self.assertNotIn("regex-automata@0.4.16", "\n".join(commands))
        self.assertNotIn("cargo update failed", self.output)

    def test_apply_retargets_a_cascade_to_another_stale_version(self) -> None:
        """A cascade that lands on a different stale version is re-targeted."""
        self._use_cascade_fixture(
            '[[package]]\nname = "regex"\nversion = "1.13.1"\n'
            '\n[[package]]\nname = "regex-automata"\nversion = "0.4.16"\n',
            "Section B: crates\n\n9.  regex 1.13.2\n"
            "12. brotli 9.1.1 (Rust crate)\n",
        )
        stub, command_log = self._write_cascade_stub(
            [('"1.13.1"', '"1.13.2"'), ('"0.4.16"', '"0.4.20"')]
        )

        exit_code = self._run_sync(check_only=False, cargo=str(stub))

        self.assertEqual(exit_code, 0)
        commands = command_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(commands), 2)
        self.assertIn("regex-automata@0.4.20", commands[1])
        self.assertIn("--precise 0.4.19", commands[1])
        self.assertNotIn("cargo update failed", self.output)

    def test_check_mode_reports_every_stale_surface(self) -> None:
        """Check mode lists lock and notices findings and exits 1."""
        self.allowed = {}
        exit_code = self._run_sync(check_only=True)

        self.assertEqual(exit_code, 1)
        self.assertIn(
            f"would update {self.fuzz_lock}: brotli-decompressor "
            "6.3.0 -> 6.3.1",
            self.output,
        )
        self.assertIn("would rewrite THIRD-PARTY-NOTICES line 22", self.output)
        self.assertIn("would rewrite THIRD-PARTY-NOTICES line 24", self.output)
        self.assertIn("would rewrite THIRD-PARTY-NOTICES line 25", self.output)

    def test_check_mode_writes_nothing(self) -> None:
        """Check mode leaves every fixture byte-identical."""
        self.allowed = {}
        before = (
            self.fuzz_lock.read_bytes(),
            self.e2e_lock.read_bytes(),
            self.notices_path.read_bytes(),
        )
        self._run_sync(check_only=True)

        after = (
            self.fuzz_lock.read_bytes(),
            self.e2e_lock.read_bytes(),
            self.notices_path.read_bytes(),
        )
        self.assertEqual(before, after)

    def test_documented_divergence_is_skipped(self) -> None:
        """An allowlisted stale version is reported, not planned."""
        self.allowed = {str(self.e2e_lock): {"brotli": {"8.0.4"}}}
        exit_code = self._run_sync(check_only=True)

        self.assertEqual(exit_code, 1)  # the fuzz lock is still stale
        self.assertIn("documented divergences left unchanged:", self.output)
        self.assertIn(f"{self.e2e_lock}: documented divergence brotli 8.0.4", self.output)
        self.assertNotIn("would update " + str(self.e2e_lock), self.output)

    def test_apply_runs_targeted_cargo_updates_and_rewrites_notices(self) -> None:
        """Apply mode calls cargo once per stale pair and rewrites notices."""
        self.allowed = {str(self.e2e_lock): {"brotli": {"8.0.4"}}}
        command_log = self.root / "cargo-commands.log"
        stub = self.root / "stub-cargo"
        stub.write_text(
            f"#!/bin/sh\nfor arg in \"$@\"; do echo \"$arg\" >> {command_log}; done\n"
            "exit 0\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)

        exit_code = self._run_sync(check_only=False, cargo=str(stub))

        self.assertEqual(exit_code, 0)
        commands = command_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            commands,
            [
                "update",
                "--manifest-path",
                str(self.fuzz_toml),
                "--package",
                "brotli-decompressor@6.3.0",
                "--precise",
                "6.3.1",
            ],
        )
        notices = self.notices_path.read_text(encoding="utf-8")
        self.assertIn("13. brotli-decompressor 6.3.1 (Rust crate)", notices)
        self.assertIn("10. encoding_rs 0.8.44", notices)
        self.assertIn(
            "18. toml 1.1.6+spec-1.1.0 (development/test dependency only)", notices
        )
        # Section A and Section C entries are never rewritten.
        self.assertIn("3.  Brotli 1.2.1 (official bundle)", notices)
        self.assertIn("19. cbindgen 0.29.4", notices)

    def test_apply_idempotent_second_run_reports_sync(self) -> None:
        """A second apply after syncing reports in sync and exits 0."""
        self.allowed = {str(self.e2e_lock): {"brotli": {"8.0.4"}}}
        self.fuzz_lock.write_text(
            FUZZ_LOCK.replace('"6.3.0"', '"6.3.1"'), encoding="utf-8"
        )
        self.notices_path.write_text(
            NOTICES.replace(
                "13. brotli-decompressor 6.3.0", "13. brotli-decompressor 6.3.1"
            )
            .replace("10. encoding_rs 0.8.43", "10. encoding_rs 0.8.44")
            .replace(
                "18. toml 1.1.5+spec-1.1.0", "18. toml 1.1.6+spec-1.1.0"
            ),
            encoding="utf-8",
        )

        exit_code = self._run_sync(check_only=False, cargo="/unused/cargo")

        self.assertEqual(exit_code, 0)
        self.assertIn("dependency surfaces are already in sync", self.output)

    def test_apply_cargo_failure_is_reported(self) -> None:
        """A failing cargo update surfaces the error and exits 1."""
        self.allowed = {str(self.e2e_lock): {"brotli": {"8.0.4"}}}
        stub = self.root / "stub-cargo"
        stub.write_text("#!/bin/sh\necho simulated failure >&2\nexit 1\n")
        stub.chmod(0o755)

        exit_code = self._run_sync(check_only=False, cargo=str(stub))

        self.assertEqual(exit_code, 1)
        self.assertIn("cargo update failed for brotli-decompressor", self.output)
        self.assertIn("simulated failure", self.output)

    def test_multi_version_baseline_selects_same_major_target(self) -> None:
        """A multi-version baseline targets the same-major candidate."""
        self.allowed = {}
        self.baseline_lock.write_text(
            '[[package]]\nname = "regex"\nversion = "1.13.0"\n'
            '\n[[package]]\nname = "syn"\nversion = "2.0.118"\n'
            '\n[[package]]\nname = "syn"\nversion = "3.0.9"\n',
            encoding="utf-8",
        )
        self.fuzz_lock.write_text(
            '[[package]]\nname = "regex"\nversion = "1.13.0"\n'
            '\n[[package]]\nname = "syn"\nversion = "2.0.106"\n',
            encoding="utf-8",
        )
        self.e2e_lock.write_text(
            '[[package]]\nname = "regex"\nversion = "1.13.0"\n',
            encoding="utf-8",
        )
        self.notices_path.write_text(
            "Section B: crates\n\n9.  regex 1.13.0\n",
            encoding="utf-8",
        )

        exit_code = self._run_sync(check_only=True)

        self.assertEqual(exit_code, 1)
        self.assertIn(
            f"would update {self.fuzz_lock}: syn 2.0.106 -> 2.0.118",
            self.output,
        )
        self.assertNotIn("manual attention", self.output)

    def test_cross_major_stale_version_needs_manual_attention(self) -> None:
        """A stale version with no same-major baseline blocks the sync."""
        self.allowed = {}
        self.baseline_lock.write_text(
            '[[package]]\nname = "brotli"\nversion = "9.1.1"\n',
            encoding="utf-8",
        )
        self.fuzz_lock.write_text(
            '[[package]]\nname = "brotli"\nversion = "7.0.0"\n',
            encoding="utf-8",
        )
        self.e2e_lock.write_text(
            '[[package]]\nname = "regex"\nversion = "1.13.0"\n',
            encoding="utf-8",
        )

        exit_code = self._run_sync(check_only=True)

        self.assertEqual(exit_code, 1)
        self.assertIn(
            f"{self.fuzz_lock}: brotli 7.0.0 is stale but the baseline has "
            "no compatible version (9.1.1); sync needs manual attention",
            self.output,
        )
        self.assertNotIn("would update", self.output)

    def test_missing_section_b_header_blocks_notices_sync(self) -> None:
        """Without a recognizable Section B, notices sync needs a human."""
        self.allowed = {}
        self.notices_path.write_text(
            "13. brotli-decompressor 6.3.0 (Rust crate)\n", encoding="utf-8"
        )

        exit_code = self._run_sync(check_only=True)

        self.assertEqual(exit_code, 1)
        self.assertIn(
            "no 'Section B:' header found; notices sync needs manual attention",
            self.output,
        )
        self.assertNotIn("would rewrite", self.output)

    def test_missing_notices_file_is_reported(self) -> None:
        """A missing notices file fails both modes without crashing."""
        self.allowed = {}
        self.notices_path.unlink()

        exit_code = self._run_sync(check_only=True)

        self.assertEqual(exit_code, 1)
        self.assertIn(f"cannot read {self.notices_path}", self.output)

    def test_missing_sub_lock_needs_manual_attention(self) -> None:
        """A missing sub-workspace lock is surfaced, not auto-generated."""
        self.allowed = {}
        self.fuzz_lock.unlink()

        exit_code = self._run_sync(check_only=True)

        self.assertEqual(exit_code, 1)
        self.assertIn(
            f"Cargo.lock missing for {self.fuzz_toml}; generate it by hand "
            "before syncing",
            self.output,
        )

    def test_describe_changes_orders_rewrites_by_line(self) -> None:
        """describe_changes renders lock updates then notices rewrites."""
        plan = SyncPlan(
            lock_updates=[
                LockUpdate(
                    cargo_toml=Path("fuzz/Cargo.toml"),
                    lock=Path("fuzz/Cargo.lock"),
                    name="brotli-decompressor",
                    stale_version="6.3.0",
                    baseline_version="6.3.1",
                )
            ],
            notice_rewrites={9: "10. encoding_rs 0.8.44\n"},
        )

        lines = describe_changes(plan)

        self.assertEqual(
            lines,
            [
                "would update fuzz/Cargo.lock: brotli-decompressor 6.3.0 -> 6.3.1",
                "would rewrite THIRD-PARTY-NOTICES line 10 with: 10. encoding_rs 0.8.44",
            ],
        )

    def test_lock_name_variants_cover_dash_spellings(self) -> None:
        """Entry and lock names match through dash/underscore spelling."""
        self.assertEqual(
            lock_name_variants("serde_json"), {"serde_json", "serde-json"}
        )
        self.assertEqual(lock_name_variants("hex"), {"hex"})

    def test_build_cargo_update_command_targets_stale_copy(self) -> None:
        """The command pins the stale copy via the name@version spec."""
        update = LockUpdate(
            cargo_toml=Path("fuzz/Cargo.toml"),
            lock=Path("fuzz/Cargo.lock"),
            name="brotli-decompressor",
            stale_version="6.3.0",
            baseline_version="6.3.1",
        )

        command = build_cargo_update_command("/usr/bin/cargo", update)

        self.assertEqual(
            command,
            [
                "/usr/bin/cargo",
                "update",
                "--manifest-path",
                "fuzz/Cargo.toml",
                "--package",
                "brotli-decompressor@6.3.0",
                "--precise",
                "6.3.1",
            ],
        )

    def test_plan_notices_rewrites_skips_non_baseline_crates(self) -> None:
        """Entries for crates absent from the baseline are never touched."""
        baseline = {"regex": {"1.13.0"}}

        rewrites, manual = plan_notices_rewrites(
            "Section B: crates\n\n4.  html5ever 0.40.1\n9.  regex 1.13.0\n",
            baseline,
        )

        self.assertEqual(rewrites, {})
        self.assertEqual(manual, [])


if __name__ == "__main__":
    unittest.main()
