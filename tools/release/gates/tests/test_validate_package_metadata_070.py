"""Tests for release gate validator: NGINX version extraction.

Run:
    python3 -m pytest tools/release/gates/tests/test_validate_package_metadata_070.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the tools package is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from tools.release.gates.validate_package_metadata_070 import (  # noqa: E402
    FORBIDDEN_NAKED_EXACT_NGINX_DEPS,
    GATE3_LOCAL_ARCH_SNIPPETS,
    NFPM_DEB_ONLY_MODULES_AVAILABLE_PATTERN,
    NFPM_POSTINSTALL_FORBIDDEN_SNIPPETS,
    NFPM_REQUIRED_SNIPPETS,
    NFPM_POSTINSTALL_SNIPPETS,
    SMOKE_RPM_REPO_SNIPPETS,
    RELEASE_BUILD_GLIBC_SNIPPETS,
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
        assert _is_nginx_version("1.25.5") is True

    def test_two_parts_rejected(self) -> None:
        assert _is_nginx_version("1.25") is False

    def test_four_parts_rejected(self) -> None:
        assert _is_nginx_version("1.25.5.1") is False

    def test_non_numeric_rejected(self) -> None:
        assert _is_nginx_version("mainline") is False

    def test_mixed_rejected(self) -> None:
        assert _is_nginx_version("1.25.x") is False

    def test_empty_string_rejected(self) -> None:
        assert _is_nginx_version("") is False


# ---------------------------------------------------------------------------
# _strip_unquoted_comment
# ---------------------------------------------------------------------------


class TestStripUnquotedComment:
    """Validate comment stripping preserves quoted content."""

    def test_no_comment(self) -> None:
        assert _strip_unquoted_comment("NGINX_VERSION=1.25.5") == "NGINX_VERSION=1.25.5"

    def test_simple_comment(self) -> None:
        assert _strip_unquoted_comment("NGINX_VERSION=1.25.5 # active") == "NGINX_VERSION=1.25.5 "

    def test_hash_inside_double_quotes_preserved(self) -> None:
        assert _strip_unquoted_comment('"value#with#hash" # comment') == '"value#with#hash" '

    def test_hash_inside_single_quotes_preserved(self) -> None:
        assert _strip_unquoted_comment("'value#hash' # comment") == "'value#hash' "

    def test_escaped_quote_inside_double_quotes(self) -> None:
        assert _strip_unquoted_comment('"value\\"#still" # comment') == '"value\\"#still" '


# ---------------------------------------------------------------------------
# _unquote
# ---------------------------------------------------------------------------


class TestUnquote:
    """Validate quote and comma stripping."""

    def test_double_quoted(self) -> None:
        assert _unquote('"1.25.5"') == "1.25.5"

    def test_single_quoted(self) -> None:
        assert _unquote("'1.25.5'") == "1.25.5"

    def test_trailing_comma(self) -> None:
        assert _unquote('"1.25.5",') == "1.25.5"

    def test_whitespace_stripped(self) -> None:
        assert _unquote('  "1.25.5"  ') == "1.25.5"

    def test_unquoted_value(self) -> None:
        assert _unquote("1.25.5") == "1.25.5"


# ---------------------------------------------------------------------------
# _split_inline_list
# ---------------------------------------------------------------------------


class TestSplitInlineList:
    """Validate YAML-style inline list splitting."""

    def test_double_quoted_items(self) -> None:
        assert _split_inline_list('"1.25.5", "1.26.1"') == ["1.25.5", "1.26.1"]

    def test_single_quoted_items(self) -> None:
        assert _split_inline_list("'1.25.5', '1.26.1'") == ["1.25.5", "1.26.1"]

    def test_unquoted_items(self) -> None:
        assert _split_inline_list("1.25.5, 1.26.1") == ["1.25.5", "1.26.1"]

    def test_mixed_quoting(self) -> None:
        assert _split_inline_list('"1.25.5", 1.26.1') == ["1.25.5", "1.26.1"]

    def test_single_item(self) -> None:
        assert _split_inline_list('"1.25.5"') == ["1.25.5"]

    def test_empty_string(self) -> None:
        assert _split_inline_list("") == []


# ---------------------------------------------------------------------------
# extract_nginx_versions — supported formats
# ---------------------------------------------------------------------------


class TestExtractNginxVersions:
    """Validate NGINX version extraction from all supported formats."""

    def test_yaml_array_double_quoted(self) -> None:
        content = 'nginx_version: ["1.25.5", "1.26.1"]'
        assert extract_nginx_versions(content) == {"1.25.5", "1.26.1"}

    def test_yaml_array_single_quoted(self) -> None:
        content = "nginx_version: ['1.25.5', '1.26.1']"
        assert extract_nginx_versions(content) == {"1.25.5", "1.26.1"}

    def test_shell_double_quoted(self) -> None:
        content = 'NGINX_VERSION="1.27.4"'
        assert extract_nginx_versions(content) == {"1.27.4"}

    def test_shell_single_quoted(self) -> None:
        content = "NGINX_VERSION='1.29.1'"
        assert extract_nginx_versions(content) == {"1.29.1"}

    def test_dockerfile_arg(self) -> None:
        content = "ARG NGINX_VERSION=1.28.0"
        assert extract_nginx_versions(content) == {"1.28.0"}

    def test_all_supported_formats_combined(self) -> None:
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
        content = '''
# nginx_version: ["9.9.9"]
NGINX_VERSION="1.27.4" # active version
'''
        assert extract_nginx_versions(content) == {"1.27.4"}

    def test_rejects_invalid_versions(self) -> None:
        content = '''
nginx_version: ["1.25", "mainline", "1.26.1"]
NGINX_VERSION="latest"
ARG NGINX_VERSION=1.28
'''
        assert extract_nginx_versions(content) == {"1.26.1"}

    def test_empty_content(self) -> None:
        assert extract_nginx_versions("") == set()

    def test_no_versions(self) -> None:
        content = "some random content without versions"
        assert extract_nginx_versions(content) == set()

    def test_yaml_array_with_spaces(self) -> None:
        content = 'nginx_version:   [  "1.25.5"  ,  "1.26.1"  ]'
        assert extract_nginx_versions(content) == {"1.25.5", "1.26.1"}

    def test_deduplication(self) -> None:
        content = '''
NGINX_VERSION="1.25.5"
ARG NGINX_VERSION=1.25.5
'''
        assert extract_nginx_versions(content) == {"1.25.5"}

    def test_yaml_no_closing_bracket(self) -> None:
        content = 'nginx_version: ["1.25.5", "1.26.1"'
        assert extract_nginx_versions(content) == set()

    def test_arg_without_space_prefix(self) -> None:
        content = "ARGNGINX_VERSION=1.25.5"
        assert extract_nginx_versions(content) == set()

    def test_version_with_inline_comment(self) -> None:
        content = 'NGINX_VERSION="1.25.5" # pinned to stable'
        assert extract_nginx_versions(content) == {"1.25.5"}


# ---------------------------------------------------------------------------
# Large adversarial input
# ---------------------------------------------------------------------------


class TestLargeInputSafety:
    """Ensure linear-time parsing on adversarial input."""

    def test_large_noisy_yaml_array(self) -> None:
        noisy_line = (
            "nginx_version: ["
            + ",".join(["not-a-version"] * 10_000)
            + "]"
        )
        content = noisy_line + '\nNGINX_VERSION="1.27.4"\n'
        assert extract_nginx_versions(content) == {"1.27.4"}

    def test_large_noisy_shell_declarations(self) -> None:
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
        assert _contains_make_build_command("make build") is True

    def test_detects_indented_make_build(self) -> None:
        assert _contains_make_build_command("    make build") is True

    def test_detects_multiple_spaces(self) -> None:
        assert _contains_make_build_command("make     build") is True

    def test_detects_make_build_with_args(self) -> None:
        assert _contains_make_build_command("make build RELEASE=1") is True

    def test_detects_make_build_with_multiple_args(self) -> None:
        assert _contains_make_build_command("make build all") is True

    def test_ignores_commented_make_build(self) -> None:
        assert _contains_make_build_command("# make build") is False

    def test_ignores_indented_commented_make_build(self) -> None:
        assert _contains_make_build_command("    # make build") is False

    def test_ignores_echo_make_build(self) -> None:
        assert _contains_make_build_command('echo "make build"') is False

    def test_ignores_percent_make_build(self) -> None:
        assert _contains_make_build_command("%make_build") is False

    def test_ignores_makebuild(self) -> None:
        assert _contains_make_build_command("makebuild") is False

    def test_ignores_make_builder(self) -> None:
        assert _contains_make_build_command("make builder") is False

    def test_ignores_make_test(self) -> None:
        assert _contains_make_build_command("make test") is False

    def test_detects_in_multiline_content(self) -> None:
        content = "# comment\nmake build\nmore stuff"
        assert _contains_make_build_command(content) is True

    def test_ignores_all_comments_in_multiline(self) -> None:
        content = "# make build\n  # make build\necho make build"
        assert _contains_make_build_command(content) is False

    def test_empty_content(self) -> None:
        assert _contains_make_build_command("") is False


# ---------------------------------------------------------------------------
# Release gate regression expectations
# ---------------------------------------------------------------------------


class TestReleaseGateSnippetExpectations:
    """Validate regression guard snippets for release/package review findings."""

    def test_nfpm_dependency_uses_non_exact_floor(self) -> None:
        assert 'nginx (>= ${NGINX_VERSION})' in NFPM_REQUIRED_SNIPPETS
        assert 'nginx (= ${NGINX_VERSION})' not in NFPM_REQUIRED_SNIPPETS
        assert "/usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so" in NFPM_REQUIRED_SNIPPETS
        assert "packager: deb" in NFPM_DEB_ONLY_MODULES_AVAILABLE_PATTERN

    def test_rpm_spec_dependency_uses_non_exact_floor(self) -> None:
        assert "Requires:       nginx >= %{nginx_version}" in STANDALONE_RPM_SPEC_SNIPPETS
        assert "Requires:       nginx = %{nginx_version}" in FORBIDDEN_NAKED_EXACT_NGINX_DEPS
        assert "/usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so" in STANDALONE_RPM_SPEC_SNIPPETS

    def test_standalone_workflows_validate_input_version(self) -> None:
        validator = './packaging/scripts/validate-version.sh "${{ inputs.version }}"'
        assert validator in STANDALONE_DEB_SNIPPETS
        assert validator in STANDALONE_RPM_WORKFLOW_SNIPPETS

    def test_nfpm_postinstall_doc_path_matches_installed_layout(self) -> None:
        assert "/usr/share/doc/nginx-markdown-for-agents/README.md" in NFPM_POSTINSTALL_SNIPPETS
        assert (
            "/usr/share/doc/nginx-module-markdown-for-agents/README.md"
            in NFPM_POSTINSTALL_FORBIDDEN_SNIPPETS
        )

    def test_gate3_local_smoke_selects_arch_specific_packages(self) -> None:
        assert 'pkg_pattern="*_${ARCH}.deb"' in GATE3_LOCAL_ARCH_SNIPPETS
        assert 'pkg_pattern="*-1.${RPM_ARCH}.rpm"' in GATE3_LOCAL_ARCH_SNIPPETS

    def test_rpm_smoke_repo_selection_covers_amazon_linux(self) -> None:
        assert "amzn)" in SMOKE_RPM_REPO_SNIPPETS
        assert "packages/amzn/" in SMOKE_RPM_REPO_SNIPPETS
        assert "packages/centos/" in SMOKE_RPM_REPO_SNIPPETS

    def test_nfpm_postinstall_accepts_rpm_lifecycle_args(self) -> None:
        assert "configure|1|2)" in NFPM_POSTINSTALL_SNIPPETS
        assert "abort-upgrade|abort-remove|abort-deconfigure)" in NFPM_POSTINSTALL_SNIPPETS

    def test_release_build_uses_rpm_glibc_baseline(self) -> None:
        snippets = "\n".join(
            snippet
            for snippet_list in RELEASE_BUILD_GLIBC_SNIPPETS.values()
            for snippet in snippet_list
        )
        assert "container: almalinux:9" in snippets
        assert "ARG OS_BASE=almalinux:9" in snippets
