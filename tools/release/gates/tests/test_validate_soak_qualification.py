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


def test_below_threshold_fails() -> None:
    with pytest.raises(SystemExit, match="below-threshold"):
        _run_fixture("soak-qualification-below-threshold.json")


def test_malformed_fails() -> None:
    with pytest.raises(SystemExit, match="malformed|missing-observation"):
        _run_fixture("soak-qualification-malformed.json")


def test_blocking_pending_fails() -> None:
    with pytest.raises(SystemExit, match="blocking-pending"):
        _run_fixture("soak-qualification-blocking-pending.json")


def test_stale_digest_fails() -> None:
    with pytest.raises(SystemExit, match="stale-digest"):
        _run_fixture("soak-qualification-stale-digest.json")


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

    with pytest.raises(SystemExit, match="error_rate"):
        validator.validate_soak_outcome(record, manifest)


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

    with pytest.raises(SystemExit, match="insufficient-data"):
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
    monkeypatch.setattr(validator, "measure_drain", lambda worker_pid: (0, False))
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
