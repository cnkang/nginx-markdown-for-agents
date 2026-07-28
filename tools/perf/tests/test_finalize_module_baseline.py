#!/usr/bin/env python3
"""Tests for finalize_module_baseline.py.

Covers:
  - Valid verbatim_run policy is written with normalized fields
  - Input validation: 40-char SHA, non-empty run URL with attempt
  - raw-input != output enforcement
  - Path safety: absolute, .., symlink escape rejected
  - Measurement timestamp comes from raw module_benchmark.timestamp
  - source_artifact_sha256 is computed and matches the raw file
  - raw commit prefix must match declared source-git-commit
  - Atomic write (no partial output on failure)
  - Metric data is never mutated during finalization
  - Refuses to re-finalize an already-normalized baseline as verbatim_run
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from finalize_module_baseline import (  # noqa: E402
    _build_policy,
    _extract_raw_timestamp,
    _resolve_repo_relative,
    _sha256_file,
    _validate_raw_commit_match,
    _validate_source_run,
    validate_read_path,
    main,
)

_GOOD_SHA = "0123456789abcdef0123456789abcdef01234567"
_GOOD_RUN_URL = (
    "https://github.com/cnkang/nginx-markdown-for-agents/"
    "actions/runs/12345/attempts/2"
)


def _empty_raw_report() -> dict:
    return {
        "module_benchmark": {
            "timestamp": "2026-01-01T00:00:00Z",
            "git_commit": _GOOD_SHA[:7],
            "platform": "linux-x86_64",
            "load_generator": "ab",
            "nginx_version": "nginx version: nginx/1.24.0",
            "scenarios": [],
        },
        "decompression_coverage": {},
    }


def _write_raw(repo: Path, name: str = "raw.json", report: dict | None = None) -> Path:
    """Write a raw report inside a repo root and return its path."""
    path = repo / name
    path.write_text(
        json.dumps(report or _empty_raw_report(), indent=2),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# validate_source_run
# ---------------------------------------------------------------------------


def test_validate_source_run_accepts_run_url_with_attempt() -> None:
    assert _validate_source_run(_GOOD_RUN_URL) == []


def test_finalizer_entrypoint_resolves_shared_tools() -> None:
    """Direct CI execution must import the repository's shared tools package."""
    script = Path(__file__).resolve().parents[1] / "finalize_module_baseline.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_validate_source_run_rejects_empty() -> None:
    errors = _validate_source_run("")
    assert errors
    assert "non-empty" in errors[0]


def test_validate_source_run_rejects_whitespace_only() -> None:
    errors = _validate_source_run("   ")
    assert errors
    assert "non-empty" in errors[0]


def test_validate_source_run_rejects_url_without_run_id() -> None:
    errors = _validate_source_run("https://github.com/foo/bar/actions")
    assert errors
    assert "actions/runs/" in errors[0]


def test_validate_source_run_rejects_url_without_attempt() -> None:
    errors = _validate_source_run(
        "https://github.com/foo/bar/actions/runs/12345"
    )
    assert errors
    assert "attempts" in errors[0]


@pytest.mark.parametrize(
    "source_run",
    [
        "https://github.com/cnkang/nginx-markdown-for-agents/"
        + "actions/runs/not-a-number/attempts/2",
        "http://github.com/cnkang/nginx-markdown-for-agents/"
        + "actions/runs/12345/attempts/2",
    ],
)
def test_validate_source_run_rejects_non_canonical_urls(source_run: str) -> None:
    """Source provenance must identify a concrete HTTPS Actions attempt."""
    assert _validate_source_run(source_run)


# ---------------------------------------------------------------------------
# resolve_repo_relative
# ---------------------------------------------------------------------------


def test_resolve_repo_relative_rejects_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="relative"):
        _resolve_repo_relative("/etc/passwd", must_exist=False, purpose="test")


def test_resolve_repo_relative_rejects_parent_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "repo").mkdir()
    monkeypatch.chdir(tmp_path / "repo")
    with pytest.raises(ValueError, match=r"\.\."):
        _resolve_repo_relative("../outside", must_exist=False, purpose="test")


def test_resolve_repo_relative_rejects_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (repo / "link.json").symlink_to(outside)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())
    with pytest.raises(ValueError, match="escapes repository root"):
        _resolve_repo_relative("link.json", must_exist=True, purpose="test")


# ---------------------------------------------------------------------------
# raw commit match
# ---------------------------------------------------------------------------


def test_validate_raw_commit_match_accepts_prefix() -> None:
    report = _empty_raw_report()
    assert _validate_raw_commit_match(report, _GOOD_SHA) == []


def test_validate_raw_commit_match_rejects_mismatch() -> None:
    report = _empty_raw_report()
    report["module_benchmark"]["git_commit"] = "deadbee"
    errors = _validate_raw_commit_match(report, _GOOD_SHA)
    assert errors
    assert "does not match" in errors[0]


def test_validate_raw_commit_match_checks_full_short_sha_prefix() -> None:
    """A mismatch after the first seven SHA characters must be rejected."""
    report = _empty_raw_report()
    report["module_benchmark"]["git_commit"] = "01234567dead"
    errors = _validate_raw_commit_match(report, _GOOD_SHA)
    assert errors
    assert "does not match" in errors[0]


def test_validate_raw_commit_match_rejects_missing() -> None:
    report = _empty_raw_report()
    del report["module_benchmark"]["git_commit"]
    errors = _validate_raw_commit_match(report, _GOOD_SHA)
    assert errors


# ---------------------------------------------------------------------------
# extract raw timestamp
# ---------------------------------------------------------------------------


def test_extract_raw_timestamp_returns_report_timestamp() -> None:
    report = _empty_raw_report()
    assert _extract_raw_timestamp(report) == "2026-01-01T00:00:00Z"


def test_extract_raw_timestamp_rejects_missing() -> None:
    report = _empty_raw_report()
    del report["module_benchmark"]["timestamp"]
    with pytest.raises(ValueError, match="timestamp"):
        _extract_raw_timestamp(report)


def test_extract_raw_timestamp_rejects_non_string() -> None:
    report = _empty_raw_report()
    report["module_benchmark"]["timestamp"] = 12345
    with pytest.raises(ValueError, match="timestamp"):
        _extract_raw_timestamp(report)


def test_extract_raw_timestamp_rejects_invalid_iso() -> None:
    report = _empty_raw_report()
    report["module_benchmark"]["timestamp"] = "not-a-date"
    with pytest.raises(ValueError, match="ISO-8601"):
        _extract_raw_timestamp(report)


@pytest.mark.parametrize(
    "timestamp",
    ["2026-01-01T00:00:00", "2026-01-01T08:00:00+08:00"],
)
def test_extract_raw_timestamp_requires_utc_offset(timestamp: str) -> None:
    """Naive and non-UTC offsets must not be labeled as UTC evidence."""
    report = _empty_raw_report()
    report["module_benchmark"]["timestamp"] = timestamp
    with pytest.raises(ValueError, match="UTC offset"):
        _extract_raw_timestamp(report)


# ---------------------------------------------------------------------------
# sha256
# ---------------------------------------------------------------------------


def test_sha256_file_matches_known_digest(tmp_path: Path) -> None:
    path = tmp_path / "raw.json"
    payload = b'{"hello": "world"}\n'
    path.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert _sha256_file(path) == expected
    assert len(_sha256_file(path)) == 64


# ---------------------------------------------------------------------------
# main: verbatim_run
# ---------------------------------------------------------------------------


def test_finalizer_writes_verbatim_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "perf" / "baselines").mkdir(parents=True)
    _write_raw(repo, "perf/baselines/raw.json")
    monkeypatch.chdir(repo)
    # REPO_ROOT is computed from __file__; patch it for the test.
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())

    out = repo / "perf" / "baselines" / "finalized.json"
    assert (
        main([
            "--raw-input", "perf/baselines/raw.json",
            "--output", "perf/baselines/finalized.json",
            "--source-git-commit", _GOOD_SHA,
            "--source-run", _GOOD_RUN_URL,
        ])
        == 0
    )
    finalized = json.loads(out.read_text(encoding="utf-8"))
    policy = finalized["baseline_policy"]
    assert policy["type"] == "verbatim_run"
    assert policy["normalization"] == "none"
    assert policy["source_git_commit"] == _GOOD_SHA
    assert policy["source_artifact"] == "perf/baselines/raw.json"
    assert policy["measurement_timestamp"] == "2026-01-01T00:00:00Z"
    assert len(policy["source_artifact_sha256"]) == 64
    # Raw data is preserved verbatim.
    assert finalized["module_benchmark"]["git_commit"] == _GOOD_SHA[:7]
    assert finalized.get("decompression_coverage") == {}


def test_finalizer_computes_correct_raw_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "perf" / "baselines").mkdir(parents=True)
    raw = _write_raw(repo, "perf/baselines/raw.json")
    monkeypatch.chdir(repo)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())

    expected = hashlib.sha256(
        validate_read_path(raw, purpose="test raw baseline").read_bytes()
    ).hexdigest()
    out = repo / "perf" / "baselines" / "finalized.json"
    assert main([
        "--raw-input", "perf/baselines/raw.json",
        "--output", "perf/baselines/finalized.json",
        "--source-git-commit", _GOOD_SHA,
        "--source-run", _GOOD_RUN_URL,
    ]) == 0
    finalized = json.loads(out.read_text(encoding="utf-8"))
    assert finalized["baseline_policy"]["source_artifact_sha256"] == expected


def test_finalizer_preserves_measured_scenario_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "perf" / "baselines").mkdir(parents=True)
    report = _empty_raw_report()
    report["module_benchmark"]["scenarios"] = [
        {"name": "streaming-first", "metrics": {"zero_copy_output_total": 47380, "rps": 30}},
    ]
    _write_raw(repo, "perf/baselines/raw.json", report)
    monkeypatch.chdir(repo)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())
    assert main([
        "--raw-input", "perf/baselines/raw.json",
        "--output", "perf/baselines/finalized.json",
        "--source-git-commit", _GOOD_SHA,
        "--source-run", _GOOD_RUN_URL,
    ]) == 0
    out = json.loads((repo / "perf/baselines/finalized.json").read_text(encoding="utf-8"))
    assert out["module_benchmark"]["scenarios"][0]["metrics"]["rps"] == 30


def test_finalizer_rejects_raw_input_equals_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "perf" / "baselines").mkdir(parents=True)
    _write_raw(repo, "perf/baselines/raw.json")
    monkeypatch.chdir(repo)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())
    assert main([
        "--raw-input", "perf/baselines/raw.json",
        "--output", "perf/baselines/raw.json",
        "--source-git-commit", _GOOD_SHA,
        "--source-run", _GOOD_RUN_URL,
    ]) == 1


def test_finalizer_rejects_source_artifact_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "perf" / "baselines").mkdir(parents=True)
    _write_raw(repo, "perf/baselines/raw.json")
    monkeypatch.chdir(repo)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())
    assert main([
        "--raw-input", "perf/baselines/raw.json",
        "--output", "perf/baselines/finalized.json",
        "--source-git-commit", _GOOD_SHA,
        "--source-run", _GOOD_RUN_URL,
        "--source-artifact", "perf/baselines/other-raw.json",
    ]) == 1


def test_finalizer_rejects_output_file_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finalized output symlink must not redirect writes outside the repo."""
    repo = tmp_path / "repo"
    (repo / "perf" / "baselines").mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("sentinel\n", encoding="utf-8")
    (repo / "perf" / "baselines" / "out.json").symlink_to(outside)
    _write_raw(repo, "perf/baselines/raw.json")
    monkeypatch.chdir(repo)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())
    assert main([
        "--raw-input", "perf/baselines/raw.json",
        "--output", "perf/baselines/out.json",
        "--source-git-commit", _GOOD_SHA,
        "--source-run", _GOOD_RUN_URL,
    ]) == 1
    assert outside.read_text(encoding="utf-8") == "sentinel\n"


def test_finalizer_rejects_output_parent_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlinked output directory must not redirect the atomic replace."""
    repo = tmp_path / "repo"
    (repo / "perf" / "baselines").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "perf" / "baselines" / "linked").symlink_to(outside, target_is_directory=True)
    _write_raw(repo, "perf/baselines/raw.json")
    monkeypatch.chdir(repo)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())
    assert main([
        "--raw-input", "perf/baselines/raw.json",
        "--output", "perf/baselines/linked/out.json",
        "--source-git-commit", _GOOD_SHA,
        "--source-run", _GOOD_RUN_URL,
    ]) == 1
    assert not (outside / "out.json").exists()


def test_finalizer_rejects_missing_raw_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())
    assert main([
        "--raw-input", "perf/baselines/nope.json",
        "--output", "perf/baselines/out.json",
        "--source-git-commit", _GOOD_SHA,
        "--source-run", _GOOD_RUN_URL,
    ]) == 1


def test_finalizer_rejects_bad_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())
    assert main([
        "--raw-input", "perf/baselines/raw.json",
        "--output", "perf/baselines/out.json",
        "--source-git-commit", "short",
        "--source-run", _GOOD_RUN_URL,
    ]) == 1
    assert "40-character" in capsys.readouterr().err


def test_finalizer_rejects_run_without_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    repo = tmp_path / "repo"
    (repo / "perf" / "baselines").mkdir(parents=True)
    _write_raw(repo, "perf/baselines/raw.json")
    monkeypatch.chdir(repo)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())
    assert main([
        "--raw-input", "perf/baselines/raw.json",
        "--output", "perf/baselines/out.json",
        "--source-git-commit", _GOOD_SHA,
        "--source-run", "https://github.com/foo/bar/actions/runs/1",
    ]) == 1
    assert "attempts" in capsys.readouterr().err


def test_finalizer_rejects_commit_prefix_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    repo = tmp_path / "repo"
    (repo / "perf" / "baselines").mkdir(parents=True)
    report = _empty_raw_report()
    report["module_benchmark"]["git_commit"] = "deadbee"
    _write_raw(repo, "perf/baselines/raw.json", report)
    monkeypatch.chdir(repo)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())
    assert main([
        "--raw-input", "perf/baselines/raw.json",
        "--output", "perf/baselines/out.json",
        "--source-git-commit", _GOOD_SHA,
        "--source-run", _GOOD_RUN_URL,
    ]) == 1
    assert "does not match" in capsys.readouterr().err


def test_finalizer_rejects_explicit_measurement_timestamp_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    repo = tmp_path / "repo"
    (repo / "perf" / "baselines").mkdir(parents=True)
    _write_raw(repo, "perf/baselines/raw.json")
    monkeypatch.chdir(repo)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())
    assert main([
        "--raw-input", "perf/baselines/raw.json",
        "--output", "perf/baselines/out.json",
        "--source-git-commit", _GOOD_SHA,
        "--source-run", _GOOD_RUN_URL,
        "--measurement-timestamp", "2025-12-31T00:00:00Z",
    ]) == 1
    err = capsys.readouterr().err
    assert "does not match" in err


def test_finalizer_accepts_matching_measurement_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "perf" / "baselines").mkdir(parents=True)
    _write_raw(repo, "perf/baselines/raw.json")
    monkeypatch.chdir(repo)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())
    assert main([
        "--raw-input", "perf/baselines/raw.json",
        "--output", "perf/baselines/out.json",
        "--source-git-commit", _GOOD_SHA,
        "--source-run", _GOOD_RUN_URL,
        "--measurement-timestamp", "2026-01-01T00:00:00Z",
    ]) == 0


def test_finalizer_refuses_re_finalize_as_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    repo = tmp_path / "repo"
    (repo / "perf" / "baselines").mkdir(parents=True)
    report = _empty_raw_report()
    report["baseline_policy"] = {"type": "verbatim_run"}
    _write_raw(repo, "perf/baselines/raw.json", report)
    monkeypatch.chdir(repo)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())
    assert main([
        "--raw-input", "perf/baselines/raw.json",
        "--output", "perf/baselines/out.json",
        "--source-git-commit", _GOOD_SHA,
        "--source-run", _GOOD_RUN_URL,
    ]) == 1
    assert "already contains" in capsys.readouterr().err


def test_finalizer_atomic_write_no_partial_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No .tmp file remains after a successful write."""
    repo = tmp_path / "repo"
    (repo / "perf" / "baselines").mkdir(parents=True)
    _write_raw(repo, "perf/baselines/raw.json")
    monkeypatch.chdir(repo)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())
    assert main([
        "--raw-input", "perf/baselines/raw.json",
        "--output", "perf/baselines/out.json",
        "--source-git-commit", _GOOD_SHA,
        "--source-run", _GOOD_RUN_URL,
    ]) == 0
    assert not (repo / "perf" / "baselines" / "out.json.tmp").exists()
    assert (repo / "perf" / "baselines" / "out.json").exists()


def test_finalizer_conservative_normalized_records_adjustments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "perf" / "baselines").mkdir(parents=True)
    _write_raw(repo, "perf/baselines/raw.json")
    monkeypatch.chdir(repo)
    import finalize_module_baseline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo.resolve())
    out = repo / "perf" / "baselines" / "out.json"
    assert main([
        "--raw-input", "perf/baselines/raw.json",
        "--output", "perf/baselines/out.json",
        "--source-git-commit", _GOOD_SHA,
        "--source-run", _GOOD_RUN_URL,
        "--policy-type", "conservative_normalized",
        "--adjustments", '{"rps": {"plain-small": -2}}',
        "--adjustment-reason", "user-approved rounding",
        "--adjustment-date", "2026-07-28",
    ]) == 0
    finalized = json.loads(out.read_text(encoding="utf-8"))
    policy = finalized["baseline_policy"]
    assert policy["type"] == "conservative_normalized"
    assert policy["adjustments"] == {"rps": {"plain-small": -2}}
    assert policy["adjustment_reason"] == "user-approved rounding"
    assert policy["source_artifact_sha256"]
    assert "source_artifact_sha256" in policy


# ---------------------------------------------------------------------------
# build_policy unit
# ---------------------------------------------------------------------------


def test_build_policy_verbatim_includes_sha256() -> None:
    policy = _build_policy(
        policy_type="verbatim_run",
        source_git_commit=_GOOD_SHA,
        source_run=_GOOD_RUN_URL,
        source_artifact="perf/baselines/raw.json",
        raw_sha256="a" * 64,
        measurement_timestamp="2026-01-01T00:00:00Z",
        adjustment_fields={},
    )
    assert policy["source_artifact_sha256"] == "a" * 64
    assert policy["normalization"] == "none"


def test_build_policy_conservative_records_rule() -> None:
    policy = _build_policy(
        policy_type="conservative_normalized",
        source_git_commit=_GOOD_SHA,
        source_run=_GOOD_RUN_URL,
        source_artifact="perf/baselines/raw.json",
        raw_sha256="b" * 64,
        measurement_timestamp="2026-01-01T00:00:00Z",
        adjustment_fields={
            "adjustments": '{"rps": 1}',
            "adjustment_reason": "test",
            "adjustment_date": "2026-07-28",
        },
    )
    assert policy["normalization"] == "conservative"
    assert "RPS" in policy["adjustment_rule"]
