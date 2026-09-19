"""Tests for the harness sync consistency checker.

Covers manifest loading, truth-surface existence, risk-pack doc contracts,
harness documentation references, AGENTS.md mapping, and optional Kiro
adapter drift detection under both quick and full modes.
"""

from __future__ import annotations

import json

import subprocess
import time
from pathlib import Path

import pytest

from tools.harness import check_harness_sync as sync


def test_collect_results_skips_missing_kiro(tmp_path, monkeypatch):
    repo = tmp_path
    _write_repo_fixture(repo, with_kiro=False)
    monkeypatch.setattr(sync, "REPO_ROOT", repo)
    monkeypatch.setattr(sync, "GITHUB_WORKFLOWS_DIR", repo / ".github" / "workflows")
    monkeypatch.setattr(sync, "MANIFEST_PATH", repo / "docs/harness/routing-manifest.json")
    monkeypatch.setattr(sync, "README_PATH", repo / "docs/harness/README.md")
    monkeypatch.setattr(sync, "CORE_PATH", repo / "docs/harness/core.md")
    monkeypatch.setattr(sync, "SUMMARY_PATH", repo / "docs/harness/routing-manifest.md")
    monkeypatch.setattr(sync, "AGENTS_PATH", repo / "AGENTS.md")

    results = sync.collect_results()
    adapter = next(item for item in results if item.name == "kiro-adapters")
    assert adapter.status == sync.SKIP_NOT_PRESENT


def test_manifest_command_reachability_accepts_repo_and_external_commands(
    tmp_path, monkeypatch
):
    (tmp_path / "Makefile").write_text("harness-check:\n\t@true\n", encoding="utf-8")
    for relative in (
        "tools/check.py",
        ".github/workflows/check.yml",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")
    (tmp_path / "tools/perf/tests").mkdir(parents=True)
    (tmp_path / "charts/nginx-markdown").mkdir(parents=True)
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)

    manifest = {
        "verification_families": {
            "fixture": {
                "commands": [
                    "make harness-check",
                    "python3 tools/check.py",
                    "PYTHONPATH=. pytest -q tools/perf/tests",
                    ".github/workflows/check.yml",
                    "helm lint charts/nginx-markdown",
                    "dpkg-deb --info dist/generated.deb",
                ]
            }
        }
    }

    result = sync._check_manifest_command_reachability(manifest)

    assert result.status == sync.PASS


def test_manifest_command_reachability_rejects_missing_surface(tmp_path, monkeypatch):
    (tmp_path / "Makefile").write_text("harness-check:\n\t@true\n", encoding="utf-8")
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)

    manifest = {
        "verification_families": {
            "fixture": {
                "commands": [
                    "make missing-target",
                    "python3 tools/missing.py",
                    ".github/workflows/missing.yml",
                ]
            }
        }
    }

    result = sync._check_manifest_command_reachability(manifest)

    assert result.status == sync.FAIL
    assert "missing-target" in result.detail
    assert "tools/missing.py" in result.detail
    assert ".github/workflows/missing.yml" in result.detail


def test_manifest_reachability_rejects_external_symlink_and_absolute_makefile(
    tmp_path, monkeypatch
):
    (tmp_path / "Makefile").write_text("check:\n\t@true\n", encoding="utf-8")
    external = tmp_path.parent / "external-harness-sync.py"
    external.write_text("# outside\n", encoding="utf-8")
    link = tmp_path / "tools" / "linked.py"
    link.parent.mkdir()
    link.symlink_to(external)
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)

    result = sync._check_manifest_command_reachability(
        {
            "verification_families": {
                "fixture": {
                    "commands": [
                        "python3 tools/linked.py",
                        f"make -f {tmp_path / 'Makefile'} check",
                    ]
                }
            }
        }
    )

    assert result.status == sync.FAIL
    assert "not repo-owned" in result.detail


def test_manifest_reachability_checks_explicit_workflow_paths(tmp_path, monkeypatch):
    """Workflow paths are validated separately from executable commands."""
    workflow = tmp_path / ".github" / "workflows" / "observation.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("# fixture\n", encoding="utf-8")
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)

    result = sync._check_manifest_command_reachability(
        {
            "verification_families": {
                "observation": {
                    "workflow_paths": [".github/workflows/observation.yml"]
                }
            }
        }
    )

    assert result.status == sync.PASS
    assert "1 workflow paths" in result.detail

    missing = sync._check_manifest_command_reachability(
        {
            "verification_families": {
                "observation": {
                    "workflow_paths": [".github/workflows/missing.yml"]
                }
            }
        }
    )

    assert missing.status == sync.FAIL
    assert "missing.yml" in missing.detail


def test_manifest_command_reachability_checks_every_compound_segment(
    tmp_path, monkeypatch
):
    (tmp_path / "Makefile").write_text("check:\n\t@true\n", encoding="utf-8")
    (tmp_path / "components" / "ok.c").parent.mkdir(parents=True)
    (tmp_path / "components" / "ok.c").write_text("", encoding="utf-8")
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)

    result = sync._check_manifest_command_reachability(
        {
            "verification_families": {
                "fixture": {
                    "commands": [
                        "make check && python3 docs/missing.py && components/ok.c",
                    ]
                }
            }
        }
    )

    assert result.status == sync.FAIL
    assert "docs/missing.py" in result.detail


def test_manifest_command_reachability_skips_make_include_dir(tmp_path, monkeypatch):
    """``make -I <dir> check`` must not treat the include directory as a
    target: ``-I`` is a value-taking flag whose argument is skipped during
    target validation (regression for the -I classification fix)."""
    (tmp_path / "Makefile").write_text("check:\n\t@true\n", encoding="utf-8")
    (tmp_path / "build" / "includes").mkdir(parents=True)
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)

    manifest = {
        "verification_families": {
            "fixture": {
                "commands": [
                    "make -I build/includes check",
                ]
            }
        }
    }

    result = sync._check_manifest_command_reachability(manifest)

    assert result.status == sync.PASS, f"expected PASS, got {result.status}: {result.detail}"


def test_collect_results_warns_for_local_kiro_drift(tmp_path, monkeypatch):
    repo = tmp_path
    _write_repo_fixture(repo, with_kiro=True, kiro_has_links=False)
    monkeypatch.setattr(sync, "REPO_ROOT", repo)
    monkeypatch.setattr(sync, "GITHUB_WORKFLOWS_DIR", repo / ".github" / "workflows")
    monkeypatch.setattr(sync, "MANIFEST_PATH", repo / "docs/harness/routing-manifest.json")
    monkeypatch.setattr(sync, "README_PATH", repo / "docs/harness/README.md")
    monkeypatch.setattr(sync, "CORE_PATH", repo / "docs/harness/core.md")
    monkeypatch.setattr(sync, "SUMMARY_PATH", repo / "docs/harness/routing-manifest.md")
    monkeypatch.setattr(sync, "AGENTS_PATH", repo / "AGENTS.md")

    results = sync.collect_results(full=False)
    adapter = next(item for item in results if item.name == "kiro-adapters")
    assert adapter.status == sync.WARN_NEEDS_AUTHOR_REVIEW

    full_results = sync.collect_results(full=True)
    full_adapter = next(item for item in full_results if item.name == "kiro-adapters")
    assert full_adapter.status == sync.FAIL


def test_collect_results_skips_git_ignored_kiro(tmp_path, monkeypatch):
    repo = tmp_path
    _write_repo_fixture(repo, with_kiro=True, kiro_has_links=False)
    (repo / ".gitignore").write_text(".kiro/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.setattr(sync, "REPO_ROOT", repo)
    monkeypatch.setattr(sync, "GITHUB_WORKFLOWS_DIR", repo / ".github" / "workflows")
    monkeypatch.setattr(sync, "MANIFEST_PATH", repo / "docs/harness/routing-manifest.json")
    monkeypatch.setattr(sync, "README_PATH", repo / "docs/harness/README.md")
    monkeypatch.setattr(sync, "CORE_PATH", repo / "docs/harness/core.md")
    monkeypatch.setattr(sync, "SUMMARY_PATH", repo / "docs/harness/routing-manifest.md")
    monkeypatch.setattr(sync, "AGENTS_PATH", repo / "AGENTS.md")

    results = sync.collect_results(full=False)
    adapter = next(item for item in results if item.name == "kiro-adapters")
    assert adapter.status == sync.SKIP_NOT_PRESENT

    full_results = sync.collect_results(full=True)
    full_adapter = next(item for item in full_results if item.name == "kiro-adapters")
    assert full_adapter.status == sync.SKIP_NOT_PRESENT


def test_collect_results_fail_when_pack_doc_missing(tmp_path, monkeypatch):
    repo = tmp_path
    _write_repo_fixture(repo, with_kiro=False)
    (repo / "docs/harness/risk-packs/runtime-streaming.md").unlink()

    monkeypatch.setattr(sync, "REPO_ROOT", repo)
    monkeypatch.setattr(sync, "GITHUB_WORKFLOWS_DIR", repo / ".github" / "workflows")
    monkeypatch.setattr(sync, "MANIFEST_PATH", repo / "docs/harness/routing-manifest.json")
    monkeypatch.setattr(sync, "README_PATH", repo / "docs/harness/README.md")
    monkeypatch.setattr(sync, "CORE_PATH", repo / "docs/harness/core.md")
    monkeypatch.setattr(sync, "SUMMARY_PATH", repo / "docs/harness/routing-manifest.md")
    monkeypatch.setattr(sync, "AGENTS_PATH", repo / "AGENTS.md")

    results = sync.collect_results()
    pack = next(item for item in results if item.name == "risk-pack-contract")
    assert pack.status == sync.FAIL
    assert "missing docs" in pack.detail


def test_collect_results_handles_missing_harness_doc_without_crash(tmp_path, monkeypatch):
    repo = tmp_path
    _write_repo_fixture(repo, with_kiro=False)
    (repo / "docs/harness/core.md").unlink()

    monkeypatch.setattr(sync, "REPO_ROOT", repo)
    monkeypatch.setattr(sync, "GITHUB_WORKFLOWS_DIR", repo / ".github" / "workflows")
    monkeypatch.setattr(sync, "MANIFEST_PATH", repo / "docs/harness/routing-manifest.json")
    monkeypatch.setattr(sync, "README_PATH", repo / "docs/harness/README.md")
    monkeypatch.setattr(sync, "CORE_PATH", repo / "docs/harness/core.md")
    monkeypatch.setattr(sync, "SUMMARY_PATH", repo / "docs/harness/routing-manifest.md")
    monkeypatch.setattr(sync, "AGENTS_PATH", repo / "AGENTS.md")

    results = sync.collect_results()
    truth = next(item for item in results if item.name == "truth-surfaces")
    docs = next(item for item in results if item.name == "harness-docs")
    assert truth.status == sync.FAIL
    assert docs.status == sync.FAIL
    assert "unreadable" in docs.detail


def test_collect_results_fail_for_invalid_manifest_json(tmp_path, monkeypatch):
    repo = tmp_path
    _write_repo_fixture(repo, with_kiro=False)
    manifest_path = repo / "docs/harness/routing-manifest.json"
    manifest_path.write_text("{not-json", encoding="utf-8")

    monkeypatch.setattr(sync, "REPO_ROOT", repo)
    monkeypatch.setattr(sync, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(sync, "README_PATH", repo / "docs/harness/README.md")
    monkeypatch.setattr(sync, "CORE_PATH", repo / "docs/harness/core.md")
    monkeypatch.setattr(sync, "SUMMARY_PATH", repo / "docs/harness/routing-manifest.md")
    monkeypatch.setattr(sync, "AGENTS_PATH", repo / "AGENTS.md")

    results = sync.collect_results()
    assert len(results) == 1
    assert results[0].name == "manifest-load"
    assert results[0].status == sync.FAIL


def test_load_manifest_rejects_non_object_root(tmp_path) -> None:
    """A scalar or list manifest cannot be treated as a valid mapping."""
    path = tmp_path / "routing-manifest.json"
    path.write_text("[]", encoding="utf-8")

    try:
        sync._load_manifest(path)
    except ValueError as exc:
        assert "root must be an object" in str(exc)
    else:  # pragma: no cover - assertion keeps the failure message explicit
        raise AssertionError("non-object manifest root was accepted")


@pytest.mark.parametrize(
    "field, value",
    [
        ("truth_surfaces", None),
        ("verification_families", None),
        ("risk_packs", None),
        ("task_entrypoints", None),
        ("spec_resolver", None),
    ],
)
def test_manifest_structure_rejects_null_nested_fields(field, value) -> None:
    """Malformed nested values return FAIL instead of raising TypeError."""
    manifest = {
        "version": 1,
        "truth_surfaces": {
            "contract": [],
            "harness": [],
            "canonical_docs": [],
            "optional_adapters": [],
        },
        "status_semantics": [
            sync.PASS,
            sync.FAIL,
            sync.SKIP_NOT_PRESENT,
            sync.WARN_NEEDS_AUTHOR_REVIEW,
        ],
        "spec_resolver": {
            "priority": [],
            "pointer_candidates": [],
            "multiple_spec_policy": "policy",
            "conflict_policy": "policy",
        },
        "verification_families": {},
        "risk_packs": [],
        "task_entrypoints": [],
    }
    manifest[field] = value

    result = sync._check_manifest_structure(manifest)

    assert result.status == sync.FAIL


def test_collect_results_fail_when_optional_adapters_key_missing(tmp_path, monkeypatch):
    repo = tmp_path
    _write_repo_fixture(repo, with_kiro=False)
    manifest_path = repo / "docs/harness/routing-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["truth_surfaces"]["optional_adapters"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(sync, "REPO_ROOT", repo)
    monkeypatch.setattr(sync, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(sync, "README_PATH", repo / "docs/harness/README.md")
    monkeypatch.setattr(sync, "CORE_PATH", repo / "docs/harness/core.md")
    monkeypatch.setattr(sync, "SUMMARY_PATH", repo / "docs/harness/routing-manifest.md")
    monkeypatch.setattr(sync, "AGENTS_PATH", repo / "AGENTS.md")

    results = sync.collect_results()
    assert len(results) == 1
    assert results[0].name == "manifest-structure"
    assert results[0].status == sync.FAIL
    assert "truth surface keys" in results[0].detail


def test_docker_runtime_security_accepts_non_root_images(tmp_path, monkeypatch):
    """Accept tracked runtime Dockerfiles with non-root users and safe paths."""
    _write_docker_runtime_fixture(tmp_path)
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)

    result = sync.check_docker_runtime_security()

    assert result.status == sync.PASS


def test_cfl_workflows_reject_unprepared_non_root_output(tmp_path, monkeypatch):
    """Reject fuzz workflows that let the root action create build-out."""
    workflows = tmp_path / ".github/workflows"
    workflows.mkdir(parents=True)
    (workflows / sync.CFLITE_PR_WORKFLOW).write_text(
        "pull_request:\n  paths:\n    - fuzz/**\n"
        "sanitizer: address\n"
        "uses: google/clusterfuzzlite/actions/build_fuzzers@sha\n",
        encoding="utf-8",
    )
    (workflows / sync.CFLITE_BATCH_WORKFLOW).write_text(
        "storage-repo: repo\n"
        "uses: google/clusterfuzzlite/actions/build_fuzzers@sha\n",
        encoding="utf-8",
    )
    (workflows / sync.CFLITE_CRON_WORKFLOW).write_text(
        "uses: google/clusterfuzzlite/actions/build_fuzzers@sha\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sync, "GITHUB_WORKFLOWS_DIR", workflows)

    result = sync.check_cfl_workflows()

    assert result.status == sync.FAIL
    assert "pre-create build-out before build_fuzzers" in result.detail


def test_docker_runtime_security_rejects_root_user(tmp_path, monkeypatch):
    """Reject a tracked runtime Dockerfile whose final user remains root."""
    _write_docker_runtime_fixture(tmp_path)
    dockerfile = tmp_path / ".clusterfuzzlite/Dockerfile"
    dockerfile.write_text("FROM scratch\nUSER root\n", encoding="utf-8")
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)

    result = sync.check_docker_runtime_security()

    assert result.status == sync.FAIL
    assert ".clusterfuzzlite/Dockerfile final USER must be non-root" in result.detail


def test_docker_runtime_security_rejects_unwritable_libfuzzer_archive(
    tmp_path, monkeypatch
):
    """Reject non-root fuzz images that cannot install libFuzzer."""
    _write_docker_runtime_fixture(tmp_path)
    dockerfile = tmp_path / ".clusterfuzzlite/Dockerfile"
    content = dockerfile.read_text(encoding="utf-8").replace(
        "    && chown fuzzer:fuzzer /usr/lib/libFuzzingEngine.a\n", ""
    )
    dockerfile.write_text(content, encoding="utf-8")
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)

    result = sync.check_docker_runtime_security()

    assert result.status == sync.FAIL
    assert "writable /usr/lib/libFuzzingEngine.a" in result.detail


def test_docker_runtime_security_rejects_unwritable_rust_sources(
    tmp_path, monkeypatch
):
    """Reject non-root fuzz images that cannot stage rustc sources."""
    _write_docker_runtime_fixture(tmp_path)
    dockerfile = tmp_path / ".clusterfuzzlite/Dockerfile"
    content = dockerfile.read_text(encoding="utf-8").replace(
        "RUN chown fuzzer:fuzzer /rustc\n", ""
    )
    dockerfile.write_text(content, encoding="utf-8")
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)

    result = sync.check_docker_runtime_security()

    assert result.status == sync.FAIL
    assert "writable /rustc" in result.detail


def test_docker_runtime_security_rejects_privileged_nginx_port(
    tmp_path, monkeypatch
):
    """Reject non-root NGINX images that regress to a privileged port."""
    _write_docker_runtime_fixture(tmp_path)
    dockerfile = tmp_path / "tools/build_release/Dockerfile.install-example"
    content = dockerfile.read_text(encoding="utf-8").replace(
        "EXPOSE 8080", "EXPOSE 80"
    )
    dockerfile.write_text(content, encoding="utf-8")
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)

    result = sync.check_docker_runtime_security()

    assert result.status == sync.FAIL
    assert "Dockerfile.install-example missing 'EXPOSE 8080'" in result.detail


def test_docker_runtime_security_rejects_missing_stage_build_args(
    tmp_path, monkeypatch
):
    """Reject install examples that lose pre-FROM args at the stage boundary."""
    _write_docker_runtime_fixture(tmp_path)
    dockerfile = tmp_path / "tools/build_release/Dockerfile.install-example"
    content = dockerfile.read_text(encoding="utf-8").replace(
        "ARG MODULE_REF\nARG INSTALL_SHA256\n", ""
    )
    dockerfile.write_text(content, encoding="utf-8")
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)

    result = sync.check_docker_runtime_security()

    assert result.status == sync.FAIL
    assert "Dockerfile.install-example missing stage ARG declarations" in result.detail


def test_trivy_local_scope_excludes_ignored_state(tmp_path, monkeypatch):
    """Accept local Trivy scope that excludes adapters and generated reports."""
    (tmp_path / "Makefile").write_text(
        "--skip-dirs .codeartsdoer --skip-dirs .kiro --skip-dirs build --skip-dirs reports\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)

    result = sync.check_trivy_local_scan_scope()

    assert result.status == sync.PASS


def test_trivy_local_scope_rejects_ignored_adapter_scan(tmp_path, monkeypatch):
    """Reject local Trivy scope when ignored Kiro state remains in scope."""
    (tmp_path / "Makefile").write_text(
        "--skip-dirs .codeartsdoer --skip-dirs build\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)

    result = sync.check_trivy_local_scan_scope()

    assert result.status == sync.FAIL
    assert ".kiro" in result.detail


def test_collect_results_accept_reordered_status_semantics(tmp_path, monkeypatch):
    repo = tmp_path
    _write_repo_fixture(repo, with_kiro=False)
    manifest_path = repo / "docs/harness/routing-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status_semantics"] = [
        sync.WARN_NEEDS_AUTHOR_REVIEW,
        sync.SKIP_NOT_PRESENT,
        sync.FAIL,
        sync.PASS,
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(sync, "REPO_ROOT", repo)
    monkeypatch.setattr(sync, "GITHUB_WORKFLOWS_DIR", repo / ".github" / "workflows")
    monkeypatch.setattr(sync, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(sync, "README_PATH", repo / "docs/harness/README.md")
    monkeypatch.setattr(sync, "CORE_PATH", repo / "docs/harness/core.md")
    monkeypatch.setattr(sync, "SUMMARY_PATH", repo / "docs/harness/routing-manifest.md")
    monkeypatch.setattr(sync, "AGENTS_PATH", repo / "AGENTS.md")

    results = sync.collect_results()
    manifest_result = next(item for item in results if item.name == "manifest-structure")
    assert manifest_result.status == sync.PASS


@pytest.mark.parametrize("bad_item", [{"nested": []}, [1, 2]])
def test_non_scalar_status_semantics_fail_structurally(tmp_path, monkeypatch, bad_item):
    """A status list holding an object or array fails the check, not the run."""
    repo = tmp_path
    _write_repo_fixture(repo, with_kiro=False)
    manifest_path = repo / "docs/harness/routing-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status_semantics"] = [
        sync.PASS,
        sync.FAIL,
        sync.SKIP_NOT_PRESENT,
        bad_item,
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(sync, "REPO_ROOT", repo)
    monkeypatch.setattr(sync, "GITHUB_WORKFLOWS_DIR", repo / ".github" / "workflows")
    monkeypatch.setattr(sync, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(sync, "README_PATH", repo / "docs/harness/README.md")
    monkeypatch.setattr(sync, "CORE_PATH", repo / "docs/harness/core.md")
    monkeypatch.setattr(sync, "SUMMARY_PATH", repo / "docs/harness/routing-manifest.md")
    monkeypatch.setattr(sync, "AGENTS_PATH", repo / "AGENTS.md")

    results = sync.collect_results()
    manifest_result = next(
        item for item in results if item.name == "manifest-status-semantics"
    )
    assert manifest_result.status == sync.FAIL
    assert "scalar" in manifest_result.detail


def test_collect_results_passes_traceable_recent_analysis_report(tmp_path, monkeypatch):
    repo = tmp_path
    _write_repo_fixture(repo, with_kiro=False)
    report = repo / "docs/project/recent-git-harness-steering-analysis-2026-04-24.md"
    report.parent.mkdir(parents=True)
    report.write_text(
        "\n".join(
            [
                "# Recent Git Harness Steering Analysis",
                "## Phase 1 Analysis",
                "summary",
                "## Findings",
                "| ID | Priority | Finding |",
                "|----|----------|---------|",
                "| P0-001 | P0 | Missing traceability |",
                "## Remediation Results",
                "| ID | Status | Evidence |",
                "|----|--------|----------|",
                "| P0-001 | fixed | checker verifies closeout |",
                "## Verification",
                "make harness-check",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(sync, "REPO_ROOT", repo)
    monkeypatch.setattr(sync, "GITHUB_WORKFLOWS_DIR", repo / ".github" / "workflows")
    monkeypatch.setattr(sync, "MANIFEST_PATH", repo / "docs/harness/routing-manifest.json")
    monkeypatch.setattr(sync, "README_PATH", repo / "docs/harness/README.md")
    monkeypatch.setattr(sync, "CORE_PATH", repo / "docs/harness/core.md")
    monkeypatch.setattr(sync, "SUMMARY_PATH", repo / "docs/harness/routing-manifest.md")
    monkeypatch.setattr(sync, "AGENTS_PATH", repo / "AGENTS.md")

    results = sync.collect_results()
    report_result = next(item for item in results if item.name == "recent-analysis-report")
    assert report_result.status == sync.PASS


def test_collect_results_fails_unclosed_recent_analysis_report(tmp_path, monkeypatch):
    repo = tmp_path
    _write_repo_fixture(repo, with_kiro=False)
    report = repo / "docs/project/recent-git-harness-steering-analysis-2026-04-24.md"
    report.parent.mkdir(parents=True)
    report.write_text(
        "\n".join(
            [
                "# Recent Git Harness Steering Analysis",
                "## Phase 1 Analysis",
                "summary",
                "## Findings",
                "| ID | Priority | Finding |",
                "|----|----------|---------|",
                "| P1-001 | P1 | Missing closeout |",
                "## Remediation Results",
                "| ID | Status | Evidence |",
                "|----|--------|----------|",
                "| P1-001 | open | not closed |",
                "## Verification",
                "pending",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(sync, "REPO_ROOT", repo)
    monkeypatch.setattr(sync, "GITHUB_WORKFLOWS_DIR", repo / ".github" / "workflows")
    monkeypatch.setattr(sync, "MANIFEST_PATH", repo / "docs/harness/routing-manifest.json")
    monkeypatch.setattr(sync, "README_PATH", repo / "docs/harness/README.md")
    monkeypatch.setattr(sync, "CORE_PATH", repo / "docs/harness/core.md")
    monkeypatch.setattr(sync, "SUMMARY_PATH", repo / "docs/harness/routing-manifest.md")
    monkeypatch.setattr(sync, "AGENTS_PATH", repo / "AGENTS.md")

    results = sync.collect_results()
    report_result = next(item for item in results if item.name == "recent-analysis-report")
    assert report_result.status == sync.FAIL
    assert "P1-001 final status" in report_result.detail


def _write_repo_fixture(repo: Path, *, with_kiro: bool, kiro_has_links: bool = True) -> None:
    for rel in [
        "docs/harness/risk-packs",
        "docs/architecture",
        "docs/testing",
        "docs",
    ]:
        (repo / rel).mkdir(parents=True, exist_ok=True)

    manifest = {
        "version": 1,
        "truth_surfaces": {
            "contract": ["AGENTS.md"],
            "harness": [
                "docs/harness/README.md",
                "docs/harness/core.md",
                "docs/harness/routing-manifest.md",
                "docs/harness/routing-manifest.json",
                "docs/harness/risk-packs/README.md",
            ],
            "canonical_docs": [
                "docs/architecture/README.md",
                "docs/testing/README.md",
                "docs/DOCUMENTATION_DUPLICATION_POLICY.md",
            ],
            "optional_adapters": [
                ".kiro/steering/product.md",
                ".kiro/steering/structure.md",
                ".kiro/steering/tech.md",
            ],
        },
        "status_semantics": [
            sync.PASS,
            sync.FAIL,
            sync.SKIP_NOT_PRESENT,
            sync.WARN_NEEDS_AUTHOR_REVIEW,
        ],
        "spec_resolver": {
            "priority": ["user-task", "active-spec-pointer"],
            "pointer_candidates": [
                ".kiro/active-spec.json",
                ".kiro/active-spec.txt",
            ],
            "multiple_spec_policy": "explain-current-choice",
            "conflict_policy": "stop-and-confirm",
        },
        "verification_families": {
            "harness-sync": {"phase": "cheap-blocker", "commands": ["make harness-check"]}
        },
        "risk_packs": [
            {
                "id": "runtime-streaming",
                "doc": "docs/harness/risk-packs/runtime-streaming.md",
                "verification_families": ["harness-sync"],
            }
        ],
        "task_entrypoints": [],
    }
    (repo / "docs/harness/routing-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (repo / "docs/harness/README.md").write_text(
        "\n".join(
            [
                "[Workflow](core.md)",
                "[Manifest JSON](routing-manifest.json)",
                "[Manifest Summary](routing-manifest.md)",
                "[Risk Packs](risk-packs/README.md)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (repo / "docs/harness/core.md").write_text(
        "\n".join(
            [
                sync.PASS,
                sync.FAIL,
                sync.SKIP_NOT_PRESENT,
                sync.WARN_NEEDS_AUTHOR_REVIEW,
                "outside voice",
                "state carrier",
                "stop and explain the mismatch",
                "tools/harness/resolve_spec.py",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "docs/harness/routing-manifest.md").write_text(
        "runtime-streaming\n", encoding="utf-8"
    )
    (repo / "docs/harness/risk-packs/README.md").write_text(
        "runtime-streaming\n", encoding="utf-8"
    )
    (repo / "docs/harness/risk-packs/runtime-streaming.md").write_text(
        "pack doc\n", encoding="utf-8"
    )
    (repo / "docs/architecture/README.md").write_text("arch\n", encoding="utf-8")
    (repo / "docs/testing/README.md").write_text("testing\n", encoding="utf-8")
    (repo / "docs/DOCUMENTATION_DUPLICATION_POLICY.md").write_text(
        "duplication\n", encoding="utf-8"
    )
    (repo / "AGENTS.md").write_text(
        "\n".join(
            [
                "- `docs/harness/README.md` is the repo-owned harness entrypoint.",
                "- `docs/harness/core.md` defines the execution loop.",
                "- `docs/harness/routing-manifest.json` is the canonical route source.",
                "- Codex-first semantics apply.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (repo / "Makefile").write_text(
        "harness-check:\n\t@true --skip-dirs .codeartsdoer --skip-dirs .kiro --skip-dirs build\n",
        encoding="utf-8",
    )

    if with_kiro:
        (repo / ".kiro/steering").mkdir(parents=True, exist_ok=True)
        content = "docs/harness/README.md\ndocs/harness/core.md\n" if kiro_has_links else "old doc\n"
        for name in ("product.md", "structure.md", "tech.md"):
            (repo / f".kiro/steering/{name}").write_text(content, encoding="utf-8")


def _write_docker_runtime_fixture(repo: Path) -> None:
    """Create the tracked Dockerfile security surfaces for focused tests."""
    dockerfiles = {
        ".clusterfuzzlite/Dockerfile": (
            "FROM scratch\n"
            "RUN mkdir -p /rustc\n"
            "RUN chown fuzzer:fuzzer /rustc\n"
            "RUN touch /usr/lib/libFuzzingEngine.a \\\n"
            "    && chown fuzzer:fuzzer /usr/lib/libFuzzingEngine.a\n"
            "USER 10001\n"
        ),
        "examples/docker/Dockerfile.official-nginx-source-build": (
            "FROM scratch\n"
            "RUN pid /tmp/nginx.pid; client_body_temp_path /tmp/client_temp;\n"
            "USER nginx\n"
            "EXPOSE 8080\n"
        ),
        "tools/build_release/Dockerfile.install-example": (
            "FROM scratch\n"
            "ARG MODULE_REF\n"
            "ARG INSTALL_SHA256\n"
            "RUN pid /tmp/nginx.pid; client_body_temp_path /tmp/client_temp;\n"
            "USER nginx\n"
            "EXPOSE 8080\n"
        ),
    }
    for relative_path, content in dockerfiles.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_final_dockerfile_user_ignores_earlier_stages() -> None:
    """Only the final stage's USER describes the image that is built."""
    builder_only = (
        "FROM debian:bookworm AS builder\n"
        "USER builder\n"
        "RUN make\n"
        "\n"
        "FROM debian:bookworm\n"
        "COPY --from=builder /out /out\n"
    )
    assert sync._dockerfile_final_user(builder_only) is None

    assert sync._dockerfile_final_user(
        builder_only.replace("COPY --from=builder /out /out\n", "USER app\n")
    ) == "app"

    assert sync._dockerfile_final_user(
        "FROM debian:bookworm\nUSER app:app\n"
    ) == "app"


def test_final_dockerfile_user_returns_none_without_any_user() -> None:
    assert sync._dockerfile_final_user("FROM debian:bookworm\nRUN true\n") is None


def _rule_check_entry(**overrides: object) -> dict:
    """Return one manifest entry, with the fields a valid one carries."""
    entry = {
        "rule": "56",
        "summary": "orphan comment closers",
        "check": "tools/harness/detect_orphan_comment_close.py",
        "files": ["components/nginx-module/src/**"],
        "stage": ["save", "commit"],
        "blocking": True,
        "test": None,
        "not_covered": "a closer built by a macro expansion is not judged",
    }
    entry.update(overrides)
    return entry


def test_rule_checks_accept_a_complete_mapping() -> None:
    """A mapping that names an existing check passes."""
    result = sync._check_rule_checks({"rule_checks": [_rule_check_entry()]})

    assert result.status == sync.PASS


def test_rule_checks_reject_a_missing_check_script() -> None:
    """A mapping that points at a script nobody wrote is not wired."""
    entry = _rule_check_entry(check="tools/harness/does_not_exist.sh")

    result = sync._check_rule_checks({"rule_checks": [entry]})

    assert result.status == sync.FAIL
    assert "not a repository file" in result.detail


def test_rule_checks_reject_a_missing_test_entry() -> None:
    """A test path that does not exist is not evidence."""
    entry = _rule_check_entry(test="tools/harness/tests/test_nope.py")

    result = sync._check_rule_checks({"rule_checks": [entry]})

    assert result.status == sync.FAIL


def test_rule_checks_reject_an_unknown_stage() -> None:
    """A stage nobody runs would never gate anything."""
    entry = _rule_check_entry(stage=["whenever"])

    result = sync._check_rule_checks({"rule_checks": [entry]})

    assert result.status == sync.FAIL


def test_rule_checks_reject_a_rule_absent_from_agents_md() -> None:
    """A rule number that AGENTS.md dropped cannot be routed to a check."""
    entry = _rule_check_entry(rule="9999")

    result = sync._check_rule_checks({"rule_checks": [entry]})

    assert result.status == sync.FAIL


def test_rule_checks_reject_a_mapping_without_not_covered() -> None:
    """The mapping has to say what it does not cover."""
    entry = _rule_check_entry()
    del entry["not_covered"]

    result = sync._check_rule_checks({"rule_checks": [entry]})

    assert result.status == sync.FAIL
    assert "not_covered" in result.detail


def test_rule_checks_reject_unknown_mapping_fields() -> None:
    """Adding an unconsumed field must not silently change the contract."""
    entry = _rule_check_entry(extra_policy="unvalidated")

    result = sync._check_rule_checks({"rule_checks": [entry]})

    assert result.status == "FAIL"
    assert "unknown field" in result.detail


def test_rule_checks_require_the_check_to_be_invoked() -> None:
    """A path that merely exists is not wiring."""
    entry = _rule_check_entry(check="README.md")

    result = sync._check_rule_checks({"rule_checks": [entry]})

    assert result.status == "FAIL"
    assert "nothing invokes" in result.detail


def test_rule_checks_reject_a_non_string_stage_entry() -> None:
    """A malformed stage entry is a structured failure, not a traceback."""
    entry = _rule_check_entry(stage=[{}])

    result = sync._check_rule_checks({"rule_checks": [entry]})

    assert result.status == "FAIL"


def test_rule_checks_reject_an_empty_files_list() -> None:
    """The mapping has to say which files the rule covers."""
    entry = _rule_check_entry(files=[])

    result = sync._check_rule_checks({"rule_checks": [entry]})

    assert result.status == "FAIL"


def test_profile_cannot_pass_when_the_change_set_is_unknown(monkeypatch) -> None:
    """A failed diff must not be read as "no C changes"."""
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo_root / "tools/ci"))
    import pre_push_profile as profile

    monkeypatch.setattr(profile, "_git", lambda args: (128, ""))
    assert profile._changed_files("origin/main") is None

    monkeypatch.setattr(profile, "_merge_base", lambda base: "deadbeef")
    assert profile.main(["prog"]) == 2


def test_running_an_interpreter_against_a_directory_is_not_an_invocation() -> None:
    """`python3 tools/harness` executes nothing from the directory."""
    wiring = "check:\n\tpython3 tools/harness\n"

    detector = "tools/harness/detect_pool_free.sh"

    assert sync._is_invoked(detector, wiring) is False


def test_a_test_runner_directory_does_cover_the_files_under_it() -> None:
    """A discovery runner reaches the tests it names by directory."""
    wiring = "check:\n\tpython3 -m pytest tools/harness/tests/ -q\n"

    assert sync._is_invoked("tools/harness/tests/test_harness_wiring.py", wiring)


def test_a_check_only_passed_as_an_argument_is_not_invoked() -> None:
    """A path read as data by another tool is not a gate."""
    wiring = "docs:\n\tpython3 tools/docs/check_docs.py tools/harness/detect_pool_free.sh\n"

    assert sync._is_invoked("tools/harness/detect_pool_free.sh", wiring) is False


def test_every_declared_stage_in_the_manifest_is_reachable() -> None:
    """The mapping the repository ships reaches its checks, stage by stage."""
    manifest = json.loads(
        (Path(__file__).resolve().parents[3] / "docs/harness/routing-manifest.json")
        .read_text(encoding="utf-8")
    )

    result = sync._check_rule_checks(manifest)

    assert result.status == sync.PASS, result.detail


def test_collection_is_not_execution() -> None:
    """A runner that only lists tests has not run the check."""
    assert sync._discovery_target(["python3", "-m", "pytest", "--collect-only", "tests/"]) is None
    assert sync._discovery_target(["python3", "-m", "pytest", "tests/"]) == "tests"


def test_unknown_test_option_does_not_expose_its_value_as_a_path() -> None:
    """An unmodeled option is ambiguous and must fail closed."""
    assert sync._discovery_target(
        ["python3", "-m", "pytest", "--unknown", "tests/"]
    ) is None
    assert sync._discovery_target(
        ["python3", "-m", "pytest", "--", "tests/"]
    ) == "tests"


def test_an_inline_option_value_does_not_stop_the_scan() -> None:
    """`--junitxml=out.xml` carries its value, so the directory still counts."""
    assert sync._discovery_target(
        ["python3", "-m", "pytest", "-q", "--junitxml=out.xml", "tools/harness/tests/"]
    ) == "tools/harness/tests"
    assert sync._discovery_target(
        ["python3", "-m", "pytest", "-q", "--junitxml=out.xml", "--unknown", "tests/"]
    ) is None


def test_a_step_running_elsewhere_does_not_certify() -> None:
    """`working-directory` decides which Makefile a step's commands reach."""
    steps = [{"run": "make root"}]

    assert sync._enabled_step_commands(steps) == ["make root"]
    assert sync._enabled_step_commands(steps, "packaging") == []
    assert sync._enabled_step_commands([{"run": "make root", "working-directory": "tools"}]) == []


def test_dynamic_workflow_step_conditions_are_not_execution_evidence() -> None:
    """Only unconditional or literal-true run steps establish a CI edge."""
    steps = [
        {"if": "always()", "run": "make dynamic"},
        {"if": "matrix.enabled == 'true'", "run": "make matrix"},
        {"if": True, "run": "make literal"},
        {"if": "${{ true }}", "run": "make string-literal"},
    ]

    assert sync._enabled_step_commands(steps) == ["make literal", "make string-literal"]


def test_malformed_workflow_shape_cannot_certify_ci() -> None:
    """A malformed job or steps list is an explicit reachability error."""
    with pytest.raises(ValueError, match="steps"):
        sync._document_run_commands({"jobs": {"broken": {"steps": "make root"}}})


def test_default_working_directories_are_read() -> None:
    """`defaults.run.working-directory` decides where a step's commands reach."""
    steps = [{"run": "make root"}]
    workflow_default = {
        "defaults": {"run": {"working-directory": "packaging"}},
        "jobs": {"j": {"steps": steps}},
    }
    job_default = {
        "jobs": {"j": {"defaults": {"run": {"working-directory": "tools"}}, "steps": steps}}
    }
    plain = {"jobs": {"j": {"steps": steps}}}

    assert sync._document_run_commands(plain) == ["make root"]
    assert sync._document_run_commands(workflow_default) == []
    assert sync._document_run_commands(job_default) == []


def test_an_option_value_is_not_a_test_path() -> None:
    """The word after `-o` belongs to the option, not to the run."""
    assert sync._discovery_target(
        ["python3", "-m", "pytest", "-o", "tools/harness/tests/test_fake.py"]
    ) is None
    assert sync._discovery_target(["python3", "-m", "pytest", "-q", "tests/"]) == "tests"


def test_an_unknown_inline_option_does_not_stop_the_scan() -> None:
    """`--junitxml=x` carries its own value, so discovery continues past it.

    An unknown option without an inline value is ambiguous (it may consume the
    next word), but `--name=value` cannot: the run's directory argument is
    still discoverable.
    """
    assert sync._discovery_target(
        ["python3", "-m", "pytest", "-q", "--junitxml=out.xml", "tools/harness/tests/"]
    ) == "tools/harness/tests"
    assert sync._discovery_target(
        ["python3", "-m", "pytest", "-q", "--some-future-flag=1", "tests/"]
    ) == "tests"
    assert sync._invocation_target(
        ["python3", "--some-future-flag=1", "tools/x.py"]
    ) == "tools/x.py"


def test_dynamic_job_conditions_are_not_execution_evidence() -> None:
    """A job's own dynamic `if:` cannot certify a CI edge.

    The job level applies the same rule as the step level: a paths-filter gate
    or any other dynamic expression can be false for the change under
    evaluation.  Reachability of such a job is established separately, by the
    paths-filter coverage check.
    """
    document = {
        "jobs": {
            "gated": {
                "if": "needs.changes.outputs.harness_tooling == 'true'",
                "steps": [{"run": "make gated"}],
            },
            "literal": {"if": True, "steps": [{"run": "make literal"}]},
            "unconditional": {"steps": [{"run": "make plain"}]},
            "disabled": {"if": False, "steps": [{"run": "make disabled"}]},
            "false-string": {"if": "false", "steps": [{"run": "make false"}]},
        }
    }

    assert sync._document_run_commands(document) == ["make literal", "make plain"]


def test_filter_gate_names_reads_only_a_pure_filter_condition() -> None:
    """A condition is attributable to a filter only when nothing else is in it."""
    assert sync._filter_gate_names(
        "needs.changes.outputs.harness_tooling == 'true'"
    ) == frozenset({"harness_tooling"})
    assert sync._filter_gate_names(
        "needs.changes.outputs.nginx == 'true' || needs.changes.outputs.workflows == 'true'"
    ) == frozenset({"nginx", "workflows"})
    assert sync._filter_gate_names(
        "needs.changes.outputs.nginx == 'true' || github.ref == 'refs/heads/main'"
    ) is None
    assert sync._filter_gate_names("always()") is None
    assert sync._filter_gate_names(None) is None


def test_advisory_job_commands_do_not_certify(monkeypatch) -> None:
    """continue-on-error jobs contribute no lane commands.

    Their commands run but never fail the workflow, so they cannot gate a
    rule; an expression-valued setting is statically undecidable and is
    treated the same way (fail closed).
    """
    document = {
        "jobs": {
            "advisory": {
                "continue-on-error": True,
                "steps": [{"run": "make advisory"}],
            },
            "expression-advisory": {
                "continue-on-error": "${{ github.event_name == 'push' }}",
                "steps": [{"run": "make expr"}],
            },
            "blocking": {"steps": [{"run": "make blocking"}]},
        }
    }
    monkeypatch.setattr(
        sync, "_workflow_documents", lambda: [("ci.yml", document)]
    )

    lanes = sync._ci_entry_point_lanes()

    commands = [
        command for _display, _names, lane_commands in lanes
        for command in lane_commands
    ]
    assert "make blocking" in commands
    assert "make advisory" not in commands
    assert "make expr" not in commands


def test_a_pattern_matching_no_tracked_file_is_covered_vacuously() -> None:
    """Empty-match rule patterns cannot be missed by any filter.

    A few rules keep patterns ahead of the surface they will cover; until a
    file matches, there is nothing a change could touch, so the vacuous
    coverage verdict is the documented reading rather than a gap.
    """
    assert (
        sync._filter_covers_pattern(["tools/**"], "no/such/surface/*.xyz")
        is True
    )


def test_a_matching_pattern_outside_the_filter_is_not_covered() -> None:
    """A pattern with real files outside the filter is a gap."""
    assert (
        sync._filter_covers_pattern(["tools/**"], ".github/workflows/*.yml")
        is False
    )


def test_a_path_pattern_does_not_cross_a_directory_separator() -> None:
    """`*.sh` is a root-level pattern, as picomatch reads it.

    `**/` matches zero or more segments (GitHub documents `**/README.md` as
    matching the repository root too), so it is the single `*` that must not
    cross a separator.
    """
    assert sync._path_pattern_matches("*.sh", "build.sh") is True
    assert sync._path_pattern_matches("*.sh", "packaging/scripts/x.sh") is False
    assert sync._path_pattern_matches("tools/*.sh", "tools/x.sh") is True
    assert sync._path_pattern_matches("tools/*.sh", "tools/a/b.sh") is False
    assert sync._path_pattern_matches("tools/**/*.sh", "tools/a/b.sh") is True
    assert sync._path_pattern_matches("tools/**/*.sh", "tools/x.sh") is True
    assert sync._path_pattern_matches("**/*.sh", "tools/a/b.sh") is True
    assert sync._path_pattern_matches("components/**", "components/nginx-module/src/a.c") is True


def test_pattern_classes_and_questions_stay_inside_one_segment() -> None:
    """Bracket classes, ranges, and `?` follow the single-separator rule."""
    assert sync._path_pattern_matches("tools/[a-c]?.sh", "tools/ab.sh") is True
    assert sync._path_pattern_matches("tools/[a-c]?.sh", "tools/dd.sh") is False
    assert sync._path_pattern_matches("tools/[^a]?.sh", "tools/bz.sh") is True
    assert sync._path_pattern_matches("tools/[^a]?.sh", "tools/az.sh") is False
    assert sync._path_pattern_matches(r"tools/[\d].sh", "tools/7.sh") is True
    assert sync._path_pattern_matches("tools/?.sh", "tools/a/b.sh") is False
    assert sync._path_pattern_matches("tools/[abc.sh", "tools/[abc.sh") is True


def test_a_wildcard_heavy_pattern_stays_deterministic() -> None:
    """A backtracking-shaped pattern keeps a correct verdict, not a blowup.

    Repeated `**/` groups separated by literals made the translated-regex
    matcher super-linear; the deterministic matcher must answer the same
    verdict in one pass over the path.
    """
    pattern = "**/" * 12 + "target"
    assert sync._path_pattern_matches(pattern, "a/" * 40 + "other") is False
    assert sync._path_pattern_matches(pattern, "a/" * 11 + "target") is True

    star_pattern = "*a" * 12 + "*b"
    assert sync._path_pattern_matches(star_pattern, "a" * 40) is False
    assert sync._path_pattern_matches(star_pattern, "a" * 12 + "b") is True


def test_a_wildcard_run_stays_linear_on_a_long_path() -> None:
    """A star run must not walk once per start position.

    A preceding `**` hands the star matcher one start per path character;
    walking to the end of the component from every start costs
    O(len(path) ** 2) and takes seconds on a 20k-character path.  The
    verdicts must arrive within a small budget at a length where the
    quadratic form cannot.
    """
    path = "a" * 20000
    started = time.perf_counter()
    assert sync._path_pattern_matches("***", path) is True
    assert sync._path_pattern_matches("*a*", path) is True
    assert sync._path_pattern_matches("*a*b*", path) is False
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"wildcard run matching took {elapsed:.2f}s"


def test_missing_mapping_entry_is_a_structural_blind_spot() -> None:
    """AGENTS.md rule rows naming a detector must appear in the mapping.

    Removing an entry from a copy of the shipped mapping has to fail: the
    forward validator can only judge entries that exist, so without reverse
    coverage a rule could be unmapped forever.
    """
    repo_root = Path(__file__).resolve().parents[3]
    manifest = json.loads(
        (repo_root / "docs/harness/routing-manifest.json").read_text(encoding="utf-8")
    )
    assert sync._check_agents_rule_coverage(manifest).status == sync.PASS

    bound = {
        path
        for paths in sync._agents_detector_rules(
            sync.AGENTS_PATH.read_text(encoding="utf-8")
        ).values()
        for path in paths
    }
    tested = 0
    for entry in manifest["rule_checks"]:
        if entry["check"] not in bound:
            # Only the AGENTS.md-bound detector entries are under test.
            continue
        tested += 1
        reduced = [
            other for other in manifest["rule_checks"] if other is not entry
        ]
        result = sync._check_agents_rule_coverage({"rule_checks": reduced})

        assert result.status == sync.FAIL, entry["rule"]
        assert "has no entry for it" in result.detail, result.detail
    assert tested >= 6, tested


def test_reverse_coverage_ignores_rows_that_do_not_name_a_detector() -> None:
    """Only AGENTS.md rows with a literal tool path are bindings.

    A rule row that describes a check without naming a tool must not fail the
    mapping: its binding is a Makefile target or a hook id, which this check
    cannot judge from the row text.
    """
    agents = "\n".join(
        [
            "| 99 | build-safety | A rule that names no tool at all |",
            "| 1 | build-safety | `bash tools/harness/detect_example.sh` |",
            "",
            "- A prose line outside the table mentioning tools/harness/detect_prose.py",
        ]
    )

    bindings = sync._agents_detector_rules(agents)

    assert bindings == {"1": {"tools/harness/detect_example.sh"}}


def test_a_removed_packaging_filter_is_a_must_fail_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A packaging script change must start the job that audits it.

    The MUST-FAIL shape from the fix list: if the harness-tooling
    paths-filter stops selecting a packaging script while a rule still covers
    it, the CI mapping claims a gate that a packaging change never starts.
    """
    repo_root = Path(__file__).resolve().parents[3]
    assert sync._check_ci_trigger_coverage(
        json.loads(
            (repo_root / "docs/harness/routing-manifest.json").read_text(encoding="utf-8")
        )["rule_checks"]
    ).status == sync.PASS

    detector = "tools/harness/detect_continuation_comments.py"
    entry = {
        "rule": "73",
        "check": detector,
        "files": ["packaging/**/*.sh"],
        "stage": ["ci"],
    }
    covered = sync._check_ci_trigger_coverage([entry])
    assert covered.status == sync.PASS, covered.detail

    # Simulate the gap: the filter no longer selects the packaging surface.
    patterns = dict(sync._paths_filter_patterns())
    patterns["harness_tooling"] = [
        pattern
        for pattern in patterns["harness_tooling"]
        if not sync._path_pattern_matches(pattern, "packaging/scripts/x.sh")
    ]
    assert "packaging/**/*.sh" not in patterns["harness_tooling"]
    monkeypatch.setattr(sync, "_paths_filter_patterns", lambda: patterns)
    gap = sync._check_ci_trigger_coverage([entry])

    assert gap.status == sync.FAIL
    assert "packaging/**/*.sh" in gap.detail
    assert "would never start the job" in gap.detail
