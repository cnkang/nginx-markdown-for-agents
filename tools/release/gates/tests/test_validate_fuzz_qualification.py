"""Regression tests for the fuzz qualification gate validator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
