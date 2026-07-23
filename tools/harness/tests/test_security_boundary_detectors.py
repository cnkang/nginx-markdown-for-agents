"""Regression tests for release, secret-scope, and auth transport policies."""

from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(REPO_ROOT / "packaging" / "scripts"))

from harness.detect_production_auth_transport import check_config  # noqa: E402
from harness.detect_release_supply_chain import (  # noqa: E402
    ALMALINUX_9,
    ALPINE_320,
    check_homebrew_formula,
    check_homebrew_publisher,
    check_ingress_smoke,
    check_ingress_builder,
    check_release_builder_digests,
)
from harness.detect_workflow_secret_scope import (  # noqa: E402
    check_sonar_token_steps,
    find_broad_env_secrets,
)
from render_homebrew_formula import render_formula  # noqa: E402
from verify_git_archive import compare_archives  # noqa: E402
from verify_homebrew_formula import verify_formula_equivalence  # noqa: E402


def test_release_builder_digest_policy_rejects_mutable_tag() -> None:
    """A release builder tag without its reviewed manifest digest fails."""
    files = {
        "tools/build_release/Dockerfile.glibc": "ARG OS_BASE=almalinux:9\n",
        "tools/build_release/Dockerfile.musl": f"ARG OS_BASE={ALPINE_320}\n",
        ".github/workflows/release-packages.yml": (
            f"    container: {ALMALINUX_9}\n"
        ),
    }

    findings = check_release_builder_digests(files)

    assert len(findings) == 1
    assert findings[0].path.endswith("Dockerfile.glibc")


def test_ingress_contract_rejects_unverified_ref_and_archive() -> None:
    """A branch clone and direct extraction cannot satisfy the build policy."""
    findings = check_ingress_builder(
        """
ARG MODULE_SHA=
RUN git clone --branch "${MODULE_REF}" "${MODULE_REPO}" /workspace/module
RUN curl https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz \
    && tar -xzf /tmp/nginx.tar.gz
"""
    )

    messages = "\n".join(finding.message for finding in findings)
    assert "full object ID" in messages
    assert "must equal" in messages
    assert "verified before extraction" in messages


def test_homebrew_formula_rejects_network_script_bootstrap() -> None:
    """The Formula cannot interpret an unverified rustup response."""
    findings = check_homebrew_formula(
        """
system "bash", "-c", "curl https://sh.rustup.rs | sh"
system "curl", "https://nginx.org/download/#{nginx_archive}"
system "tar", "-xzf", nginx_archive
"""
    )

    messages = "\n".join(finding.message for finding in findings)
    assert "network rustup script" in messages
    assert "checksum-verifying rustup helper" in messages
    assert "verified before extraction" in messages


def test_current_formula_verifier_rejects_historical_unverified_bootstrap() -> None:
    """Old executable Formula content cannot bypass the current template."""
    trusted = """class Example < Formula
  url "https://example.test/v1.0.0.tar.gz"
  sha256 "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  def install
    system "bash", "packaging/scripts/install-verified-rustup.sh"
  end
end
"""
    historical = """class Example < Formula
  url "https://example.test/v0.9.0.tar.gz"
  sha256 "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  def install
    system "bash", "-c", "curl https://sh.rustup.rs | sh"
  end
end
"""

    try:
        verify_formula_equivalence(
            trusted,
            historical,
            url="https://example.test/v1.0.0.tar.gz",
            sha256="a" * 64,
            version="1.0.0",
        )
    except ValueError as exc:
        assert "differs from the trusted" in str(exc)
    else:
        raise AssertionError("unverified historical Formula must be rejected")


def test_homebrew_publisher_requires_audit_before_token() -> None:
    """Tap credentials cannot precede tag-derived rendering and audit."""
    findings = check_homebrew_publisher(
        """
github.event.repository.default_branch
HOMEBREW_TAP_TOKEN: ${{ secrets.HOMEBREW_TAP_TOKEN }}
git show "${TAG_COMMIT}:${FORMULA_SOURCE}"
render_homebrew_formula.py
git archive --format=tar --prefix=reference/
verify_git_archive.py
brew audit --strict nginx-markdown-module
"""
    )

    assert any("credentials" in finding.message for finding in findings)


def test_ingress_smoke_requires_module_sha_build_arg() -> None:
    """The smoke cannot call the fail-closed Dockerfile without its commit."""
    findings = check_ingress_smoke(
        'docker build -f "$DOCKERFILE" "$BUILD_CONTEXT"\n'
    )

    assert any("receive" in finding.message for finding in findings)


def test_job_level_secret_is_rejected_but_step_secret_is_allowed() -> None:
    """Secret expressions are permitted only under a step-local env map."""
    broad = """
jobs:
  scan:
    env:
      TOKEN: ${{ secrets.TOKEN }}
    steps: []
"""
    narrow = """
jobs:
  scan:
    steps:
      - name: Scan
        env:
          TOKEN: ${{ secrets.TOKEN }}
        run: scanner
"""

    assert find_broad_env_secrets(broad, "broad.yml")
    assert not find_broad_env_secrets(narrow, "narrow.yml")


def test_sonar_token_is_limited_to_two_named_steps() -> None:
    """The presence check runs before checkout and the scanner is the consumer."""
    workflow = """
jobs:
  sonar:
    steps:
      - name: Check Sonar token
        env:
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
        run: check
      - name: Checkout repository
        uses: actions/checkout@0000000000000000000000000000000000000000
      - name: SonarCloud Scan
        env:
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
        uses: SonarSource/scan@0000000000000000000000000000000000000000
"""

    assert not check_sonar_token_steps(workflow)


def test_sonar_token_rejects_an_extra_expression() -> None:
    """No third step may inherit or directly interpolate the scanner token."""
    workflow = """
jobs:
  sonar:
    steps:
      - name: Check Sonar token
        env:
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
        run: check
      - name: Checkout repository
        uses: actions/checkout@0000000000000000000000000000000000000000
      - name: Extra consumer
        run: echo "${{ secrets.SONAR_TOKEN }}"
      - name: SonarCloud Scan
        env:
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
        uses: SonarSource/scan@0000000000000000000000000000000000000000
"""

    assert check_sonar_token_steps(workflow)


def test_basic_auth_wildcard_listener_is_rejected() -> None:
    """A server_name does not turn a wildcard cleartext listener into TLS."""
    config = """
server {
    listen 8080;
    server_name internal.example.test;
    auth_basic "Internal";
}
"""

    findings = check_config(config, "private.conf")

    assert len(findings) == 1
    assert "loopback-only" in findings[0].message


def test_basic_auth_loopback_with_tls_contract_is_accepted() -> None:
    """Loopback is safe only with an explicit mandatory co-located terminator."""
    config = """
# A co-located TLS terminator is mandatory.
server {
    listen 127.0.0.1:8080;
    auth_basic "Internal";
}
"""

    assert not check_config(config, "private.conf")


def test_authenticated_http_client_guidance_is_rejected() -> None:
    """Credential-bearing example requests must target HTTPS."""
    config = """
# curl -u admin:pass http://internal.example.test/
server {
    listen 443 ssl;
    auth_basic "Internal";
}
"""

    findings = check_config(config, "private.conf")

    assert any("client guidance" in finding.message for finding in findings)


def test_formula_renderer_preserves_nested_resource_identity() -> None:
    """Only class-level release identity fields are rewritten."""
    source = """class Example < Formula
  url "https://example.test/old.tar.gz"
  sha256 "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  resource "tool" do
    url "https://example.test/tool"
    sha256 "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  end
end
"""

    rendered = render_formula(
        source,
        "https://example.test/v1.2.3.tar.gz",
        "c" * 64,
        "1.2.3",
    )

    assert '  version "1.2.3"\n  sha256 "' in rendered
    assert '    url "https://example.test/tool"' in rendered
    assert f'    sha256 "{"b" * 64}"' in rendered


def _write_tar(path: Path, root: str, content: bytes) -> None:
    """Write a minimal source archive fixture."""
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(f"{root}/tracked.txt")
        info.size = len(content)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(content))


def test_archive_comparison_ignores_root_and_container_metadata(
    tmp_path: Path,
) -> None:
    """Different wrapper names still represent the same resolved Git tree."""
    candidate = tmp_path / "candidate.tar.gz"
    reference = tmp_path / "reference.tar.gz"
    _write_tar(candidate, "project-v1.2.3", b"reviewed content")
    _write_tar(reference, "reference", b"reviewed content")

    compare_archives(candidate, reference)


def test_archive_comparison_rejects_changed_content(tmp_path: Path) -> None:
    """A tag archive with different bytes cannot bind to the resolved commit."""
    candidate = tmp_path / "candidate.tar.gz"
    reference = tmp_path / "reference.tar.gz"
    _write_tar(candidate, "project-v1.2.3", b"different content")
    _write_tar(reference, "reference", b"reviewed content")

    try:
        compare_archives(candidate, reference)
    except ValueError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("different source trees must be rejected")
