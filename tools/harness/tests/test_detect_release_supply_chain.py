"""Pytest tests for detect_release_supply_chain.py — release integrity.

Validates immutable builder digests, ingress source verification,
Homebrew formula identity, and release workflow contracts.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS_DIR))

from harness.detect_release_supply_chain import (  # noqa: E402
    Finding,
    check_homebrew_formula,
    check_ingress_builder,
    check_official_nginx_builder,
    check_release_builder_digests,
    scan_repository,
)

DETECTOR = Path(__file__).resolve().parent.parent / "detect_release_supply_chain.py"

ALMALINUX_9 = (
    "almalinux@sha256:"
    "d2515c769e7b73f95c4fde38c0a505336ff38f14990c0b7253b77060a049a743"
)
ALPINE_320 = (
    "alpine@sha256:"
    "d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"
)


# ---------------------------------------------------------------------------
# check_release_builder_digests — container image digests
# ---------------------------------------------------------------------------

class TestBuilderDigests:
    def test_correct_digests_pass(self):
        files = {
            "tools/build_release/Dockerfile.glibc": f"ARG OS_BASE={ALMALINUX_9}",
            "tools/build_release/Dockerfile.musl": f"ARG OS_BASE={ALPINE_320}",
            ".github/workflows/release-packages.yml": f"container: {ALMALINUX_9}",
        }
        findings = check_release_builder_digests(files)
        assert findings == []

    def test_stale_digest_fails(self):
        files = {
            "tools/build_release/Dockerfile.glibc": "ARG OS_BASE=almalinux:9",
            "tools/build_release/Dockerfile.musl": f"ARG OS_BASE={ALPINE_320}",
            ".github/workflows/release-packages.yml": f"container: {ALMALINUX_9}",
        }
        findings = check_release_builder_digests(files)
        assert len(findings) == 1
        assert "digest" in findings[0].message.lower()

    def test_missing_digest_fails(self):
        files = {
            "tools/build_release/Dockerfile.glibc": "",
            "tools/build_release/Dockerfile.musl": f"ARG OS_BASE={ALPINE_320}",
            ".github/workflows/release-packages.yml": f"container: {ALMALINUX_9}",
        }
        findings = check_release_builder_digests(files)
        assert len(findings) >= 1


# ---------------------------------------------------------------------------
# check_ingress_builder — source identity verification
# ---------------------------------------------------------------------------

class TestIngressBuilder:
    def test_complete_verification_passes(self):
        text = (
            "ARG MODULE_SHA=\n"
            "RUN grep -Eq '^[0-9a-f]{40}$' <<< \"${MODULE_SHA}\"\n"
            "RUN actual_sha=\"$(git rev-parse 'FETCH_HEAD^{commit}')\"\n"
            "RUN test \"${actual_sha}\" = \"${MODULE_SHA}\"\n"
            "RUN git checkout --detach \"${actual_sha}\"\n"
            "RUN curl -fsSL https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz -o /tmp/nginx.tar.gz\n"
            "RUN /opt/nginx-markdown/verify-checksum.sh /tmp/nginx.tar.gz\n"
            "RUN tar -xzf /tmp/nginx.tar.gz\n"
        )
        findings = check_ingress_builder(text)
        assert findings == []

    def test_missing_sha_arg_fails(self):
        text = (
            "RUN git clone https://github.com/example/repo.git\n"
            "RUN git checkout main\n"
        )
        findings = check_ingress_builder(text)
        assert len(findings) >= 1

    def test_missing_rev_parse_fails(self):
        text = (
            "ARG MODULE_SHA=\n"
            "RUN test \"${actual_sha}\" = \"${MODULE_SHA}\"\n"
        )
        findings = check_ingress_builder(text)
        assert len(findings) >= 1

    def test_missing_nginx_verify_order_fails(self):
        text = (
            "ARG MODULE_SHA=\n"
            "RUN grep -Eq '^[0-9a-f]{40}$' <<< \"${MODULE_SHA}\"\n"
            "RUN actual_sha=\"$(git rev-parse 'FETCH_HEAD^{commit}')\"\n"
            "RUN test \"${actual_sha}\" = \"${MODULE_SHA}\"\n"
            "RUN git checkout --detach \"${actual_sha}\"\n"
            "RUN curl -fsSL https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz -o /tmp/nginx.tar.gz\n"
            "RUN tar -xzf /tmp/nginx.tar.gz\n"
        )
        findings = check_ingress_builder(text)
        assert any("verified before extraction" in f.message for f in findings)


# ---------------------------------------------------------------------------
# check_official_nginx_builder — verify-before-extract ordering
# ---------------------------------------------------------------------------

class TestOfficialNginxBuilder:
    def test_correct_order_passes(self):
        text = (
            "RUN curl -fsSL https://nginx.org/download/nginx-${nginx_version}.tar.gz -o /tmp/nginx.tar.gz\n"
            "RUN bash /opt/nginx-markdown/verify-checksum.sh /tmp/nginx.tar.gz\n"
            "RUN tar -xzf /tmp/nginx.tar.gz\n"
        )
        findings = check_official_nginx_builder(text)
        assert findings == []

    def test_missing_verify_fails(self):
        text = (
            "RUN curl -fsSL https://nginx.org/download/nginx-${nginx_version}.tar.gz -o /tmp/nginx.tar.gz\n"
            "RUN tar -xzf /tmp/nginx.tar.gz\n"
        )
        findings = check_official_nginx_builder(text)
        assert len(findings) == 1


# ---------------------------------------------------------------------------
# check_homebrew_formula — formula identity
# ---------------------------------------------------------------------------

class TestHomebrewFormula:
    def test_valid_formula_passes(self):
        text = (
            'class NginxMarkdownModule < Formula\n'
            '  desc "NGINX module for Markdown conversion"\n'
            '  homepage "https://github.com/cnkang/nginx-markdown-for-agents"\n'
            '  url "https://github.com/cnkang/nginx-markdown-for-agents/archive/refs/tags/v0.9.1.tar.gz"\n'
            '  sha256 "abc123"\n'
            '  depends_on "nginx"\n'
            '  depends_on "rust" => :build\n'
            '  def install\n'
            '    system "./tools/harness/install-verified-rustup.sh"\n'
            '    system "rustup", "target", "add", "--os", "darwin", Hardware::CPU.arm? ? "aarch64-apple-darwin" : "x86_64-apple-darwin"\n'
            '    nginx_archive = "nginx-#{version}.tar.gz"\n'
            '    system "curl", "-fsSL", "https://nginx.org/download/#{nginx_archive}", "-o", buildpath/nginx_archive\n'
            '    system "bash", "tools/harness/verify-checksum.sh", buildpath/nginx_archive\n'
            '    system "tar", "-xzf", nginx_archive\n'
            '  end\n'
        )
        findings = check_homebrew_formula(text)
        assert findings == []

    def test_missing_homepage_fails(self):
        text = (
            'class NginxMarkdownModule < Formula\n'
            '  desc "NGINX module"\n'
            '  url "https://github.com/cnkang/nginx-markdown-for-agents/archive/refs/tags/v0.9.1.tar.gz"\n'
        )
        findings = check_homebrew_formula(text)
        assert len(findings) >= 1


# ---------------------------------------------------------------------------
# scan_repository — full integration (actual repo)
# ---------------------------------------------------------------------------

class TestScanRepository:
    def test_actual_repo_passes(self):
        """The real repo must pass its own supply-chain checks."""
        repo_root = Path(__file__).resolve().parents[3]
        findings = scan_repository(repo_root)
        assert findings == []


# ---------------------------------------------------------------------------
# CLI contract
# ---------------------------------------------------------------------------

class TestCLI:
    def test_cli_runs_and_returns_valid_exit_code(self, tmp_path: Path):
        result = subprocess.run(
            [sys.executable, str(DETECTOR)],
            capture_output=True, text=True,
            cwd=tmp_path,
        )
        assert result.returncode in (0, 1)
