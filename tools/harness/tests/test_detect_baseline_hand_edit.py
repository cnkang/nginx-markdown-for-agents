"""Pytest tests for detect_baseline_hand_edit.py (Rule 61 lifecycle).

Adversarial fixtures reproduce the churn shapes from the 2026-08-20/21
cluster: hand-edited finalized baselines (d7012e42/cf6aea8e revert pair,
8c899644 derived-field patch) and provenance drift.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import detect_baseline_hand_edit as module


def setup_function(_function):
    module.repo_commit_exists = lambda sha: True


def make_baseline(tmp_path, policy_overrides=None, benchmark_commit=None,
                  baseline_type="verbatim_run"):
    module.REPO_ROOT = tmp_path
    base_dir = tmp_path / "perf" / "baselines"
    base_dir.mkdir(parents=True, exist_ok=True)
    raw = base_dir / "module-baseline-t-raw.json"
    raw.write_text("{\"scenarios\": []}", encoding="utf-8")
    policy = {
        "type": baseline_type,
        "source_git_commit": "a" * 40,
        "source_run": "https://github.com/o/r/actions/runs/1/attempts/1",
        "source_artifact": "perf/baselines/module-baseline-t-raw.json",
        "source_artifact_sha256": module.hashlib.sha256(
            raw.read_bytes()).hexdigest(),
        "measurement_timestamp": "2026-08-21T00:00:00Z",
        "normalization": "none",
    }
    policy.update(policy_overrides or {})
    doc = {
        "module_benchmark": {
            "git_commit": benchmark_commit or policy["source_git_commit"],
            "timestamp": "2026-08-21T00:00:00Z",
        },
        "baseline_policy": policy,
    }
    baseline = base_dir / "module-baseline-t.json"
    import json
    baseline.write_text(json.dumps(doc), encoding="utf-8")
    return baseline


def test_clean_baseline_passes(tmp_path):
    findings = []
    module.audit_baseline(make_baseline(tmp_path), findings)
    assert findings == []


def test_missing_provenance_field_fails_closed(tmp_path):
    findings = []
    module.audit_baseline(
        make_baseline(tmp_path, {"source_run": ""}), findings)
    assert any("source_run" in f for f in findings)


def test_abbreviated_commit_rejected(tmp_path):
    findings = []
    module.audit_baseline(
        make_baseline(tmp_path, {"source_git_commit": "a" * 12}), findings)
    assert any("40-hex" in f for f in findings)


def test_benchmark_policy_commit_mismatch_rejected(tmp_path):
    findings = []
    module.audit_baseline(
        make_baseline(tmp_path, benchmark_commit="b" * 40), findings)
    assert any("does not match" in f for f in findings)


def test_non_utc_timestamp_rejected(tmp_path):
    findings = []
    module.audit_baseline(
        make_baseline(
            tmp_path,
            {"measurement_timestamp": "2026-08-21T00:00:00"},
        ),
        findings,
    )
    assert any("ISO-8601" in f for f in findings)


def test_digest_mismatch_rejected(tmp_path):
    findings = []
    module.audit_baseline(
        make_baseline(tmp_path, {"source_artifact_sha256": "c" * 64}),
        findings,
    )
    assert any("mismatch" in f for f in findings)


def test_absolute_artifact_path_rejected(tmp_path):
    findings = []
    module.audit_baseline(
        make_baseline(tmp_path, {"source_artifact": "/etc/passwd"}),
        findings,
    )
    assert any("repo-relative" in f for f in findings)


def test_archival_import_profile_skips_finalizer_fields(tmp_path):
    baseline = make_baseline(
        tmp_path,
        {
            "type": "verbatim_import",
            "source_run": "",
            "measurement_timestamp": "",
            "normalization": "none; metrics copied from retained report",
        },
        benchmark_commit="a" * 12,
    )
    findings = []
    module.audit_baseline(baseline, findings)
    assert findings == []


def test_changed_mode_flags_finalized_without_raw():
    findings = []
    module.check_changed(
        ["perf/baselines/module-baseline-092.json"], findings)
    assert len(findings) == 1
    assert "hand edits are forbidden" in findings[0]


def test_changed_mode_accepts_finalized_with_raw():
    findings = []
    module.check_changed(
        [
            "perf/baselines/module-baseline-092.json",
            "perf/baselines/module-baseline-092-raw.json",
            "perf/baselines/module-baseline-092-raw-probes/plain-small.json",
        ],
        findings,
    )
    assert findings == []


def test_changed_mode_ignores_unrelated_paths():
    findings = []
    module.check_changed(["docs/README.md", "Makefile"], findings)
    assert findings == []


def test_symlink_escaping_repo_root_rejected(tmp_path):
    import os
    module.REPO_ROOT = tmp_path
    # Target lives OUTSIDE the fake repository root.
    outside = tmp_path.parent / "h-baseline-escape-target.json"
    outside.write_text("{\"leaked\": true}", encoding="utf-8")
    base_dir = tmp_path / "perf" / "baselines"
    base_dir.mkdir(parents=True, exist_ok=True)
    link = base_dir / "module-baseline-t-raw.json"
    os.symlink(outside, link)
    policy = {
        "type": "verbatim_run",
        "source_git_commit": "a" * 40,
        "source_run": "https://github.com/o/r/actions/runs/1/attempts/1",
        "source_artifact": "perf/baselines/module-baseline-t-raw.json",
        "source_artifact_sha256": module.hashlib.sha256(
            outside.read_bytes()).hexdigest(),
        "measurement_timestamp": "2026-08-21T00:00:00Z",
        "normalization": "none",
    }
    doc = {
        "module_benchmark": {
            "git_commit": "a" * 40,
            "timestamp": "2026-08-21T00:00:00Z",
        },
        "baseline_policy": policy,
    }
    import json
    baseline = base_dir / "module-baseline-t.json"
    baseline.write_text(json.dumps(doc), encoding="utf-8")
    findings = []
    module.audit_baseline(baseline, findings)
    assert any("outside the repository" in f for f in findings)
