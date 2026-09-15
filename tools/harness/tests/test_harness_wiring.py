"""Wiring tests for the pre-commit hooks and the detectors they call.

The detectors are tested on their own elsewhere.  These tests cover the wiring
around them, which is where a hook can look active while a change slips past:
the trigger pattern, the argument the detector receives, and the exit code it
uses when it cannot scan at all.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / ".pre-commit-config.yaml"

CLEAN_SOURCE = """#include <ngx_config.h>
void clean(void) {
    char *buffer = malloc(16);
    free(buffer);
}
"""

# A pool-allocated pointer freed with ngx_free: the shape Rule 43 forbids.
INFRINGING_SOURCE = """#include <ngx_config.h>
void infringing(ngx_pool_t *pool) {
    char *buffer = ngx_palloc(pool, 16);
    ngx_free(buffer);
}
"""


def _hooks() -> dict[str, dict]:
    """Return every hook in the repository's pre-commit configuration."""
    import yaml

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    found: dict[str, dict] = {}
    for repo in config.get("repos", []):
        for hook in repo.get("hooks", []):
            found[hook["id"]] = hook
    return found


def test_the_pool_free_hook_scans_the_tree_not_one_filename() -> None:
    """Without `pass_filenames: false`, the detector reads only its first arg."""
    hook = _hooks()["detect-pool-free"]

    assert hook.get("pass_filenames") is False


@pytest.mark.parametrize(
    "name",
    ["first_clean.c", "second_clean.h", "third_clean_impl.h"],
)
def test_the_pool_free_detector_reports_a_violation_in_any_extension(
    tmp_path: Path, name: str
) -> None:
    """A clean file must not hide the violation that follows it."""
    (tmp_path / "a_clean.c").write_text(CLEAN_SOURCE, encoding="utf-8")
    (tmp_path / name).write_text(INFRINGING_SOURCE, encoding="utf-8")

    result = subprocess.run(
        ["bash", "tools/harness/detect_pool_free.sh", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1, result.stderr
    assert name in result.stderr


@pytest.mark.parametrize(
    "hook_id",
    ["detect-pool-free", "detect-backpressure-resume"],
)
def test_c_hooks_also_fire_for_header_implementations(hook_id: str) -> None:
    """Much of the module is implemented in `*_impl.h`, so headers must trigger."""
    pattern = _hooks()[hook_id]["files"]

    assert pattern.endswith(r"\.(c|h)$"), pattern


@pytest.mark.parametrize(
    "command,detector",
    [
        (["bash", "tools/harness/detect_pool_free.sh"], "pool/free"),
        (
            [sys.executable, "tools/harness/detect_orphan_comment_close.py"],
            "orphan comment",
        ),
    ],
)
def test_a_missing_directory_is_not_reported_as_a_clean_scan(
    command: list[str], detector: str, tmp_path: Path
) -> None:
    """Cannot-scan is exit 2, which is distinct from a clean run (0)."""
    missing = tmp_path / "missing-harness-wiring-check"
    result = subprocess.run(
        command + [str(missing)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2, f"{detector}: {result.stdout}{result.stderr}"


def test_the_orphan_detector_reports_how_much_it_scanned() -> None:
    """A count makes an empty scan visible, rather than looking like a pass."""
    result = subprocess.run(
        [sys.executable, "tools/harness/detect_orphan_comment_close.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Scanned" in result.stderr


def test_the_harness_sync_self_tests_run_in_the_aggregate_entry() -> None:
    """The self-tests pass on their own, so nothing should filter them out."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert 'not check_harness_sync' not in makefile


def test_pool_free_enumeration_failure_is_not_a_clean_scan(tmp_path: Path) -> None:
    """A failed `find` must not read as a tree without violations."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "find").write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    (fake_bin / "find").chmod(0o755)

    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
    result = subprocess.run(
        ["bash", "tools/harness/detect_pool_free.sh", str(tmp_path)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False, env=env,
    )

    assert result.returncode == 2, result.stderr
    assert "PASS" not in result.stderr


def test_pool_free_parse_failure_is_not_a_clean_scan(tmp_path: Path) -> None:
    """A failing parser must report the file instead of skipping it."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "awk").write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    (fake_bin / "awk").chmod(0o755)
    src = tmp_path / "src"
    src.mkdir()
    (src / "sample.c").write_text("void f(void) {}\n", encoding="utf-8")

    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
    result = subprocess.run(
        ["bash", "tools/harness/detect_pool_free.sh", str(src)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False, env=env,
    )

    assert result.returncode == 2, result.stderr


def test_orphan_detector_refuses_a_file_argument(tmp_path: Path) -> None:
    """A file argument scans nothing, which must not look like a clean run."""
    a_file = tmp_path / "sample.c"
    a_file.write_text("int x;\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "tools/harness/detect_orphan_comment_close.py", str(a_file)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 2, result.stdout + result.stderr


def test_a_scoped_hook_does_not_always_run() -> None:
    """A hook narrowed by a pattern must not run on every commit anyway."""
    import yaml

    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            if hook.get("files") and hook.get("always_run"):
                raise AssertionError(
                    f"{hook['id']} sets files and always_run: the pattern is dead"
                )


def test_the_harness_job_provisions_the_pinned_rust_toolchain() -> None:
    """A harness test runs `rustc -Vv`, so the job must set the toolchain up."""
    import yaml

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["harness-tooling"]
    names = [step.get("name") for step in job["steps"]]
    assert "Set up Rust toolchain" in names, names
    setup = next(s for s in job["steps"] if s.get("name") == "Set up Rust toolchain")
    assert setup["with"]["toolchain"] == "1.98.1"
