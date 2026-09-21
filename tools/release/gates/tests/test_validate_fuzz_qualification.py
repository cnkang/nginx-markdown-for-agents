"""Regression tests for the fuzz qualification gate validator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tools.release.gates import validate_fuzz_qualification as validator

MANIFEST_FIXTURE = "fuzz-qualification-manifest.json"


def _fixture_path(name: str) -> Path:
    """Return the repo-relative path of a release fixture."""
    return Path(validator.REPO_ROOT) / "tests" / "fixtures" / "release" / name


def _write_staged(tmp_path: Path, name: str) -> Path:
    """Copy a fixture into tmp_path and return its staged path."""
    staged = tmp_path / name
    staged.write_text(_fixture_path(name).read_text(encoding="utf-8"),
                      encoding="utf-8")
    return staged


def _fixture_argv(tmp_path: Path, record_name: str) -> list[str]:
    """Build fixture-mode argv with manifest and record staged in tmp_path."""
    manifest = _write_staged(tmp_path, MANIFEST_FIXTURE)
    record = _write_staged(tmp_path, record_name)
    return [
        "validate_fuzz_qualification.py",
        "--mode", "fixture",
        "--manifest", str(manifest),
        "--record-input", str(record),
    ]


def _run(monkeypatch, capsys, *flags: str) -> int:
    """Run the validator CLI with staged argv."""
    monkeypatch.setattr(sys, "argv", list(flags))
    return validator.main()


def test_valid_fixture_passes(tmp_path: Path, monkeypatch, capsys) -> None:
    """A fully qualified record must pass fixture-mode validation."""
    rc = _run(monkeypatch, capsys,
              *_fixture_argv(tmp_path, "fuzz-qualification-valid.json"))
    captured = capsys.readouterr()

    assert rc == 0
    assert "PASS:" in captured.out


def test_below_threshold_fixture_fails(tmp_path: Path, monkeypatch,
                                       capsys) -> None:
    """Below-threshold runs must be rejected with an identifiable reason."""
    rc = _run(monkeypatch, capsys,
              *_fixture_argv(
                  tmp_path, "fuzz-qualification-below-threshold.json"))
    captured = capsys.readouterr()

    assert rc == 1
    assert "below-threshold" in captured.err


def test_malformed_fixture_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    """A truncated record must be rejected as malformed."""
    rc = _run(monkeypatch, capsys,
              *_fixture_argv(tmp_path, "fuzz-qualification-malformed.json"))
    captured = capsys.readouterr()

    assert rc == 1
    assert "malformed" in captured.err


def test_blocking_pending_fixture_fails(tmp_path: Path, monkeypatch,
                                        capsys) -> None:
    """A blocking target that is not pass must be rejected as pending."""
    rc = _run(monkeypatch, capsys,
              *_fixture_argv(
                  tmp_path, "fuzz-qualification-blocking-pending.json"))
    captured = capsys.readouterr()

    assert rc == 1
    assert "blocking-pending" in captured.err


def test_stale_digest_fixture_fails(tmp_path: Path, monkeypatch,
                                    capsys) -> None:
    """A record for a different candidate sha must be rejected as stale."""
    rc = _run(monkeypatch, capsys,
              *_fixture_argv(
                  tmp_path, "fuzz-qualification-stale-digest.json"))
    captured = capsys.readouterr()

    assert rc == 1
    assert "stale-digest" in captured.err
    assert "candidate sha mismatch" in captured.err


def test_missing_observation_fixture_fails(tmp_path: Path, monkeypatch,
                                           capsys) -> None:
    """Incomplete per-target observations must be rejected explicitly."""
    rc = _run(monkeypatch, capsys,
              *_fixture_argv(
                  tmp_path, "fuzz-qualification-missing-observation.json"))
    captured = capsys.readouterr()

    assert rc == 1
    assert "missing-observation" in captured.err
    assert "executions_total" in captured.err


@pytest.mark.parametrize("field", ["elapsed_seconds_total", "crashes",
                                    "sanitizer_findings"])
def test_non_finite_or_non_integer_observations_fail(
        field: str, tmp_path: Path, monkeypatch, capsys) -> None:
    """Non-finite measurements and non-integer zero counters fail closed."""
    manifest = _write_staged(tmp_path, MANIFEST_FIXTURE)
    record = json.loads(
        _fixture_path("fuzz-qualification-valid.json").read_text(
            encoding="utf-8"))
    record["per_target"][0][field] = (
        float("nan") if field == "elapsed_seconds_total" else "0")
    record_path = tmp_path / "record.json"
    record_path.write_text(json.dumps(record), encoding="utf-8")

    rc = _run(monkeypatch, capsys,
              "validate_fuzz_qualification.py", "--mode", "fixture",
              "--manifest", str(manifest), "--record-input", str(record_path))

    captured = capsys.readouterr()
    assert rc == 1
    assert field in captured.err
    if field == "elapsed_seconds_total":
        assert "finite numeric" in captured.err
    else:
        assert "must be an integer" in captured.err


def test_fixture_mode_requires_record_input(tmp_path: Path, monkeypatch,
                                            capsys) -> None:
    """Fixture mode must fail closed when no record input is provided."""
    manifest = _write_staged(tmp_path, MANIFEST_FIXTURE)
    rc = _run(monkeypatch, capsys,
              "validate_fuzz_qualification.py",
              "--mode", "fixture",
              "--manifest", str(manifest))
    captured = capsys.readouterr()

    assert rc == 1
    assert "malformed" in captured.err


def test_target_manifest_rejects_path_like_target_name() -> None:
    """Manifest target names must not become command or path components."""
    manifest = json.loads(
        _fixture_path(MANIFEST_FIXTURE).read_text(encoding="utf-8")
    )
    manifest["targets"][0]["name"] = "../escape"

    with pytest.raises(ValueError, match="invalid"):
        validator.validate_target_manifest(manifest)


def test_record_output_path_stays_within_repository(tmp_path: Path) -> None:
    """Real-mode output cannot be redirected by a caller."""
    args = type("Args", (), {"output": str(tmp_path / "record.json"),
                              "record": "unused.json"})()

    with pytest.raises(ValueError, match="Output path"):
        validator._write_record({}, args)


def test_record_output_path_cannot_change_artifact_name() -> None:
    """Even a repository-local alternate filename is rejected."""
    args = type("Args", (), {"output": None, "record": "alternate.json"})()

    with pytest.raises(ValueError, match="fixed"):
        validator._write_record({}, args)


def test_seed_digest_rejects_escape_when_called_directly(
        tmp_path: Path, monkeypatch) -> None:
    """Digest verification must repeat the corpus-root boundary check."""
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    (tmp_path / "outside-seed").write_bytes(b"outside")
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "CORPUS_ROOT", corpus_root)

    error = validator._validate_seed_digest(
        {"seed_path": "../outside-seed", "digest": "sha256:" + "0" * 64},
        "parser_html",
    )

    assert error is not None
    assert "unreadable" in error


def test_skipped_target_record_keeps_seed_path_field() -> None:
    entry = {"name": "convert_html", "seed": 7}

    record = validator._skipped_record(entry, "not blocking")

    assert "seed_path" in record


def test_real_mode_rejects_malformed_manifest(tmp_path: Path, monkeypatch,
                                              capsys) -> None:
    """Real mode must fail closed on a malformed target manifest."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text("not json", encoding="utf-8")
    rc = _run(monkeypatch, capsys,
              "validate_fuzz_qualification.py",
              "--mode", "real",
              "--manifest", str(manifest),
              "--corpus-manifest", str(tmp_path / "corpus.json"),
              "--record", str(tmp_path / "record.json"))
    captured = capsys.readouterr()

    assert rc == 1
    assert "malformed" in captured.err


def test_real_mode_fails_closed_when_corpus_seed_missing(
        tmp_path: Path, monkeypatch, capsys) -> None:
    """A blocking target without a seed entry must fail before fuzzing."""
    manifest = _write_staged(tmp_path, MANIFEST_FIXTURE)
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps({
        "schema_version": "release.corpus-seed.v1",
        "candidate_sha": "9d" * 20,
        "seeds": [{
            "target": "parser_html",
            "seed_path": "components/rust-converter/fuzz/corpus/parser_html",
            "digest": "0123456789abcdef",
        }],
    }), encoding="utf-8")
    rc = _run(monkeypatch, capsys,
              "validate_fuzz_qualification.py",
              "--mode", "real",
              "--manifest", str(manifest),
              "--corpus-manifest", str(corpus),
              "--record", str(tmp_path / "record.json"))
    captured = capsys.readouterr()

    assert rc == 1
    assert "missing corpus seed entries" in captured.err
    assert "convert_html" in captured.err


def test_parse_fuzz_output_extracts_stats() -> None:
    """Final-stat lines must be extracted from libFuzzer output."""
    stdout = ("INFO: Running with entropic power schedule\n"
              "stat::number_of_executed_units: 150000\n"
              "stat::elapsed_seconds: 950\n")

    executions, elapsed, finding = validator._parse_fuzz_output(stdout, "")

    assert executions == 150000
    assert elapsed == 950.0
    assert finding is None


def test_parse_fuzz_output_detects_sanitizer_marker() -> None:
    """A sanitizer report must be surfaced as a failure finding."""
    stdout = ("==ERROR: AddressSanitizer: heap-buffer-overflow on address\n"
              "SUMMARY: AddressSanitizer: heap-buffer-overflow\n")

    executions, elapsed, finding = validator._parse_fuzz_output(stdout, "")

    assert executions == 0
    assert finding is not None
    assert "AddressSanitizer" in finding


def test_classify_finding_distinguishes_crashes_and_sanitizers() -> None:
    """Sanitizer reports must be counted separately from crashes."""
    assert validator._classify_finding("") == (0, 0)
    assert validator._classify_finding(
        "ERROR: libFuzzer: deadly signal") == (1, 0)
    assert validator._classify_finding(
        "SUMMARY: AddressSanitizer: heap-buffer-overflow") == (0, 1)
    assert validator._classify_finding(
        "SUMMARY: UndefinedBehaviorSanitizer: signed integer overflow") == (0, 1)
    assert validator._classify_finding(
        "runtime error: load of misaligned address") == (0, 1)
    assert validator._classify_finding(
        "fuzz job budget exhausted before the floors were met") == (0, 0)
    assert validator._classify_finding(
        "executions floor not reached within the continuation budget") == (0, 0)



def test_soak_excludes_startup_overhead_from_executions(
        tmp_path: Path, monkeypatch) -> None:
    """Startup-corpus replay and the empty-input callback must not count
    toward the required-executions floor (libFuzzer reports them inside
    stat::number_of_executed_units on every launch)."""
    import tools.release.gates.validate_fuzz_qualification as validator

    corpus_dir = tmp_path / "fuzz_target"
    corpus_dir.mkdir()
    (corpus_dir / "seed-a").write_bytes(b"a")
    (corpus_dir / "seed-b").write_bytes(b"b")

    reported = "stat::number_of_executed_units: 100003\n" \
        "stat::seconds_since_epoch: 0\n"
    monkeypatch.setattr(validator, "CORPUS_ROOT", tmp_path)
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path.parent)
    monkeypatch.setattr(
        validator, "_invoke_fuzz",
        lambda target, flags, timeout: {
            "returncode": 0,
            "stdout": reported,
            "stderr": "",
            "wall_elapsed": 1.0,
        })

    result = validator._run_target_soak(
        "fuzz_target", seed=1, required_executions=100000,
        required_seconds=0, log_path=tmp_path / "soak.log")

    assert result["executions_total"] == 100000
    assert result["status"] == "pass"


def test_startup_corpus_size_counts_seed_files(tmp_path: Path) -> None:
    """_startup_corpus_size counts files, and reports 0 for absent dirs."""
    import tools.release.gates.validate_fuzz_qualification as validator

    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "seed-a").write_bytes(b"a")
    (corpus_dir / "seed-b").write_bytes(b"b")
    (corpus_dir / "seed-c").write_bytes(b"c")

    assert validator._startup_corpus_size(corpus_dir) == 3
    assert validator._startup_corpus_size(tmp_path / "absent") == 0


def test_cargo_fuzz_available_uses_the_rustup_shim(
    tmp_path: Path, monkeypatch
) -> None:
    shim = tmp_path / "cargo"
    shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setattr(validator, "_resolve_fuzz_cargo", lambda: str(shim))
    assert validator._cargo_fuzz_available() is True


def test_cargo_fuzz_available_rejects_broken_cargo(
    tmp_path: Path, monkeypatch
) -> None:
    broken = tmp_path / "cargo"
    broken.write_text("#!/bin/sh\nexit 101\n", encoding="utf-8")
    broken.chmod(0o755)
    monkeypatch.setattr(validator, "_resolve_fuzz_cargo", lambda: str(broken))
    assert validator._cargo_fuzz_available() is False


def test_cargo_fuzz_unavailable_without_a_shim(monkeypatch) -> None:
    monkeypatch.setattr(validator, "_resolve_fuzz_cargo", lambda: None)
    assert validator._cargo_fuzz_available() is False


def test_invoke_fuzz_runs_through_the_shim(tmp_path: Path, monkeypatch) -> None:
    seen: dict = {}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        seen["command"] = list(command)
        return _Result()

    monkeypatch.setattr(validator, "_resolve_fuzz_cargo", lambda: "/fake/cargo")
    monkeypatch.setattr(validator.subprocess, "run", fake_run)
    result = validator._invoke_fuzz("corpus_population", [], 10)
    assert result["returncode"] == 0
    assert seen["command"][0] == "/fake/cargo"
    assert seen["command"][1] == "+nightly"


def test_soak_chases_time_before_runs_without_a_runs_cap(
    tmp_path: Path, monkeypatch
) -> None:
    """The soak-time floor must be chased without a runs cap.

    With both caps present, libFuzzer stops at whichever trips first; on fast
    targets the runs cap ends each invocation in about a second, and the
    short bursts can cancel out against the startup-corpus replay deduction,
    leaving runs_remaining unchanged: the fixed point that failed every
    blocking target live.  The first invocation therefore must carry only
    -max_total_time.
    """
    import tools.release.gates.validate_fuzz_qualification as validator

    calls: list[list[str]] = []

    def fake_invoke(target, flags, timeout):
        calls.append(list(flags))
        if not any(f.startswith("-runs=") for f in flags):
            return {
                "returncode": 0,
                "stdout": "stat::number_of_executed_units: 1000000\n",
                "stderr": "",
                "wall_elapsed": 900.0,
            }
        return {
            "returncode": 0,
            "stdout": "stat::number_of_executed_units: 100\n",
            "stderr": "",
            "wall_elapsed": 1.0,
        }

    monkeypatch.setattr(validator, "CORPUS_ROOT", tmp_path / "corpus")
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path.parent)
    monkeypatch.setattr(validator, "_invoke_fuzz", fake_invoke)

    result = validator._run_target_soak(
        "target", seed=1, required_executions=100000,
        required_seconds=900, log_path=tmp_path / "soak.log")

    assert result["status"] == "pass"
    assert result["elapsed_seconds_total"] >= 900
    assert not any(f.startswith("-runs=") for f in calls[0])
    assert "-max_total_time=900" in calls[0]


def test_soak_chases_runs_only_after_time_is_met(
    tmp_path: Path, monkeypatch
) -> None:
    import tools.release.gates.validate_fuzz_qualification as validator

    calls: list[list[str]] = []

    def fake_invoke(target, flags, timeout):
        calls.append(list(flags))
        if any(f.startswith("-runs=") for f in flags):
            return {
                "returncode": 0,
                "stdout": "stat::number_of_executed_units: 200000\n",
                "stderr": "",
                "wall_elapsed": 30.0,
            }
        return {
            "returncode": 0,
            "stdout": "stat::number_of_executed_units: 1000\n",
            "stderr": "",
            "wall_elapsed": 900.0,
        }

    monkeypatch.setattr(validator, "CORPUS_ROOT", tmp_path / "corpus")
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path.parent)
    monkeypatch.setattr(validator, "_invoke_fuzz", fake_invoke)

    result = validator._run_target_soak(
        "target", seed=1, required_executions=100000,
        required_seconds=900, log_path=tmp_path / "soak.log")

    assert result["status"] == "pass"
    assert len(calls) == 2
    assert "-max_total_time=900" in calls[0]
    # runs_remaining (100000 minus 999 credited executions) plus the
    # startup-replay overhead (empty corpus: one empty-input callback).
    assert "-runs=99002" in calls[1]
    # The chase cap fits the continuation budget with its margin reserved.
    assert "-max_total_time=3600" in calls[1]


def test_soak_fails_fast_when_the_continuation_budget_is_spent(
    tmp_path: Path, monkeypatch
) -> None:
    """A pathologically slow target must fail with a record instead of
    consuming the release job budget until CI kills the job."""
    import tools.release.gates.validate_fuzz_qualification as validator

    calls: list[list[str]] = []

    def fake_invoke(target, flags, timeout):
        calls.append(list(flags))
        if any(f.startswith("-runs=") for f in flags):
            return {
                "returncode": 0,
                "stdout": "stat::number_of_executed_units: 1\n",
                "stderr": "",
                "wall_elapsed": 900.0,
            }
        return {
            "returncode": 0,
            "stdout": "stat::number_of_executed_units: 1000\n",
            "stderr": "",
            "wall_elapsed": 900.0,
        }

    monkeypatch.setattr(validator, "CORPUS_ROOT", tmp_path / "corpus")
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path.parent)
    monkeypatch.setattr(validator, "_invoke_fuzz", fake_invoke)

    result = validator._run_target_soak(
        "target", seed=1, required_executions=100000,
        required_seconds=900, log_path=tmp_path / "soak.log")

    assert result["status"] == "fail"
    assert "continuation budget" in result["failure_reason"]
    # one soak invocation plus the five wall-charged chases the budget fits
    assert len(calls) == 6


def test_soak_rejects_sub_second_continuation_budgets(
    tmp_path: Path, monkeypatch
) -> None:
    """A fractional leftover budget must not become -max_total_time=0."""
    import tools.release.gates.validate_fuzz_qualification as validator

    calls: list[list[str]] = []

    def fake_invoke(target, flags, timeout):
        calls.append(list(flags))
        if any(f.startswith("-runs=") for f in flags):
            return {
                "returncode": 0,
                "stdout": "stat::number_of_executed_units: 1\n",
                "stderr": "",
                "wall_elapsed": 5399.5,
            }
        return {
            "returncode": 0,
            "stdout": "stat::number_of_executed_units: 1000\n",
            "stderr": "",
            "wall_elapsed": 900.0,
        }

    monkeypatch.setattr(validator, "CORPUS_ROOT", tmp_path / "corpus")
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path.parent)
    monkeypatch.setattr(validator, "_invoke_fuzz", fake_invoke)

    result = validator._run_target_soak(
        "target", seed=1, required_executions=100000,
        required_seconds=900, log_path=tmp_path / "soak.log")

    assert result["status"] == "fail"
    assert "continuation budget" in result["failure_reason"]
    assert not any("-max_total_time=0" in flag for call in calls for flag in call)
    # the sub-second leftover never schedules another invocation
    assert len(calls) == 2


def test_soak_schedule_applies_the_cap_limit(monkeypatch) -> None:
    import tools.release.gates.validate_fuzz_qualification as validator

    monkeypatch.setattr(validator.time, "monotonic", lambda: 1000.0)

    flags, time_cap, failure = validator._soak_invocation_schedule(
        seed=1, seconds_remaining=900, runs_remaining=0,
        startup_overhead=1, continuation_spent=0.0, deadline=1100.0)
    assert failure is None
    assert time_cap == 100
    assert "-max_total_time=100" in flags

    flags, time_cap, failure = validator._soak_invocation_schedule(
        seed=1, seconds_remaining=0, runs_remaining=500,
        startup_overhead=1, continuation_spent=0.0, deadline=1060.0)
    assert failure is None
    assert time_cap == 60
    assert "-max_total_time=60" in flags

    flags, time_cap, failure = validator._soak_invocation_schedule(
        seed=1, seconds_remaining=900, runs_remaining=0,
        startup_overhead=1, continuation_spent=0.0, deadline=999.0)
    assert failure is not None
    assert "job budget exhausted" in failure
    assert flags == []


def test_soak_stops_immediately_when_the_job_deadline_passed(
    tmp_path: Path, monkeypatch
) -> None:
    import time

    import tools.release.gates.validate_fuzz_qualification as validator

    calls: list[list[str]] = []
    monkeypatch.setattr(validator, "CORPUS_ROOT", tmp_path / "corpus")
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path.parent)
    monkeypatch.setattr(
        validator, "_invoke_fuzz",
        lambda target, flags, timeout: calls.append(list(flags)))

    result = validator._run_target_soak(
        "target", seed=1, required_executions=100000, required_seconds=900,
        log_path=tmp_path / "soak.log", deadline=time.monotonic() - 5)

    assert result["status"] == "fail"
    assert "job budget exhausted" in result["failure_reason"]
    assert calls == []


def test_soak_continuation_budget_charges_wall_time(
    tmp_path: Path, monkeypatch
) -> None:
    """Parsed fuzz time must not shrink the wall-clock continuation charge."""
    import tools.release.gates.validate_fuzz_qualification as validator

    calls: list[list[str]] = []

    def fake_invoke(target, flags, timeout):
        calls.append(list(flags))
        if any(f.startswith("-runs=") for f in flags):
            return {
                "returncode": 0,
                "stdout": "stat::number_of_executed_units: 1\n"
                          "stat::elapsed_seconds: 0.1\n",
                "stderr": "",
                "wall_elapsed": 900.0,
            }
        return {
            "returncode": 0,
            "stdout": "stat::number_of_executed_units: 1000\n",
            "stderr": "",
            "wall_elapsed": 900.0,
        }

    monkeypatch.setattr(validator, "CORPUS_ROOT", tmp_path / "corpus")
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path.parent)
    monkeypatch.setattr(validator, "_invoke_fuzz", fake_invoke)

    result = validator._run_target_soak(
        "target", seed=1, required_executions=100000,
        required_seconds=900, log_path=tmp_path / "soak.log")

    assert result["status"] == "fail"
    assert "continuation budget" in result["failure_reason"]
    # one soak invocation plus five wall-charged (900s) chase invocations
    assert len(calls) == 6


def test_soak_bounds_the_invocation_timeout_under_a_deadline(
    tmp_path: Path, monkeypatch
) -> None:
    """With a shared deadline, the subprocess margin shrinks to the tight
    bound so a single overrun stays small; without one it stays generous."""
    import time

    import tools.release.gates.validate_fuzz_qualification as validator

    seen_timeouts: list[int] = []

    def fake_invoke(target, flags, timeout):
        seen_timeouts.append(timeout)
        return {
            "returncode": 0,
            "stdout": "stat::number_of_executed_units: 1000000\n"
                      "stat::elapsed_seconds: 900\n",
            "stderr": "",
            "wall_elapsed": 900.0,
        }

    monkeypatch.setattr(validator, "CORPUS_ROOT", tmp_path / "corpus")
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path.parent)
    monkeypatch.setattr(validator, "_invoke_fuzz", fake_invoke)

    result = validator._run_target_soak(
        "target", seed=1, required_executions=100000, required_seconds=900,
        log_path=tmp_path / "soak.log",
        deadline=time.monotonic() + 100000)
    assert result["status"] == "pass"
    # Uniform margin: it must cover startup replay, which -max_total_time
    # does not count, so it cannot be tightened near the deadline without
    # risking a killed valid invocation.
    assert seen_timeouts[0] == 900 + validator.INVOCATION_TIMEOUT_MARGIN


def test_soak_chase_timeout_fits_the_budget_and_exceeds_its_cap(
    tmp_path: Path, monkeypatch
) -> None:
    """A chase's subprocess allowance must both exceed its own cap (so a
    healthy invocation is never killed at the cap) and fit the remaining
    continuation budget (so the margin cannot push past it)."""
    import tools.release.gates.validate_fuzz_qualification as validator

    seen_timeouts: list[int] = []
    seen_flags: list[list[str]] = []

    def fake_invoke(target, flags, timeout):
        seen_timeouts.append(timeout)
        seen_flags.append(list(flags))
        if any(f.startswith("-runs=") for f in flags):
            return {
                "returncode": 0,
                "stdout": "stat::number_of_executed_units: 200000\n",
                "stderr": "",
                "wall_elapsed": 60.0,
            }
        return {
            "returncode": 0,
            "stdout": "stat::number_of_executed_units: 1000\n",
            "stderr": "",
            "wall_elapsed": 900.0,
        }

    monkeypatch.setattr(validator, "CORPUS_ROOT", tmp_path / "corpus")
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path.parent)
    monkeypatch.setattr(validator, "_invoke_fuzz", fake_invoke)

    result = validator._run_target_soak(
        "target", seed=1, required_executions=100000,
        required_seconds=900, log_path=tmp_path / "soak.log")

    assert result["status"] == "pass"
    assert len(seen_timeouts) == 2
    margin = validator.INVOCATION_TIMEOUT_MARGIN
    # The chase cap reserves the margin inside the continuation budget.
    assert "-max_total_time=3600" in seen_flags[1]
    # The allowance exceeds the cap and fits the remaining budget exactly.
    assert seen_timeouts[1] == 3600 + margin
    assert seen_timeouts[1] <= validator.TIME_CONTINUATION_BUDGET - 900


def test_soak_chase_cap_shrinks_with_the_remaining_budget(
    tmp_path: Path, monkeypatch
) -> None:
    """When the remaining budget is small the cap shrinks so the allowance
    still fits; a leftover too small for cap plus margin never schedules a
    doomed invocation."""
    import tools.release.gates.validate_fuzz_qualification as validator

    seen_timeouts: list[int] = []
    seen_flags: list[list[str]] = []

    chase_results = iter([(5000, 4300.0), (200000, 50.0)])

    def fake_invoke(target, flags, timeout):
        seen_timeouts.append(timeout)
        seen_flags.append(list(flags))
        if any(f.startswith("-runs=") for f in flags):
            executions, wall = next(chase_results)
            return {
                "returncode": 0,
                "stdout": f"stat::number_of_executed_units: {executions}\n",
                "stderr": "",
                "wall_elapsed": wall,
            }
        return {
            "returncode": 0,
            "stdout": "stat::number_of_executed_units: 1000\n",
            "stderr": "",
            "wall_elapsed": 900.0,
        }

    monkeypatch.setattr(validator, "CORPUS_ROOT", tmp_path / "corpus")
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path.parent)
    monkeypatch.setattr(validator, "_invoke_fuzz", fake_invoke)

    result = validator._run_target_soak(
        "target", seed=1, required_executions=100000,
        required_seconds=900, log_path=tmp_path / "soak.log")

    assert result["status"] == "pass"
    margin = validator.INVOCATION_TIMEOUT_MARGIN
    assert len(seen_timeouts) == 3
    # After the first chase charged 4300s, the budget fits only 1100 more:
    # the cap shrinks to 200 and the allowance to cap + margin == 1100.
    assert "-max_total_time=200" in seen_flags[2]
    assert seen_timeouts[2] == 200 + margin
    assert seen_timeouts[2] <= validator.TIME_CONTINUATION_BUDGET - 4300


def test_soak_never_schedules_a_chase_without_room_for_its_margin(
    tmp_path: Path, monkeypatch
) -> None:
    """A remaining budget that only covers the margin must fail the target
    instead of launching an invocation whose allowance would equal its cap
    (killed at the cap before it can exit)."""
    import tools.release.gates.validate_fuzz_qualification as validator

    calls: list[list[str]] = []

    def fake_invoke(target, flags, timeout):
        calls.append(list(flags))
        if any(f.startswith("-runs=") for f in flags):
            return {
                "returncode": 0,
                "stdout": "stat::number_of_executed_units: 1\n",
                "stderr": "",
                "wall_elapsed": 4600.0,
            }
        return {
            "returncode": 0,
            "stdout": "stat::number_of_executed_units: 1000\n",
            "stderr": "",
            "wall_elapsed": 900.0,
        }

    monkeypatch.setattr(validator, "CORPUS_ROOT", tmp_path / "corpus")
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path.parent)
    monkeypatch.setattr(validator, "_invoke_fuzz", fake_invoke)

    result = validator._run_target_soak(
        "target", seed=1, required_executions=100000,
        required_seconds=900, log_path=tmp_path / "soak.log")

    assert result["status"] == "fail"
    assert "continuation budget" in result["failure_reason"]
    # The soak and the first chase ran; the leftover 800s cannot host
    # another invocation with its margin, so none was ever launched.
    assert len(calls) == 2


def test_soak_credits_the_done_reported_loop_time_not_wall() -> None:
    """The soak-time floor must use libFuzzer's own loop time, not the
    subprocess wall time: startup/corpus replay happens before the loop
    ("Done ... in 7 second(s)" while wall was 8.46 in the traced run, so
    replay time is not credited toward the floor)."""
    import tools.release.gates.validate_fuzz_qualification as validator

    invocation = {
        "returncode": 0,
        "stdout": "stat::number_of_executed_units: 100000\n"
                  "Done 100000 runs in 7 second(s)\n",
        "stderr": "",
        "wall_elapsed": 8.46,
    }
    executions, elapsed, failure = validator._soak_outcome(invocation)
    assert failure is None
    assert executions == 100000
    assert elapsed == 7.0

    # The wall fallback only applies when the fuzzer reported no loop time.
    invocation = {
        "returncode": 0,
        "stdout": "stat::number_of_executed_units: 3000\n",
        "stderr": "",
        "wall_elapsed": 5.0,
    }
    executions, elapsed, failure = validator._soak_outcome(invocation)
    assert failure is None
    assert elapsed == 5.0
