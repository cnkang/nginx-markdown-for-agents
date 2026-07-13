"""Tests for the harness sync consistency checker.

Covers manifest loading, truth-surface existence, risk-pack doc contracts,
harness documentation references, AGENTS.md mapping, and optional Kiro
adapter drift detection under both quick and full modes.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
        "--skip-dirs .codeartsdoer --skip-dirs .kiro --skip-dirs build\n",
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
        "--skip-dirs .codeartsdoer --skip-dirs .kiro --skip-dirs build\n",
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
