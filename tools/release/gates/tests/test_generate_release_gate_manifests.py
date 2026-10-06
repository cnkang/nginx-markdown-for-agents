"""Consumer-side tests for candidate-bound release evidence generation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.release.gates import generate_release_gate_manifests as generator
from tools.release.gates import validate_fuzz_qualification as fuzz_validator

CANDIDATE_SHA = "a" * 40
GENERATED_AT = "2026-09-25T12:00:00+00:00"


def _write_fuzz_record(tmp_path: Path, record: dict) -> None:
    """Write one record at the path consumed by final-evidence generation."""
    path = _release_root(tmp_path) / generator.FUZZ_QUALIFICATION_RECORD_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record), encoding="utf-8")


def _release_root(tmp_path: Path) -> Path:
    return tmp_path / "artifacts" / "release" / "0.9.2"


def _write_fuzz_manifest(tmp_path: Path, manifest: object) -> None:
    """Write the candidate-bound blocking fuzz target manifest."""
    path = _release_root(tmp_path) / "blocking-fuzz-target-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest), encoding="utf-8")


def _write_fuzz_support_files(tmp_path: Path) -> None:
    """Create the candidate-bound corpus seed and raw-log inputs."""
    root = _release_root(tmp_path)
    seeds = []
    for target in ("parser_html", "convert_html"):
        corpus_dir = (
            tmp_path / generator.FUZZ_CORPUS_RELATIVE_ROOT.as_posix() / target
        )
        corpus_dir.mkdir(parents=True, exist_ok=True)
        seed_path = corpus_dir / "basic.seed"
        seed_bytes = f"seed:{target}".encode("utf-8")
        seed_path.write_bytes(seed_bytes)
        seeds.append({
            "target": target,
            "seed_path": seed_path.relative_to(tmp_path).as_posix(),
            "digest": "sha256:" + hashlib.sha256(seed_bytes).hexdigest(),
        })
        log_path = root / "fuzz-logs" / f"{target}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("qualification log\n", encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    (root / "corpus-seed-manifest.json").write_text(
        json.dumps({
            "schema_version": "release.corpus-seed.v1",
            "candidate_sha": CANDIDATE_SHA,
            "seeds": seeds,
        }),
        encoding="utf-8",
    )


def _soak_status(tmp_path: Path, monkeypatch) -> str:
    monkeypatch.setattr(
        generator, "_release_state",
        lambda: ("0.9.2", tmp_path, tmp_path),
    )
    evidence, _ = generator.build_final_evidence(CANDIDATE_SHA, GENERATED_AT)
    return next(
        entry["status"] for entry in evidence["entries"]
        if entry["domain"] == "soak"
    )


# Metrics inside every module-level threshold, and the config that judges them.
# The engine, not this file, decides what the pass verdict is spelled -- the
# generator must accept that spelling.
_PASSING_METRICS = {
    "p50_latency_small_pct": 1.0,
    "p95_latency_small_pct": 1.5,
    "p50_latency_large_pct": 5.0,
    "ttfb_streaming_large_pct": 3.0,
    "fallback_rate_abs": 0.02,
    "memory_slope_pct": 0.5,
}
_MODULE_THRESHOLDS = {
    "module_level": {
        "p50_latency_small_pct": 10,
        "p95_latency_small_pct": 15,
        "p50_latency_large_pct": 5,
        "ttfb_streaming_large_pct": 10,
        "fallback_rate_abs": 0.05,
        "memory_slope_pct": 20,
    },
}


def _perf_status(tmp_path: Path, monkeypatch, pack: dict | None) -> str:
    """Return the final evidence status for its blocking performance domain."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir(parents=True, exist_ok=True)
    (schemas_dir / "final-evidence-manifest.schema.json").write_text(
        "{}\n", encoding="utf-8")
    (schemas_dir / "observation-state.schema.json").write_text(
        "{}\n", encoding="utf-8")
    monkeypatch.setattr(
        generator, "FINAL_EVIDENCE_SCHEMA",
        "schemas/final-evidence-manifest.schema.json")
    monkeypatch.setattr(
        generator, "OBSERVATION_STATE_SCHEMA",
        "schemas/observation-state.schema.json")
    monkeypatch.setattr(generator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        generator, "_release_state", lambda: ("0.9.2", Path("."), tmp_path))
    if pack is not None:
        path = tmp_path / "perf" / "reports" / "evidence-092.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(pack), encoding="utf-8")
    evidence, _ = generator.build_final_evidence(CANDIDATE_SHA, GENERATED_AT)
    return next(
        entry["status"] for entry in evidence["entries"]
        if entry["domain"] == "performance"
    )


def test_final_evidence_accepts_the_verdict_the_perf_gate_writes(
    tmp_path, monkeypatch
):
    """The perf gate's own pass verdict must count as a pass.

    The generator compared the evidence pack's verdict against ``"PASS"``, a
    value that pack never contains: the gate writes GO / NO_GO /
    MISSING_EVIDENCE (see tools/perf/threshold_engine.py). This blocking entry
    therefore reported fail on every release run, even when the performance gate
    printed ``Verdict: GO`` and exited zero.

    The pass spelling comes from the engine rather than a literal here, so
    renaming it in the engine fails this test rather than silently re-breaking
    the release gate.
    """
    from tools.perf.threshold_engine import evaluate_module_level

    baseline = dict(_PASSING_METRICS)
    baseline["fallback_rate_abs"] = 0.01
    pass_verdict = evaluate_module_level(
        _PASSING_METRICS, baseline, _MODULE_THRESHOLDS
    )["verdict"]
    assert pass_verdict == generator.PERF_EVIDENCE_PASS_VERDICT, {
        "engine": pass_verdict,
        "generator": generator.PERF_EVIDENCE_PASS_VERDICT,
        "why": "the generator must accept the verdict the perf gate writes",
    }

    assert _perf_status(
        tmp_path, monkeypatch,
        {"verdict": pass_verdict, "breaches": [], "results": []},
    ) == "pass"

    # Every non-pass verdict the gate can write must stay a fail. Enumerated
    # rather than inferred from "anything that is not GO": the defect was a
    # comparison against a value that never occurs, so the test states the
    # values that DO occur.
    for other in ("NO_GO", "MISSING_EVIDENCE", "SKIPPED"):
        assert _perf_status(
            tmp_path, monkeypatch,
            {"verdict": other, "breaches": [], "results": []},
        ) == "fail", other


def test_final_evidence_fails_the_perf_entry_without_evidence(
    tmp_path, monkeypatch
):
    """Absent or unreadable evidence stays a fail, not a skip."""
    assert _perf_status(tmp_path, monkeypatch, None) == "fail"

    path = tmp_path / "perf" / "reports" / "evidence-092.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(generator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        generator, "_release_state", lambda: ("0.9.2", Path("."), tmp_path))
    evidence, _ = generator.build_final_evidence(CANDIDATE_SHA, GENERATED_AT)
    assert next(
        entry["status"] for entry in evidence["entries"]
        if entry["domain"] == "performance"
    ) == "fail"


def test_final_evidence_accepts_only_candidate_bound_soak(tmp_path, monkeypatch):
    fixture = json.loads(
        (Path(__file__).parents[4] / "tests/fixtures/release/"
         / "soak-qualification-valid.json").read_text(encoding="utf-8")
    )
    fixture["candidate_sha"] = CANDIDATE_SHA
    (tmp_path / generator.SOAK_QUALIFICATION_RECORD_NAME).write_text(
        json.dumps(fixture), encoding="utf-8"
    )
    assert _soak_status(tmp_path, monkeypatch) == "pass"

    fixture["candidate_sha"] = "b" * 40
    (tmp_path / generator.SOAK_QUALIFICATION_RECORD_NAME).write_text(
        json.dumps(fixture), encoding="utf-8"
    )
    assert _soak_status(tmp_path, monkeypatch) == "fail"


def _fuzz_status(
    tmp_path: Path, monkeypatch, *, prepare_support: bool = True
) -> str:
    """Return the final evidence status for its blocking fuzz domain."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir(parents=True, exist_ok=True)
    final_schema = "schemas/final-evidence-manifest.schema.json"
    observation_schema = "schemas/observation-state.schema.json"
    (tmp_path / final_schema).write_text("{}\n", encoding="utf-8")
    (tmp_path / observation_schema).write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(generator, "FINAL_EVIDENCE_SCHEMA", final_schema)
    monkeypatch.setattr(generator, "OBSERVATION_STATE_SCHEMA", observation_schema)
    relative_root = Path("artifacts") / "release" / "0.9.2"
    release_root = tmp_path / relative_root
    monkeypatch.setattr(generator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        generator, "_release_state",
        lambda: ("0.9.2", relative_root, release_root),
    )
    if prepare_support:
        _write_fuzz_support_files(tmp_path)
    evidence, _ = generator.build_final_evidence(CANDIDATE_SHA, GENERATED_AT)
    return next(
        entry["status"] for entry in evidence["entries"]
        if entry["domain"] == "fuzz"
    )


def _valid_manifest() -> dict:
    """Build a minimal blocking fuzz target manifest matching the record."""
    return {
        "schema_version": "release.blocking-fuzz-target-manifest.v1",
        "candidate_sha": CANDIDATE_SHA,
        "created_at": GENERATED_AT,
        "targets": [
            {"name": "parser_html", "seed": 12345,
             "required_minutes": 15, "required_executions": 100000,
             "blocking": True},
            {"name": "convert_html", "seed": 12345,
             "required_minutes": 15, "required_executions": 100000,
             "blocking": True},
        ],
        "threshold_reference": "test",
    }


def _valid_record() -> dict:
    """Build a minimal passing record with the pinned identity."""
    return {
        "schema_version": fuzz_validator.SCHEMA_VERSION,
        "candidate_sha": CANDIDATE_SHA,
        "blocking_pass": True,
        "toolchain_identity": dict(
            fuzz_validator._EXPECTED_FUZZ_TOOLCHAIN_IDENTITY),
        "per_target": [
            {"target": "parser_html", "seed": 12345,
             "elapsed_seconds_total": 950, "executions_total": 150000,
             "crashes": 0, "sanitizer_findings": 0,
             "corpus_dir": "components/rust-converter/fuzz/corpus/parser_html",
             "seed_path": "components/rust-converter/fuzz/corpus/parser_html/basic.seed",
             "raw_log_ref": "artifacts/release/0.9.2/fuzz-logs/parser_html.log",
             "status": "pass"},
            {"target": "convert_html", "seed": 12345,
             "elapsed_seconds_total": 950, "executions_total": 150000,
             "crashes": 0, "sanitizer_findings": 0,
             "corpus_dir": "components/rust-converter/fuzz/corpus/convert_html",
             "seed_path": "components/rust-converter/fuzz/corpus/convert_html/basic.seed",
             "raw_log_ref": "artifacts/release/0.9.2/fuzz-logs/convert_html.log",
             "status": "pass"},
        ],
    }


def test_final_evidence_rejects_missing_fuzz_record_file(
    tmp_path: Path, monkeypatch
) -> None:
    _write_fuzz_manifest(tmp_path, _valid_manifest())
    assert _fuzz_status(tmp_path, monkeypatch) == "fail"


def test_final_evidence_accepts_valid_candidate_bound_toolchain(
        tmp_path: Path, monkeypatch) -> None:
    """A valid record from the pinned toolchain reaches the consumer as pass."""
    _write_fuzz_record(tmp_path, _valid_record())
    _write_fuzz_manifest(tmp_path, _valid_manifest())

    assert _fuzz_status(tmp_path, monkeypatch) == "pass"


def test_produced_seed_manifest_is_accepted_by_final_evidence(
        tmp_path: Path, monkeypatch) -> None:
    """The production manifest writer and release consumer share a schema."""
    monkeypatch.setattr(generator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(generator, "_fuzz_targets", lambda: ["parser_html"])
    seed_path = (
        tmp_path / generator.FUZZ_CORPUS_RELATIVE_ROOT.as_posix()
        / "parser_html" / "basic.seed"
    )
    seed_path.parent.mkdir(parents=True)
    seed_path.write_bytes(b"seed:parser_html")
    blocking_manifest, seed_manifest = generator.build_fuzz_manifests(
        CANDIDATE_SHA, GENERATED_AT)
    release_root = _release_root(tmp_path)
    release_root.mkdir(parents=True)
    (release_root / "blocking-fuzz-target-manifest.json").write_text(
        json.dumps(blocking_manifest), encoding="utf-8")
    (release_root / "corpus-seed-manifest.json").write_text(
        json.dumps(seed_manifest), encoding="utf-8")
    record = _valid_record()
    record["per_target"] = record["per_target"][:1]
    _write_fuzz_record(tmp_path, record)
    log_path = release_root / "fuzz-logs" / "parser_html.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("qualification log\n", encoding="utf-8")

    assert _fuzz_status(
        tmp_path, monkeypatch, prepare_support=False) == "pass"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("elapsed_seconds_total",
         fuzz_validator.MAX_RECORD_ELAPSED_SECONDS + 1),
        ("executions_total",
         fuzz_validator.MAX_LIBFUZZER_EXECUTIONS
         * fuzz_validator.MAX_FUZZ_INVOCATIONS + 1),
    ],
)
def test_final_evidence_rejects_impossible_fuzz_observations(
        field: str, value: int, tmp_path: Path, monkeypatch) -> None:
    """An implausible count or elapsed total cannot certify release evidence."""
    record = _valid_record()
    record["per_target"][0][field] = value
    _write_fuzz_record(tmp_path, record)
    _write_fuzz_manifest(tmp_path, _valid_manifest())
    _write_fuzz_support_files(tmp_path)

    assert _fuzz_status(tmp_path, monkeypatch) == "fail"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("corpus_dir", "../../outside-corpus"),
        ("corpus_dir", "components/rust-converter/fuzz/corpus/convert_html"),
        ("seed_path", "/tmp/outside-seed"),
        ("seed_path", "components/rust-converter/fuzz/corpus/convert_html/basic.seed"),
        ("raw_log_ref", "../../outside.log"),
        ("raw_log_ref", "artifacts/release/0.9.1/fuzz-logs/parser_html.log"),
    ],
)
def test_final_evidence_rejects_unbound_fuzz_path_references(
        field: str, value: str, tmp_path: Path, monkeypatch) -> None:
    """Fuzz evidence paths must stay candidate/target bound and repo-local."""
    record = _valid_record()
    record["per_target"][0][field] = value
    _write_fuzz_record(tmp_path, record)
    _write_fuzz_manifest(tmp_path, _valid_manifest())

    assert _fuzz_status(tmp_path, monkeypatch) == "fail"


@pytest.mark.parametrize("mutation", ["missing", "stale-candidate"])
def test_final_evidence_requires_candidate_bound_corpus_manifest(
        mutation: str, tmp_path: Path, monkeypatch) -> None:
    _write_fuzz_record(tmp_path, _valid_record())
    _write_fuzz_manifest(tmp_path, _valid_manifest())
    _write_fuzz_support_files(tmp_path)
    seed_manifest = _release_root(tmp_path) / "corpus-seed-manifest.json"
    if mutation == "missing":
        seed_manifest.unlink()
    else:
        document = json.loads(seed_manifest.read_text(encoding="utf-8"))
        document["candidate_sha"] = "b" * 40
        seed_manifest.write_text(json.dumps(document), encoding="utf-8")

    assert _fuzz_status(
        tmp_path, monkeypatch, prepare_support=False) == "fail"


@pytest.mark.parametrize("field", ["corpus_dir", "seed_path", "raw_log_ref"])
def test_final_evidence_rejects_symlinked_fuzz_path_references(
        field: str, tmp_path: Path, monkeypatch) -> None:
    _write_fuzz_record(tmp_path, _valid_record())
    _write_fuzz_manifest(tmp_path, _valid_manifest())
    _write_fuzz_support_files(tmp_path)
    target = "parser_html"
    target_corpus = (
        tmp_path / generator.FUZZ_CORPUS_RELATIVE_ROOT.as_posix() / target
    )
    if field == "corpus_dir":
        (target_corpus / "basic.seed").unlink()
        target_corpus.rmdir()
        alternate = tmp_path / "alternate-corpus"
        alternate.mkdir()
        target_corpus.symlink_to(alternate, target_is_directory=True)
    elif field == "seed_path":
        seed_path = target_corpus / "basic.seed"
        seed_path.unlink()
        alternate = tmp_path / "alternate-seed"
        alternate.write_bytes(b"seed:parser_html")
        seed_path.symlink_to(alternate)
    else:
        log_path = _release_root(tmp_path) / "fuzz-logs" / f"{target}.log"
        log_path.unlink()
        alternate = tmp_path / "alternate-log"
        alternate.write_text("qualification log\n", encoding="utf-8")
        log_path.symlink_to(alternate)

    assert _fuzz_status(
        tmp_path, monkeypatch, prepare_support=False) == "fail"


def _mutate_record_case(record: dict, mutation: str) -> None:
    """Apply one record-only negative-fixture mutation."""
    mutations = {
        "missing-identity": lambda: record.pop("toolchain_identity"),
        "malformed-identity": lambda: record["toolchain_identity"].update(
            llvm_version="22.1.8"
        ),
        "stale-candidate": lambda: record.update(candidate_sha="b" * 40),
        "old-schema": lambda: record.update(
            schema_version="release.fuzz-qualification.v1"
        ),
        "missing-per-target": lambda: record.pop("per_target"),
        "duplicate-target": lambda: record["per_target"].append(
            dict(record["per_target"][0])
        ),
        "unknown-record-target": lambda: record["per_target"].append(
            {"target": "unlisted", "status": "pass"}
        ),
        "failed-blocking-target": lambda: record["per_target"][0].update(
            status="fail"
        ),
        "blocking-pass-false": lambda: record.update(blocking_pass=False),
        "below-elapsed-threshold": lambda: record["per_target"][0].update(
            elapsed_seconds_total=899
        ),
        "below-execution-threshold": lambda: record["per_target"][0].update(
            executions_total=99999
        ),
        "missing-elapsed-observation": lambda: record["per_target"][0].pop(
            "elapsed_seconds_total"
        ),
        "missing-execution-observation": lambda: record["per_target"][0].pop(
            "executions_total"
        ),
        "non-finite-elapsed-observation": lambda: record["per_target"][0].update(
            elapsed_seconds_total=float("inf")
        ),
        "wrong-target-seed": lambda: record["per_target"][0].update(seed=12346),
        "missing-raw-log-reference": lambda: record["per_target"][0].pop(
            "raw_log_ref"
        ),
        "crash-observation": lambda: record["per_target"][0].update(crashes=1),
        "sanitizer-observation": lambda: record["per_target"][0].update(
            sanitizer_findings=1
        ),
    }
    mutations[mutation]()


def _mutate_manifest_case(manifest: object, mutation: str) -> object:
    """Apply one malformed-manifest negative-fixture mutation."""
    if mutation == "manifest-not-object":
        return []
    assert isinstance(manifest, dict)
    mutations = {
        "manifest-targets-not-list": lambda: manifest.update(
            targets="not-an-array"
        ),
        "manifest-empty-targets": lambda: manifest.update(targets=[]),
        "manifest-target-not-object": lambda: manifest["targets"].__setitem__(
            0, "not-an-object"
        ),
        "manifest-missing-target-name": lambda: manifest["targets"][0].pop(
            "name"
        ),
        "manifest-non-string-target-name": lambda: manifest["targets"][0].update(
            name=17
        ),
        "manifest-invalid-seed": lambda: manifest["targets"][0].update(
            seed=True
        ),
        "manifest-invalid-blocking-flag": lambda: manifest["targets"][0].update(
            blocking="true"
        ),
        "manifest-missing-required-minutes": lambda: manifest["targets"][0].pop(
            "required_minutes"
        ),
        "manifest-non-finite-required-minutes": lambda: manifest["targets"][0].update(
            required_minutes=float("inf")
        ),
        "manifest-no-blocking-targets": lambda: [
            target.update(blocking=False) for target in manifest["targets"]
        ],
        "manifest-invalid-required-executions": lambda: manifest["targets"][0].update(
            required_executions=-1
        ),
        "manifest-duplicate-target": lambda: manifest["targets"].append(
            dict(manifest["targets"][0])
        ),
        "manifest-wrong-schema": lambda: manifest.update(
            schema_version="release.other.v1"
        ),
        "manifest-stale-candidate": lambda: manifest.update(
            candidate_sha="b" * 40
        ),
    }
    mutations[mutation]()
    return manifest


@pytest.mark.parametrize("malformed_target", ["record", "manifest"])
def test_final_evidence_fails_closed_on_malformed_fuzz_json(
    malformed_target: str, tmp_path: Path, monkeypatch
) -> None:
    """Malformed producer artifacts fail instead of aborting finalization."""
    _write_fuzz_record(tmp_path, _valid_record())
    _write_fuzz_manifest(tmp_path, _valid_manifest())
    path = _release_root(tmp_path) / (
        generator.FUZZ_QUALIFICATION_RECORD_NAME
        if malformed_target == "record"
        else "blocking-fuzz-target-manifest.json"
    )
    path.write_text("{", encoding="utf-8")

    assert _fuzz_status(tmp_path, monkeypatch) == "fail"


@pytest.mark.parametrize("mutation", [
    "missing-identity",
    "malformed-identity",
    "stale-candidate",
    "old-schema",
    "missing-manifest",
    "missing-record",
    "missing-per-target",
    "duplicate-target",
    "unknown-record-target",
    "failed-blocking-target",
    "blocking-pass-false",
    "below-elapsed-threshold",
    "below-execution-threshold",
    "missing-elapsed-observation",
    "missing-execution-observation",
    "non-finite-elapsed-observation",
    "wrong-target-seed",
    "missing-raw-log-reference",
    "crash-observation",
    "sanitizer-observation",
    "manifest-not-object",
    "manifest-targets-not-list",
    "manifest-empty-targets",
    "manifest-target-not-object",
    "manifest-missing-target-name",
    "manifest-non-string-target-name",
    "manifest-invalid-seed",
    "manifest-invalid-blocking-flag",
    "manifest-missing-required-minutes",
    "manifest-non-finite-required-minutes",
    "manifest-no-blocking-targets",
    "manifest-invalid-required-executions",
    "manifest-duplicate-target",
    "manifest-wrong-schema",
    "manifest-stale-candidate",
])
def test_final_evidence_rejects_incomplete_or_unbound_fuzz_record(
        mutation: str, tmp_path: Path, monkeypatch) -> None:
    """The consumer must not trust only the producer's blocking_pass flag."""
    record = _valid_record()
    manifest = _valid_manifest()
    if mutation == "missing-manifest":
        # Don't write the manifest
        _write_fuzz_record(tmp_path, record)
        assert _fuzz_status(tmp_path, monkeypatch) == "fail"
        return
    if mutation == "missing-record":
        _write_fuzz_manifest(tmp_path, manifest)
        assert _fuzz_status(tmp_path, monkeypatch) == "fail"
        return
    if mutation.startswith("manifest-"):
        manifest = _mutate_manifest_case(manifest, mutation)
    else:
        _mutate_record_case(record, mutation)
    _write_fuzz_record(tmp_path, record)
    _write_fuzz_manifest(tmp_path, manifest)

    assert _fuzz_status(tmp_path, monkeypatch) == "fail"
