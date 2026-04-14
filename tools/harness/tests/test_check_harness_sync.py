from __future__ import annotations

import json
from pathlib import Path

from tools.harness import check_harness_sync as sync


def test_collect_results_skips_missing_kiro(tmp_path, monkeypatch):
    repo = tmp_path
    _write_repo_fixture(repo, with_kiro=False)
    monkeypatch.setattr(sync, "REPO_ROOT", repo)
    monkeypatch.setattr(sync, "MANIFEST_PATH", repo / "docs/harness/routing-manifest.json")
    monkeypatch.setattr(sync, "README_PATH", repo / "docs/harness/README.md")
    monkeypatch.setattr(sync, "CORE_PATH", repo / "docs/harness/core.md")
    monkeypatch.setattr(sync, "SUMMARY_PATH", repo / "docs/harness/routing-manifest.md")
    monkeypatch.setattr(sync, "PACK_INDEX_PATH", repo / "docs/harness/risk-packs/README.md")
    monkeypatch.setattr(sync, "AGENTS_PATH", repo / "AGENTS.md")

    results = sync.collect_results()
    adapter = next(item for item in results if item.name == "kiro-adapters")
    assert adapter.status == sync.SKIP_NOT_PRESENT


def test_collect_results_warns_for_local_kiro_drift(tmp_path, monkeypatch):
    repo = tmp_path
    _write_repo_fixture(repo, with_kiro=True, kiro_has_links=False)
    monkeypatch.setattr(sync, "REPO_ROOT", repo)
    monkeypatch.setattr(sync, "MANIFEST_PATH", repo / "docs/harness/routing-manifest.json")
    monkeypatch.setattr(sync, "README_PATH", repo / "docs/harness/README.md")
    monkeypatch.setattr(sync, "CORE_PATH", repo / "docs/harness/core.md")
    monkeypatch.setattr(sync, "SUMMARY_PATH", repo / "docs/harness/routing-manifest.md")
    monkeypatch.setattr(sync, "PACK_INDEX_PATH", repo / "docs/harness/risk-packs/README.md")
    monkeypatch.setattr(sync, "AGENTS_PATH", repo / "AGENTS.md")

    results = sync.collect_results(full=False)
    adapter = next(item for item in results if item.name == "kiro-adapters")
    assert adapter.status == sync.WARN_NEEDS_AUTHOR_REVIEW

    full_results = sync.collect_results(full=True)
    full_adapter = next(item for item in full_results if item.name == "kiro-adapters")
    assert full_adapter.status == sync.FAIL


def test_collect_results_fail_when_pack_doc_missing(tmp_path, monkeypatch):
    repo = tmp_path
    _write_repo_fixture(repo, with_kiro=False)
    (repo / "docs/harness/risk-packs/runtime-streaming.md").unlink()

    monkeypatch.setattr(sync, "REPO_ROOT", repo)
    monkeypatch.setattr(sync, "MANIFEST_PATH", repo / "docs/harness/routing-manifest.json")
    monkeypatch.setattr(sync, "README_PATH", repo / "docs/harness/README.md")
    monkeypatch.setattr(sync, "CORE_PATH", repo / "docs/harness/core.md")
    monkeypatch.setattr(sync, "SUMMARY_PATH", repo / "docs/harness/routing-manifest.md")
    monkeypatch.setattr(sync, "PACK_INDEX_PATH", repo / "docs/harness/risk-packs/README.md")
    monkeypatch.setattr(sync, "AGENTS_PATH", repo / "AGENTS.md")

    results = sync.collect_results()
    pack = next(item for item in results if item.name == "risk-pack-contract")
    assert pack.status == sync.FAIL
    assert "missing docs" in pack.detail


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
    monkeypatch.setattr(sync, "PACK_INDEX_PATH", repo / "docs/harness/risk-packs/README.md")
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
    monkeypatch.setattr(sync, "PACK_INDEX_PATH", repo / "docs/harness/risk-packs/README.md")
    monkeypatch.setattr(sync, "AGENTS_PATH", repo / "AGENTS.md")

    results = sync.collect_results()
    assert len(results) == 1
    assert results[0].name == "manifest-structure"
    assert results[0].status == sync.FAIL
    assert "truth surface keys" in results[0].detail


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
        "spec_resolver": {},
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
        "core.md\nrouting-manifest.json\nrouting-manifest.md\nrisk-packs/README.md\n",
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
        "docs/harness/README.md\ndocs/harness/core.md\ndocs/harness/routing-manifest.json\nCodex-first\n",
        encoding="utf-8",
    )

    if with_kiro:
        (repo / ".kiro/steering").mkdir(parents=True, exist_ok=True)
        content = "docs/harness/README.md\ndocs/harness/core.md\n" if kiro_has_links else "old doc\n"
        for name in ("product.md", "structure.md", "tech.md"):
            (repo / f".kiro/steering/{name}").write_text(content, encoding="utf-8")
