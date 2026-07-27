"""Benign fail-closed tests for the verified rustup bootstrap helper."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALLER = REPO_ROOT / "packaging" / "scripts" / "install-verified-rustup.sh"


def _fake_curl(tmp_path: Path, payload: Path) -> tuple[Path, Path]:
    """Create a curl shim that copies inert local fixture bytes."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    url_log = tmp_path / "url.log"
    script = bin_dir / "curl"
    script.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
output=""
url=""
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        -o)
            output="$2"
            shift 2
            ;;
        http*)
            url="$1"
            shift
            ;;
        *)
            shift
            ;;
    esac
done
cp "${FAKE_RUSTUP_PAYLOAD}" "${output}"
printf '%s\\n' "${url}" > "${FAKE_RUSTUP_URL_LOG}"
"""
    )
    script.chmod(0o755)
    return bin_dir, url_log


def _run_installer(tmp_path: Path, expected_hash: str) -> subprocess.CompletedProcess[str]:
    """Run the helper against an inert Darwin rustup-init fixture."""
    invocation_log = tmp_path / "invocation.log"
    payload = tmp_path / "rustup-init"
    payload.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$*" > "${RUSTUP_TEST_INVOCATION_LOG}"
"""
    )
    payload.chmod(0o755)
    bin_dir, url_log = _fake_curl(tmp_path, payload)
    checksums = tmp_path / "checksums.sha256"
    checksums.write_text(
        f"{expected_hash}  rustup-init-1.28.2-aarch64-apple-darwin\n"
    )
    env = os.environ.copy()
    env |= {
        "FAKE_RUSTUP_PAYLOAD": str(payload),
        "FAKE_RUSTUP_URL_LOG": str(url_log),
        "RUSTUP_TEST_INVOCATION_LOG": str(invocation_log),
        "PATH": f"{bin_dir}:{env['PATH']}",
        "TMPDIR": str(tmp_path),
    }
    result = subprocess.run(
        [
            "bash",
            str(INSTALLER),
            "--os",
            "darwin",
            "--arch",
            "arm64",
            "--toolchain",
            "1.97.0",
            "--checksums",
            str(checksums),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    result.url_log = url_log  # type: ignore[attr-defined]
    result.invocation_log = invocation_log  # type: ignore[attr-defined]
    return result


def test_darwin_target_is_verified_before_execution(tmp_path: Path) -> None:
    """A matching Darwin fixture is selected, verified, then invoked locally."""
    payload_bytes = (
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" > \"${RUSTUP_TEST_INVOCATION_LOG}\"\n"
    ).encode()
    result = _run_installer(tmp_path, hashlib.sha256(payload_bytes).hexdigest())

    assert result.returncode == 0, result.stderr
    assert "aarch64-apple-darwin/rustup-init" in result.url_log.read_text()
    invocation = result.invocation_log.read_text()
    assert "--profile minimal" in invocation
    assert "--no-modify-path" in invocation
    assert "--default-toolchain 1.97.0" in invocation


def test_checksum_mismatch_fails_before_execution(tmp_path: Path) -> None:
    """A benign mismatch cannot reach the downloaded fixture's entrypoint."""
    result = _run_installer(tmp_path, "0" * 64)

    assert result.returncode != 0
    assert "SHA256 mismatch" in result.stderr
    assert not result.invocation_log.exists()
