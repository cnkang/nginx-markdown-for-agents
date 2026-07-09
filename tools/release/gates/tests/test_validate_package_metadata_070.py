"""Tests for release gate validator: NGINX version extraction.

Run:
    python3 -m pytest tools/release/gates/tests/test_validate_package_metadata_070.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the tools package is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import tools.release.gates.validate_package_metadata_070 as validator  # noqa: E402
from tools.release.gates.validate_package_metadata_070 import (  # noqa: E402
    FORBIDDEN_NAKED_EXACT_NGINX_DEPS,
    GATE3_LOCAL_ARCH_SNIPPETS,
    NFPM_DEB_ONLY_MODULES_AVAILABLE_PATTERN,
    NFPM_POSTINSTALL_FORBIDDEN_SNIPPETS,
    NFPM_REQUIRED_SNIPPETS,
    NFPM_POSTINSTALL_SNIPPETS,
    SMOKE_RPM_REPO_SNIPPETS,
    RELEASE_BUILD_GLIBC_SNIPPETS,
    SIGN_AND_PUBLISH_FORBIDDEN_SNIPPETS,
    SIGN_AND_PUBLISH_SECURITY_SNIPPETS,
    STANDALONE_DEB_SNIPPETS,
    STANDALONE_RPM_SPEC_SNIPPETS,
    STANDALONE_RPM_WORKFLOW_SNIPPETS,
    _contains_make_build_command,
    _is_nginx_version,
    _split_inline_list,
    _strip_unquoted_comment,
    _unquote,
    extract_nginx_versions,
)


# ---------------------------------------------------------------------------
# _is_nginx_version
# ---------------------------------------------------------------------------


class TestIsNginxVersion:
    """Validate strict three-part numeric version detection."""

    def test_valid_version(self) -> None:
        """Accept a standard three-part NGINX version string."""
        assert _is_nginx_version("1.25.5") is True

    def test_two_parts_rejected(self) -> None:
        """Reject version strings with only two numeric parts."""
        assert _is_nginx_version("1.25") is False

    def test_four_parts_rejected(self) -> None:
        """Reject version strings with four numeric parts."""
        assert _is_nginx_version("1.25.5.1") is False

    def test_non_numeric_rejected(self) -> None:
        """Reject non-numeric version strings like 'mainline'."""
        assert _is_nginx_version("mainline") is False

    def test_mixed_rejected(self) -> None:
        """Reject version strings mixing numeric and non-numeric parts."""
        assert _is_nginx_version("1.25.x") is False

    def test_empty_string_rejected(self) -> None:
        """Reject empty strings as invalid versions."""
        assert _is_nginx_version("") is False


# ---------------------------------------------------------------------------
# _strip_unquoted_comment
# ---------------------------------------------------------------------------


class TestStripUnquotedComment:
    """Validate comment stripping preserves quoted content."""

    def test_no_comment(self) -> None:
        """Return input unchanged when no comment marker is present."""
        assert _strip_unquoted_comment("NGINX_VERSION=1.25.5") == "NGINX_VERSION=1.25.5"

    def test_simple_comment(self) -> None:
        """Strip trailing unquoted comment after hash marker."""
        assert _strip_unquoted_comment("NGINX_VERSION=1.25.5 # active") == "NGINX_VERSION=1.25.5 "

    def test_hash_inside_double_quotes_preserved(self) -> None:
        """Preserve hash characters inside double-quoted strings."""
        assert _strip_unquoted_comment('"value#with#hash" # comment') == '"value#with#hash" '

    def test_hash_inside_single_quotes_preserved(self) -> None:
        """Preserve hash characters inside single-quoted strings."""
        assert _strip_unquoted_comment("'value#hash' # comment") == "'value#hash' "

    def test_escaped_quote_inside_double_quotes(self) -> None:
        """Preserve escaped quotes and their contained hash characters."""
        assert _strip_unquoted_comment('"value\\"#still" # comment') == '"value\\"#still" '


# ---------------------------------------------------------------------------
# _unquote
# ---------------------------------------------------------------------------


class TestUnquote:
    """Validate quote and comma stripping."""

    def test_double_quoted(self) -> None:
        """Strip surrounding double quotes from a value."""
        assert _unquote('"1.25.5"') == "1.25.5"

    def test_single_quoted(self) -> None:
        """Strip surrounding single quotes from a value."""
        assert _unquote("'1.25.5'") == "1.25.5"

    def test_trailing_comma(self) -> None:
        """Strip trailing comma after quoted value."""
        assert _unquote('"1.25.5",') == "1.25.5"

    def test_whitespace_stripped(self) -> None:
        """Strip leading and trailing whitespace from value."""
        assert _unquote('  "1.25.5"  ') == "1.25.5"

    def test_unquoted_value(self) -> None:
        """Return unquoted values unchanged after whitespace stripping."""
        assert _unquote("1.25.5") == "1.25.5"


# ---------------------------------------------------------------------------
# _split_inline_list
# ---------------------------------------------------------------------------


class TestSplitInlineList:
    """Validate YAML-style inline list splitting."""

    def test_double_quoted_items(self) -> None:
        """Split a comma-separated list of double-quoted items."""
        assert _split_inline_list('"1.25.5", "1.26.1"') == ["1.25.5", "1.26.1"]

    def test_single_quoted_items(self) -> None:
        """Split a comma-separated list of single-quoted items."""
        assert _split_inline_list("'1.25.5', '1.26.1'") == ["1.25.5", "1.26.1"]

    def test_unquoted_items(self) -> None:
        """Split a comma-separated list of unquoted items."""
        assert _split_inline_list("1.25.5, 1.26.1") == ["1.25.5", "1.26.1"]

    def test_mixed_quoting(self) -> None:
        """Split a list mixing quoted and unquoted items."""
        assert _split_inline_list('"1.25.5", 1.26.1') == ["1.25.5", "1.26.1"]

    def test_single_item(self) -> None:
        """Return a single-item list when input has no commas."""
        assert _split_inline_list('"1.25.5"') == ["1.25.5"]

    def test_empty_string(self) -> None:
        """Return empty list for empty input string."""
        assert _split_inline_list("") == []


# ---------------------------------------------------------------------------
# extract_nginx_versions — supported formats
# ---------------------------------------------------------------------------


class TestExtractNginxVersions:
    """Validate NGINX version extraction from all supported formats."""

    def test_yaml_array_double_quoted(self) -> None:
        """Extract versions from a YAML inline array with double-quoted items."""
        content = 'nginx_version: ["1.25.5", "1.26.1"]'
        assert extract_nginx_versions(content) == {"1.25.5", "1.26.1"}

    def test_yaml_array_single_quoted(self) -> None:
        """Extract versions from a YAML inline array with single-quoted items."""
        content = "nginx_version: ['1.25.5', '1.26.1']"
        assert extract_nginx_versions(content) == {"1.25.5", "1.26.1"}

    def test_shell_double_quoted(self) -> None:
        """Extract version from a shell-style double-quoted assignment."""
        content = 'NGINX_VERSION="1.27.4"'
        assert extract_nginx_versions(content) == {"1.27.4"}

    def test_shell_single_quoted(self) -> None:
        """Extract version from a shell-style single-quoted assignment."""
        content = "NGINX_VERSION='1.29.1'"
        assert extract_nginx_versions(content) == {"1.29.1"}

    def test_dockerfile_arg(self) -> None:
        """Extract version from a Dockerfile ARG instruction."""
        content = "ARG NGINX_VERSION=1.28.0"
        assert extract_nginx_versions(content) == {"1.28.0"}

    def test_all_supported_formats_combined(self) -> None:
        """Extract all versions when multiple formats appear in one file."""
        content = '''
nginx_version: ["1.25.5", "1.26.1"]
NGINX_VERSION="1.27.4"
ARG NGINX_VERSION=1.28.0
NGINX_VERSION='1.29.1'
'''
        assert extract_nginx_versions(content) == {
            "1.25.5",
            "1.26.1",
            "1.27.4",
            "1.28.0",
            "1.29.1",
        }

    def test_ignores_comments(self) -> None:
        """Skip version declarations that appear in comments."""
        content = '''
# nginx_version: ["9.9.9"]
NGINX_VERSION="1.27.4" # active version
'''
        assert extract_nginx_versions(content) == {"1.27.4"}

    def test_rejects_invalid_versions(self) -> None:
        """Filter out non-conforming version strings from extraction."""
        content = '''
nginx_version: ["1.25", "mainline", "1.26.1"]
NGINX_VERSION="latest"
ARG NGINX_VERSION=1.28
'''
        assert extract_nginx_versions(content) == {"1.26.1"}

    def test_empty_content(self) -> None:
        """Return empty set for empty input content."""
        assert extract_nginx_versions("") == set()

    def test_no_versions(self) -> None:
        """Return empty set when no version declarations exist."""
        content = "some random content without versions"
        assert extract_nginx_versions(content) == set()

    def test_yaml_array_with_spaces(self) -> None:
        """Extract versions from YAML array with irregular whitespace."""
        content = 'nginx_version:   [  "1.25.5"  ,  "1.26.1"  ]'
        assert extract_nginx_versions(content) == {"1.25.5", "1.26.1"}

    def test_deduplication(self) -> None:
        """Deduplicate identical versions from multiple declarations."""
        content = '''
NGINX_VERSION="1.25.5"
ARG NGINX_VERSION=1.25.5
'''
        assert extract_nginx_versions(content) == {"1.25.5"}

    def test_yaml_no_closing_bracket(self) -> None:
        """Return empty set for malformed YAML array missing closing bracket."""
        content = 'nginx_version: ["1.25.5", "1.26.1"'
        assert extract_nginx_versions(content) == set()

    def test_arg_without_space_prefix(self) -> None:
        """Reject ARGNGINX_VERSION without space separator from ARG keyword."""
        content = "ARGNGINX_VERSION=1.25.5"
        assert extract_nginx_versions(content) == set()

    def test_version_with_inline_comment(self) -> None:
        """Extract version correctly when followed by an inline comment."""
        content = 'NGINX_VERSION="1.25.5" # pinned to stable'
        assert extract_nginx_versions(content) == {"1.25.5"}

    def test_current_release_matrix_schema(self, monkeypatch) -> None:
        """Extract release-blocking glibc versions from the current matrix schema."""
        matrix_json = json.dumps(
            {
                "schema_version": "1.0",
                "entries": [
                    {
                        "nginx_version": "1.30.2",
                        "support_tier": "supported",
                        "libc": "glibc",
                        "release_blocking": True,
                    },
                    {
                        "nginx_version": "1.30.2",
                        "support_tier": "supported",
                        "libc": "musl",
                        "release_blocking": False,
                    },
                    {
                        "nginx_version": "1.31.1",
                        "support_tier": "experimental",
                        "libc": "glibc",
                        "release_blocking": False,
                    },
                ],
            }
        )
        monkeypatch.setattr(validator, "read_safe", lambda _path: matrix_json)

        content = "matrix source: tools/release-matrix.json"

        assert extract_nginx_versions(content) == {"1.30.2"}


# ---------------------------------------------------------------------------
# Large adversarial input
# ---------------------------------------------------------------------------


class TestLargeInputSafety:
    """Ensure linear-time parsing on adversarial input."""

    def test_large_noisy_yaml_array(self) -> None:
        """Handle large YAML array of invalid versions without performance degradation."""
        noisy_line = (
            "nginx_version: ["
            + ",".join(["not-a-version"] * 10_000)
            + "]"
        )
        content = noisy_line + '\nNGINX_VERSION="1.27.4"\n'
        assert extract_nginx_versions(content) == {"1.27.4"}

    def test_large_noisy_shell_declarations(self) -> None:
        """Handle large set of non-matching shell declarations efficiently."""
        lines = [f'NOT_NGINX_VERSION="val{i}"' for i in range(5_000)]
        lines.append('NGINX_VERSION="1.27.4"')
        content = "\n".join(lines)
        assert extract_nginx_versions(content) == {"1.27.4"}


# ---------------------------------------------------------------------------
# _contains_make_build_command
# ---------------------------------------------------------------------------


class TestContainsMakeBuildCommand:
    """Validate active make build command detection without regex."""

    def test_detects_simple_make_build(self) -> None:
        """Detect a plain 'make build' command."""
        assert _contains_make_build_command("make build") is True

    def test_detects_indented_make_build(self) -> None:
        """Detect 'make build' with leading whitespace."""
        assert _contains_make_build_command("    make build") is True

    def test_detects_multiple_spaces(self) -> None:
        """Detect 'make build' with multiple spaces between tokens."""
        assert _contains_make_build_command("make     build") is True

    def test_detects_make_build_with_args(self) -> None:
        """Detect 'make build' followed by arguments."""
        assert _contains_make_build_command("make build RELEASE=1") is True

    def test_detects_make_build_with_multiple_args(self) -> None:
        """Detect 'make build' with multiple trailing arguments."""
        assert _contains_make_build_command("make build all") is True

    def test_ignores_commented_make_build(self) -> None:
        """Ignore 'make build' that is commented out."""
        assert _contains_make_build_command("# make build") is False

    def test_ignores_indented_commented_make_build(self) -> None:
        """Ignore indented commented-out 'make build'."""
        assert _contains_make_build_command("    # make build") is False

    def test_ignores_echo_make_build(self) -> None:
        """Ignore 'make build' appearing inside an echo statement."""
        assert _contains_make_build_command('echo "make build"') is False

    def test_ignores_percent_make_build(self) -> None:
        """Ignore '%make_build' which is not a valid make command."""
        assert _contains_make_build_command("%make_build") is False

    def test_ignores_makebuild(self) -> None:
        """Ignore 'makebuild' without space separator."""
        assert _contains_make_build_command("makebuild") is False

    def test_ignores_make_builder(self) -> None:
        """Ignore 'make builder' which is not the 'build' target."""
        assert _contains_make_build_command("make builder") is False

    def test_ignores_make_test(self) -> None:
        """Ignore 'make test' which is a different target."""
        assert _contains_make_build_command("make test") is False

    def test_detects_in_multiline_content(self) -> None:
        """Detect 'make build' within multiline content."""
        content = "# comment\nmake build\nmore stuff"
        assert _contains_make_build_command(content) is True

    def test_ignores_all_comments_in_multiline(self) -> None:
        """Ignore all commented or non-command occurrences in multiline content."""
        content = "# make build\n  # make build\necho make build"
        assert _contains_make_build_command(content) is False

    def test_empty_content(self) -> None:
        """Return False for empty input content."""
        assert _contains_make_build_command("") is False


# ---------------------------------------------------------------------------
# Release gate regression expectations
# ---------------------------------------------------------------------------


class TestReleaseGateSnippetExpectations:
    """Validate regression guard snippets for release/package review findings."""

    def test_nfpm_dependency_uses_non_exact_floor(self) -> None:
        """Ensure NFPM dependency uses non-exact floor version and correct module path."""
        assert 'nginx (>= ${NGINX_VERSION_FLOOR})' in NFPM_REQUIRED_SNIPPETS
        assert 'nginx (<< ${NGINX_VERSION_CEIL})' in NFPM_REQUIRED_SNIPPETS
        assert 'nginx (= ${NGINX_VERSION})' not in NFPM_REQUIRED_SNIPPETS
        assert "/usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so" in NFPM_REQUIRED_SNIPPETS
        assert "packager: deb" in NFPM_DEB_ONLY_MODULES_AVAILABLE_PATTERN

    def test_rpm_spec_dependency_uses_non_exact_floor(self) -> None:
        """Ensure RPM spec uses non-exact floor dependency and correct module path."""
        assert "Requires:       nginx >= 1:%{nginx_version_floor}" in STANDALONE_RPM_SPEC_SNIPPETS
        assert "Requires:       nginx = %{nginx_version}" in FORBIDDEN_NAKED_EXACT_NGINX_DEPS
        assert "/usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so" in STANDALONE_RPM_SPEC_SNIPPETS

    def test_standalone_workflows_validate_input_version(self) -> None:
        """Ensure standalone DEB and RPM workflows validate input versions."""
        validator = './packaging/scripts/validate-version.sh "${{ inputs.version }}"'
        assert validator in STANDALONE_DEB_SNIPPETS
        assert validator in STANDALONE_RPM_WORKFLOW_SNIPPETS

    def test_sign_and_publish_uses_trusted_checkout_before_secrets(self) -> None:
        """Ensure signing workflow scripts come from the default branch."""
        assert "ref: ${{ github.event.repository.default_branch }}" in (
            SIGN_AND_PUBLISH_SECURITY_SNIPPETS
        )
        assert "persist-credentials: false" in SIGN_AND_PUBLISH_SECURITY_SNIPPETS
        assert "Validate release tag input" in SIGN_AND_PUBLISH_SECURITY_SNIPPETS

    def test_sign_and_publish_forbids_caller_selected_ref_checkout(self) -> None:
        """Ensure signing workflow cannot reintroduce caller-selected checkout."""
        assert "ref: ${{ inputs.version }}" in SIGN_AND_PUBLISH_FORBIDDEN_SNIPPETS

    def test_nfpm_postinstall_doc_path_matches_installed_layout(self) -> None:
        """Ensure postinstall doc path matches the installed package layout."""
        assert "/usr/share/doc/nginx-markdown-for-agents/README.md" in NFPM_POSTINSTALL_SNIPPETS
        assert (
            "/usr/share/doc/nginx-module-markdown-for-agents/README.md"
            in NFPM_POSTINSTALL_FORBIDDEN_SNIPPETS
        )

    def test_gate3_local_smoke_selects_arch_specific_packages(self) -> None:
        """Ensure gate3 local smoke uses architecture-specific package patterns."""
        assert 'pkg_pattern="*_${ARCH}.deb"' in GATE3_LOCAL_ARCH_SNIPPETS
        assert 'pkg_pattern="*-1.${RPM_ARCH}.rpm"' in GATE3_LOCAL_ARCH_SNIPPETS

    def test_rpm_smoke_repo_selection_covers_amazon_linux(self) -> None:
        """Ensure RPM smoke repo selection includes Amazon Linux and CentOS paths."""
        assert "amzn)" in SMOKE_RPM_REPO_SNIPPETS
        assert "nginx_repo_channel()" in SMOKE_RPM_REPO_SNIPPETS
        assert "packages/%samzn/" in SMOKE_RPM_REPO_SNIPPETS
        assert "packages/%scentos/" in SMOKE_RPM_REPO_SNIPPETS

    def test_nfpm_postinstall_accepts_rpm_lifecycle_args(self) -> None:
        """Ensure postinstall script handles RPM lifecycle arguments."""
        assert "configure|1|2)" in NFPM_POSTINSTALL_SNIPPETS
        assert "abort-upgrade|abort-remove|abort-deconfigure)" in NFPM_POSTINSTALL_SNIPPETS

    def test_release_build_uses_rpm_glibc_baseline(self) -> None:
        """Ensure release build uses RPM-compatible glibc baseline container."""
        snippets = "\n".join(
            snippet
            for snippet_list in RELEASE_BUILD_GLIBC_SNIPPETS.values()
            for snippet in snippet_list
        )
        assert "container: almalinux:9" in snippets
        assert "ARG OS_BASE=almalinux:9" in snippets
        assert "install-verified-rustup.sh" in snippets
        assert "--toolchain none" in snippets
        assert "COPY rust-toolchain.toml /src/rust-toolchain.toml" in snippets
        assert "rustup toolchain install" in snippets
