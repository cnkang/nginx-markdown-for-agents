"""rust-toolchain.toml must be the only place the stable channel is written.

`dtolnay/rust-toolchain` cannot read the file: its `toolchain` input is
required and it drives rustup from that input alone, so every workflow had to
repeat the channel by hand. This guards the composite action that replaced
those literals -- if one of them comes back, or the action stops passing the
resolved channel through, CI would build on a version nobody pinned.
"""

import pathlib
import re
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
ACTION = REPO_ROOT / ".github" / "actions" / "setup-rust" / "action.yml"
RESOLVER = REPO_ROOT / "tools" / "ci" / "rust_toolchain_channel.sh"
MANIFEST = REPO_ROOT / "rust-toolchain.toml"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

SHA = "29eef336d9b2848a0b548edc03f92a220660cdb8"


def _workflow_files() -> list[pathlib.Path]:
    return sorted(WORKFLOWS.glob("*.yml"))


def _steps(doc: dict):
    for job_name, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            yield job_name, step


def test_resolver_reports_the_pinned_channel():
    out = subprocess.run(
        ["bash", str(RESOLVER)], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out, "the resolver printed nothing"
    assert re.fullmatch(r"\d+\.\d+\.\d+", out), f"not a stable version: {out!r}"


def test_resolver_fails_when_the_manifest_is_unreadable(tmp_path):
    """A resolver that silently printed nothing would pin nothing."""
    script = tmp_path / "rust_toolchain_channel.sh"
    # Same script, but pointed at a directory with no rust-toolchain.toml.
    script.write_text(RESOLVER.read_text(encoding="utf-8").replace(
        'REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"',
        f'REPO_ROOT="{tmp_path}"',
    ), encoding="utf-8")
    result = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert result.returncode != 0, "a missing manifest must fail, not pass"


def test_action_reads_the_channel_instead_of_pinning_one():
    doc = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    assert doc["runs"]["using"] == "composite", "must be a composite action"

    install = [
        step
        for step in doc["runs"]["steps"]
        if "dtolnay/rust-toolchain" in ((step or {}).get("uses") or "")
    ]
    assert len(install) == 1, f"expected one install step, found {len(install)}"

    toolchain = (install[0].get("with") or {}).get("toolchain", "")
    assert "${{" in toolchain, f"the channel is still a literal: {toolchain!r}"
    assert "steps.read.outputs.channel" in toolchain, toolchain


def _resolver_with_manifest(tmp_path, manifest: str) -> subprocess.CompletedProcess:
    """Run the resolver against a manifest we control, not the repo's."""
    (tmp_path / "rust-toolchain.toml").write_text(manifest, encoding="utf-8")
    script = tmp_path / "resolver.sh"
    script.write_text(
        RESOLVER.read_text(encoding="utf-8").replace(
            'REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"',
            f'REPO_ROOT="{tmp_path}"',
        ),
        encoding="utf-8",
    )
    return subprocess.run(["bash", str(script)], capture_output=True, text=True)


@pytest.mark.parametrize(
    "manifest, expected",
    [
        ('[toolchain]\nchannel = "1.98.1"\n', "1.98.1"),
        # TOML allows single quotes; the resolver must not only accept the one
        # spelling this repository happens to use.
        ("[toolchain]\nchannel = '1.98.1'\n", "1.98.1"),
        # A trailing comment is legal TOML.
        ('[toolchain]\nchannel = "1.98.1" # stable\n', "1.98.1"),
        # Leading whitespace and a component list after the channel.
        ('[toolchain]\n  channel   =   "1.99.0"\nprofile = "minimal"\n', "1.99.0"),
    ],
    ids=["double-quoted", "single-quoted", "with-comment", "extra-whitespace"],
)
def test_resolver_accepts_valid_toml_spellings(tmp_path, manifest, expected):
    result = _resolver_with_manifest(tmp_path, manifest)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    "manifest, reason",
    [
        ('[toolchain]\nchannel = "nightly-2026-09-21"\n', "a dated nightly"),
        ('[toolchain]\nchannel = "stable"\n', "a floating channel"),
        ('[toolchain]\nchannel = "beta"\n', "a pre-release channel"),
        ('[toolchain]\nchannel = "1.98"\n', "a two-component version"),
        ('[toolchain]\nchannel = "path/to/toolchain"\n', "a path"),
    ],
    ids=["nightly", "stable", "beta", "two-component", "path"],
)
def test_resolver_refuses_anything_but_a_stable_version(tmp_path, manifest, reason):
    """A non-stable channel here would build the stable pipeline on the wrong
    toolchain without saying so. The fuzz path pins its own nightly and does not
    come through this script, so nothing legitimate is rejected."""
    result = _resolver_with_manifest(tmp_path, manifest)
    assert result.returncode != 0, (
        f"the resolver accepted {reason}: {result.stdout.strip()!r}"
    )
    assert "not a stable version" in result.stderr, result.stderr


def test_resolver_does_not_die_on_sigpipe_from_head(tmp_path):
    """`sed | head -1` can hand sed a SIGPIPE under `pipefail`.

    The real manifest is four lines so it never triggers, but a repo that grows
    the file would see the resolver start failing for an unrelated reason.
    """
    manifest = "[toolchain]\n" + "".join(
        f'# filler line {n}\n' for n in range(500)
    ) + 'channel = "1.98.1"\n'
    result = _resolver_with_manifest(tmp_path, manifest)
    assert result.returncode == 0, (
        f"a large manifest broke the resolver: {result.stderr}"
    )
    assert result.stdout.strip() == "1.98.1"


def test_action_script_path_resolves_from_the_action_directory():
    """The literal ../ count was wrong by one, and no other test could see it.

    Every test here runs from the repo root, so a path expressed relative to
    GITHUB_ACTION_PATH looked fine while resolving to `.github` instead of the
    checkout -- CI would have failed in the first step of the first job. This
    resolves the path the way the action does and requires it to be the file.
    """
    doc = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    steps = [
        step
        for step in doc["runs"]["steps"]
        if "rust_toolchain_channel.sh" in (step.get("run") or "")
    ]
    assert len(steps) == 1, f"expected one resolver step, found {len(steps)}"

    match = re.search(r'\$\{GITHUB_ACTION_PATH\}(/[^"]+)rust_toolchain_channel\.sh',
                      steps[0]["run"])
    assert match, f"no GITHUB_ACTION_PATH reference: {steps[0]['run']!r}"

    # `resolve()` normalises the `..` segments the way the shell would.
    resolved = (
        ACTION.parent / match.group(1).lstrip("/") / "rust_toolchain_channel.sh"
    ).resolve()
    assert resolved == RESOLVER, (
        f"the action would run {resolved}, but the resolver is {RESOLVER}"
    )
    assert resolved.is_file(), f"{resolved} does not exist"


def test_action_keeps_its_pinned_action_sha():
    """The delegated action itself stays SHA-pinned: only the channel is dynamic."""
    doc = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    for step in doc["runs"]["steps"]:
        uses = (step or {}).get("uses") or ""
        if "dtolnay/rust-toolchain" in uses:
            assert f"@{SHA}" in uses, f"delegated action lost its SHA pin: {uses}"


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_no_workflow_pins_the_stable_channel_by_hand(path):
    """A re-added literal is exactly the drift this change removes."""
    text = path.read_text(encoding="utf-8")
    offenders = re.findall(r"toolchain:\s*1\.\d+\.\d+", text)
    assert not offenders, (
        f"{path.name} pins the channel by hand ({offenders}); "
        "rust-toolchain.toml is the single source"
    )


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_workflow_still_parses(path):
    """The composite call must not have broken the document."""
    yaml.safe_load(path.read_text(encoding="utf-8"))


def test_every_workflow_toolchain_step_goes_through_the_resolver():
    """Nightly steps are the only allowed exception, and cargo-fuzz needs them."""
    total = 0
    for path in _workflow_files():
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, step in _steps(doc):
            uses = (step or {}).get("uses") or ""
            if "rust-toolchain" not in uses and "setup-rust" not in uses:
                continue
            total += 1
            with_block = step.get("with") or {}
            channel = with_block.get("toolchain")
            if channel is None:
                assert uses.endswith("setup-rust"), (
                    f"{path.name}:{job_name} has no channel and does not use "
                    f"the resolver action"
                )
            else:
                assert channel == "nightly", (
                    f"{path.name}:{job_name} pins {channel!r}; only the nightly "
                    "fuzz path may set a channel directly"
                )
    assert total > 0, "no toolchain steps found; the guard is not looking at anything"


def test_resolver_matches_the_manifest():
    """The script and the manifest must not be able to drift either."""
    text = MANIFEST.read_text(encoding="utf-8")
    match = re.search(r'^\s*channel\s*=\s*"([^"]+)"', text, re.M)
    assert match, f"no channel in {MANIFEST.name}"
    resolved = subprocess.run(
        ["bash", str(RESOLVER)], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert resolved == match.group(1), (
        f"resolver reports {resolved}, manifest pins {match.group(1)}"
    )


def test_resolver_is_executable_and_has_a_shebang():
    """CI runs it through bash, but the bit keeps a direct run working."""
    text = RESOLVER.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash"), "missing shebang"
    assert RESOLVER.stat().st_mode & 0o111, "resolver is not executable"


def test_nightly_fuzz_path_still_uses_its_own_nightly():
    """cargo-fuzz cannot run on stable; this must never be folded in."""
    seen = False
    for path in _workflow_files():
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, step in _steps(doc):
            uses = (step or {}).get("uses") or ""
            if (step.get("with") or {}).get("toolchain") == "nightly":
                assert "dtolnay/rust-toolchain" in uses, (
                    f"{path.name}:{job_name} lost the direct action for nightly"
                )
                seen = True
    assert seen, "no nightly step found; the exception has silently disappeared"