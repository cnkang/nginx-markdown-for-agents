"""Regression tests for tools/release/gates/validate_soak_qualification.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.release.gates import validate_soak_qualification as validator

REPO_ROOT = Path(validator.__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "release"
MANIFEST = FIXTURE_DIR / "soak-qualification-manifest.json"


def _run_fixture(record_name: str) -> int:
    return validator.main(
        [
            "--mode",
            "fixture",
            "--manifest",
            str(MANIFEST),
            "--record-input",
            str(FIXTURE_DIR / record_name),
        ]
    )


def test_valid_soak_record_passes() -> None:
    assert _run_fixture("soak-qualification-valid.json") == 0


def test_fixture_skip_record_is_accepted_before_threshold_checks(
    tmp_path: Path,
) -> None:
    """An explicitly skipped fixture does not need fabricated soak evidence."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record = json.loads(
        (FIXTURE_DIR / "soak-qualification-valid.json").read_text(
            encoding="utf-8"
        )
    )
    record["candidate_sha"] = "f" * 40
    record["status"] = "skip"
    record["skip_reason"] = "module binary unavailable in fixture environment"

    manifest_path = tmp_path / "manifest.json"
    record_path = tmp_path / "record.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    record_path.write_text(json.dumps(record), encoding="utf-8")

    assert validator.main(
        [
            "--mode",
            "fixture",
            "--manifest",
            str(manifest_path),
            "--record-input",
            str(record_path),
        ]
    ) == 0


def test_below_threshold_fails() -> None:
    assert _run_fixture("soak-qualification-below-threshold.json") == 1


def test_malformed_fails() -> None:
    with pytest.raises(SystemExit, match="malformed|missing-observation"):
        _run_fixture("soak-qualification-schema-invalid.json")


def test_blocking_pending_fails() -> None:
    assert _run_fixture("soak-qualification-blocking-pending.json") == 1


def test_stale_digest_fails() -> None:
    assert _run_fixture("soak-qualification-stale-digest.json") == 1


def test_missing_observation_fails() -> None:
    with pytest.raises(SystemExit, match="missing-observation"):
        _run_fixture("soak-qualification-missing-observation.json")


def test_manifest_contract_valid() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["duration_minutes"] == 30
    assert manifest["concurrency"] == 16
    assert {entry["id"] for entry in manifest["corpus"]} == {
        "small", "medium", "large"
    }


def test_real_soak_requires_module_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real mode must not run a stock NGINX without the module."""
    args = type("Args", (), {"allow_skip_soak": False})()
    manifest = {"candidate_sha": "a" * 40}
    monkeypatch.setattr(validator, "_validated_nginx_binary", lambda: Path("nginx"))
    monkeypatch.setattr(validator, "_validated_module", lambda: None)

    assert validator.handle_missing_nginx(args, manifest) == 1


def test_allowed_soak_skip_writes_a_structurally_valid_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {"candidate_sha": "a" * 40, "concurrency": 4}
    args = type("Args", (), {
        "allow_skip_soak": True,
        "output": None,
        "record": "artifacts/release/0.9.2/soak-record.json",
    })()
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "_validated_nginx_binary", lambda: None)
    monkeypatch.setattr(validator, "_validated_module", lambda: None)

    assert validator.handle_missing_nginx(args, manifest) == 0
    record = json.loads(
        (tmp_path / "artifacts" / "release" / "0.9.2" /
         "soak-record.json").read_text(encoding="utf-8")
    )
    validator.validate_record_structure(record)
    assert record["status"] == "skip"


def test_manifest_rejects_path_like_scenario_id(tmp_path: Path) -> None:
    """Scenario IDs must remain within the fixed local URL allowlist."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["corpus"][0]["id"] = "../escape"
    staged = tmp_path / "manifest.json"
    staged.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SystemExit, match="must be one of"):
        validator.load_manifest(str(staged))


def test_local_url_rejects_traversal() -> None:
    """The HTTP client must not accept a traversal URL path."""
    with pytest.raises(ValueError, match="Invalid"):
        validator._validated_local_url("http://127.0.0.1:19200/../etc/passwd")


def test_worker_child_skips_malformed_pid() -> None:
    """A malformed process-table row must not abort worker discovery."""
    output = "not-a-pid 42 nginx\n1234 42 nginx: worker process\n"
    assert validator._find_worker_child(output, 42) == 1234


def test_worker_child_returns_not_found_for_only_malformed_rows() -> None:
    """Malformed matching rows are not valid worker PIDs."""
    assert validator._find_worker_child("not-a-pid 42 nginx\n", 42) == -1


def test_worker_child_ignores_unrelated_child_processes() -> None:
    """A same-parent helper process must not become RSS evidence."""
    assert validator._find_worker_child("1234 42 helper\n", 42) == -1


def test_rss_evidence_requires_samples_and_nonnegative_values() -> None:
    """A pass record cannot omit or sentinel-fill worker RSS evidence."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record = json.loads(
        (FIXTURE_DIR / "soak-qualification-valid.json").read_text(
            encoding="utf-8"
        )
    )

    record["rss_time_series"] = []
    with pytest.raises(
        validator.SoakQualificationValidationError, match="insufficient-data"
    ):
        validator.validate_soak_outcome(record, manifest)

    record["rss_time_series"] = [[0.0, 100], [1.0, 101], [2.0, -1]]
    with pytest.raises(
        validator.SoakQualificationValidationError, match="insufficient-data"
    ):
        validator.validate_soak_outcome(record, manifest)

    record["rss_time_series"] = [[0.0, 100], [1.0, 101], [2.0, 102]]
    record["worker_rss_drain_samples"] = []
    with pytest.raises(
        validator.SoakQualificationValidationError, match="insufficient-data"
    ):
        validator.validate_soak_outcome(record, manifest)


def test_peak_memory_metric_parser_requires_positive_gauge() -> None:
    body = (
        "# TYPE nginx_markdown_streaming_peak_memory_bytes gauge\n"
        "nginx_markdown_streaming_peak_memory_bytes 65536\n"
    )
    assert validator._parse_peak_memory_metric(body) == 65536
    assert validator._parse_peak_memory_metric(
        "nginx_markdown_streaming_peak_memory_bytes 0\n"
    ) is None
    assert validator._parse_peak_memory_metric("other_metric 65536\n") is None


def test_runtime_directory_rejects_external_override(
    tmp_path: Path, monkeypatch
) -> None:
    """SOAK_RUNTIME_DIR must stay inside the repository build tree."""
    monkeypatch.setenv("SOAK_RUNTIME_DIR", str(tmp_path / "runtime"))

    with pytest.raises(ValueError, match="escapes root"):
        validator._runtime_directory()


def test_soak_nginx_runs_in_foreground_for_reliable_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The soak master must own the launcher process and its shutdown."""
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    runtime_dir = tmp_path / "runtime"
    document_root = runtime_dir / "html"
    document_root.mkdir(parents=True)

    validator.write_nginx_conf(runtime_dir, 19000, str(document_root), None)
    config = (runtime_dir / "nginx.conf").read_text(encoding="utf-8")

    assert "daemon off;" in config
    assert f"pid {runtime_dir}/nginx.pid;" in config


def test_record_output_path_rejects_external_override(tmp_path: Path) -> None:
    """Qualification records must stay under the generated output root."""
    args = type("Args", (), {
        "output": str(tmp_path / "soak-record.json"),
        "record": "unused.json",
    })()

    with pytest.raises(ValueError, match="Output path"):
        validator._write_record({}, args)


def test_negative_error_rate_is_not_treated_as_zero() -> None:
    """Non-zero floating-point error rates must fail the fixture gate."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record = json.loads(
        (FIXTURE_DIR / "soak-qualification-valid.json").read_text(
            encoding="utf-8"
        )
    )
    record["per_scenario"][0]["error_rate"] = -0.1

    with pytest.raises(
        validator.SoakQualificationValidationError, match="error_rate"
    ):
        validator.validate_soak_outcome(record, manifest)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "0"])
def test_non_finite_error_rate_is_rejected(value) -> None:
    """NaN, infinity, and non-numeric error rates cannot qualify."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record = json.loads(
        (FIXTURE_DIR / "soak-qualification-valid.json").read_text(
            encoding="utf-8"))
    record["per_scenario"][0]["error_rate"] = value

    with pytest.raises(
        validator.SoakQualificationValidationError, match="error_rate"
    ):
        validator.validate_soak_outcome(record, manifest)


def test_parse_ab_report_uses_requests_per_second() -> None:
    """rps must come from ab's request-rate line, not transfer rate."""
    report = validator.parse_ab_report(
        "Transfer rate: 9000.00 [Kbytes/sec] received\n"
        "Requests per second:    42.50 [#/sec] (mean)\n")

    assert report["rps"] == 42.5


def test_missing_per_request_peak_is_insufficient_data() -> None:
    """A pass record must include an observed module-managed peak."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record = json.loads(
        (FIXTURE_DIR / "soak-qualification-valid.json").read_text(
            encoding="utf-8"
        )
    )
    record["module_managed_peak_observed"] = False
    record["per_request_peak_bytes"] = None

    with pytest.raises(
        validator.SoakQualificationValidationError, match="insufficient-data"
    ):
        validator.validate_soak_outcome(record, manifest)


def test_real_mode_records_insufficient_peak_as_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """Real mode must write fail, not pass, without module peak evidence."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record_path = (
        tmp_path / "artifacts" / "release" / "0.9.2" / "soak-record.json"
    )
    runtime_dir = tmp_path / "runtime"

    class FakeNginx:
        def terminate(self) -> None:
            pass

        def wait(self, timeout: int) -> None:
            pass

    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "load_manifest", lambda path: manifest)
    monkeypatch.setattr(validator, "handle_missing_nginx", lambda args, data: None)
    monkeypatch.setattr(
        validator,
        "prepare_runtime",
        lambda base_url, data, module_so: (runtime_dir, {"small": "small.html"}, FakeNginx()),
    )
    monkeypatch.setattr(validator, "wait_for_ready", lambda url: True)
    monkeypatch.setattr(validator, "find_worker_pid", lambda path: -1)
    monkeypatch.setattr(
        validator,
        "run_load_loop",
        lambda corpus, worker_pid, duration, started, concurrency, runtime: (
            [], {}
        ),
    )
    monkeypatch.setattr(
        validator,
        "measure_drain",
        lambda worker_pid: (0, False, [100, 100, 100]),
    )
    monkeypatch.setattr(validator, "read_module_peak_memory", lambda base_url: None)

    args = type(
        "Args",
        (),
        {
            "manifest": str(MANIFEST),
            "record": "artifacts/release/0.9.2/soak-record.json",
            "output": None,
            "allow_skip_soak": False,
        },
    )()

    assert validator.real_main(args) == 1
    saved = json.loads(record_path.read_text(encoding="utf-8"))
    assert saved["status"] == "fail"
    assert any("insufficient-data" in error for error in saved["errors"])


def test_real_mode_cannot_pass_with_missing_worker_rss_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    """A valid module peak cannot mask a missing worker RSS observation."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record_path = (
        tmp_path / "artifacts" / "release" / "0.9.2" / "soak-record.json"
    )
    runtime_dir = tmp_path / "runtime"

    class FakeNginx:
        def terminate(self) -> None:
            pass

        def wait(self, timeout: int) -> None:
            pass

    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "load_manifest", lambda path: manifest)
    monkeypatch.setattr(validator, "handle_missing_nginx", lambda args, data: None)
    monkeypatch.setattr(
        validator,
        "prepare_runtime",
        lambda base_url, data, module_so: (
            runtime_dir,
            {"small": "small.html"},
            FakeNginx(),
        ),
    )
    monkeypatch.setattr(validator, "wait_for_ready", lambda url: True)
    monkeypatch.setattr(validator, "find_worker_pid", lambda path: -1)
    monkeypatch.setattr(
        validator,
        "run_load_loop",
        lambda corpus, worker_pid, duration, started, concurrency, runtime: (
            [], {}
        ),
    )
    monkeypatch.setattr(
        validator,
        "measure_drain",
        lambda worker_pid: (None, False, []),
    )
    monkeypatch.setattr(validator, "read_module_peak_memory", lambda base_url: 65536)

    args = type(
        "Args",
        (),
        {
            "manifest": str(MANIFEST),
            "record": "artifacts/release/0.9.2/soak-record.json",
            "output": None,
            "allow_skip_soak": False,
        },
    )()

    assert validator.real_main(args) == 1
    saved = json.loads(record_path.read_text(encoding="utf-8"))
    assert saved["status"] == "fail"
    assert any("worker RSS" in error for error in saved["errors"])
