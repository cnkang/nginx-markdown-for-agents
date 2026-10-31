"""Tests for tools/ci/retry_transient.sh.

The helper may retry only transient network or registry failures.  A real
failure must keep its exit code and must run exactly once.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

HELPER = pathlib.Path(__file__).resolve().parent / "retry_transient.sh"
DOCKER_METADATA_RESOLUTION_ERROR = (
    "ERROR: failed to resolve source metadata for docker.io/docker/dockerfile:1.7: "
    + "unexpected status from HEAD request to https://registry-1.docker.io/v2/: "
    + "502 Bad Gateway"
)


def _fake(tmp_path: pathlib.Path, *, fail_times: int, message: str, rc: int) -> list[str]:
    """Return a command that fails ``fail_times`` times, then succeeds."""
    counter = tmp_path / "count"
    counter.write_text("0", encoding="utf-8")
    script = tmp_path / "fake.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'n="$(cat "{counter}")"\n'
        "n=$((n + 1))\n"
        f'printf "%s" "$n" > "{counter}"\n'
        f'if [[ "$n" -le {fail_times} ]]; then\n'
        f"  printf '%s\\n' '{message}' >&2\n"
        f"  exit {rc}\n"
        "fi\n"
        "echo ok\n",
        encoding="utf-8",
    )
    return ["bash", str(script)]


def _run(cmd: list[str], *extra: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "RETRY_TRANSIENT_DELAY": "0"}
    return subprocess.run(
        ["bash", str(HELPER), *extra, "--", *cmd],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _count(tmp_path: pathlib.Path) -> int:
    return int((tmp_path / "count").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "message",
    [
        DOCKER_METADATA_RESOLUTION_ERROR,
        "curl: (22) The requested URL returned error: 503",
        "toomanyrequests: You have reached your pull rate limit",
        "curl: (28) Connection timed out after 30001 milliseconds",
        "read tcp 10.0.0.1:443: connection reset by peer",
        "net/http: TLS handshake timeout",
        "Temporary failure resolving 'archive.ubuntu.com'",
        'failed to do request: Head "https://registry-1.docker.io/v2/x": EOF',
        "HTTP/2 429",
    ],
)
def test_transient_failure_is_retried_until_success(tmp_path, message):
    result = _run(_fake(tmp_path, fail_times=2, message=message, rc=7))
    assert result.returncode == 0, result.stderr
    assert _count(tmp_path) == 3
    assert "ok" in result.stdout


def test_transient_failure_stops_after_three_attempts(tmp_path):
    result = _run(_fake(tmp_path, fail_times=5, message="502 Bad Gateway", rc=17))
    assert result.returncode == 17
    assert _count(tmp_path) == 3
    assert "giving up" in result.stderr
    # The original output of every attempt stays in the log.
    assert result.stdout.count("502 Bad Gateway") == 3


@pytest.mark.parametrize(
    "message",
    [
        "error[E0308]: mismatched types",
        "test result: FAILED. 1 passed; 1 failed",
        "sha256 checksum mismatch",
        "E: Unable to locate package nginx-does-not-exist",
    ],
)
def test_non_transient_failure_is_not_retried(tmp_path, message):
    result = _run(_fake(tmp_path, fail_times=5, message=message, rc=3))
    assert result.returncode == 3
    assert _count(tmp_path) == 1
    assert "not a transient failure" in result.stderr
    assert message in result.stdout


def test_transient_marker_before_a_real_error_is_not_retried(tmp_path):
    """Only the tail is classified, where the failing error is printed."""
    earlier = "\\n".join(
        ["W: Failed to fetch http://x/: Connection timed out"]
        + [f"   Compiling crate{i} v0.1.0" for i in range(60)]
        + ["error[E0425]: cannot find value `x` in this scope"]
    )
    cmd = _fake(tmp_path, fail_times=5, message="PLACEHOLDER", rc=101)
    script = tmp_path / "fake.sh"
    script.write_text(
        script.read_text(encoding="utf-8").replace(
            "printf '%s\\n' 'PLACEHOLDER' >&2", f"printf '{earlier}\\n' >&2"
        ),
        encoding="utf-8",
    )
    result = _run(cmd)
    assert result.returncode == 101
    assert _count(tmp_path) == 1


def test_crate_named_like_a_timeout_is_not_transient(tmp_path):
    message = "   Compiling wait-timeout v0.2.1\nerror: could not compile `x`"
    result = _run(_fake(tmp_path, fail_times=5, message=message, rc=101))
    assert result.returncode == 101
    assert _count(tmp_path) == 1


def test_success_runs_once(tmp_path):
    result = _run(_fake(tmp_path, fail_times=0, message="", rc=1))
    assert result.returncode == 0
    assert _count(tmp_path) == 1


def test_attempts_cannot_exceed_three(tmp_path):
    result = _run(_fake(tmp_path, fail_times=0, message="", rc=1), "--attempts", "4")
    assert result.returncode == 2
    assert _count(tmp_path) == 0


def test_attempts_option_lowers_the_budget(tmp_path):
    result = _run(
        _fake(tmp_path, fail_times=5, message="503 Service Unavailable", rc=9),
        "--attempts",
        "2",
    )
    assert result.returncode == 9
    assert _count(tmp_path) == 2


def test_missing_command_is_a_usage_error():
    result = subprocess.run(
        ["bash", str(HELPER), "--"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 2
