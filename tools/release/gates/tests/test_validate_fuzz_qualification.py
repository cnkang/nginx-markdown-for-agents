"""Regression tests for the fuzz qualification gate validator."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from tools.release.gates import validate_fuzz_qualification as validator

MANIFEST_FIXTURE = "fuzz-qualification-manifest.json"

_real_popen = subprocess.Popen


class _PopenAdapter:
    """Expose a real ``Popen`` behind the surface ``_invoke_fuzz`` uses.

    ``_invoke_fuzz`` drives ``subprocess.Popen`` through the module's
    ``subprocess`` reference, so the tests substitute a spawner that runs
    a scripted stream producer instead of the fuzzer; the adapter keeps
    the pipe, ``wait``/``kill`` and ``returncode`` contract identical.
    """

    def __init__(self, process) -> None:
        self._process = process
        self.stdout = process.stdout
        self.stderr = process.stderr
        self.returncode = None

    @property
    def pid(self) -> int:
        """Return the real child's process id for process-group signaling."""
        return self._process.pid

    def poll(self):
        """Poll the process, mirroring ``Popen.poll``."""
        self.returncode = self._process.poll()
        return self.returncode

    def wait(self, timeout=None):
        """Wait for the process, mirroring ``Popen.wait``."""
        self.returncode = self._process.wait(timeout=timeout)
        return self.returncode

    def kill(self) -> None:
        """Kill the process, mirroring ``Popen.kill``."""
        self._process.kill()


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


def _marker_stream_script(padding_chars_per_side: int, marker: str | None) -> str:
    """Return a subprocess script emitting a large stream with a marker.

    The stream is deliberately much larger than the validator's capture
    cap so the marker line (when requested) lands in the elided middle
    region of the retained text, while the script remains a single
    short-lived Python process so the test stays bounded.
    """
    lines = ["import sys", "w = sys.stdout.buffer.write",
             f"for _ in range({padding_chars_per_side} // 100):"
             " w(b'P' * 99 + b'\\n')"]
    if marker is not None:
        lines.append(f"w({marker.encode()!r})")
    lines.append(f"for _ in range({padding_chars_per_side} // 100):"
                 " w(b'Q' * 99 + b'\\n')")
    lines.append("w(b'stat::number_of_executed_units: 7\\n')")
    return "\n".join(lines)


def _install_streaming_popen(monkeypatch, script: str) -> None:
    """Route _invoke_fuzz's Popen to a real subprocess running ``script``.

    The pipe wiring, the reader threads and the timeout path stay
    production code under test; only the fuzzer command itself is
    replaced by the scripted stream producer.
    """
    def fake_popen(command, **kwargs):
        real = _real_popen(
            [sys.executable, "-c", script], cwd=kwargs.get("cwd"),
            stdout=kwargs.get("stdout"), stderr=kwargs.get("stderr"),
            start_new_session=kwargs.get("start_new_session", False))
        return _PopenAdapter(real)

    monkeypatch.setattr(validator, "_resolve_fuzz_cargo", lambda: "/fake/cargo")
    monkeypatch.setattr(validator.subprocess, "Popen", fake_popen)


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


def test_run_real_gate_writes_the_qualification_record(
    tmp_path: Path, monkeypatch, capsys) -> None:
    """The real gate must persist the composed record after the pool ran.

    Everything heavy is mocked (cargo availability, the worker pool, the
    corpus-seed validation); the recorded wiring under test is the record
    composition and its single canonical write path, including the
    non-blocking target's skipped entry and the printed PASS lines.
    """
    manifest = json.loads(
        _fixture_path(MANIFEST_FIXTURE).read_text(encoding="utf-8"))
    manifest["targets"][1]["blocking"] = False  # convert_html skips
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "CORPUS_ROOT", tmp_path / "corpus")
    monkeypatch.setattr(validator, "_cargo_fuzz_available", lambda: True)
    monkeypatch.setattr(
        validator, "validate_corpus_seeds",
        lambda data, sha, names: {"parser_html": {"seed_path": "seed"}})
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps({"seeds": [
        {"target": "parser_html", "seed_path": "seed",
         "digest": "sha256:" + "0" * 64}]}), encoding="utf-8")

    def fake_run_blocking(entries, seeds, deadline):
        assert [entry["name"] for entry in entries] == ["parser_html"]
        return {"parser_html": {
            "target": "parser_html", "seed": 12345,
            "elapsed_seconds_total": 900, "executions_total": 100000,
            "crashes": 0, "sanitizer_findings": 0, "corpus_dir": "",
            "seed_path": "seed", "raw_log_ref": "", "status": "pass",
            "failure_reason": None,
        }}

    monkeypatch.setattr(validator, "_run_blocking_targets", fake_run_blocking)
    args = validator.build_arg_parser().parse_args([
        "--mode", "real", "--manifest", str(manifest_path),
        "--corpus-manifest", str(tmp_path / "corpus.json"),
    ])

    rc = validator.run_real_gate(args)

    captured = capsys.readouterr()
    assert rc == 0
    assert "[PASS] parser_html" in captured.out
    assert "[SKIPPED] convert_html" in captured.out
    record_path = (tmp_path / validator.DEFAULT_RECORD)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["schema_version"] == validator.SCHEMA_VERSION
    assert record["candidate_sha"] == manifest["candidate_sha"]
    assert record["blocking_pass"] is True
    assert record["blocking_failures"] == []
    statuses = {entry["target"]: entry["status"]
                for entry in record["per_target"]}
    assert statuses == {"parser_html": "pass", "convert_html": "skipped"}


def test_run_real_gate_reports_blocking_failures(
    tmp_path: Path, monkeypatch, capsys) -> None:
    """A failing blocking target must produce rc 1 and a FAIL record.

    The record is still written (it is the diagnostic evidence for the
    failed job), with the target named in ``blocking_failures``.
    """
    manifest_path = _write_staged(tmp_path, MANIFEST_FIXTURE)
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "CORPUS_ROOT", tmp_path / "corpus")
    monkeypatch.setattr(validator, "_cargo_fuzz_available", lambda: True)
    monkeypatch.setattr(
        validator, "validate_corpus_seeds",
        lambda data, sha, names: {name: {"seed_path": "seed"}
                                  for name in names})
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps({"seeds": []}), encoding="utf-8")

    def fake_run_blocking(entries, seeds, deadline):
        return {entry["name"]: {
            "target": entry["name"], "seed": entry["seed"],
            "elapsed_seconds_total": 900, "executions_total": 100000,
            "crashes": 1, "sanitizer_findings": 0, "corpus_dir": "",
            "seed_path": "seed", "raw_log_ref": "", "status": "fail",
            "failure_reason": "ERROR: libFuzzer: deadly signal",
        } for entry in entries}

    monkeypatch.setattr(validator, "_run_blocking_targets", fake_run_blocking)
    args = validator.build_arg_parser().parse_args([
        "--mode", "real", "--manifest", str(manifest_path),
        "--corpus-manifest", str(tmp_path / "corpus.json"),
    ])

    rc = validator.run_real_gate(args)

    captured = capsys.readouterr()
    assert rc == 1
    assert "FAIL: blocking fuzz targets not qualified" in captured.out
    record = json.loads(
        (tmp_path / validator.DEFAULT_RECORD).read_text(encoding="utf-8"))
    assert record["blocking_pass"] is False
    assert sorted(record["blocking_failures"]) == ["convert_html",
                                                   "parser_html"]


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
    script = ("import sys\n"
              "sys.stdout.write('stat::number_of_executed_units: 3\\n')\n")

    def fake_popen(command, **kwargs):
        seen["command"] = list(command)
        real = _real_popen(
            [sys.executable, "-c", script], cwd=kwargs.get("cwd"),
            stdout=kwargs.get("stdout"), stderr=kwargs.get("stderr"),
            start_new_session=kwargs.get("start_new_session", False))
        return _PopenAdapter(real)

    monkeypatch.setattr(validator, "_resolve_fuzz_cargo", lambda: "/fake/cargo")
    monkeypatch.setattr(validator.subprocess, "Popen", fake_popen)
    result = validator._invoke_fuzz("corpus_population", [], 10)
    assert result["returncode"] == 0
    assert seen["command"][0] == "/fake/cargo"
    assert seen["command"][1] == "+nightly"
    assert "stat::number_of_executed_units: 3" in result["stdout"]


def test_streaming_capture_detects_a_marker_past_the_capture_cap(
    monkeypatch,
) -> None:
    """A failure marker past the capture cap must still fail the target.

    The retained text keeps only a head and a rolling tail, so a marker
    line in the elided middle is invisible to a post-hoc parse of the
    capped text; the streaming scan must surface it, with the marker text
    in the finding, and the invocation must not classify as a pass.
    """
    cap = validator._MAX_CAPTURE_CHARS
    marker = "==ERROR: AddressSanitizer: heap-buffer-overflow on address\n"
    script = _marker_stream_script(cap, marker)
    _install_streaming_popen(monkeypatch, script)

    result = validator._invoke_fuzz("corpus_population", [], 120)

    assert result["returncode"] == 0
    # The mid-stream marker line is not in the retained text at all; only
    # the streaming scan can see it, which is exactly the regression.
    assert marker.strip() not in result["stdout"]
    assert "elided by the capture cap" in result["stdout"]
    assert result["marker_finding"] is not None
    assert "AddressSanitizer" in result["marker_finding"]
    _, _, failure = validator._soak_outcome(dict(result))
    assert failure is not None
    assert "AddressSanitizer" in failure
    assert validator._classify_finding(failure) == (0, 1)


def test_streaming_capture_keeps_a_clean_large_stream_passing(
    monkeypatch,
) -> None:
    """A clean stream larger than the cap must still pass and stay capped."""
    script = _marker_stream_script(validator._MAX_CAPTURE_CHARS, None)
    _install_streaming_popen(monkeypatch, script)

    result = validator._invoke_fuzz("corpus_population", [], 120)

    assert result["returncode"] == 0
    assert result["marker_finding"] is None
    assert len(result["stdout"]) <= validator._MAX_CAPTURE_CHARS + 100
    assert "elided by the capture cap" in result["stdout"]
    executions, _, failure = validator._soak_outcome(dict(result))
    assert failure is None
    assert executions == 7


def test_streaming_capture_retains_the_same_head_and_tail_as_the_cap(
    monkeypatch,
) -> None:
    """The streamed retention must equal ``_bounded_capture`` of the full text."""
    cap = validator._MAX_CAPTURE_CHARS
    script = _marker_stream_script(cap, None)
    _install_streaming_popen(monkeypatch, script)

    result = validator._invoke_fuzz("corpus_population", [], 120)

    lines_per_side = cap // 100
    full = (("P" * 99 + "\n") * lines_per_side
            + ("Q" * 99 + "\n") * lines_per_side
            + "stat::number_of_executed_units: 7\n")
    assert result["stdout"] == validator._bounded_capture(full)


def test_streaming_capture_bounds_memory_below_the_full_stream(
    monkeypatch,
) -> None:
    """The drained buffers must not hold the full stream in memory.

    Measured with ``tracemalloc``: the traced peak stays bounded by the
    capture cap no matter how many times the cap the stream produces,
    which is the difference between draining a pipe and buffering it
    whole (the latter peaks at or above the produced size).
    """
    import tracemalloc

    cap = validator._MAX_CAPTURE_CHARS
    script = _marker_stream_script(cap * 8, None)
    _install_streaming_popen(monkeypatch, script)

    tracemalloc.start()
    try:
        result = validator._invoke_fuzz("corpus_population", [], 600)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    produced = (cap * 8 // 100) * 100 * 2  # two padded halves
    assert produced > cap * 4, "the stream must dwarf the cap for this test"
    assert len(result["stdout"]) <= cap + 100
    # A buffering capture would peak at (at least) the produced size; the
    # streaming drain peaks at the retained buffers plus the joined text.
    assert peak < produced // 2, (peak, produced)


def test_streaming_capture_decodes_split_multibyte_characters(
    monkeypatch,
) -> None:
    """A multi-byte character split across reads must survive the drain.

    Decoding each raw read independently would turn a character straddling
    a read boundary into replacement characters; the incremental decoder
    must keep the text intact.
    """
    script = ("import sys\n"
              "sys.stdout.buffer.write('crash near \\u00e9\\u00e8\\u20ac ok\\n'"
              ".encode('utf-8'))\n")
    _install_streaming_popen(monkeypatch, script)

    result = validator._invoke_fuzz("corpus_population", [], 30)

    assert result["returncode"] == 0
    assert "crash near \u00e9\u00e8\u20ac ok" in result["stdout"]
    assert "\ufffd" not in result["stdout"]


def test_streaming_capture_timeout_kills_and_keeps_partial_output(
    monkeypatch,
) -> None:
    """A timed-out invocation keeps the partial streams and its wall time."""
    script = ("import sys, time\n"
              "w = sys.stdout.buffer.write\n"
              "w(b'INFO: starting up\\n')\n"
              "w(b'stat::number_of_executed_units: 11\\n')\n"
              "sys.stdout.flush()\n"
              "time.sleep(60)\n")
    _install_streaming_popen(monkeypatch, script)

    result = validator._invoke_fuzz("corpus_population", [], 1)

    assert result["returncode"] == -1
    assert result["stderr"].startswith("timed out: ")
    assert "wall_elapsed" in result
    assert result["wall_elapsed"] >= 0
    assert "stat::number_of_executed_units: 11" in result["stdout"]
    executions, _, failure = validator._soak_outcome(dict(result))
    assert executions == 11
    assert failure == "timed out: fuzz invocation exceeded its time cap"


def test_invoke_fuzz_returns_wall_elapsed_on_every_return_path(
    monkeypatch,
) -> None:
    """Timeout, spawn failure and success must all carry ``wall_elapsed``.

    The soak's continuation budget charges chase invocations their wall
    time, so a return path without the field silently mis-charges them.
    """
    monkeypatch.setattr(validator, "_resolve_fuzz_cargo", lambda: None)
    missing_cargo = validator._invoke_fuzz("corpus_population", [], 10)
    assert missing_cargo["returncode"] == -1
    assert "wall_elapsed" in missing_cargo

    monkeypatch.setattr(validator, "_resolve_fuzz_cargo", lambda: "/fake/cargo")

    def failing_popen(command, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr(validator.subprocess, "Popen", failing_popen)
    spawn_failed = validator._invoke_fuzz("corpus_population", [], 10)
    assert spawn_failed["returncode"] == -1
    assert spawn_failed["stderr"].startswith("spawn failed:")
    assert "wall_elapsed" in spawn_failed


def test_soak_uses_the_streamed_marker_when_the_capped_text_hides_it() -> None:
    """``_soak_outcome`` must prefer the streamed marker evidence.

    The invocation shape here mirrors a real one: capped text whose only
    marker line is gone, plus the marker the streaming scan retained.
    """
    invocation = {
        "returncode": 0,
        "stdout": ("INFO: running\n[... 9000000 chars elided ...]\n"
                   "stat::number_of_executed_units: 900000\n"),
        "stderr": "",
        "wall_elapsed": 901.0,
        "marker_finding": "==ERROR: AddressSanitizer: heap-buffer-overflow on address",
    }

    executions, _, failure = validator._soak_outcome(invocation)

    assert executions == 900000
    assert failure is not None
    assert "AddressSanitizer" in failure
    assert validator._classify_finding(failure) == (0, 1)


def test_soak_ignores_marker_evidence_when_there_is_none() -> None:
    """A clean invocation keeps passing with the streaming field present."""
    invocation = {
        "returncode": 0,
        "stdout": "stat::number_of_executed_units: 900000\n",
        "stderr": "",
        "wall_elapsed": 901.0,
        "marker_finding": None,
    }

    executions, elapsed, failure = validator._soak_outcome(invocation)

    assert executions == 900000
    assert elapsed == 901.0
    assert failure is None


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

    # Pin a mid-range continuation budget so the invocation-count arithmetic
    # below stays deterministic; the production tuning is asserted by the
    # envelope arithmetic test instead.
    monkeypatch.setattr(validator, "TIME_CONTINUATION_BUDGET", 5400)
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

    # Pin a mid-range continuation budget so the leftover arithmetic below
    # stays deterministic; the production tuning is asserted by the envelope
    # arithmetic test instead.
    monkeypatch.setattr(validator, "TIME_CONTINUATION_BUDGET", 5400)
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

    # Pin a mid-range continuation budget so the invocation-count arithmetic
    # below stays deterministic; the production tuning is asserted by the
    # envelope arithmetic test instead.
    monkeypatch.setattr(validator, "TIME_CONTINUATION_BUDGET", 5400)
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

    # Pin a mid-range continuation budget so the cap arithmetic below stays
    # deterministic; the production tuning is asserted by the envelope
    # arithmetic test instead.
    monkeypatch.setattr(validator, "TIME_CONTINUATION_BUDGET", 5400)
    seen_timeouts: list[int] = []
    seen_flags: list[list[str]] = []

    chase_results = iter([(5000, 3000.0), (200000, 60.0)])

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
    # The first chase runs with the full envelope: cap 3600, allowance 4500.
    assert "-max_total_time=3600" in seen_flags[1]
    assert seen_timeouts[1] == 3600 + margin
    # The first chase charged 3000 wall seconds, so the second chase's cap
    # drops to 2400 - 900 = 1500 -- the old clamping implementation would
    # have issued 2400 here, so this assertion distinguishes the fix.
    assert "-max_total_time=1500" in seen_flags[2]
    assert seen_timeouts[2] == 1500 + margin
    assert seen_timeouts[2] <= validator.TIME_CONTINUATION_BUDGET - 3000
    assert seen_timeouts[2] > 1500


def test_soak_chase_cap_shrinks_with_the_remaining_budget(
    tmp_path: Path, monkeypatch
) -> None:
    """When the remaining budget is small the cap shrinks so the allowance
    still fits; a leftover too small for cap plus margin never schedules a
    doomed invocation."""
    import tools.release.gates.validate_fuzz_qualification as validator

    # Pin a mid-range continuation budget so the cap arithmetic below stays
    # deterministic; the production tuning is asserted by the envelope
    # arithmetic test instead.
    monkeypatch.setattr(validator, "TIME_CONTINUATION_BUDGET", 5400)
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

    # Pin a mid-range continuation budget so the leftover arithmetic below
    # stays deterministic; the production tuning is asserted by the envelope
    # arithmetic test instead.
    monkeypatch.setattr(validator, "TIME_CONTINUATION_BUDGET", 5400)
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


def _scope_fuzz_targets() -> list[str]:
    """Return the fuzz target names in the tracked scope file, in order.

    The scope file is the tracked source the manifest generator counts and
    orders the blocking targets by, so a clean checkout (the generated
    blocking manifest is ignored by git) can still model the schedule
    exactly: the round-robin queue layout follows this order.
    """
    scope = json.loads(
        (Path(__file__).resolve().parents[4] / "release" / "scope"
         / "fuzz-scope.json").read_text(encoding="utf-8"))
    targets = scope["targets"]
    assert isinstance(targets, list) and targets
    return targets


def _blocking_target_count() -> int:
    """How many fuzz binaries the crate declares (from tracked inputs).

    The blocking manifest is generated at run time and ignored by git, so a
    clean checkout cannot read it; the fuzz crate's binary list is the
    tracked source of truth for the target set, and the envelope model
    cross-checks it against the scope file's target list.
    """
    import re

    root = Path(__file__).resolve().parents[4]
    cargo = (root / "components" / "rust-converter" / "fuzz" / "Cargo.toml")
    return len(re.findall(
        r"^\[\[bin\]\]", cargo.read_text(encoding="utf-8"), re.MULTILINE))


def test_fuzz_envelope_fits_the_dedicated_job_limit() -> None:
    """The fuzz envelope must leave room for setup and the single invocation
    that may still be running at expiry inside the dedicated fuzz job."""
    import tools.release.gates.validate_fuzz_qualification as validator

    total = (
        validator.SETUP_ALLOWANCE_SECONDS
        + validator.FUZZ_JOB_BUDGET
        + validator.INVOCATION_TIMEOUT_MARGIN
        + validator.REPLAY_ALLOWANCE_SECONDS
    )
    assert total <= validator.RELEASE_JOB_LIMIT_SECONDS
    # The upper-bound assertion alone would accept reverting FUZZ_JOB_BUDGET
    # (the pre-fix value still fits the total), silently consuming the
    # post-envelope reserve; enforce the reserve floor as well.
    reserve = validator.RELEASE_JOB_LIMIT_SECONDS - total
    assert reserve >= validator.MIN_POST_ENVELOPE_RESERVE_SECONDS
    # The worker pool must fit the worst-case schedule inside the envelope:
    # the slow decode target is scheduled last, so it starts once a worker
    # frees -- floor(fast targets / workers) fast soaks in -- and then chases
    # the executions floor at the slowest supported rate.  The model charges
    # each invocation its cap plus a generous startup, replay and shutdown
    # overhead; the measured CI band is above the floor.
    per_invocation_overhead = 60
    supported_rate = 7  # executions per second
    fast_soak = 900 + per_invocation_overhead
    scope_targets = _scope_fuzz_targets()
    fast_targets = len(scope_targets) - 1
    # The crate's binary list and the scope file must describe the same
    # target set, or the schedule model below is built on the wrong count.
    assert _blocking_target_count() == len(scope_targets)
    workers = validator.TARGET_WORKER_COUNT
    # Pin the slow target's queue position the model above relies on: the
    # slow decode target must be the last entry of its worker's queue (so
    # no further work follows it), and the number of fast soaks ahead of it
    # in the actual round-robin layout must equal the model's start term.
    # A scope reorder that changes its position (or puts work after it)
    # fails here instead of silently invalidating the envelope arithmetic.
    slow_target = "fuzz_multilayer_decode"
    slow_index = scope_targets.index(slow_target)
    queues = validator._worker_queue(
        [{"name": name} for name in scope_targets], workers)
    slow_queue_names = [entry["name"]
                        for entry in queues[slow_index % workers]]
    assert slow_queue_names[-1] == slow_target, slow_queue_names
    fast_ahead_of_slow = len(slow_queue_names) - 1
    assert fast_ahead_of_slow == fast_targets // workers, slow_queue_names
    slow_start = fast_ahead_of_slow * fast_soak
    chase_seconds = 100000 // supported_rate - 900
    chase_invocations = -(-chase_seconds // validator.TIME_CONTINUATION_CEILING)
    slow_chase = chase_seconds + chase_invocations * per_invocation_overhead
    slow_total = fast_soak + slow_chase
    assert slow_start + slow_total <= validator.FUZZ_JOB_BUDGET
    # The chase must also fit the per-target continuation budget together
    # with its invocation margin.
    assert (slow_chase + validator.INVOCATION_TIMEOUT_MARGIN
            <= validator.TIME_CONTINUATION_BUDGET)
    # The margin is a budget bound, not spendable capacity: if every chase
    # invocation ran to its subprocess timeout (a hung fuzzer), the budget
    # could not host the floor at the supported rate -- and that case fails
    # the target by design, because a hung invocation means a broken target.
    worst_case_executions = 900 * supported_rate
    spent = 0.0
    for _ in range(validator.MAX_FUZZ_INVOCATIONS):
        remaining = validator.TIME_CONTINUATION_BUDGET - spent
        cap_max = int(remaining) - validator.INVOCATION_TIMEOUT_MARGIN
        if remaining < 1 or cap_max < 1:
            break
        cap = min(validator.TIME_CONTINUATION_CEILING, cap_max)
        spent += cap + validator.INVOCATION_TIMEOUT_MARGIN
        worst_case_executions += cap * supported_rate
    assert worst_case_executions < 100000
    # Overlapping workers are what makes the schedule fit; a pool of one
    # would serialize the chase behind every fast soak.
    assert 2 <= validator.TARGET_WORKER_COUNT <= 4


def test_blocking_targets_run_on_an_overlapping_worker_pool(
    monkeypatch,
) -> None:
    """The slow target's chase must overlap the fast soaks instead of
    queueing behind them.

    The schedule is made deterministic with a barrier handshake: each
    target blocks until its partner is running, so overlap is proven by
    the handshake itself instead of a timing margin.  A queueing schedule
    would deadlock the barrier (and fail via the timeout), while an
    overlapping one passes regardless of machine load.
    """
    import threading
    import time as time_module

    import tools.release.gates.validate_fuzz_qualification as validator

    # Two workers, round-robin over [fast-0, fast-1, fast-2, slow]: worker 0
    # gets fast-0 and fast-2, worker 1 gets fast-1 and the slow target.  The
    # handshake pairs fast-0 with slow: the barrier releases only when both
    # are inside the faked record call at the same time, so overlap is
    # proven by the handshake itself (a queueing schedule would deadlock the
    # barrier and fail via its timeout) instead of a timing margin.
    barrier = threading.Barrier(2, timeout=10)
    spans: list[tuple[str, float, float]] = []
    spans_lock = threading.Lock()

    def fake_record(entry, seed_path, deadline=None):
        start = time_module.monotonic()
        if entry["name"] in ("fast-0", "slow"):
            barrier.wait()
        time_module.sleep(0.05)
        with spans_lock:
            spans.append((entry["name"], start, time_module.monotonic()))
        return {
            "target": entry["name"], "seed": entry["seed"],
            "elapsed_seconds_total": 1, "executions_total": 1, "crashes": 0,
            "sanitizer_findings": 0, "corpus_dir": "", "seed_path": seed_path,
            "raw_log_ref": "", "status": "pass", "failure_reason": None,
        }

    monkeypatch.setattr(validator, "_run_target_record", fake_record)
    # Pin the pool so the schedule is deterministic: with two workers the
    # slow chase shares the pool with a fast soak.
    monkeypatch.setattr(validator, "TARGET_WORKER_COUNT", 2)
    entries = [{"name": f"fast-{index}", "seed": 1} for index in range(3)]
    entries.append({"name": "slow", "seed": 1})
    seeds = {entry["name"]: {"seed_path": "seed"} for entry in entries}
    # Pin the queue layout the handshake relies on instead of trusting the
    # round-robin order implicitly.
    queues = validator._worker_queue(entries, 2)
    assert [entry["name"] for entry in queues[0]] == ["fast-0", "fast-2"]
    assert [entry["name"] for entry in queues[1]] == ["fast-1", "slow"]
    start = time_module.monotonic()
    records = validator._run_blocking_targets(
        entries, seeds, deadline=start + 60)
    assert set(records) == {entry["name"] for entry in entries}
    # Both handshake participants recorded; overlap was proven by the
    # barrier (both were running together, or the wait timed out).
    names = {span[0] for span in spans}
    assert {"fast-0", "slow"} <= names, spans


def _patch_threading(monkeypatch, *, event_class=None, thread_factory=None):
    """Swap the validator's ``threading`` for a shim with test seams.

    ``validator.threading`` is the real module, so mutating its attributes
    would change ``threading`` process-wide (including the test's own
    events).  The shim is a namespace exposing the three names the pool
    uses -- ``Thread``, ``Lock`` and ``Event`` -- with the real objects by
    default; only the production lookups are redirected.
    """
    shim = types.SimpleNamespace(
        Thread=thread_factory or threading.Thread,
        Lock=threading.Lock,
        Event=event_class or threading.Event,
    )
    monkeypatch.setattr(validator, "threading", shim)


def _signaling_event(monkeypatch, *, thread_factory=None) -> threading.Event:
    """Return an Event that fires whenever the pool's stop event is set.

    The pool creates its stop event internally, so a test that needs to
    order sibling work against the stop cannot poll it.  The validator's
    ``threading`` is shimmed with a delegating Event subclass (identical
    behaviour) whose ``set()`` also signals the returned event; the
    handshake then triggers on the production call itself instead of
    racing it with a sleep.  The stop event is the only Event the pool
    creates, so what the test observes is exactly the sibling-stop signal.
    ``thread_factory``, when given, shims the worker-thread factory in the
    same pass.
    """
    observed = threading.Event()

    class _SignalingEvent(threading.Event):
        """A real Event whose set() also signals the test's observer."""

        def set(self) -> None:
            super().set()
            observed.set()

    _patch_threading(
        monkeypatch, event_class=_SignalingEvent, thread_factory=thread_factory)
    return observed


def _record_stub(entry: dict, seed_path: str) -> dict:
    """Return a passing per-target record for a faked target run."""
    return {
        "target": entry["name"], "seed": entry["seed"],
        "elapsed_seconds_total": 1, "executions_total": 1, "crashes": 0,
        "sanitizer_findings": 0, "corpus_dir": "", "seed_path": seed_path,
        "raw_log_ref": "", "status": "pass", "failure_reason": None,
    }


def _run_pool_with_stop_gate(monkeypatch, failing_name, failure) -> dict:
    """Run the production pool behind a deterministic stop-gate handshake.

    Two workers, round-robin over ``[failing, gate, pad, queued]``: worker
    0 owns ``failing`` and ``pad``, worker 1 owns ``gate`` and ``queued``.
    ``gate`` runs only while ``failing`` has not yet raised, and it returns
    only once the pool's stop event is set; the queued entry behind it must
    then be skipped at the boundary.  Every step is ordered by events (the
    failing entry raises after the gate is in flight; the gate resumes only
    after the stop), so "exactly the in-flight sibling entry ran" is a
    deterministic fact rather than a timing race.

    Returns ``ran`` (sibling entries that completed), the observed stop
    event and whether the gate ever timed out.
    """
    monkeypatch.setattr(validator, "TARGET_WORKER_COUNT", 2)
    stop_observed = _signaling_event(monkeypatch)
    gate_in_flight = threading.Event()
    ran: list[str] = []
    state = {"gate_timeout": False}

    def fake_record(entry, seed_path, deadline=None):
        if entry["name"] == "gate":
            gate_in_flight.set()
            if not stop_observed.wait(10):
                state["gate_timeout"] = True
            ran.append(entry["name"])
            return _record_stub(entry, seed_path)
        if entry["name"] == failing_name:
            # Raise only once the gate (the sibling's in-flight entry) is
            # provably running, so it is the entry the stop interrupts.
            assert gate_in_flight.wait(10), "the gate never started"
            raise failure
        ran.append(entry["name"])
        return _record_stub(entry, seed_path)

    monkeypatch.setattr(validator, "_run_target_record", fake_record)
    entries = [{"name": failing_name, "seed": 1},
               {"name": "gate", "seed": 1},
               {"name": "pad", "seed": 1},
               {"name": "queued", "seed": 1}]
    seeds = {entry["name"]: {"seed_path": "seed"} for entry in entries}
    state["ran"] = ran
    state["stop_observed"] = stop_observed
    state["entries"] = entries
    state["seeds"] = seeds
    return state


def test_worker_interrupt_stops_siblings_and_is_recorded_for_join(
    monkeypatch,
) -> None:
    """An interpreter-level exit in a worker stops siblings and re-raises.

    KeyboardInterrupt/SystemExit are recorded like any other worker error and
    re-raised at join time (``errors[0]``), while the in-thread re-raise
    keeps the exit visible to the interpreter's thread-exception hook
    instead of silently swallowing it.  The hook is captured here so the
    assertion covers the re-raise itself, not a pytest warning.  The
    sibling handshake is deterministic (see ``_run_pool_with_stop_gate``).
    """
    state = _run_pool_with_stop_gate(
        monkeypatch, "exit", SystemExit("interrupted"))
    seen: list[str] = []
    monkeypatch.setattr(
        threading, "excepthook",
        lambda args: seen.append(type(args.exc_value).__name__))

    try:
        validator._run_blocking_targets(
            state["entries"], state["seeds"], deadline=0)
    except SystemExit:
        pass
    else:
        raise AssertionError("the worker exit must re-raise at join")

    assert not state["gate_timeout"], "the gate never saw the stop event"
    assert state["stop_observed"].is_set()
    # The in-thread re-raise reached the interpreter's thread-exception
    # hook: the exit stays visible instead of being swallowed.
    assert "SystemExit" in seen, seen
    # Only the sibling entry already in flight completed; the queued entry
    # behind it was skipped at its boundary.
    assert state["ran"] == ["gate"], state["ran"]


def test_worker_failure_stops_siblings_and_leaves_in_flight_work_only(
    monkeypatch,
) -> None:
    """A failing worker stops its siblings at their next queue boundary.

    The early-stop event must prevent sibling queues from draining the
    whole envelope after one worker fails (their records would be wasted
    runs), and the first error must still re-raise at join.  The handshake
    makes both facts deterministic: the failure fires only once the gate
    entry is in flight, and the gate returns only once the stop event is
    set, so the queued entry behind it can never start.
    """
    state = _run_pool_with_stop_gate(
        monkeypatch, "boom", RuntimeError("worker exploded"))

    try:
        validator._run_blocking_targets(
            state["entries"], state["seeds"], deadline=0)
    except RuntimeError as exc:
        assert "worker exploded" in str(exc)
    else:
        raise AssertionError("the first worker error must re-raise")

    assert not state["gate_timeout"], "the gate never saw the stop event"
    assert state["stop_observed"].is_set()
    assert state["ran"] == ["gate"], state["ran"]


def test_run_blocking_targets_skips_queued_entries_after_an_external_stop(
    monkeypatch,
) -> None:
    """A stop set at interrupt time must keep queued entries from running.

    The interrupt-at-join path sets the pool's stop event before it joins
    the workers; a worker that is mid-entry when the interrupt fires must
    finish that entry and leave the rest of its queue untouched.  The
    interrupt stands in for an external stop (ctrl-c in the fuzz job's
    terminal) and fires only once a real sibling worker is provably in
    flight, so ordering is deterministic; that sibling runs the production
    worker loop, so the boundary check under test is the real one.
    """
    real_thread = threading.Thread
    in_flight = threading.Event()
    calls: list[str] = []

    class _InterruptingJoin:
        """Thread stand-in whose unbounded join raises KeyboardInterrupt.

        It takes the first worker slot (whose queue never runs), so the
        interrupt fires exactly in the join loop the fix guards; its
        cleanup join records the bounded grace it was given.
        """

        def __init__(self) -> None:
            self.cleanup_timeouts: list[float | None] = []

        def start(self) -> None:
            return None

        def join(self, timeout=None):
            if timeout is None:
                assert in_flight.wait(10), "the sibling never started"
                raise KeyboardInterrupt("external interrupt at join")
            self.cleanup_timeouts.append(timeout)
            return None

    stand_in = _InterruptingJoin()
    handed_out: list = []

    def fake_thread(target=None, args=(), name=None):
        if not handed_out:
            handed_out.append(True)
            return stand_in
        return real_thread(target=target, args=args, name=name)

    stop_observed = _signaling_event(monkeypatch, thread_factory=fake_thread)

    def fake_record(entry, seed_path, deadline=None):
        calls.append(entry["name"])
        in_flight.set()
        # Only the interrupt handler's stop can release this entry, so the
        # queued entry behind it is skipped deterministically.
        assert stop_observed.wait(10), "the interrupt never set the stop"
        return _record_stub(entry, seed_path)

    monkeypatch.setattr(validator, "_run_target_record", fake_record)
    monkeypatch.setattr(validator, "TARGET_WORKER_COUNT", 2)
    # Round-robin over [pad, first, pad-2, queued] with two workers: worker
    # 0 (the interrupting stand-in, whose queue never runs) owns the pads,
    # worker 1 (a real thread running the production worker loop) owns
    # [first, queued].  first is in flight when the interrupt fires and
    # releases only after the handler's stop; the queued entry behind it on
    # the same real queue is then the boundary check under test.
    entries = [{"name": "pad", "seed": 1}, {"name": "first", "seed": 1},
               {"name": "pad-2", "seed": 1}, {"name": "queued", "seed": 1}]
    seeds = {entry["name"]: {"seed_path": "seed"} for entry in entries}

    with pytest.raises(KeyboardInterrupt, match="external interrupt"):
        validator._run_blocking_targets(entries, seeds, deadline=0)

    # The in-flight entry completed, and the stop the interrupt handler
    # set stopped the queued entry behind it from ever starting.
    assert calls == ["first"], calls
    assert stop_observed.is_set()
    assert stand_in.cleanup_timeouts == [
        validator._INTERRUPT_JOIN_GRACE_SECONDS]


def test_join_workers_interrupt_stops_siblings_and_waits_for_cleanup() -> None:
    """An interrupt during the join must stop siblings, then re-raise.

    The first join raises KeyboardInterrupt; the handler must set the
    shared stop event (so the still-running worker returns at its next
    boundary), join within the bounded cleanup grace, and re-raise so the
    process still exits as interrupted.
    """
    import tools.release.gates.validate_fuzz_qualification as validator

    stop = threading.Event()
    sibling_finished = threading.Event()

    def sibling() -> None:
        # Only the interrupt handler's stop can release this worker, so a
        # successful cleanup join is proof the event was set.
        assert stop.wait(10), "the interrupt never set the stop event"
        sibling_finished.set()

    worker = threading.Thread(target=sibling, name="fuzz-worker-sibling")
    worker.start()
    interrupt_raised = False

    class _InterruptingJoin:
        """Thread stand-in whose unbounded join raises once."""

        def __init__(self) -> None:
            self.cleanup_timeouts: list[float | None] = []

        def join(self, timeout=None):
            if timeout is None:
                raise KeyboardInterrupt("ctrl-c at join")
            self.cleanup_timeouts.append(timeout)

    stand_in = _InterruptingJoin()
    try:
        validator._join_workers([stand_in, worker], stop)
    except KeyboardInterrupt:
        interrupt_raised = True

    assert interrupt_raised, "the interrupt must re-raise after cleanup"
    assert stop.is_set()
    assert stand_in.cleanup_timeouts == [
        validator._INTERRUPT_JOIN_GRACE_SECONDS]
    assert sibling_finished.wait(10), "the cleanup join did not wait"
    worker.join(10)
    assert not worker.is_alive()


def test_worker_failure_aggregates_errors_beyond_the_first(
    monkeypatch,
    capsys,
) -> None:
    """Both workers' errors are seen and the sibling one is printed.

    The barrier puts both workers inside the failure path before either
    raises, so the pool deterministically records exactly two errors: the
    first re-raises and the aggregation must print every error beyond it
    (one line here) instead of swallowing them.
    """
    import tools.release.gates.validate_fuzz_qualification as validator

    monkeypatch.setattr(validator, "TARGET_WORKER_COUNT", 2)
    barrier = threading.Barrier(2, timeout=10)

    def fail_all(entry, seed_path, deadline=None):
        barrier.wait()
        raise ValueError(f"failure for {entry['name']}")

    monkeypatch.setattr(validator, "_run_target_record", fail_all)
    entries = [{"name": f"t{index}", "seed": 1} for index in range(4)]
    seeds = {entry["name"]: {"seed_path": "seed"} for entry in entries}
    try:
        validator._run_blocking_targets(entries, seeds, deadline=0)
    except ValueError as exc:
        assert "failure for" in str(exc)
    else:
        raise AssertionError("the first worker error must re-raise")
    err = capsys.readouterr().err
    # Exactly one sibling error exists beyond the raised first one (the two
    # in-flight workers both failed before the stop could skip them), and
    # the aggregation printed it rather than swallowing it.
    assert err.count("additional worker error") == 1, err


def _process_tree_script(tmp_path: Path, marker_delay: float = 0.8) -> tuple[str, Path, Path]:
    """Create parent/child scripts whose child survives TERM unless group-killed."""
    child_pid_path = tmp_path / "child.pid"
    child_marker = tmp_path / "child-survived"
    child_code = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"time.sleep({marker_delay})\n"
        f"open({str(child_marker)!r}, 'w').write('survived')\n"
        "time.sleep(30)\n"
    )
    parent_code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        f"{child_code!r}])\n"
        f"open({str(child_pid_path)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(30)\n"
    )
    return parent_code, child_pid_path, child_marker


def _wait_for_file(path: Path, timeout: float = 5.0) -> None:
    """Wait boundedly for a subprocess marker file."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not path.exists():
        time.sleep(0.01)
    assert path.exists(), f"timed out waiting for {path}"


def _kill_test_process_group(pid_path: Path) -> None:
    """Remove any process descendants left by a deliberately mutated probe."""
    if not pid_path.is_file():
        return
    child_pid = int(pid_path.read_text(encoding="utf-8"))
    try:
        process_group = os.getpgid(child_pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _install_real_script_popen(monkeypatch, script: str) -> None:
    """Run the supplied Python script while preserving Popen process options."""
    def fake_popen(command, **kwargs):
        return _real_popen([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(validator, "_resolve_fuzz_cargo", lambda: "/fake/cargo")
    monkeypatch.setattr(validator.subprocess, "Popen", fake_popen)


def test_invoke_fuzz_timeout_terminates_descendant_processes(
    tmp_path: Path, monkeypatch
) -> None:
    """A timeout kills the whole invocation group and releases inherited pipes."""
    script, child_pid_path, child_marker = _process_tree_script(tmp_path)
    _install_real_script_popen(monkeypatch, script)
    monkeypatch.setattr(validator, "_PROCESS_TERMINATION_GRACE_SECONDS", 0.1)

    try:
        result = validator._invoke_fuzz("corpus_population", [], 0.25)

        assert result["returncode"] == -1
        assert result["stderr"].startswith("timed out: ")
        _wait_for_file(child_pid_path)
        time.sleep(1.0)
        assert not child_marker.exists(), "a descendant survived the timeout"
        assert not validator._ACTIVE_FUZZ_PROCESSES
    finally:
        _kill_test_process_group(child_pid_path)


def test_parent_interrupt_cancels_active_process_groups(
    tmp_path: Path, monkeypatch
) -> None:
    """Interrupt cleanup stops live fuzz workers and their descendants."""
    script, child_pid_path, child_marker = _process_tree_script(tmp_path)
    _install_real_script_popen(monkeypatch, script)
    monkeypatch.setattr(validator, "_PROCESS_TERMINATION_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(validator, "_FUZZ_CANCEL_REQUESTED", threading.Event())
    results: list[dict] = []
    worker = threading.Thread(
        target=lambda: results.append(
            validator._invoke_fuzz("corpus_population", [], 30)
        ),
        name="fuzz-process-worker",
    )
    worker.start()
    try:
        _wait_for_file(child_pid_path)

        class _InterruptingJoin(threading.Thread):
            """Raise once for the initial join, then tolerate cleanup joining."""

            def __init__(self) -> None:
                super().__init__(name="interrupt-trigger")

            def join(self, timeout=None):
                if timeout is None:
                    raise KeyboardInterrupt("simulated parent interrupt")

        stop = threading.Event()
        try:
            validator._join_workers([_InterruptingJoin(), worker], stop)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("the parent interrupt must be re-raised")

        worker.join(5)
        assert stop.is_set()
        assert not worker.is_alive()
        assert len(results) == 1
        assert not validator._ACTIVE_FUZZ_PROCESSES
        time.sleep(1.0)
        assert not child_marker.exists(), "a descendant survived parent cancellation"
    finally:
        _kill_test_process_group(child_pid_path)
        worker.join(5)


def test_default_artifact_paths_follow_the_cargo_package_version() -> None:
    """Artifact defaults are derived from Cargo and work for another version."""
    current_version = validator._cargo_package_version()
    current = validator._release_artifact_paths(current_version)
    assert validator.DEFAULT_MANIFEST == current["manifest"]
    assert validator.DEFAULT_CORPUS_MANIFEST == current["corpus_manifest"]
    assert validator.DEFAULT_RECORD == current["record"]
    assert validator.DEFAULT_LOG_DIR == current["log_dir"]

    alternate = validator._release_artifact_paths("7.8.9")
    assert alternate["manifest"] == (
        "artifacts/release/7.8.9/blocking-fuzz-target-manifest.json"
    )
    assert alternate["corpus_manifest"] == (
        "artifacts/release/7.8.9/corpus-seed-manifest.json"
    )
    assert alternate["record"] == (
        "artifacts/release/7.8.9/fuzz-qualification-record.json"
    )
    assert alternate["log_dir"] == "artifacts/release/7.8.9/fuzz-logs"
