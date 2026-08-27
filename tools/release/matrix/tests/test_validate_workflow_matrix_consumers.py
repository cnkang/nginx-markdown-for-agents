"""Unit tests for validate_workflow_matrix_consumers.py."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import sys

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)
# The test bootstraps the script directory to exercise the CLI module directly.
from validate_workflow_matrix_consumers import (  # noqa: E402  # pylint: disable=import-error
    NGINX_VERSION_RE,
    _is_excluded_line,
    _has_top_level_workflow_call,
    _publish_job_needs,
    _uses_dynamic_resolution,
    extract_hardcoded_versions,
    load_matrix_versions,
    validate_canonical_workflows,
    validate_legacy_workflows,
    validate_official_docker_matrix_coverage,
    validate_owner_workflow_refs,
    validate_release_blocking_publish_dag,
)
from official_docker_matrix import (  # noqa: E402  # pylint: disable=import-error
    resolve_official_docker_entries,
)


class TestNginxVersionRegex:
    """Tests for the NGINX version detection regex."""

    def test_matches_valid_nginx_versions(self) -> None:
        assert NGINX_VERSION_RE.search("1.24.0")
        assert NGINX_VERSION_RE.search("1.26.3")
        assert NGINX_VERSION_RE.search("1.31.1")
        assert NGINX_VERSION_RE.search("2.0.0")

    def test_does_not_match_non_versions(self) -> None:
        assert not NGINX_VERSION_RE.search("hello world")
        assert not NGINX_VERSION_RE.search("1.2")


class TestExcludedLine:
    """Tests for the line exclusion logic."""

    def test_yaml_comment_excluded(self) -> None:
        assert _is_excluded_line("  # NGINX_VERSION=1.26.3")

    def test_description_excluded(self) -> None:
        assert _is_excluded_line('  description: "e.g. v1.0.0"')

    def test_eg_excluded(self) -> None:
        assert _is_excluded_line("  # e.g. 1.26.3")

    def test_normal_line_not_excluded(self) -> None:
        assert not _is_excluded_line('  NGINX_VERSION="1.26.3"')


class TestDynamicResolution:
    """Tests for dynamic resolution detection."""

    def test_detects_release_matrix_reference(self) -> None:
        content = 'with open("tools/release-matrix.json") as f:'
        assert _uses_dynamic_resolution(content)

    def test_no_reference_returns_false(self) -> None:
        content = "NGINX_VERSION=1.26.3"
        assert not _uses_dynamic_resolution(content)


class TestExtractHardcodedVersions:
    """Tests for extract_hardcoded_versions."""

    def test_finds_hardcoded_version(self) -> None:
        content = textwrap.dedent("""\
            name: Test
            jobs:
              build:
                env:
                  NGINX_VERSION: "1.26.3"
        """)
        found = extract_hardcoded_versions(content)
        versions = [v for _, v, _ in found]
        assert "1.26.3" in versions

    def test_skips_rust_toolchain(self) -> None:
        self._assert_no_hardcoded_versions("  RUST_TOOLCHAIN: 1.97.0")

    def test_skips_future_rust_toolchain(self) -> None:
        self._assert_no_hardcoded_versions("  RUST_TOOLCHAIN: 1.120.0")

    def test_skips_python_version(self) -> None:
        self._assert_no_hardcoded_versions("  python-version: 3.14.3")

    def test_skips_tool_version(self) -> None:
        self._assert_no_hardcoded_versions("  NFPM_VERSION: 2.46.3")

    def test_finds_future_nginx_major_when_context_is_explicit(self) -> None:
        found = extract_hardcoded_versions('  NGINX_VERSION: "2.0.0"')
        assert [version for _, version, _ in found] == ["2.0.0"]

    def test_finds_versions_in_nginx_yaml_block(self) -> None:
        content = "nginx_versions:\n  - 1.30.3\n  - 1.31.2\npython-version: 3.14.3\n"
        found = extract_hardcoded_versions(content)
        assert [version for _, version, _ in found] == ["1.30.3", "1.31.2"]

    def test_skips_commented_versions(self) -> None:
        self._assert_no_hardcoded_versions("  # NGINX_VERSION=1.26.3")

    def test_skips_description_versions(self) -> None:
        self._assert_no_hardcoded_versions('  description: "e.g. v1.26.3"')

    def _assert_no_hardcoded_versions(self, content):
        found = extract_hardcoded_versions(content)
        assert len(found) == 0


class TestLoadMatrixVersions:
    """Tests for load_matrix_versions."""

    def test_loads_versions_from_file(self, tmp_path: Path) -> None:
        matrix = {
            "entries": [
                {"nginx_version": "1.24.0"},
                {"nginx_version": "1.26.3"},
            ]
        }
        versions = self._write_matrix_file_and_load_versions(tmp_path, matrix)
        assert versions == {"1.24.0", "1.26.3"}

    def test_empty_matrix(self, tmp_path: Path) -> None:
        matrix = {"entries": []}
        matrix_file = tmp_path / "release-matrix.json"
        matrix_file.write_text(json.dumps(matrix))
        with pytest.raises(ValueError, match="non-empty"):
            load_matrix_versions(matrix_file)

    def test_rejects_legacy_aliases(self, tmp_path: Path) -> None:
        matrix = {"matrix": [{"nginx_version": "1.26.3"}]}
        matrix_file = tmp_path / "release-matrix.json"
        matrix_file.write_text(json.dumps(matrix))
        with pytest.raises(ValueError, match="legacy"):
            load_matrix_versions(matrix_file)

    def _write_matrix_file_and_load_versions(
        self, tmp_path: Path, matrix: dict
    ) -> set[str]:
        """Write a release matrix to a temporary file and load its versions.
        
        Parameters:
            tmp_path (Path): Directory for the temporary matrix file.
            matrix (dict): Release matrix data to serialize.
        
        Returns:
            set[str]: Versions extracted from the release matrix.
        """
        matrix_file = tmp_path / "release-matrix.json"
        matrix_file.write_text(json.dumps(matrix))
        return load_matrix_versions(matrix_file)


class TestValidateCanonicalWorkflows:
    """Tests for validate_canonical_workflows."""

    def test_passes_when_workflows_use_dynamic_resolution(
        self, tmp_path: Path
    ) -> None:
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)

        for name in ["release-packages.yml", "release-binaries.yml", "install-verify.yml"]:
            (wf_dir / name).write_text(
                'open("tools/release-matrix.json") as f:\n'
                "  data = json.load(f)\n"
            )

        with patch(
            "validate_workflow_matrix_consumers.WORKFLOWS_DIR", wf_dir
        ):
            errors, warnings = validate_canonical_workflows()

        assert errors == []
        assert warnings == []

    def test_fails_when_canonical_workflow_missing_dynamic_resolution(
        self, tmp_path: Path
    ) -> None:
        wf_dir = self._make_canonical_workflow_dir(
            tmp_path, 'NGINX_VERSION="1.26.3"\n'
        )

        errors = self._validate_canonical_workflows_in_dir(
            wf_dir, "does not reference"
        )
        assert "1.26.3" not in errors[0]

    def test_fails_when_canonical_workflow_has_dynamic_and_hardcoded(
        self, tmp_path: Path
    ) -> None:
        """Canonical workflows must not hardcode versions even if they also
        reference the matrix dynamically and the version exists in the matrix."""
        wf_dir = self._make_canonical_workflow_dir(
            tmp_path,
            'open("tools/release-matrix.json") as f:\n'
            '  data = json.load(f)\n'
            'NGINX_VERSION="1.26.3"\n',
        )

        errors = self._validate_canonical_workflows_in_dir(
            wf_dir, "canonical workflow must not hardcode"
        )
        assert "1.26.3" in errors[0]

    def _validate_canonical_workflows_in_dir(
        self, wf_dir: Path, expected_fragment: str
    ) -> list[str]:
        with patch("validate_workflow_matrix_consumers.WORKFLOWS_DIR", wf_dir):
            errors, warnings = validate_canonical_workflows()
        assert warnings == []
        assert len(errors) == 1
        assert "release-packages.yml" in errors[0]
        assert expected_fragment in errors[0]
        return errors

    def _make_canonical_workflow_dir(
        self, tmp_path: Path, release_packages_content: str
    ) -> Path:
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "release-packages.yml").write_text(release_packages_content)
        (wf_dir / "release-binaries.yml").write_text(
            'open("tools/release-matrix.json")\n'
        )
        (wf_dir / "install-verify.yml").write_text(
            'open("tools/release-matrix.json")\n'
        )
        return wf_dir


class TestValidateLegacyWorkflows:
    """Tests for validate_legacy_workflows."""

    def test_known_version_is_warning_not_error(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)

        (wf_dir / "release-rpm.yml").write_text(
            'name: "Legacy: Release RPM (nginx 1.26.3 only)"\n'
            'env:\n'
            '  NGINX_VERSION: "1.26.3"\n'
        )

        matrix_versions = {"1.24.0", "1.26.3"}

        with patch(
            "validate_workflow_matrix_consumers.WORKFLOWS_DIR", wf_dir
        ):
            errors, warnings = validate_legacy_workflows(matrix_versions)

        assert errors == []
        assert len(warnings) > 0
        assert all("1.26.3" in w for w in warnings)

    def test_unknown_version_is_error(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)

        (wf_dir / "release-rpm.yml").write_text(
            'NGINX_VERSION="1.22.0"\n'
        )

        matrix_versions = {"1.24.0", "1.26.3"}

        with patch(
            "validate_workflow_matrix_consumers.WORKFLOWS_DIR", wf_dir
        ):
            errors, _ = validate_legacy_workflows(matrix_versions)

        assert len(errors) == 1
        assert "1.22.0" in errors[0]


class TestValidateOwnerWorkflowRefs:
    """Tests for validate_owner_workflow_refs."""

    def test_passes_when_all_workflows_exist(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "release-packages.yml").write_text("")

        errors = self._write_matrix_with_owner_workflow(
            ".github/workflows/release-packages.yml", tmp_path
        )
        assert errors == []

    def test_fails_when_workflow_missing(self, tmp_path: Path) -> None:
        errors = self._write_matrix_with_owner_workflow(
            ".github/workflows/nonexistent.yml", tmp_path
        )
        assert len(errors) == 1
        assert "nonexistent.yml" in errors[0]

    def test_rejects_legacy_matrix_shape(self, tmp_path: Path) -> None:
        """Rule 62: legacy 'matrix'/'additional_artifacts' shapes are rejected."""
        matrix_file = tmp_path / "tools" / "release-matrix.json"
        matrix_file.parent.mkdir(parents=True)
        legacy = {
            "matrix": [
                {
                    "nginx": "1.26.3",
                    "owner_workflow": ".github/workflows/release-packages.yml",
                }
            ],
            "additional_artifacts": [],
        }
        matrix_file.write_text(json.dumps(legacy))
        with patch("validate_workflow_matrix_consumers.REPO_ROOT", tmp_path):
            errors = validate_owner_workflow_refs(matrix_file)
        assert len(errors) == 1
        assert "canonical 'entries'" in errors[0]

    def test_rejects_non_object_entries(self, tmp_path: Path) -> None:
        """A list of non-objects is a malformed canonical shape, not a crash."""
        matrix_file = tmp_path / "tools" / "release-matrix.json"
        matrix_file.parent.mkdir(parents=True)
        matrix_file.write_text(json.dumps({"entries": [None]}))
        with patch("validate_workflow_matrix_consumers.REPO_ROOT", tmp_path):
            errors = validate_owner_workflow_refs(matrix_file)
        assert len(errors) == 1
        assert "canonical 'entries'" in errors[0]

    def test_rejects_non_string_version_fields(self, tmp_path: Path) -> None:
        """Malformed version scalars must become controlled validation errors."""
        matrix_file = tmp_path / "tools" / "release-matrix.json"
        matrix_file.parent.mkdir(parents=True)
        matrix_file.write_text(
            json.dumps({"entries": [{"nginx_version": {"value": "1.26.3"}}]})
        )
        with pytest.raises(ValueError, match="must be a string"):
            load_matrix_versions(matrix_file)

    def _write_matrix_with_owner_workflow(
        self, owner_workflow: str, tmp_path: Path
    ) -> list[str]:
        """Validate an owner workflow reference against a temporary release matrix.
        
        Parameters:
        	owner_workflow (str): Workflow reference to include in the matrix.
        	tmp_path (Path): Temporary repository root used for the matrix file.
        
        Returns:
        	list[str]: Validation messages produced for the owner workflow reference.
        """
        matrix = {
            "entries": [
                {"nginx": "1.26.3", "owner_workflow": owner_workflow},
            ],
        }
        matrix_file = tmp_path / "tools" / "release-matrix.json"
        matrix_file.parent.mkdir(parents=True)
        matrix_file.write_text(json.dumps(matrix))
        with patch("validate_workflow_matrix_consumers.REPO_ROOT", tmp_path):
            result = validate_owner_workflow_refs(matrix_file)
        return result


class TestValidateReleaseBlockingPublishDag:
    """Tests for release-blocking Docker workflow wiring."""

    def test_requires_official_docker_gate_in_publish(self, tmp_path: Path) -> None:
        matrix_file, wf_dir = self._make_fixture(
            tmp_path,
            "  publish:\n"
            "    needs: [release-gate]\n",
        )

        with patch("validate_workflow_matrix_consumers.REPO_ROOT", tmp_path), patch(
            "validate_workflow_matrix_consumers.WORKFLOWS_DIR", wf_dir
        ):
            errors = validate_release_blocking_publish_dag(matrix_file)

        assert any("publish job does not depend" in error for error in errors)

    def test_accepts_exact_reusable_docker_gate(self, tmp_path: Path) -> None:
        matrix_file, wf_dir = self._make_fixture(
            tmp_path,
            "  official-docker-release-gate:\n"
            "    uses: ./.github/workflows/official-nginx-docker.yml\n"
            "  publish:\n"
            "    needs: [release-gate, official-docker-release-gate]\n",
        )

        with patch("validate_workflow_matrix_consumers.REPO_ROOT", tmp_path), patch(
            "validate_workflow_matrix_consumers.WORKFLOWS_DIR", wf_dir
        ):
            errors = validate_release_blocking_publish_dag(matrix_file)

        assert errors == []

    def test_rejects_substring_reusable_docker_gate(self, tmp_path: Path) -> None:
        matrix_file, wf_dir = self._make_fixture(
            tmp_path,
            "  official-docker-release-gate:\n"
            "    uses: ./.github/workflows/official-nginx-docker.yml-untrusted\n"
            "  publish:\n"
            "    needs: [official-docker-release-gate]\n",
        )

        with patch("validate_workflow_matrix_consumers.REPO_ROOT", tmp_path), patch(
            "validate_workflow_matrix_consumers.WORKFLOWS_DIR", wf_dir
        ):
            errors = validate_release_blocking_publish_dag(matrix_file)

        assert any("must use" in error for error in errors)

    def test_publish_needs_does_not_cross_job_boundary(self) -> None:
        content = (
            "  publish:\n"
            "    runs-on: ubuntu-latest\n"
            "  other:\n"
            "    needs: [official-docker-release-gate]\n"
        )

        assert _publish_job_needs(content) == set()

    def test_publish_needs_returns_inline_dependencies(self) -> None:
        content = "  publish:\n    needs: [release-gate, official-docker-release-gate]\n"

        assert _publish_job_needs(content) == {
            "release-gate",
            "official-docker-release-gate",
        }

    def test_publish_needs_returns_scalar_dependency(self) -> None:
        assert _publish_job_needs(
            "  publish:\n    needs: official-docker-release-gate\n"
        ) == {"official-docker-release-gate"}

    def test_publish_needs_returns_block_dependencies(self) -> None:
        content = (
            "  publish:\n"
            "    needs:\n"
            "      - release-gate\n"
            "      - official-docker-release-gate\n"
        )
        assert _publish_job_needs(content) == {
            "release-gate",
            "official-docker-release-gate",
        }

    def test_workflow_call_requires_two_space_job_indent(self) -> None:
        assert _has_top_level_workflow_call("  workflow_call:\n")
        assert not _has_top_level_workflow_call("    workflow_call:\n")

    def _make_fixture(
        self, tmp_path: Path, release_workflow_body: str
    ) -> tuple[Path, Path]:
        """
        Create temporary workflow and release-matrix fixtures for workflow validation tests.
        
        Parameters:
            tmp_path (Path): Root directory for the temporary fixture files.
            release_workflow_body (str): Workflow content appended to the release workflow trigger.
        
        Returns:
            tuple[Path, Path]: Paths to the release matrix file and workflows directory.
        """
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "official-nginx-docker.yml").write_text(
            "on:\n"
            "  workflow_call:\n"
        )
        (wf_dir / "release-packages.yml").write_text(
            "on:\n"
            + release_workflow_body
        )
        matrix_file = tmp_path / "tools" / "release-matrix.json"
        matrix_file.parent.mkdir(parents=True)
        matrix_file.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "artifact_type": "docker-image",
                            "release_blocking": True,
                            "owner_workflow": (
                                ".github/workflows/official-nginx-docker.yml"
                            ),
                        }
                    ]
                }
            )
        )
        return matrix_file, wf_dir


class TestOfficialDockerMatrix:
    """Tests for exact release-matrix Docker row resolution."""

    @staticmethod
    def _entry() -> dict:
        return {
            "nginx_version": "1.31.4",
            "os": "debian12",
            "libc": "glibc",
            "arch": "amd64",
            "artifact_type": "docker-image",
            "support_tier": "supported",
            "release_blocking": True,
            "owner_workflow": ".github/workflows/official-nginx-docker.yml",
            "image_ref": "nginx:1.31.4",
            "image_digest": "sha256:" + "a" * 64,
        }

    def test_resolves_exact_version_and_row_identity(self) -> None:
        rows = resolve_official_docker_entries({"entries": [self._entry()]})
        assert rows == [
            {
                "matrix_row_id": "1.31.4/debian12/glibc/amd64",
                "docker_tag": "1.31.4-debian12-glibc-amd64",
                "nginx_version": "1.31.4",
                "os": "debian12",
                "libc": "glibc",
                "arch": "amd64",
                "image_ref": "nginx:1.31.4",
                "image_digest": "sha256:" + "a" * 64,
            }
        ]

    def test_rejects_version_image_mismatch(self) -> None:
        entry = self._entry()
        entry["image_ref"] = "nginx:1.31.3"
        with pytest.raises(ValueError, match="does not match"):
            resolve_official_docker_entries({"entries": [entry]})

    def test_rejects_owned_release_blocking_unsupported_tier(self) -> None:
        entry = self._entry()
        entry["support_tier"] = "experimental"
        with pytest.raises(ValueError, match="support_tier"):
            resolve_official_docker_entries({"entries": [entry]})

    def test_rejects_unknown_architecture(self) -> None:
        entry = self._entry()
        entry["arch"] = "aarch64"
        with pytest.raises(ValueError, match="architecture"):
            resolve_official_docker_entries({"entries": [entry]})

    def test_rejects_path_bearing_operating_system(self) -> None:
        entry = self._entry()
        entry["os"] = "../debian12"
        with pytest.raises(ValueError, match="operating system"):
            resolve_official_docker_entries({"entries": [entry]})

    def test_rejects_unsupported_os_libc_pair(self) -> None:
        entry = self._entry()
        entry["libc"] = "musl"
        with pytest.raises(ValueError, match="libc"):
            resolve_official_docker_entries({"entries": [entry]})


class TestOfficialDockerWorkflowCoverage:
    """Tests for structural official Docker workflow validation."""

    def test_reports_missing_row_identity_without_crashing(
        self, tmp_path: Path
    ) -> None:
        workflow_dir = tmp_path / ".github" / "workflows"
        workflow_dir.mkdir(parents=True)
        (workflow_dir / "official-nginx-docker.yml").write_text("jobs: {}\n")

        with patch(
            "validate_workflow_matrix_consumers.WORKFLOWS_DIR", workflow_dir
        ), patch(
            "validate_workflow_matrix_consumers.load_official_docker_entries",
            return_value=[{}],
        ):
            errors = validate_official_docker_matrix_coverage(
                tmp_path / "release-matrix.json"
            )

        assert any("matrix_row_id" in error for error in errors)

    def test_rejects_contract_markers_only_in_comments(self, tmp_path: Path) -> None:
        matrix_file = tmp_path / "release-matrix.json"
        matrix_file.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "nginx_version": "1.31.4",
                            "os": "debian12",
                            "libc": "glibc",
                            "arch": "amd64",
                            "artifact_type": "docker-image",
                            "support_tier": "supported",
                            "release_blocking": True,
                            "owner_workflow": (
                                ".github/workflows/official-nginx-docker.yml"
                            ),
                            "image_ref": "nginx:1.31.4",
                            "image_digest": "sha256:" + "a" * 64,
                        }
                    ]
                }
            )
        )
        workflow_dir = tmp_path / ".github" / "workflows"
        workflow_dir.mkdir(parents=True)
        (workflow_dir / "official-nginx-docker.yml").write_text(
            "jobs:\n"
            "  prepare:\n"
            "    runs-on: ubuntu-latest\n"
            "    # official_docker_matrix.py\n"
            "    # load_official_docker_entries\n"
            "  build-and-verify:\n"
            "    needs: prepare\n"
            "    runs-on: ubuntu-latest\n"
            "    # fromJson(needs.prepare.outputs.matrix)\n"
            "    # matrix.image_ref matrix.image_digest\n"
        )

        with patch(
            "validate_workflow_matrix_consumers.WORKFLOWS_DIR", workflow_dir
        ):
            errors = validate_official_docker_matrix_coverage(matrix_file)

        assert any("prepare job" in error for error in errors)
        assert any("build-and-verify" in error for error in errors)
