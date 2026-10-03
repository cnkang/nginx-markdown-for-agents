"""The release gate must benchmark on the NGINX version the baselines recorded.

`tools/perf/evidence_gate.py` fails closed when the run's `nginx_version`
differs from the baseline's, so the benchmark version is not a free choice: it
has to be the one the checked-in baseline was measured on. The workflow used to
take the first amd64 entry in the release matrix instead, which drifted to
1.24.0 while every baseline recorded 1.30.4 -- so each run compared two
environments and the evidence gate rejected its own comparison.

These tests cover the resolver and the workflow wiring, because the two drift
together: a correct resolver nobody calls is as broken as the old selector.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
RESOLVER = REPO_ROOT / "tools" / "perf" / "baseline_nginx_version.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"
BASELINE_DIR = REPO_ROOT / "perf" / "baselines"

_spec = importlib.util.spec_from_file_location("baseline_nginx_version", RESOLVER)
assert _spec and _spec.loader
resolver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(resolver)


def _write_baseline(directory: pathlib.Path, name: str, version: str) -> None:
    (directory / name).write_text(
        json.dumps({"module_benchmark": {"nginx_version": version}}),
        encoding="utf-8",
    )


def test_it_reads_the_version_out_of_a_baseline(tmp_path: pathlib.Path) -> None:
    _write_baseline(tmp_path, "module-baseline-091.json", "nginx version: nginx/1.30.4")

    assert resolver.resolve(tmp_path, "module-baseline-*.json") == "1.30.4"


def test_several_baselines_agreeing_resolve_to_one_version(tmp_path: pathlib.Path) -> None:
    for name in ("091", "092", "092-brotli"):
        _write_baseline(tmp_path, f"module-baseline-{name}.json", "nginx/1.31.5")

    assert resolver.resolve(tmp_path, "module-baseline-*.json") == "1.31.5"


def test_baselines_disagreeing_fail_and_name_both_versions(tmp_path: pathlib.Path) -> None:
    """Disagreement is unsatisfiable, and must not resolve to either version."""
    _write_baseline(tmp_path, "module-baseline-091.json", "nginx/1.24.0")
    _write_baseline(tmp_path, "module-baseline-092.json", "nginx/1.30.4")

    with pytest.raises(SystemExit) as excinfo:
        resolver.resolve(tmp_path, "module-baseline-*.json")

    message = str(excinfo.value)
    assert "1.24.0" in message and "1.30.4" in message, message


def test_an_unreadable_baseline_is_ignored_not_fatal(tmp_path: pathlib.Path) -> None:
    (tmp_path / "module-baseline-broken.json").write_text("{not json", encoding="utf-8")
    _write_baseline(tmp_path, "module-baseline-092.json", "nginx/1.30.4")

    assert resolver.resolve(tmp_path, "module-baseline-*.json") == "1.30.4"


def test_no_baselines_resolves_to_empty_so_the_caller_fails_closed(
    tmp_path: pathlib.Path,
) -> None:
    """Empty is not a silent fallback to the matrix: the step rejects it."""
    assert resolver.resolve(tmp_path, "module-baseline-*.json") == ""


def test_the_checked_in_baselines_all_agree() -> None:
    """The shipped baselines must satisfy the resolver they now drive."""
    resolved = subprocess.run(
        [
            sys.executable,
            str(RESOLVER),
            "--baseline-dir",
            str(BASELINE_DIR),
            "--require-glob",
            "module-baseline-*.json",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert resolved, "no checked-in baseline recorded an NGINX version"


def _benchmark_step_body() -> str:
    """Return the shell body of the benchmark-version step, as CI runs it."""
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("- name: Determine canonical benchmark NGINX version")
    body_at = text.index("run: |", start) + len("run: |")
    lines: list[str] = []
    for line in text[body_at:].splitlines():
        if line.strip() and not line.startswith(" " * 10):
            break
        lines.append(line[10:] if line.startswith(" " * 10) else line)
    return "\n".join(lines)


def test_the_benchmark_step_rejects_an_unusable_baseline_version(
    tmp_path: pathlib.Path,
) -> None:
    """A run that cannot build the baseline's version must fail, not fall back."""
    script = tmp_path / "step.sh"
    script.write_text(_benchmark_step_body(), encoding="utf-8")
    versions = json.dumps(["1.24.0", "1.26.3"])

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "NGINX_VERSIONS": versions,
            "BASELINE_NGINX_VERSION": "1.30.4",
            "GITHUB_OUTPUT": str(tmp_path / "out.txt"),
        },
    )

    assert result.returncode != 0, (
        "the step must not benchmark a version the baselines do not describe"
    )
    assert not (tmp_path / "out.txt").exists() or not (
        tmp_path / "out.txt"
    ).read_text().strip(), "no benchmark version may be selected"


def test_the_benchmark_step_rejects_an_unresolvable_baseline(
    tmp_path: pathlib.Path,
) -> None:
    """A baseline directory with no readable version fails the step.

    The step computes `BASELINE_NGINX_VERSION` itself, so this runs the real
    body against a baseline directory that yields nothing rather than injecting
    an empty environment variable. An empty version used to mean "fall back to
    the matrix", which benchmarks an environment no baseline describes.
    """
    script = tmp_path / "step.sh"
    script.write_text(_benchmark_step_body(), encoding="utf-8")
    empty_dir = tmp_path / "baselines"
    empty_dir.mkdir()
    versions = json.dumps(["1.24.0", "1.26.3", "1.30.4"])

    # Redirect the resolver at the empty directory the way the workflow's own
    # flags allow, by rewriting the resolver invocation in the extracted body.
    body = script.read_text(encoding="utf-8").replace(
        "--baseline-dir perf/baselines", f"--baseline-dir {empty_dir}"
    )
    script.write_text(body, encoding="utf-8")

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "NGINX_VERSIONS": versions,
            "GITHUB_OUTPUT": str(tmp_path / "out.txt"),
        },
    )

    assert result.returncode != 0, (
        "an unreadable baseline must fail the step; falling back to the matrix "
        "would compare against an environment no baseline describes"
    )


def test_the_workflow_benchmark_step_uses_the_resolver() -> None:
    """The step must call the resolver, not re-derive the version itself."""
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("- name: Determine canonical benchmark NGINX version")
    step = text[start : start + 6000]

    assert "baseline_nginx_version.py" in step, (
        "the benchmark-version step must read the baseline's NGINX version; "
        "selecting the first amd64 matrix entry drifts away from the baseline "
        "and makes the evidence gate reject its own comparison"
    )
    assert "next((v for v in versions if v in amd64)" not in step, (
        "the matrix-first selector must be gone, not merely deprioritised: "
        "with it as a fallback an unreadable baseline silently benchmarks an "
        "environment no baseline describes"
    )


def test_the_resolver_is_invoked_in_the_step_body_not_in_env() -> None:
    """A workflow `env:` value is a literal; Actions never runs it.

    A command substitution in `env:` reaches the step as its own text, so the
    selector would compare against a command string instead of a version and
    reject every run. The resolver therefore has to be called from `run:`.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("- name: Determine canonical benchmark NGINX version")
    end = text.index("- name:", start + 10)
    step = text[start:end]

    env_block = step.split("run: |", 1)[0]
    assert "$(" not in env_block, (
        "env: values are literals in GitHub Actions, so a command substitution "
        "there never executes; call the resolver from the step body instead"
    )
    run_block = step.split("run: |", 1)[1]
    expected = (
        'BASELINE_NGINX_VERSION="$(python3 tools/perf/baseline_nginx_version.py'
    )
    assert expected in run_block, (
        "the step body must assign the resolver output to BASELINE_NGINX_VERSION"
    )


def test_the_benchmark_step_selects_the_baseline_version(tmp_path: pathlib.Path) -> None:
    """Run the step's own body: it must pick the baseline's version.

    Asserting on the YAML text is not enough -- the resolver call and the
    selection that consumes it can drift apart, and then the workflow still
    benchmarks the wrong version while every text assertion passes.
    """
    body = _benchmark_step_body()
    script = tmp_path / "step.sh"
    script.write_text(body, encoding="utf-8")
    versions = json.dumps(["1.24.0", "1.26.3", "1.28.3", "1.30.4", "1.31.5"])
    env_version = subprocess.run(
        [
            sys.executable,
            str(RESOLVER),
            "--baseline-dir",
            str(BASELINE_DIR),
            "--require-glob",
            "module-baseline-*.json",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "NGINX_VERSIONS": versions,
            "BASELINE_NGINX_VERSION": env_version,
            "GITHUB_OUTPUT": str(tmp_path / "out.txt"),
        },
    )

    assert result.returncode == 0, result.stderr
    selected = [
        line.split("=", 1)[1]
        for line in (tmp_path / "out.txt").read_text().splitlines()
        if line.startswith("bench_nginx_version=")
    ]
    assert selected == [env_version], (
        f"the step selected {selected}, but the baselines require {env_version}"
    )
