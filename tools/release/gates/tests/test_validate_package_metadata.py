"""Tests for release gate validator: NGINX version extraction.

Run:
    python3 -m pytest tools/release/gates/tests/test_validate_package_metadata.py -v
"""

from __future__ import annotations

import json
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
