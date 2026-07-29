"""Pytest tests for detect_production_auth_transport.py — transport safety.

Validates that Basic Auth in production examples is only enabled over TLS
or loopback with an explicit TLS terminator contract.

Rule 48 (security-static-analysis): deployable Basic Auth examples must
use an SSL listener or a loopback-only backend behind a mandatory
co-located TLS terminator.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS_DIR))

from harness.detect_production_auth_transport import (  # noqa: E402
    _server_blocks,
    check_config,
)

DETECTOR = Path(__file__).resolve().parent.parent / "detect_production_auth_transport.py"

# Contract phrase that must appear in a comment above any loopback+auth_basic
# server block to satisfy Rule 48's TLS-terminator requirement.
TLS_CONTRACT = "A co-located TLS terminator is mandatory"


# ---------------------------------------------------------------------------
# check_config — Basic Auth transport validation
# ---------------------------------------------------------------------------

class TestCheckConfig:
    """check_config: Basic Auth must be over TLS or loopback+contract."""

    def test_tls_listener_passes(self):
        """SSL listener satisfies the transport requirement."""
        conf = (
            "server {\n"
            "    listen 443 ssl;\n"
            "    auth_basic \"Restricted\";\n"
            "    auth_basic_user_file /etc/nginx/.htpasswd;\n"
            "}\n"
        )
        findings = check_config(conf, "test.conf")
        assert findings == []

    def test_loopback_with_tls_contract_passes(self):
        """Loopback listener is allowed when the TLS-contract comment is present."""
        conf = (
            f"# {TLS_CONTRACT}\n"
            "server {\n"
            "    listen 127.0.0.1:8080;\n"
            "    auth_basic \"Restricted\";\n"
            "    auth_basic_user_file /etc/nginx/.htpasswd;\n"
            "}\n"
        )
        findings = check_config(conf, "test.conf")
        assert findings == []

    def test_plaintext_non_loopback_fails(self):
        """Non-loopback plaintext listener with auth_basic is forbidden."""
        conf = (
            "server {\n"
            "    listen 0.0.0.0:8080;\n"
            "    auth_basic \"Restricted\";\n"
            "    auth_basic_user_file /etc/nginx/.htpasswd;\n"
            "}\n"
        )
        findings = check_config(conf, "test.conf")
        assert len(findings) == 1

    def test_loopback_without_contract_fails(self):
        """Loopback listener without the TLS-contract comment is still a finding."""
        conf = (
            "server {\n"
            "    listen 127.0.0.1:8080;\n"
            "    auth_basic \"Restricted\";\n"
            "    auth_basic_user_file /etc/nginx/.htpasswd;\n"
            "}\n"
        )
        findings = check_config(conf, "test.conf")
        assert len(findings) == 1

    def test_auth_off_ignored(self):
        """auth_basic off is not an active auth directive — no finding."""
        conf = (
            "server {\n"
            "    listen 0.0.0.0:8080;\n"
            "    auth_basic off;\n"
            "}\n"
        )
        findings = check_config(conf, "test.conf")
        assert findings == []

    def test_no_auth_no_findings(self):
        """No auth_basic directive means no transport-safety concern."""
        conf = (
            "server {\n"
            "    listen 0.0.0.0:8080;\n"
            "    location / { proxy_pass http://upstream; }\n"
            "}\n"
        )
        findings = check_config(conf, "test.conf")
        assert findings == []


# ---------------------------------------------------------------------------
# _server_blocks — parser robustness
# ---------------------------------------------------------------------------

class TestServerBlocks:
    """_server_blocks: nginx config block extraction robustness."""

    def test_unterminated_block_detected(self):
        """An unclosed server block must set the unterminated flag."""
        conf = "server {\n    listen 80;\n"
        _blocks, unterminated = _server_blocks(conf)
        assert unterminated is True

    def test_multiple_blocks_extracted(self):
        """Multiple well-formed server blocks are all extracted."""
        conf = (
            "server {\n"
            "    listen 80;\n"
            "}\n"
            "server {\n"
            "    listen 443 ssl;\n"
            "}\n"
        )
        blocks, unterminated = _server_blocks(conf)
        assert unterminated is False
        assert len(blocks) == 2

    def test_comments_dont_break_parsing(self):
        """Comment lines containing braces must not be parsed as block delimiters."""
        conf = (
            "# server { this is a comment\n"
            "server {\n"
            "    listen 80;\n"
            "}\n"
        )
        blocks, unterminated = _server_blocks(conf)
        assert unterminated is False
        assert len(blocks) == 1


# ---------------------------------------------------------------------------
# CLI contract
# ---------------------------------------------------------------------------

class TestCLI:
    """CLI contract: the detector must exit 0 or 1."""

    def test_cli_runs_and_returns_valid_exit_code(self, tmp_path: Path):
        result = subprocess.run(
            [sys.executable, str(DETECTOR)],
            capture_output=True, text=True,
            cwd=tmp_path,
            check=False,
        )
        assert result.returncode == 0, f"expected exit 0, got {result.returncode}; stderr:\n{result.stderr}"
