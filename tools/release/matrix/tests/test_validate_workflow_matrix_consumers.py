"""Unit tests for validate_workflow_matrix_consumers.py."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import patch


import sys

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)
from validate_workflow_matrix_consumers import (
    NGINX_VERSION_RE,
    _is_excluded_line,
    _uses_dynamic_resolution,
    extract_hardcoded_versions,
    load_matrix_versions,
    validate_canonical_workflows,
    validate_legacy_workflows,
    validate_owner_workflow_refs,
)


class TestNginxVersionRegex:
    """Tests for the NGINX version detection regex."""

    def test_matches_valid_nginx_versions(self) -> None:
        assert NGINX_VERSION_RE.search("1.24.0")
        assert NGINX_VERSION_RE.search("1.26.3")
        assert NGINX_VERSION_RE.search("1.31.1")

    def test_does_not_match_non_versions(self) -> None:
        assert not NGINX_VERSION_RE.search("hello world")
        assert not NGINX_VERSION_RE.search("2.0.0")


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
        self._assert_no_hardcoded_versions("  RUST_TOOLCHAIN: 1.91.1")

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
            "matrix": [
                {"nginx": "1.24.0", "nginx_version": "1.24.0"},
                {"nginx": "1.26.3", "nginx_version": "1.26.3"},
            ]
        }
        versions = self._write_matrix_file_and_load_versions(tmp_path, matrix)
        assert versions == {"1.24.0", "1.26.3"}

    def test_empty_matrix(self, tmp_path: Path) -> None:
        matrix = {"matrix": []}
        versions = self._write_matrix_file_and_load_versions(tmp_path, matrix)
        assert versions == set()

    def _write_matrix_file_and_load_versions(
        self, tmp_path: Path, matrix: dict
    ) -> set[str]:
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

        (wf_dir / "release-deb.yml").write_text(
            'name: "Legacy: Release DEB (nginx 1.26.3 only)"\n'
            'env:\n'
            '  NGINX_VERSION: "1.26.3"\n'
        )
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

        (wf_dir / "release-deb.yml").write_text(
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

    def _write_matrix_with_owner_workflow(
        self, owner_workflow: str, tmp_path: Path
    ) -> list[str]:
        matrix = {
            "matrix": [{"nginx": "1.26.3", "owner_workflow": owner_workflow}],
            "additional_artifacts": [],
        }
        matrix_file = tmp_path / "tools" / "release-matrix.json"
        matrix_file.parent.mkdir(parents=True)
        matrix_file.write_text(json.dumps(matrix))
        with patch("validate_workflow_matrix_consumers.REPO_ROOT", tmp_path):
            result = validate_owner_workflow_refs(matrix_file)
        return result
