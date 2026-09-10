"""Tests for release gate validator: NGINX version extraction.

Run:
    python3 -m pytest tools/release/gates/tests/test_validate_package_metadata.py -v
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Ensure the tools package is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import tools.release.gates.validate_package_metadata as validator  # noqa: E402



# ---------------------------------------------------------------------------
# validator._is_nginx_version
# ---------------------------------------------------------------------------


class TestIsNginxVersion:
    """Validate strict three-part numeric version detection."""

    def test_valid_version(self) -> None:
        """Accept a standard three-part NGINX version string."""
        assert validator._is_nginx_version("1.25.5") is True

    def test_two_parts_rejected(self) -> None:
        """Reject version strings with only two numeric parts."""
        assert validator._is_nginx_version("1.25") is False

    def test_four_parts_rejected(self) -> None:
        """Reject version strings with four numeric parts."""
        assert validator._is_nginx_version("1.25.5.1") is False

    def test_non_numeric_rejected(self) -> None:
        """Reject non-numeric version strings like 'mainline'."""
        assert validator._is_nginx_version("mainline") is False

    def test_mixed_rejected(self) -> None:
        """Reject version strings mixing numeric and non-numeric parts."""
        assert validator._is_nginx_version("1.25.x") is False

    def test_empty_string_rejected(self) -> None:
        """Reject empty strings as invalid versions."""
        assert validator._is_nginx_version("") is False


# ---------------------------------------------------------------------------
# validator._strip_unquoted_comment
# ---------------------------------------------------------------------------


class TestStripUnquotedComment:
    """Validate comment stripping preserves quoted content."""

    def test_no_comment(self) -> None:
        """Return input unchanged when no comment marker is present."""
        assert validator._strip_unquoted_comment("NGINX_VERSION=1.25.5") == "NGINX_VERSION=1.25.5"

    def test_simple_comment(self) -> None:
        """Strip trailing unquoted comment after hash marker."""
        assert validator._strip_unquoted_comment("NGINX_VERSION=1.25.5 # active") == "NGINX_VERSION=1.25.5 "

    def test_hash_inside_double_quotes_preserved(self) -> None:
        """Preserve hash characters inside double-quoted strings."""
        assert validator._strip_unquoted_comment('"value#with#hash" # comment') == '"value#with#hash" '

    def test_hash_inside_single_quotes_preserved(self) -> None:
        """Preserve hash characters inside single-quoted strings."""
        assert validator._strip_unquoted_comment("'value#hash' # comment") == "'value#hash' "

    def test_escaped_quote_inside_double_quotes(self) -> None:
        """Preserve escaped quotes and their contained hash characters."""
        assert validator._strip_unquoted_comment('"value\\"#still" # comment') == '"value\\"#still" '


# ---------------------------------------------------------------------------
# validator._unquote
# ---------------------------------------------------------------------------


class TestUnquote:
    """Validate quote and comma stripping."""

    def test_double_quoted(self) -> None:
        """Strip surrounding double quotes from a value."""
        assert validator._unquote('"1.25.5"') == "1.25.5"

    def test_single_quoted(self) -> None:
        """Strip surrounding single quotes from a value."""
        assert validator._unquote("'1.25.5'") == "1.25.5"

    def test_trailing_comma(self) -> None:
        """Strip trailing comma after quoted value."""
        assert validator._unquote('"1.25.5",') == "1.25.5"

    def test_whitespace_stripped(self) -> None:
        """Strip leading and trailing whitespace from value."""
        assert validator._unquote('  "1.25.5"  ') == "1.25.5"

    def test_unquoted_value(self) -> None:
        """Return unquoted values unchanged after whitespace stripping."""
        assert validator._unquote("1.25.5") == "1.25.5"


# ---------------------------------------------------------------------------
# validator._split_inline_list
# ---------------------------------------------------------------------------


class TestSplitInlineList:
    """Validate YAML-style inline list splitting."""

    def test_double_quoted_items(self) -> None:
        """Split a comma-separated list of double-quoted items."""
        assert validator._split_inline_list('"1.25.5", "1.26.1"') == ["1.25.5", "1.26.1"]

    def test_single_quoted_items(self) -> None:
        """Split a comma-separated list of single-quoted items."""
        assert validator._split_inline_list("'1.25.5', '1.26.1'") == ["1.25.5", "1.26.1"]

    def test_unquoted_items(self) -> None:
        """Split a comma-separated list of unquoted items."""
        assert validator._split_inline_list("1.25.5, 1.26.1") == ["1.25.5", "1.26.1"]

    def test_mixed_quoting(self) -> None:
        """Split a list mixing quoted and unquoted items."""
        assert validator._split_inline_list('"1.25.5", 1.26.1') == ["1.25.5", "1.26.1"]

    def test_single_item(self) -> None:
        """Return a single-item list when input has no commas."""
        assert validator._split_inline_list('"1.25.5"') == ["1.25.5"]

    def test_empty_string(self) -> None:
        """Return empty list for empty input string."""
        assert validator._split_inline_list("") == []


# ---------------------------------------------------------------------------
# validator.extract_nginx_versions — supported formats
# ---------------------------------------------------------------------------


class TestExtractNginxVersions:
    """Validate NGINX version extraction from all supported formats."""

    def test_yaml_array_double_quoted(self) -> None:
        """Extract versions from a YAML inline array with double-quoted items."""
        content = 'nginx_version: ["1.25.5", "1.26.1"]'
        assert validator.extract_nginx_versions(content) == {"1.25.5", "1.26.1"}

    def test_yaml_array_single_quoted(self) -> None:
        """Extract versions from a YAML inline array with single-quoted items."""
        content = "nginx_version: ['1.25.5', '1.26.1']"
        assert validator.extract_nginx_versions(content) == {"1.25.5", "1.26.1"}

    def test_shell_double_quoted(self) -> None:
        """Extract version from a shell-style double-quoted assignment."""
        content = 'NGINX_VERSION="1.27.4"'
        assert validator.extract_nginx_versions(content) == {"1.27.4"}

    def test_shell_single_quoted(self) -> None:
        """Extract version from a shell-style single-quoted assignment."""
        content = "NGINX_VERSION='1.29.1'"
        assert validator.extract_nginx_versions(content) == {"1.29.1"}

    def test_dockerfile_arg(self) -> None:
        """Extract version from a Dockerfile ARG instruction."""
        content = "ARG NGINX_VERSION=1.28.0"
        assert validator.extract_nginx_versions(content) == {"1.28.0"}

    def test_all_supported_formats_combined(self) -> None:
        """Extract all versions when multiple formats appear in one file."""
        content = '''
nginx_version: ["1.25.5", "1.26.1"]
NGINX_VERSION="1.27.4"
ARG NGINX_VERSION=1.28.0
NGINX_VERSION='1.29.1'
'''
        assert validator.extract_nginx_versions(content) == {
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
        assert validator.extract_nginx_versions(content) == {"1.27.4"}

    def test_rejects_invalid_versions(self) -> None:
        """Filter out non-conforming version strings from extraction."""
        content = '''
nginx_version: ["1.25", "mainline", "1.26.1"]
NGINX_VERSION="latest"
ARG NGINX_VERSION=1.28
'''
        assert validator.extract_nginx_versions(content) == {"1.26.1"}

    def test_empty_content(self) -> None:
        """Return empty set for empty input content."""
        assert validator.extract_nginx_versions("") == set()

    def test_no_versions(self) -> None:
        """Return empty set when no version declarations exist."""
        content = "some random content without versions"
        assert validator.extract_nginx_versions(content) == set()

    def test_yaml_array_with_spaces(self) -> None:
        """Extract versions from YAML array with irregular whitespace."""
        content = 'nginx_version:   [  "1.25.5"  ,  "1.26.1"  ]'
        assert validator.extract_nginx_versions(content) == {"1.25.5", "1.26.1"}

    def test_deduplication(self) -> None:
        """Deduplicate identical versions from multiple declarations."""
        content = '''
NGINX_VERSION="1.25.5"
ARG NGINX_VERSION=1.25.5
'''
        assert validator.extract_nginx_versions(content) == {"1.25.5"}

    def test_yaml_no_closing_bracket(self) -> None:
        """Return empty set for malformed YAML array missing closing bracket."""
        content = 'nginx_version: ["1.25.5", "1.26.1"'
        assert validator.extract_nginx_versions(content) == set()

    def test_arg_without_space_prefix(self) -> None:
        """Reject ARGNGINX_VERSION without space separator from ARG keyword."""
        content = "ARGNGINX_VERSION=1.25.5"
        assert validator.extract_nginx_versions(content) == set()

    def test_version_with_inline_comment(self) -> None:
        """Extract version correctly when followed by an inline comment."""
        content = 'NGINX_VERSION="1.25.5" # pinned to stable'
        assert validator.extract_nginx_versions(content) == {"1.25.5"}

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

        assert validator.extract_nginx_versions(content) == {"1.30.2"}


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
        assert validator.extract_nginx_versions(content) == {"1.27.4"}

    def test_large_noisy_shell_declarations(self) -> None:
        """Handle large set of non-matching shell declarations efficiently."""
        lines = [f'NOT_NGINX_VERSION="val{i}"' for i in range(5_000)]
        lines.append('NGINX_VERSION="1.27.4"')
        content = "\n".join(lines)
        assert validator.extract_nginx_versions(content) == {"1.27.4"}


# ---------------------------------------------------------------------------
# validator._contains_make_build_command
# ---------------------------------------------------------------------------


class TestContainsMakeBuildCommand:
    """Validate active make build command detection without regex."""

    def test_detects_simple_make_build(self) -> None:
        """Detect a plain 'make build' command."""
        assert validator._contains_make_build_command("make build") is True

    def test_detects_indented_make_build(self) -> None:
        """Detect 'make build' with leading whitespace."""
        assert validator._contains_make_build_command("    make build") is True

    def test_detects_multiple_spaces(self) -> None:
        """Detect 'make build' with multiple spaces between tokens."""
        assert validator._contains_make_build_command("make     build") is True

    def test_detects_make_build_with_args(self) -> None:
        """Detect 'make build' followed by arguments."""
        assert validator._contains_make_build_command("make build RELEASE=1") is True

    def test_detects_make_build_with_multiple_args(self) -> None:
        """Detect 'make build' with multiple trailing arguments."""
        assert validator._contains_make_build_command("make build all") is True

    def test_ignores_commented_make_build(self) -> None:
        """Ignore 'make build' that is commented out."""
        assert validator._contains_make_build_command("# make build") is False

    def test_ignores_indented_commented_make_build(self) -> None:
        """Ignore indented commented-out 'make build'."""
        assert validator._contains_make_build_command("    # make build") is False

    def test_ignores_echo_make_build(self) -> None:
        """Ignore 'make build' appearing inside an echo statement."""
        assert validator._contains_make_build_command('echo "make build"') is False

    def test_ignores_percent_make_build(self) -> None:
        """Ignore '%make_build' which is not a valid make command."""
        assert validator._contains_make_build_command("%make_build") is False

    def test_ignores_makebuild(self) -> None:
        """Ignore 'makebuild' without space separator."""
        assert validator._contains_make_build_command("makebuild") is False

    def test_ignores_make_builder(self) -> None:
        """Ignore 'make builder' which is not the 'build' target."""
        assert validator._contains_make_build_command("make builder") is False

    def test_ignores_make_test(self) -> None:
        """Ignore 'make test' which is a different target."""
        assert validator._contains_make_build_command("make test") is False

    def test_detects_in_multiline_content(self) -> None:
        """Detect 'make build' within multiline content."""
        content = "# comment\nmake build\nmore stuff"
        assert validator._contains_make_build_command(content) is True

    def test_ignores_all_comments_in_multiline(self) -> None:
        """Ignore all commented or non-command occurrences in multiline content."""
        content = "# make build\n  # make build\necho make build"
        assert validator._contains_make_build_command(content) is False

    def test_empty_content(self) -> None:
        """Return False for empty input content."""
        assert validator._contains_make_build_command("") is False


# ---------------------------------------------------------------------------
# Release gate regression expectations
# ---------------------------------------------------------------------------


class TestReleaseGateSnippetExpectations:
    """Validate regression guard snippets for release/package review findings."""

    def test_nfpm_deb_dependency_preserves_exact_nginx_abi(self) -> None:
        """DEB dependency pins the exact upstream version as a closed interval.

        NGINX dynamic modules require an exact version match.  The DEB
        dependency must keep the `>= floor` (distro revisions of the pinned
        version stay installable) and add the exclusive `<<` ceiling, so a
        plain NGINX patch upgrade can no longer satisfy the dependency and
        strand the module.  The gate enforces this semantically (parsed
        constraints probed with dpkg-compatible comparisons), not via literal
        snippet matching.
        """
        assert "nginx (>= ${NGINX_VERSION})" not in validator.NFPM_REQUIRED_SNIPPETS
        assert "nginx >= ${RPM_NGINX_EVR}" in validator.NFPM_REQUIRED_SNIPPETS
        assert "nginx < ${RPM_NGINX_EVR_CEIL}" in validator.NFPM_REQUIRED_SNIPPETS

        nfpm_content = validator.NFPM_CONFIG.read_text(encoding="utf-8")
        contract_ok, contract_errors = validator.validate_nfpm_deb_dependency_contract(
            nfpm_content
        )
        assert contract_ok, contract_errors

        # The contract must fail an interval-less dependency (the historical
        # floor-only shape that let `apt upgrade` strand the module).
        floor_only = nfpm_content.replace('      - "nginx (<< ${NGINX_VERSION_CEIL})"\n', "")
        assert not validator.validate_nfpm_deb_dependency_contract(floor_only)[0]
        assert "/usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so" in validator.NFPM_REQUIRED_SNIPPETS
        assert "packager: deb" in validator.NFPM_DEB_ONLY_MODULES_AVAILABLE_PATTERN

    def test_dpkg_version_ordering_matches_debian_semantics(self) -> None:
        """dpkg-compatible ordering: distro suffixes and numeric segments."""
        satisfies = validator._dpkg_version_satisfies
        assert satisfies("1.28.3-1~bookworm", ">=", "1.28.3")
        assert satisfies("1.28.3-2~bookworm", ">=", "1.28.3-1~bookworm")
        assert satisfies("1.28.3-1~bookworm", "<", "1.28.3-1")
        assert satisfies("1.28.3-1", ">", "1.28.3")
        assert satisfies("1.28.3~rc1", "<", "1.28.3")
        assert not satisfies("1.28.4", "<<", "1.28.4")
        assert satisfies("1.28.3", "<<", "1.28.4")
        assert satisfies("1.28.10", ">>", "1.28.9")
        assert satisfies("1:1.28.3", ">=", "1.28.3")

    def test_deb_dependency_contract_uses_each_matrix_version(self) -> None:
        """Probe the interval for versions beyond the historical fixture."""
        nfpm_content = validator.NFPM_CONFIG.read_text(encoding="utf-8")

        contract_ok, errors = validator.validate_nfpm_deb_dependency_contract(
            nfpm_content,
            nginx_versions={"1.28.9", "1.28.10"},
        )

        assert contract_ok, errors

    def test_rpm_spec_dependency_uses_exact_nginx_version(self) -> None:
        """Ensure RPM spec pins the EXACT NGINX version (epoch-aware) and correct module path.

        NGINX dynamic modules require an exact version match; the core
        loader rejects any difference (including patch) before signature
        checks. The RPM metadata must require the official nginx-r capability
        as well as express a closed floor-and-ceiling interval between the
        pinned version and the next patch, never a floor-only branch-scoped
        dependency and never a naked exact dep without the epoch.
        """
        assert "nginx-r${NGINX_VERSION}" in validator.NFPM_REQUIRED_SNIPPETS
        assert (
            "Requires:       nginx-r%{nginx_version}"
            in validator.STANDALONE_RPM_SPEC_SNIPPETS
        )
        assert "Requires:       nginx >= 1:%{nginx_version}" in validator.STANDALONE_RPM_SPEC_SNIPPETS
        assert "Conflicts:      nginx >= 1:%{nginx_version_ceil}" in validator.STANDALONE_RPM_SPEC_SNIPPETS
        assert "nginx = 1:%{nginx_version}" not in validator.STANDALONE_RPM_SPEC_SNIPPETS
        assert "Requires:       nginx = %{nginx_version}" in validator.FORBIDDEN_NAKED_EXACT_NGINX_DEPS
        assert "/usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so" in validator.STANDALONE_RPM_SPEC_SNIPPETS
        assert "PATH=/usr/sbin:/usr/bin:/sbin:/bin" in validator.STANDALONE_RPM_SPEC_SNIPPETS
        assert "NGINX_BIN=/usr/sbin/nginx" in validator.STANDALONE_RPM_SPEC_SNIPPETS
        assert "SED_BIN=/usr/bin/sed" in validator.STANDALONE_RPM_SPEC_SNIPPETS
        assert '"$NGINX_BIN" -v 2>&1 | "$SED_BIN" -n' in validator.STANDALONE_RPM_SPEC_SNIPPETS

    def test_preremove_regex_is_shell_ere_safe(self) -> None:
        """Keep the module-load regex free of invalid ERE quote escapes."""
        content = validator.NFPM_PREREMOVE.read_text(encoding="utf-8")

        assert "MODULE_REFERENCE_PATTERN='" in content
        assert r"ngx_http_markdown_filter_module\.so" in content
        assert r"ngx_http_markdown_filter_module\\.so" not in content

    def test_standalone_rpm_workflow_validate_input_version(self) -> None:
        """Ensure workflow expressions are isolated from shell evaluation."""
        env_binding = "INPUT_VERSION: ${{ inputs.version }}"
        validator_cmd = './packaging/scripts/validate-version.sh "$INPUT_VERSION"'
        direct_interpolation = (
            './packaging/scripts/validate-version.sh "${{ inputs.version }}"'
        )

        snippets = validator.STANDALONE_RPM_WORKFLOW_SNIPPETS
        assert env_binding in snippets
        assert validator_cmd in snippets
        assert "NGINX_VERSION: ${{ steps.nginx_version.outputs.version }}" in snippets
        assert "packaging/nfpm/scripts/render-nfpm-config.sh" in snippets
        assert '"/tmp/${TARBALL_DIR}/preremove.sh"' in snippets
        assert direct_interpolation not in snippets
        assert direct_interpolation in validator.STANDALONE_VERSION_FORBIDDEN_SNIPPETS

    def test_standalone_rpm_workflow_requires_rendered_preremove(
        self, monkeypatch
    ) -> None:
        """The standalone tarball must not carry an unresolved template."""
        output_path = '"/tmp/${TARBALL_DIR}/preremove.sh"'
        removed_snippets = {
            output_path,
            validator.STANDALONE_RPM_PREREMOVE_RENDER_SNIPPET,
        }
        content = "\n".join(
            snippet
            for snippet in validator.STANDALONE_RPM_WORKFLOW_SNIPPETS
            if snippet not in removed_snippets
        )
        monkeypatch.setattr(validator, "read_safe", lambda _path: content)
        result = validator.ValidationResult()

        validator._validate_standalone_rpm_workflow(result)

        assert result.has_failures

    def test_standalone_rpm_workflow_rejects_direct_expression_in_shell(
        self, monkeypatch
    ) -> None:
        """Fail when a workflow expression is interpolated into shell source."""
        direct_interpolation = (
            './packaging/scripts/validate-version.sh "${{ inputs.version }}"'
        )
        content = "\n".join([
            *validator.STANDALONE_RPM_WORKFLOW_SNIPPETS,
            direct_interpolation,
        ])
        monkeypatch.setattr(validator, "read_safe", lambda _path: content)
        result = validator.ValidationResult()

        validator._validate_standalone_rpm_workflow(result)

        assert any(
            status == "FAIL" and ":forbid:" in check_id
            for status, check_id, _message in result.results
        )

    def test_checksum_signing_uses_immutable_checkout_and_release_environment(self) -> None:
        """Ensure canonical checksum signing binds secrets to the prepared commit."""
        snippets = validator.RELEASE_CHECKSUM_SIGNING_SECURITY_SNIPPETS
        assert "integrity-signature:" in snippets
        assert "environment: release-signing" in snippets
        assert "ref: ${{ github.sha }}" in snippets
        assert "persist-credentials: false" in snippets
        assert (
            './packaging/scripts/gpg-sign-checksums.sh artifacts/SHA256SUMS "${GPG_KEY_ID}"'
            in snippets
        )

    def test_checksum_signing_forbids_caller_selected_ref_checkout(self) -> None:
        """Ensure signing workflow cannot reintroduce caller-selected checkout."""
        assert (
            "ref: ${{ inputs.version }}"
            in validator.RELEASE_CHECKSUM_SIGNING_FORBIDDEN_SNIPPETS
        )

    def test_checksum_signing_validator_fails_when_live_job_is_missing(
        self, monkeypatch
    ) -> None:
        """Do not pass when the canonical signing job is absent."""
        monkeypatch.setattr(validator, "read_safe", lambda _path: "name: unrelated")
        result = validator.ValidationResult()

        validator._validate_release_checksum_signing_security(result)

        assert result.has_failures

    def test_checksum_signing_validator_checks_the_live_job(self, monkeypatch) -> None:
        """Validate the canonical release-packages signing job contract."""
        job = "\n  integrity-signature:\n" + "\n".join(
            f"    {snippet}" for snippet in validator.RELEASE_CHECKSUM_SIGNING_SECURITY_SNIPPETS
        ) + "\n  publish:\n"
        monkeypatch.setattr(validator, "read_safe", lambda _path: job)
        result = validator.ValidationResult()

        validator._validate_release_checksum_signing_security(result)

        assert not result.has_failures, result.results

    def test_nfpm_postinstall_doc_path_matches_installed_layout(self) -> None:
        """Ensure postinstall doc path matches the installed package layout."""
        assert "/usr/share/doc/nginx-markdown-for-agents/README.md" in validator.NFPM_POSTINSTALL_SNIPPETS
        assert (
            "/usr/share/doc/nginx-module-markdown-for-agents/README.md"
            in validator.NFPM_POSTINSTALL_FORBIDDEN_SNIPPETS
        )

    def test_installation_index_is_not_treated_as_package_surface(self) -> None:
        """Keep the short legacy index out of the canonical package checks."""
        assert validator.PROJECT_ROOT / "docs" / "guides" / "INSTALL.md" not in validator.MODULE_NAME_SURFACES
        assert validator.PACKAGE_INSTALLATION_DOC in validator.MODULE_NAME_SURFACES

    def test_gate3_local_smoke_selects_arch_specific_packages(self) -> None:
        """Ensure gate3 local smoke uses architecture-specific package patterns."""
        assert 'pkg_pattern="*_${ARCH}.deb"' in validator.GATE3_LOCAL_ARCH_SNIPPETS
        assert 'pkg_pattern="*-1.${RPM_ARCH}.rpm"' in validator.GATE3_LOCAL_ARCH_SNIPPETS

    def test_rpm_smoke_repo_selection_covers_amazon_linux(self) -> None:
        """Ensure RPM smoke repo selection includes Amazon Linux and CentOS paths."""
        assert "amzn)" in validator.SMOKE_RPM_REPO_SNIPPETS
        assert "nginx_repo_channel()" in validator.SMOKE_RPM_REPO_SNIPPETS
        assert "packages/%samzn/" in validator.SMOKE_RPM_REPO_SNIPPETS
        assert "packages/%scentos/" in validator.SMOKE_RPM_REPO_SNIPPETS

    def test_rpm_smoke_install_resolves_package_dependencies(self) -> None:
        """Ensure RPM smoke tests use dnf/yum instead of raw rpm installation."""
        assert (
            'dnf install -y "${PACKAGE_FILE}"'
            in validator.SMOKE_RPM_INSTALL_SNIPPETS
        )
        assert (
            'yum install -y "${PACKAGE_FILE}"'
            in validator.SMOKE_RPM_INSTALL_SNIPPETS
        )

        result = validator.ValidationResult()
        validator.validate_smoke_test_rpm_install(result)
        assert not result.has_failures

    def test_package_smoke_covers_real_removal_lifecycle(self) -> None:
        """Ensure package smoke tests exercise block-then-remove behavior."""
        result = validator.ValidationResult()
        validator.validate_smoke_test_removal_lifecycle(result)
        assert not result.has_failures, result.results

    def test_nfpm_postinstall_accepts_rpm_lifecycle_args(self) -> None:
        """Ensure postinstall script handles RPM lifecycle arguments."""
        assert "configure|1|2)" in validator.NFPM_POSTINSTALL_SNIPPETS
        assert "abort-upgrade|abort-remove|abort-deconfigure)" in validator.NFPM_POSTINSTALL_SNIPPETS

    def test_package_removal_guard_is_fail_closed_for_deb_and_rpm(self) -> None:
        """Ensure both package formats block removal while the module is loaded."""
        result = validator.ValidationResult()
        validator.validate_nfpm_preremove_lifecycle(result)
        assert not result.has_failures, result.results
        assert "remove|0)" in validator.NFPM_PREREMOVE_SNIPPETS
        assert "no trailing newline" in validator.NFPM_PREREMOVE_SNIPPETS
        assert validator.RPM_FORCE_REMOVE_INSTRUCTION_SNIPPETS == [
            "printf '%s' 'nginx-markdown-module force-remove v1'",
            "sudo tee /etc/nginx/markdown-module-force-remove >/dev/null",
        ]
        assert "%preun" in validator.RPM_PREUN_SNIPPETS

    def test_release_build_uses_rpm_glibc_baseline(self) -> None:
        """Ensure release build uses RPM-compatible glibc baseline container."""
        snippets = "\n".join(
            snippet
            for snippet_list in validator.RELEASE_BUILD_GLIBC_SNIPPETS.values()
            for snippet in snippet_list
        )
        assert "container: almalinux@sha256:" in snippets
        assert "AlmaLinux 9 manifest" in snippets
        assert "ARG OS_BASE=almalinux@sha256:" in snippets
        assert "install-verified-rustup.sh" in snippets
        assert "--toolchain none" in snippets
        assert "COPY rust-toolchain.toml /src/rust-toolchain.toml" in snippets
        assert "rustup toolchain install" in snippets

    def test_release_build_requires_only_current_ffi_constructors(self) -> None:
        """Keep release symbol checks on the current FFI contract."""
        assert "markdown_streaming_new_with_code" in validator.RELEASE_RUST_BUILD_INVARIANTS
        assert "markdown_streaming_new" in validator.RETIRED_RELEASE_FFI_SYMBOLS
        assert "markdown_incremental_new" in validator.RETIRED_RELEASE_FFI_SYMBOLS
        assert "markdown_incremental_new_with_code" in validator.RETIRED_RELEASE_FFI_SYMBOLS
        assert "markdown_incremental_feed" in validator.RETIRED_RELEASE_FFI_SYMBOLS
        assert "markdown_incremental_finalize" in validator.RETIRED_RELEASE_FFI_SYMBOLS
        assert "markdown_incremental_free" in validator.RETIRED_RELEASE_FFI_SYMBOLS
        assert "markdown_streaming_free" in validator.RETIRED_RELEASE_FFI_SYMBOLS


# ---------------------------------------------------------------------------
# Module snippet best practices (NGINX dynamic-module loading)
# ---------------------------------------------------------------------------


class TestModuleSnippetBestPractices:
    """The shipped module snippets follow NGINX dynamic-module practice.

    NGINX loads dynamic modules only through a main-context ``load_module``
    directive, and a relative path there resolves against the NGINX *prefix*
    (not ``--modules-path``).  Each snippet must therefore use the form that
    actually resolves on its package family, must keep loading an explicit
    operator decision, and must not carry configuration directives.
    """

    def test_shipped_snippets_use_resolvable_form_and_document_main_context(
        self,
    ) -> None:
        for path in (validator.DEB_MODULE_SNIPPET, validator.RPM_MODULE_SNIPPET):
            content = path.read_text(encoding="utf-8")
            lowered = content.lower()
            assert "main context" in lowered, path
            assert "top level" in lowered, path
            assert "prefix" in lowered, path

        deb = validator.DEB_MODULE_SNIPPET.read_text(encoding="utf-8")
        rpm = validator.RPM_MODULE_SNIPPET.read_text(encoding="utf-8")
        # DEB ships the module only in /usr/lib/nginx/modules, so a relative
        # path would resolve under the prefix and miss the file.
        assert validator.MODULE_SNIPPET_DEB_LOAD_LINE in deb
        # nginx.org RPM packages ship /etc/nginx/modules -> modules-path, so the
        # relative form resolves.
        assert validator.MODULE_SNIPPET_RPM_LOAD_LINE in rpm

    def test_deb_snippet_is_active_while_rpm_snippet_stays_opt_in(self) -> None:
        deb = validator.DEB_MODULE_SNIPPET.read_text(encoding="utf-8")
        rpm = validator.RPM_MODULE_SNIPPET.read_text(encoding="utf-8")

        assert re.search(
            rf"^(?!#){re.escape(validator.MODULE_SNIPPET_DEB_LOAD_LINE)}",
            deb,
            re.MULTILINE,
        )
        assert validator.MODULE_SNIPPET_INACTIVE_LOAD_LINE in rpm
        assert not re.search(
            rf"^(?!#){re.escape(validator.MODULE_SNIPPET_RPM_LOAD_LINE)}",
            rpm,
            re.MULTILINE,
        )

    def test_validator_flags_active_rpm_directive(self, monkeypatch) -> None:
        """An auto-loaded RPM snippet must fail the gate."""

        def fake_read_safe(path: Path) -> str:
            if path == validator.RPM_MODULE_SNIPPET:
                return (
                    "# main context / top level, prefix-relative form\n"
                    "load_module modules/ngx_http_markdown_filter_module.so;\n"
                )
            return (
                "# main context / top level, prefix-relative notes\n"
                "load_module /usr/lib/nginx/modules/"
                "ngx_http_markdown_filter_module.so;\n"
            )

        monkeypatch.setattr(validator, "read_safe", fake_read_safe)
        result = validator.ValidationResult()
        validator.validate_module_snippet_best_practices(result)

        assert any(
            status == "FAIL" and check_id == "snippet:rpm:opt-in-loading"
            for status, check_id, _message in result.results
        )

    def test_validator_flags_unresolvable_module_path(self, monkeypatch) -> None:
        """A path form that cannot resolve on the family must fail the gate."""

        def fake_read_safe(path: Path) -> str:
            if path == validator.DEB_MODULE_SNIPPET:
                # Relative path: resolves under the prefix, where this package
                # installs nothing.
                return (
                    "# main context, top level of nginx.conf, prefix notes\n"
                    "load_module modules/ngx_http_markdown_filter_module.so;\n"
                )
            # Absolute RPM-family path: valid everywhere but not the form the
            # RPM snippet is specified to ship.
            return (
                "# main context, top level of nginx.conf, prefix notes\n"
                "load_module /usr/lib64/nginx/modules/"
                "ngx_http_markdown_filter_module.so;\n"
            )

        monkeypatch.setattr(validator, "read_safe", fake_read_safe)
        result = validator.ValidationResult()
        validator.validate_module_snippet_best_practices(result)

        failures = [
            check_id
            for status, check_id, _message in result.results
            if status == "FAIL"
        ]
        assert "snippet:deb:load-module-form" in failures
        assert "snippet:rpm:load-module-form" in failures

    def test_validator_flags_conversion_directive_in_snippet(
        self, monkeypatch
    ) -> None:
        """Loader snippets must not carry response-conversion directives."""

        def fake_read_safe(_path: Path) -> str:
            return (
                "# main context, top level of nginx.conf, prefix notes\n"
                "load_module /usr/lib/nginx/modules/"
                "ngx_http_markdown_filter_module.so;\n"
                "markdown_filter on;\n"
            )

        monkeypatch.setattr(validator, "read_safe", fake_read_safe)
        result = validator.ValidationResult()
        validator.validate_module_snippet_best_practices(result)

        assert any(
            status == "FAIL"
            and check_id.endswith(":only-load-module-directive")
            for status, check_id, _message in result.results
        )

    def test_nfpm_ships_rpm_snippet_at_nginxorg_reference_path(self) -> None:
        """The RPM snippet is packaged for the RPM family only."""
        content = validator.NFPM_CONFIG.read_text(encoding="utf-8")
        assert re.search(validator.NFPM_RPM_ONLY_MODULES_PATTERN, content)

        without_entry = content.replace(
            '  - src: "./packaging/nfpm/modules/mod-markdown.conf"\n'
            '    dst: "/usr/share/nginx/modules/mod-markdown.conf"\n'
            "    type: config|noreplace\n"
            "    packager: rpm\n",
            "",
        )
        assert without_entry != content
        assert not re.search(validator.NFPM_RPM_ONLY_MODULES_PATTERN, without_entry)

class TestModuleBuildCompat:
    """Release modules must be configured with --with-compat."""

    def test_release_surfaces_keep_the_compat_flag(self) -> None:
        assert validator.WITH_COMPAT_FLAG == "--with-compat"
        assert validator.RELEASE_PACKAGES_WORKFLOW in validator.WITH_COMPAT_BUILD_SURFACES
        assert validator.RELEASE_RPM_WORKFLOW in validator.WITH_COMPAT_BUILD_SURFACES

        result = validator.ValidationResult()
        validator.validate_module_build_compat(result)
        assert not result.has_failures

    def test_validator_flags_a_surface_without_the_compat_flag(
        self, monkeypatch
    ) -> None:
        """A build surface that drops --with-compat must fail the gate."""

        def fake_read_safe(path: Path) -> str:
            if path == validator.RELEASE_PACKAGES_WORKFLOW:
                # A configure line without the compat flag still builds and
                # packages, so only this guard catches it.
                return "./configure --add-dynamic-module=components/nginx-module"
            return "--with-compat --add-dynamic-module=components/nginx-module"

        monkeypatch.setattr(validator, "read_safe", fake_read_safe)
        result = validator.ValidationResult()
        validator.validate_module_build_compat(result)

        assert any(
            status == "FAIL" and "build-compat" in check_id
            for status, check_id, _message in result.results
        )

class TestRpmSpecSourcesAreStaged:
    """Every RPM spec install source must reach the rpmbuild tarball."""

    def test_repository_spec_sources_are_all_staged(self) -> None:
        result = validator.ValidationResult()
        validator.validate_rpm_spec_sources_are_staged(result)
        assert not result.has_failures, [
            msg for status, _cid, msg in result.results if status == "FAIL"
        ]

    def test_validator_flags_a_source_missing_from_the_tarball(
        self, monkeypatch
    ) -> None:
        """A spec install line the workflow never stages must fail the gate."""

        def fake_read_safe(path: Path) -> str:
            if path == validator.RPM_SPEC:
                return (
                    "%install\n"
                    "install -m 0644 README.md \\\n"
                    "    %{buildroot}/usr/share/doc/nginx-markdown-for-agents/README.md\n"
                    "install -m 0644 packaging/nfpm/modules/mod-markdown.conf \\\n"
                    "    %{buildroot}/usr/share/nginx/modules/mod-markdown.conf\n"
                )
            # The workflow stages README.md only: the snippet is missing.
            return 'cp README.md "/tmp/${TARBALL_DIR}/"\n'

        monkeypatch.setattr(validator, "read_safe", fake_read_safe)
        result = validator.ValidationResult()
        validator.validate_rpm_spec_sources_are_staged(result)

        failures = [
            check_id
            for status, check_id, _message in result.results
            if status == "FAIL"
        ]
        assert "rpm-spec-sources:mod-markdown.conf" in failures
        assert not any("README" in cid for cid in failures)

class TestModuleSnippetEdgeCases:
    """Edge cases codex flagged in the snippet/compat gate rules."""

    def test_compat_flag_inside_a_comment_does_not_satisfy_the_gate(
        self, monkeypatch
    ) -> None:
        def fake_read_safe(_path: Path) -> str:
            return (
                "# remember to add --with-compat here\n"
                "./configure --add-dynamic-module=components/nginx-module\n"
            )

        monkeypatch.setattr(validator, "read_safe", fake_read_safe)
        result = validator.ValidationResult()
        validator.validate_module_build_compat(result)

        assert any(
            status == "FAIL" and check_id.startswith("build-compat:")
            for status, check_id, _message in result.results
        )

    def test_indented_active_loader_directive_defeats_opt_in(
        self, monkeypatch
    ) -> None:
        def fake_read_safe(path: Path) -> str:
            body = "# main context, top level of nginx.conf, prefix notes\n"
            if path == validator.RPM_MODULE_SNIPPET:
                # Commented form plus an indented live directive.
                return (
                    body
                    + "#load_module modules/ngx_http_markdown_filter_module.so;\n"
                    + "  load_module modules/ngx_http_markdown_filter_module.so;\n"
                )
            return body + validator.MODULE_SNIPPET_DEB_LOAD_LINE + "\n"

        monkeypatch.setattr(validator, "read_safe", fake_read_safe)
        result = validator.ValidationResult()
        validator.validate_module_snippet_best_practices(result)

        assert any(
            status == "FAIL" and check_id == "snippet:rpm:opt-in-loading"
            for status, check_id, _message in result.results
        )

    def test_comment_ending_in_a_backslash_does_not_hide_the_next_line(self) -> None:
        content = "# note: this comment ends with a backslash \\\nload_module y;\n"
        lines = validator._logical_lines(content)
        # The comment keeps the backslash and its own line, so the directive
        # that follows stays a separate logical line.
        assert lines[0] == "# note: this comment ends with a backslash \\"
        assert lines[1] == "load_module y;"

    def test_removal_command_referencing_the_tree_does_not_prove_staging(
        self, monkeypatch
    ) -> None:
        def fake_read(path: Path) -> str:
            if path == validator.RPM_SPEC:
                return (
                    "%install\n"
                    "install -m 0644 packaging/nfpm/modules/mod-markdown.conf "
                    "%{buildroot}/usr/share/nginx/modules/mod-markdown.conf\n"
                    "%files\n"
                    "%config(noreplace) /usr/share/nginx/modules/mod-markdown.conf\n"
                )
            return 'rm -f "/tmp/${TARBALL_DIR}/packaging/nfpm/modules/mod-markdown.conf"\n'

        monkeypatch.setattr(validator, "read_safe", fake_read)
        result = validator.ValidationResult()
        validator.validate_rpm_spec_sources_are_staged(result)

        assert any(
            status == "FAIL" and check_id.endswith("mod-markdown.conf")
            for status, check_id, _message in result.results
        )

    def test_comment_destination_does_not_satisfy_the_install_check(
        self, monkeypatch
    ) -> None:
        def fake_read(path: Path) -> str:
            if path == validator.RPM_SPEC:
                # The packaged path appears only inside a trailing comment, and
                # the real destination is a different directory.
                return (
                    "%install\n"
                    "install -m 0644 packaging/nfpm/modules/mod-markdown.conf "
                    "%{buildroot}/tmp/ # %{buildroot}/usr/share/nginx/modules/mod-markdown.conf\n"
                    "%files\n"
                    "%config(noreplace) /usr/share/nginx/modules/mod-markdown.conf\n"
                )
            return ""

        monkeypatch.setattr(validator, "read_safe", fake_read)
        result = validator.ValidationResult()
        validator.validate_rpm_spec_snippet(result)

        assert any(
            status == "FAIL" and check_id == "rpm:modules:install"
            for status, check_id, _message in result.results
        )

    def test_directory_qualified_look_alike_does_not_prove_staging(self) -> None:
        tokens = [
            "cp",
            "vendor/packaging/nfpm/modules/mod-markdown.conf",
            "/tmp/${TARBALL_DIR}/packaging/nfpm/modules/",
        ]
        assert not validator._is_staging_command(
            tokens, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_bare_spec_name_accepts_the_built_artifact(self) -> None:
        tokens = [
            "cp",
            "build/ngx_http_markdown_filter_module.so",
            "/tmp/${TARBALL_DIR}/",
        ]
        assert validator._is_staging_command(tokens, "ngx_http_markdown_filter_module.so")

    def test_commented_staging_destination_does_not_prove_staging(self) -> None:
        workflow = (
            "cp packaging/nfpm/modules/mod-markdown.conf /tmp/elsewhere "
            '# "/tmp/${TARBALL_DIR}/packaging/nfpm/modules/"\n'
        )
        assert not validator._workflow_stages_into_tarball(
            workflow, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_commented_source_does_not_prove_staging(self) -> None:
        workflow = (
            "cp /tmp/elsewhere/mod-markdown.conf "
            '# packaging/nfpm/modules/mod-markdown.conf "/tmp/${TARBALL_DIR}/"\n'
        )
        assert not validator._workflow_stages_into_tarball(
            workflow, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_staging_under_another_name_does_not_prove_staging(self) -> None:
        tokens = [
            "cp",
            "packaging/nfpm/modules/mod-markdown.conf",
            "/tmp/${TARBALL_DIR}/renamed.conf",
        ]
        assert not validator._is_staging_command(
            tokens, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_symlink_does_not_prove_staging(self) -> None:
        tokens = ["ln", "-s", "packaging/nfpm/modules/mod-markdown.conf", "${TARBALL_DIR}/"]
        assert not validator._is_staging_command(
            tokens, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_function_body_install_does_not_satisfy_the_snippet_check(
        self, monkeypatch
    ) -> None:
        def fake_read(path: Path) -> str:
            if path == validator.RPM_SPEC:
                # The install sits in a function that is never called.
                return (
                    "%install\n"
                    "stage_snippet() {\n"
                    "  install -m 0644 packaging/nfpm/modules/mod-markdown.conf "
                    "%{buildroot}/usr/share/nginx/modules/mod-markdown.conf\n"
                    "}\n"
                    "%files\n"
                    "%config(noreplace) /usr/share/nginx/modules/mod-markdown.conf\n"
                )
            return ""

        monkeypatch.setattr(validator, "read_safe", fake_read)
        result = validator.ValidationResult()
        validator.validate_rpm_spec_snippet(result)

        assert any(
            status == "FAIL" and check_id == "rpm:modules:install"
            for status, check_id, _message in result.results
        )

    def test_foreign_staging_root_does_not_prove_staging(self) -> None:
        source = "packaging/nfpm/modules/mod-markdown.conf"
        for destination in (
            "$STAGE_ROOT/${TARBALL_DIR}/packaging/nfpm/modules/",
            "./wrong/../${TARBALL_DIR}/packaging/nfpm/modules/",
            "/tmp/../../outside/${TARBALL_DIR}/packaging/nfpm/modules/",
        ):
            assert not validator._is_staging_command(
                ["cp", source, destination], source
            )

    def test_relative_source_after_a_directory_change_does_not_prove_staging(self) -> None:
        workflow = (
            "      - name: stage\n"
            "        run: |\n"
            "          cd /tmp\n"
            '          cp packaging/nfpm/modules/mod-markdown.conf "/tmp/${TARBALL_DIR}/packaging/nfpm/modules/"\n'
        )
        assert not validator._workflow_stages_into_tarball(
            workflow, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_directory_state_resets_at_a_new_step(self) -> None:
        workflow = (
            "      - name: build\n"
            "        run: |\n"
            "          cd components/rust-converter\n"
            "      - name: stage\n"
            "        run: |\n"
            '          cp packaging/nfpm/modules/mod-markdown.conf "/tmp/${TARBALL_DIR}/packaging/nfpm/modules/"\n'
        )
        assert validator._workflow_stages_into_tarball(
            workflow, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_guard_inside_a_function_group_is_tracked(self, monkeypatch) -> None:
        def fake_read(path: Path) -> str:
            if path == validator.RPM_SPEC:
                # The guard opens inside a group on the same line.
                return (
                    "%install\n"
                    "f() { if false; then install -m 0644 "
                    "packaging/nfpm/modules/mod-markdown.conf "
                    "%{buildroot}/usr/share/nginx/modules/mod-markdown.conf; fi; }\n"
                    "f\n"
                    "%files\n"
                    "%config(noreplace) /usr/share/nginx/modules/mod-markdown.conf\n"
                )
            return ""

        monkeypatch.setattr(validator, "read_safe", fake_read)
        result = validator.ValidationResult()
        validator.validate_rpm_spec_snippet(result)

        assert any(
            status == "FAIL" and check_id == "rpm:modules:install"
            for status, check_id, _message in result.results
        )

    def test_separator_inside_a_comment_stays_inactive(self) -> None:
        spec = (
            "%install\n"
            "echo ok # disabled; install -m 0644 "
            "packaging/nfpm/modules/mod-markdown.conf "
            "%{buildroot}/usr/share/nginx/modules/mod-markdown.conf\n"
        )
        assert validator._spec_install_sources(spec) == []

    def test_marker_without_a_path_boundary_does_not_prove_staging(self) -> None:
        tokens = [
            "cp",
            "packaging/nfpm/modules/mod-markdown.conf",
            "/tmp/${TARBALL_DIR}packaging/nfpm/modules/",
        ]
        assert not validator._is_staging_command(
            tokens, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_commented_install_after_a_separator_stays_inactive(self) -> None:
        spec = (
            "%install\n"
            "install -m 0644 packaging/nfpm/modules/mod-markdown.conf "
            "%{buildroot}/usr/share/nginx/modules/ # note; "
            "install -m 0644 other.conf %{buildroot}/tmp/\n"
        )
        sources = validator._spec_install_sources(spec)
        assert sources == ["packaging/nfpm/modules/mod-markdown.conf"]

    def test_marker_prefixed_by_text_does_not_prove_staging(self) -> None:
        tokens = [
            "cp",
            "packaging/nfpm/modules/mod-markdown.conf",
            "/tmp/prefix${TARBALL_DIR}/packaging/nfpm/modules/",
        ]
        assert not validator._is_staging_command(
            tokens, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_parent_relative_source_does_not_prove_staging(self) -> None:
        tokens = [
            "cp",
            "../vendored/packaging/nfpm/modules/mod-markdown.conf",
            "/tmp/${TARBALL_DIR}/packaging/nfpm/modules/",
        ]
        assert not validator._is_staging_command(
            tokens, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_single_dot_prefix_is_accepted(self) -> None:
        tokens = [
            "cp",
            "./packaging/nfpm/modules/mod-markdown.conf",
            "/tmp/${TARBALL_DIR}/packaging/nfpm/modules/",
        ]
        assert validator._is_staging_command(
            tokens, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_multiline_guarded_install_fails_the_snippet_check(
        self, monkeypatch
    ) -> None:
        def fake_read(path: Path) -> str:
            if path == validator.RPM_SPEC:
                # The guard opens on one line and closes on another.
                return (
                    "%install\n"
                    "if true; then\n"
                    "install -m 0644 packaging/nfpm/modules/mod-markdown.conf "
                    "%{buildroot}/usr/share/nginx/modules/mod-markdown.conf\n"
                    "fi\n"
                    "%files\n"
                    "%config(noreplace) /usr/share/nginx/modules/mod-markdown.conf\n"
                )
            return ""

        monkeypatch.setattr(validator, "read_safe", fake_read)
        result = validator.ValidationResult()
        validator.validate_rpm_spec_snippet(result)

        assert any(
            status == "FAIL" and check_id == "rpm:modules:install"
            for status, check_id, _message in result.results
        )

    def test_guarded_snippet_install_fails_the_check(self, monkeypatch) -> None:
        def fake_read(path: Path) -> str:
            if path == validator.RPM_SPEC:
                # The install sits inside a branch that never runs.
                return (
                    "%install\n"
                    "if false; then install -m 0644 packaging/nfpm/modules/mod-markdown.conf "
                    "%{buildroot}/usr/share/nginx/modules/mod-markdown.conf; fi\n"
                    "%files\n"
                    "%config(noreplace) /usr/share/nginx/modules/mod-markdown.conf\n"
                )
            return ""

        monkeypatch.setattr(validator, "read_safe", fake_read)
        result = validator.ValidationResult()
        validator.validate_rpm_spec_snippet(result)

        assert any(
            status == "FAIL" and check_id == "rpm:modules:install"
            for status, check_id, _message in result.results
        )

    def test_temporary_directory_does_not_prove_final_staging(self) -> None:
        tokens = [
            "cp",
            "packaging/nfpm/scripts/preremove.sh",
            "/tmp/${TARBALL_DIR}/.render/preremove.sh",
        ]
        assert not validator._is_staging_command(
            tokens, "packaging/nfpm/scripts/preremove.sh"
        )

    def test_single_quoted_staging_path_does_not_prove_staging(self) -> None:
        tokens = [
            "cp",
            "packaging/nfpm/modules/mod-markdown.conf",
            "'${TARBALL_DIR}/packaging/nfpm/modules/'",
        ]
        assert not validator._is_staging_command(
            tokens, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_escaped_variable_does_not_prove_staging(self) -> None:
        tokens = [
            "cp",
            "packaging/nfpm/modules/mod-markdown.conf",
            "\\${TARBALL_DIR}/packaging/nfpm/modules/",
        ]
        assert not validator._is_staging_command(
            tokens, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_rendered_copy_into_the_root_proves_staging(self) -> None:
        tokens = [
            "cp",
            "${RUNNER_TEMP:-/tmp}/markdown-render/preremove.sh",
            "/tmp/${TARBALL_DIR}/preremove.sh",
        ]
        assert validator._is_staging_command(tokens, "preremove.sh")

    def test_install_after_a_logical_and_is_parsed(self) -> None:
        spec = (
            "%install\n"
            "test -f present.conf && install -m 0644 present.conf %{buildroot}/etc/\n"
            "install -m 0644 missing.conf %{buildroot}/etc/ || exit 1\n"
        )
        assert validator._spec_install_sources(spec) == ["present.conf", "missing.conf"]

    def test_guarded_install_is_parsed(self) -> None:
        spec = (
            "%install\n"
            "install -m 0644 present.conf %{buildroot}/etc/\n"
            "if true; then install -m 0644 missing.conf %{buildroot}/etc/; fi\n"
        )
        assert validator._spec_install_sources(spec) == ["present.conf", "missing.conf"]

    def test_look_alike_name_does_not_prove_staging(self) -> None:
        tokens = [
            "cp",
            "packaging/nfpm/modules/mod-markdown.conf.bak",
            "/tmp/${TARBALL_DIR}/packaging/nfpm/modules/",
        ]
        assert not validator._is_staging_command(
            tokens, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_copy_from_another_directory_does_not_prove_staging(self) -> None:
        tokens = [
            "cp",
            "vendor/nfpm/modules/mod-markdown.conf",
            "/tmp/${TARBALL_DIR}/packaging/nfpm/modules/",
        ]
        assert not validator._is_staging_command(
            tokens, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_echo_of_the_tarball_path_does_not_prove_staging(self, monkeypatch) -> None:
        def fake_read(path: Path) -> str:
            if path == validator.RPM_SPEC:
                return (
                    "%install\n"
                    "install -m 0644 packaging/nfpm/modules/mod-markdown.conf \\\n"
                    "    %{buildroot}/usr/share/nginx/modules/mod-markdown.conf\n"
                    "%files\n"
                    "%config(noreplace) /usr/share/nginx/modules/mod-markdown.conf\n"
                )
            return (
                'echo "copied packaging/nfpm/modules/mod-markdown.conf into'
                ' ${TARBALL_DIR}/"\n'
            )

        monkeypatch.setattr(validator, "read_safe", fake_read)
        result = validator.ValidationResult()
        validator.validate_rpm_spec_sources_are_staged(result)

        assert any(
            status == "FAIL" and check_id.endswith("mod-markdown.conf")
            for status, check_id, _message in result.results
        )

    def test_multi_source_install_checks_every_source(self) -> None:
        spec = "%install\ninstall -m 0644 a.conf b.conf %{buildroot}/etc/nginx/modules/\n"
        assert validator._spec_install_sources(spec) == ["a.conf", "b.conf"]

    def test_source_and_destination_must_share_one_install_command(
        self, monkeypatch
    ) -> None:
        def fake_read(path: Path) -> str:
            if path == validator.RPM_SPEC:
                # The packaged path appears on a non-install line, which the
                # independent-substring check accepted.
                return (
                    "%install\n"
                    "install -m 0644 packaging/nfpm/modules/mod-markdown.conf %{buildroot}/tmp/\n"
                    "echo %{buildroot}/usr/share/nginx/modules/mod-markdown.conf\n"
                    "%files\n"
                    "%config(noreplace) /usr/share/nginx/modules/mod-markdown.conf\n"
                )
            return ""

        monkeypatch.setattr(validator, "read_safe", fake_read)
        result = validator.ValidationResult()
        validator.validate_rpm_spec_snippet(result)

        assert any(
            status == "FAIL" and check_id == "rpm:modules:install"
            for status, check_id, _message in result.results
        )

    def test_swapped_install_operands_fail_the_install_check(self, monkeypatch) -> None:
        def fake_read(path: Path) -> str:
            if path == validator.RPM_SPEC:
                # Source and destination are swapped: rpmbuild would look for the
                # buildroot path as its input.
                return (
                    "%install\n"
                    "install -m 0644 %{buildroot}/usr/share/nginx/modules/mod-markdown.conf "
                    "packaging/nfpm/modules/mod-markdown.conf\n"
                    "%files\n"
                    "%config(noreplace) /usr/share/nginx/modules/mod-markdown.conf\n"
                )
            return ""

        monkeypatch.setattr(validator, "read_safe", fake_read)
        result = validator.ValidationResult()
        validator.validate_rpm_spec_snippet(result)

        assert any(
            status == "FAIL" and check_id == "rpm:modules:install"
            for status, check_id, _message in result.results
        )

    def test_target_directory_equals_form_keeps_every_operand_a_source(self) -> None:
        spec = (
            "%install\n"
            "install --target-directory=%{buildroot}/usr/share/doc "
            "packaging/nfpm/modules/not-staged.conf\n"
        )
        assert validator._spec_install_sources(spec) == [
            "packaging/nfpm/modules/not-staged.conf"
        ]

    def test_similar_variable_name_does_not_prove_staging(self) -> None:
        tokens = [
            "cp",
            "packaging/nfpm/modules/mod-markdown.conf",
            "$NOT_TARBALL_DIR/modules/",
        ]
        assert not validator._is_staging_command(
            tokens, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_touch_does_not_prove_staging(self) -> None:
        tokens = [
            "touch",
            "packaging/nfpm/modules/mod-markdown.conf",
            "${TARBALL_DIR}/",
        ]
        assert not validator._is_staging_command(
            tokens, "packaging/nfpm/modules/mod-markdown.conf"
        )

    def test_prose_mentioning_the_loader_directive_defeats_the_contract(
        self, monkeypatch
    ) -> None:
        def fake_read_safe(path: Path) -> str:
            body = "# main context, top level of nginx.conf, prefix notes\n"
            if path == validator.RPM_MODULE_SNIPPET:
                return body + "#" + validator.MODULE_SNIPPET_RPM_LOAD_LINE + "\n"
            # The DEB snippet mentions the directive only inside a sentence: a
            # substring hit must not satisfy the loader contract.
            return (
                body
                + "# Enable conversion with "
                + validator.MODULE_SNIPPET_DEB_LOAD_LINE
                + "\n"
            )

        monkeypatch.setattr(validator, "read_safe", fake_read_safe)
        result = validator.ValidationResult()
        validator.validate_module_snippet_best_practices(result)

        assert any(
            status == "FAIL" and check_id == "snippet:deb:load-module-form"
            for status, check_id, _message in result.results
        )

    def test_block_form_directive_defeats_the_loader_only_rule(
        self, monkeypatch
    ) -> None:
        def fake_read_safe(path: Path) -> str:
            body = "# main context, top level of nginx.conf, prefix notes\n"
            if path == validator.RPM_MODULE_SNIPPET:
                return (
                    body
                    + "http {\n"
                    + validator.MODULE_SNIPPET_RPM_LOAD_LINE
                    + "\n}\n"
                )
            return body + validator.MODULE_SNIPPET_DEB_LOAD_LINE + "\n"

        monkeypatch.setattr(validator, "read_safe", fake_read_safe)
        result = validator.ValidationResult()
        validator.validate_module_snippet_best_practices(result)

        assert any(
            status == "FAIL"
            and check_id.endswith(":only-load-module-directive")
            for status, check_id, _message in result.results
        )

    def test_rpm_spec_must_install_and_ship_the_snippet(self, monkeypatch) -> None:
        def fake_read_safe(path: Path) -> str:
            if path == validator.RPM_SPEC:
                # The install line and the %files entry are both missing.
                return "Name: nginx-module-markdown-for-agents\n"
            if path == validator.NFPM_CONFIG:
                return (
                    '  - src: "./packaging/nfpm/modules/mod-markdown.conf"\n'
                    '    dst: "/usr/share/nginx/modules/mod-markdown.conf"\n'
                    "    type: config|noreplace\n"
                    "    packager: rpm\n"
                )
            return ""

        monkeypatch.setattr(validator, "read_safe", fake_read_safe)
        result = validator.ValidationResult()
        validator.validate_rpm_spec_snippet(result)

        failures = [
            check_id
            for status, check_id, _message in result.results
            if status == "FAIL"
        ]
        assert "rpm:modules:install" in failures
        assert "rpm:modules:files" in failures

class TestStagingProofAndSectionScoping:
    """Tightened rules: staging proof inside the tarball tree, section scoping."""

    def test_source_only_mentioned_in_a_comment_does_not_prove_staging(
        self, monkeypatch
    ) -> None:
        def fake_read_safe(path: Path) -> str:
            if path == validator.RPM_SPEC:
                return (
                    "%install\n"
                    "install -m 0644 packaging/nfpm/modules/mod-markdown.conf \\\n"
                    "    %{buildroot}/usr/share/nginx/modules/mod-markdown.conf\n"
                )
            # The path appears in a comment only.
            return '# TODO: copy packaging/nfpm/modules/mod-markdown.conf later\n'

        monkeypatch.setattr(validator, "read_safe", fake_read_safe)
        result = validator.ValidationResult()
        validator.validate_rpm_spec_sources_are_staged(result)

        assert any(
            status == "FAIL" and check_id.endswith("mod-markdown.conf")
            for status, check_id, _message in result.results
        )

    def test_source_copied_outside_the_tarball_does_not_prove_staging(
        self, monkeypatch
    ) -> None:
        def fake_read_safe(path: Path) -> str:
            if path == validator.RPM_SPEC:
                return (
                    "%install\n"
                    "install -m 0644 packaging/nfpm/modules/mod-markdown.conf \\\n"
                    "    %{buildroot}/usr/share/nginx/modules/mod-markdown.conf\n"
                )
            return "cp packaging/nfpm/modules/mod-markdown.conf /somewhere/else/\n"

        monkeypatch.setattr(validator, "read_safe", fake_read_safe)
        result = validator.ValidationResult()
        validator.validate_rpm_spec_sources_are_staged(result)

        assert any(status == "FAIL" for status, _cid, _msg in result.results)

    def test_files_entry_outside_the_files_section_fails(self, monkeypatch) -> None:
        def fake_read_safe(path: Path) -> str:
            if path == validator.RPM_SPEC:
                return (
                    "%install\n"
                    "install -m 0644 packaging/nfpm/modules/mod-markdown.conf \\\n"
                    "    %{buildroot}/usr/share/nginx/modules/mod-markdown.conf\n"
                    "%files\n"
                    "/usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so\n"
                    "%changelog\n"
                    "%config(noreplace) /usr/share/nginx/modules/mod-markdown.conf\n"
                )
            return ""

        monkeypatch.setattr(validator, "read_safe", fake_read_safe)
        result = validator.ValidationResult()
        validator.validate_rpm_spec_snippet(result)

        assert any(
            status == "FAIL" and check_id == "rpm:modules:files"
            for status, check_id, _message in result.results
        )

    def test_install_line_outside_the_install_section_fails(self, monkeypatch) -> None:
        def fake_read_safe(path: Path) -> str:
            if path == validator.RPM_SPEC:
                return (
                    "%prep\n"
                    "install -m 0644 packaging/nfpm/modules/mod-markdown.conf \\\n"
                    "    %{buildroot}/usr/share/nginx/modules/mod-markdown.conf\n"
                    "%files\n"
                    "%config(noreplace) /usr/share/nginx/modules/mod-markdown.conf\n"
                )
            return ""

        monkeypatch.setattr(validator, "read_safe", fake_read_safe)
        result = validator.ValidationResult()
        validator.validate_rpm_spec_snippet(result)

        assert any(
            status == "FAIL" and check_id == "rpm:modules:install"
            for status, check_id, _message in result.results
        )
