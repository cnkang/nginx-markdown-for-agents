"""Exercise installer signature acceptance without downloading artifacts."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FINGERPRINT = "A" * 40


def run_verification(tmp_path, status, exit_code, fingerprint=FINGERPRINT):
    """Run the production helper with deterministic GPG status and exit code."""
    installer = (ROOT / "tools/install.sh").read_text()
    body = installer.split("verify_release_signature() {", 1)[1].split("\n}\n", 1)[0]
    helper = tmp_path / "verify.sh"
    helper.write_text("verify_release_signature() {" + body + "\n}\n")
    script = r'''
set -euo pipefail
source "$1"
fake_gpg() {
  case "$*" in
    *--import*) return 0 ;;
    *) printf '%s\n' "$TEST_GPG_STATUS"; return "$TEST_GPG_EXIT" ;;
  esac
}
GPG_BIN=fake_gpg
MKTEMP_BIN="$(command -v mktemp)"
CHMOD_BIN="$(command -v chmod)"
RM_BIN="$(command -v rm)"
AWK_BIN="$(command -v awk)"
TR_BIN="$(command -v tr)"
_json_error_message=""
if verify_release_signature manifest signature key "$2"; then
  exit 0
fi
printf '%s\n' "$_json_error_message" >&2
exit 1
'''
    env = dict(os.environ, TEST_GPG_STATUS=status, TEST_GPG_EXIT=str(exit_code),
               TMPDIR=str(tmp_path))
    return subprocess.run(["bash", "-c", script, "_", str(helper), fingerprint],
                          env=env, capture_output=True, text=True, check=False)


def test_valid_signature_matching_pin_succeeds(tmp_path):
    result = run_verification(tmp_path, f"[GNUPG:] VALIDSIG {FINGERPRINT}", 0)
    assert result.returncode == 0, result.stderr
    assert "Release signature verified" in result.stdout
    assert not list(tmp_path.glob("tmp.*"))


@pytest.mark.parametrize("failure", ["EXPSIG", "BADSIG", "FAILURE"])
def test_failed_verification_with_validsig_is_rejected(tmp_path, failure):
    status = f"[GNUPG:] {failure} fixture\n[GNUPG:] VALIDSIG {FINGERPRINT}"
    result = run_verification(tmp_path, status, 1)
    assert result.returncode == 1
    assert "gpg rejected" in result.stderr
    assert "Release signature verified" not in result.stdout
    assert not list(tmp_path.glob("tmp.*"))


@pytest.mark.parametrize("status", ["", "[GNUPG:] VALIDSIG " + "B" * 40])
def test_successful_gpg_without_matching_pin_is_rejected(tmp_path, status):
    result = run_verification(tmp_path, status, 0)
    assert result.returncode == 1
    assert "Release signature verified" not in result.stdout
    assert not list(tmp_path.glob("tmp.*"))
