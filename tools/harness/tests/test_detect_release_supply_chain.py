"""Pytest tests for detect_release_supply_chain.py — release integrity.

Validates immutable builder digests, ingress source verification,
Homebrew formula identity, and release workflow contracts.

Rule 13 (ci-gating): release builder images use reviewed multi-architecture
manifest digests, not mutable tags; external source/tool bytes are
checksum-verified before extraction or execution.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS_DIR))

from harness.detect_release_supply_chain import (  # noqa: E402
    check_homebrew_formula,
    check_ingress_builder,
    check_official_nginx_builder,
    check_release_builder_digests,
    scan_repository,
)

DETECTOR = Path(__file__).resolve().parent.parent / "detect_release_supply_chain.py"

# Pinned container image digests for the two release builder families.
# These must match the actual ARG OS_BASE values in the Dockerfiles and
# the container: field in release-packages.yml.
#: Reuse the detector's single source of truth so tests cannot drift.
from harness.detect_release_supply_chain import ALMALINUX_9, ALPINE_320  # noqa: E402 - detector constants


# ---------------------------------------------------------------------------
# check_release_builder_digests — container image digests
# ---------------------------------------------------------------------------

class TestBuilderDigests:
    """check_release_builder_digests: container images must use immutable digests."""

    def test_correct_digests_pass(self):
        """All three builder references use the expected pinned digests."""
        files = {
            "tools/build_release/Dockerfile.glibc": f"ARG OS_BASE={ALMALINUX_9}",
            "tools/build_release/Dockerfile.musl": f"ARG OS_BASE={ALPINE_320}",
            ".github/workflows/release-packages.yml": f"container: {ALMALINUX_9}",
        }
        findings = check_release_builder_digests(files)
        assert findings == []

    def test_stale_digest_fails(self):
        """A mutable tag (no @sha256:...) in any builder is a finding."""
        files = {
            "tools/build_release/Dockerfile.glibc": "ARG OS_BASE=almalinux:9",
            "tools/build_release/Dockerfile.musl": f"ARG OS_BASE={ALPINE_320}",
            ".github/workflows/release-packages.yml": f"container: {ALMALINUX_9}",
        }
        findings = check_release_builder_digests(files)
        assert len(findings) == 1
        assert "digest" in findings[0].message.lower()

    def test_missing_digest_fails(self):
        """An empty builder reference is treated as missing digest."""
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
    """check_ingress_builder: Dockerfile ingress must verify source identity."""

    def test_complete_verification_passes(self):
        """Full chain: SHA arg → rev-parse → equality → checkout → download → checksum → extract."""
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
        """Cloning without a pinned SHA arg is a supply-chain finding."""
        text = (
            "RUN git clone https://github.com/example/repo.git\n"
            "RUN git checkout main\n"
        )
        findings = check_ingress_builder(text)
        assert len(findings) >= 1

    def test_missing_rev_parse_fails(self):
        """SHA arg present but no git rev-parse verification is a finding."""
        text = (
            "ARG MODULE_SHA=\n"
            "RUN test \"${actual_sha}\" = \"${MODULE_SHA}\"\n"
        )
        findings = check_ingress_builder(text)
        assert len(findings) >= 1

    def test_missing_nginx_verify_order_fails(self):
        """Download without checksum verification before extraction is a finding."""
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
    """check_official_nginx_builder: NGINX tarball must be verified before extraction."""

    def test_correct_order_passes(self):
        """curl → verify-checksum → tar passes the ordering check."""
        text = (
            "RUN curl -fsSL https://nginx.org/download/nginx-${nginx_version}.tar.gz -o /tmp/nginx.tar.gz\n"
            "RUN bash /opt/nginx-markdown/verify-checksum.sh /tmp/nginx.tar.gz\n"
            "RUN tar -xzf /tmp/nginx.tar.gz\n"
        )
        findings = check_official_nginx_builder(text)
        assert findings == []

    def test_missing_verify_fails(self):
        """curl → tar without an intervening verify step is a finding."""
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
    """check_homebrew_formula: Homebrew formula identity and structure."""

    def test_valid_formula_passes(self):
        """A formula with homepage, url, sha256, and nginx deps passes."""
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
        """A formula without a homepage field is a finding."""
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
    """scan_repository: full integration against the actual repo."""

    def test_actual_repo_passes(self):
        """The real repo must pass its own supply-chain checks."""
        repo_root = Path(__file__).resolve().parents[3]
        findings = scan_repository(repo_root)
        assert findings == []


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
        assert result.returncode in (0, 1)
