"""The cargo-fmt pre-commit hook must FORMAT, not only check -- and re-stage.

A check-only entry fights the documented workflow: CONTRIBUTING.md tells a
contributor to run `cargo fmt` before committing, pre-commit then stashes the
staged tree when the check fails and restores its own patch, which reverts
that formatting, and the same commit fails again for the same reason.

Formatting alone is not enough either: `cargo fmt` writes the WORKING TREE and
not the index, so a staged blob that was unformatted would still be committed
verbatim. The hook must re-stage what it reformatted and fail, so the
contributor reviews the change and retries.
"""

from __future__ import annotations

import pathlib
import re

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
HOOK_SCRIPT = REPO_ROOT / "tools" / "ci" / "pre_commit_cargo_fmt.sh"


def _hook() -> dict:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            if hook.get("id") == "cargo-fmt":
                return hook
    raise AssertionError("the cargo-fmt pre-commit hook is missing")


def test_the_hook_entry_formats_before_it_checks() -> None:
    """The entry must run a formatter, not `make rust-fmt-check` alone."""
    entry = _hook()["entry"]
    assert entry != "make rust-fmt-check", (
        "a check-only entry makes the documented `cargo fmt` then commit "
        "workflow fail, and pre-commit's stash silently reverts the fix"
    )
    assert "pre_commit_cargo_fmt.sh" in entry, entry


def test_the_hook_script_exists_and_formats_every_checked_crate() -> None:
    assert HOOK_SCRIPT.is_file(), f"missing {HOOK_SCRIPT}"
    body = HOOK_SCRIPT.read_text(encoding="utf-8")
    # The Make target checks these three crates; the hook must format all of
    # them or it converges on one and leaves the others unformatted.
    for crate in (
        "components/rust-converter",
        "tools/corpus/test-corpus-conversion",
        "tools/e2e-harness",
    ):
        assert crate in body, f"the hook does not format {crate}"
    assert re.search(r"cargo fmt\b", body), "the hook never runs cargo fmt"
    # Convergence is still verified, so a crate that cannot be formatted fails
    # the commit instead of shipping unformatted.
    assert "rust-fmt-check" in body, "the hook must still verify convergence"


def test_the_hook_restages_what_it_reformatted() -> None:
    """Reformatting is useless unless the INDEX is updated too.

    `cargo fmt` rewrites files in place, so a commit whose staged blob was
    unformatted keeps the unformatted bytes unless the hook re-stages them.
    """
    text = HOOK_SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"git add -- ", text), (
        "the hook never re-stages what cargo fmt reformatted, so the commit "
        "would still carry the unformatted blob it just formatted away from"
    )
    # It must also fail, so the contributor sees the reformat and retries rather
    # than committing silently.
    assert re.search(r"git commit.*again|run 'git commit' again", text), (
        "the hook re-stages but does not tell the contributor to retry"
    )


def test_the_hook_script_is_executable() -> None:
    assert HOOK_SCRIPT.stat().st_mode & 0o111, (
        "the hook is invoked through bash, but the exec bit is what the "
        "repository's script-exec guard checks"
    )
