"""Tests for the retained module probe artifact validator."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.perf.validate_module_probe_artifacts import (
    EXPECTED_RESPONSE_FIELDS,
    SCENARIOS,
    _validate_response_schema,
    main,
    validate_probe_artifacts,
)


def _write_probe_pack(root: Path) -> tuple[Path, Path]:
    probe_dir = root / "perf" / "baselines" / "module-baseline-091-raw-probes"
    probe_dir.mkdir(parents=True)
    scenarios = []
    for scenario in SCENARIOS:
        body = f"# {scenario}\nTail\n".encode()
        (probe_dir / f"{scenario}.headers").write_text(
            "HTTP/1.1 200 OK\nContent-Type: text/markdown\n",
            encoding="utf-8",
        )
        (probe_dir / f"{scenario}.body").write_bytes(body)
        probe = {
            "http_status": 200,
            "headers": {"content-type": "text/markdown"},
            "content_type": "text/markdown",
            "content_encoding": "",
            "body_bytes": len(body),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "heading_present": True,
            "tail_token_present": True,
            "tail_token_count": 1,
            "verdict": "pass",
            "failure_reason": "",
            "curl_exit_code": 0,
            "header_artifact": f"{scenario}.headers",
            "body_artifact": f"{scenario}.body",
        }
        (probe_dir / f"{scenario}.json").write_text(
            json.dumps(probe), encoding="utf-8"
        )
        scenarios.append({"name": scenario, "response_correctness": probe.copy()})

    baseline = root / "perf" / "baselines" / "module-baseline-091.json"
    baseline.write_text(
        json.dumps({"module_benchmark": {"scenarios": scenarios}}),
        encoding="utf-8",
    )
    return probe_dir, baseline


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def test_validates_all_eight_probe_triplets_and_baseline(tmp_path: Path) -> None:
    probe_dir, baseline = _write_probe_pack(tmp_path)

    errors = validate_probe_artifacts(
        _relative(tmp_path, probe_dir),
        baseline=_relative(tmp_path, baseline),
        repo_root=tmp_path,
    )

    assert errors == []


@pytest.mark.parametrize("field", EXPECTED_RESPONSE_FIELDS)
def test_missing_complete_response_field_fails(
    tmp_path: Path, field: str
) -> None:
    probe_dir, _baseline = _write_probe_pack(tmp_path)
    path = probe_dir / "plain-small.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop(field)
    path.write_text(json.dumps(payload), encoding="utf-8")

    errors = validate_probe_artifacts(
        _relative(tmp_path, probe_dir), repo_root=tmp_path
    )

    assert any(
        "plain-small" in error and field in error for error in errors
    )


def test_unexpected_response_field_fails(tmp_path: Path) -> None:
    probe_dir, _baseline = _write_probe_pack(tmp_path)
    path = probe_dir / "plain-small.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_extension"] = "not allowed"
    path.write_text(json.dumps(payload), encoding="utf-8")

    errors = validate_probe_artifacts(
        _relative(tmp_path, probe_dir), repo_root=tmp_path
    )

    assert any(
        "plain-small" in error and "unexpected response fields" in error
        for error in errors
    )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("http_status", True, "http_status must be an int"),
        ("http_status", 201, "http_status must be 200"),
        ("headers", [], "headers must be an object"),
        ("headers", {"content-type": 7}, "headers key/value pairs must be strings"),
        ("headers", {"Content-Type": "text/markdown"}, "normalized lowercase"),
        ("content_type", "text/plain", "text/markdown media type"),
        ("content_encoding", "gzip", "content_encoding must be empty"),
        ("failure_reason", "unexpected", "failure_reason must be empty"),
    ],
)
def test_complete_response_schema_is_strict(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    probe_dir, _baseline = _write_probe_pack(tmp_path)
    path = probe_dir / "large-body.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    errors = validate_probe_artifacts(
        _relative(tmp_path, probe_dir), repo_root=tmp_path
    )

    assert any("large-body" in error and expected in error for error in errors)


def test_response_schema_rejects_non_string_header_key() -> None:
    payload = {
        "http_status": 200,
        "headers": {1: "text/markdown"},
        "content_type": "text/markdown",
        "content_encoding": "",
        "body_bytes": 1,
        "body_sha256": "0" * 64,
        "heading_present": True,
        "tail_token_present": True,
        "tail_token_count": 1,
        "verdict": "pass",
        "failure_reason": "",
        "curl_exit_code": 0,
        "header_artifact": "x.headers",
        "body_artifact": "x.body",
    }

    errors = _validate_response_schema("plain-small", payload)

    assert any("headers key/value pairs must be strings" in error for error in errors)


@pytest.mark.parametrize(
    "header_text",
    [
        "not an HTTP header",
        "HTTP/1.1 201 Created\nContent-Type: text/markdown\n",
        "HTTP/1.1 200 OK\nContent-Type: text/html\n",
        "HTTP/1.1 200 OK\nContent-Type: text/markdown\nContent-Encoding: gzip\n",
    ],
)
def test_headers_artifact_must_bind_to_probe_json(
    tmp_path: Path, header_text: str
) -> None:
    probe_dir, _baseline = _write_probe_pack(tmp_path)
    (probe_dir / "plain-small.headers").write_text(
        header_text, encoding="utf-8"
    )

    errors = validate_probe_artifacts(
        _relative(tmp_path, probe_dir), repo_root=tmp_path
    )

    assert any("plain-small" in error for error in errors)


def test_headers_values_must_match_probe_json(tmp_path: Path) -> None:
    probe_dir, _baseline = _write_probe_pack(tmp_path)
    (probe_dir / "plain-small.headers").write_text(
        "HTTP/1.1 200 OK\nContent-Type: text/markdown\nX-Test: actual\n",
        encoding="utf-8",
    )

    errors = validate_probe_artifacts(
        _relative(tmp_path, probe_dir), repo_root=tmp_path
    )

    assert any(
        "plain-small.headers" in error and "do not match JSON headers" in error
        for error in errors
    )


def test_headers_parser_uses_final_response_block(tmp_path: Path) -> None:
    probe_dir, _baseline = _write_probe_pack(tmp_path)
    (probe_dir / "plain-small.headers").write_text(
        "HTTP/1.1 100 Continue\nX-Interim: ignored\n\n"
        "HTTP/1.1 200 OK\nContent-Type: text/markdown\n",
        encoding="utf-8",
    )

    errors = validate_probe_artifacts(
        _relative(tmp_path, probe_dir), repo_root=tmp_path
    )

    assert errors == []


@pytest.mark.parametrize("suffix", ["headers", "body", "json"])
def test_missing_any_required_file_fails(tmp_path: Path, suffix: str) -> None:
    probe_dir, _baseline = _write_probe_pack(tmp_path)
    (probe_dir / f"plain-small.{suffix}").unlink()

    errors = validate_probe_artifacts(
        _relative(tmp_path, probe_dir), repo_root=tmp_path
    )

    assert any("plain-small" in error and suffix in error for error in errors)


def test_empty_body_fails(tmp_path: Path) -> None:
    probe_dir, _baseline = _write_probe_pack(tmp_path)
    (probe_dir / "gzip-large.body").write_bytes(b"")

    errors = validate_probe_artifacts(
        _relative(tmp_path, probe_dir), repo_root=tmp_path
    )

    assert any("gzip-large.body" in error and "empty" in error for error in errors)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("verdict", "fail", "verdict"),
        ("curl_exit_code", 7, "curl_exit_code"),
        ("header_artifact", "wrong.headers", "header_artifact"),
        ("body_artifact", "wrong.body", "body_artifact"),
    ],
)
def test_probe_json_contract_is_fail_closed(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    probe_dir, _baseline = _write_probe_pack(tmp_path)
    path = probe_dir / "large-body.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    errors = validate_probe_artifacts(
        _relative(tmp_path, probe_dir), repo_root=tmp_path
    )

    assert any("large-body" in error and expected in error for error in errors)


def test_body_digest_and_byte_count_are_verified(tmp_path: Path) -> None:
    probe_dir, _baseline = _write_probe_pack(tmp_path)
    path = probe_dir / "streaming-first.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["body_sha256"] = "0" * 64
    payload["body_bytes"] += 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    errors = validate_probe_artifacts(
        _relative(tmp_path, probe_dir), repo_root=tmp_path
    )

    assert any("streaming-first.body" in error and "SHA-256" in error for error in errors)
    assert any("streaming-first.body" in error and "byte count" in error for error in errors)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("curl_exit_code", True, "curl_exit_code must be an int"),
        ("heading_present", 1, "heading_present must be true"),
        ("tail_token_present", "true", "tail_token_present must be true"),
        ("tail_token_count", 0, "tail_token_count must be > 0"),
        ("tail_token_count", True, "tail_token_count must be an int"),
        ("body_bytes", "1", "body_bytes must be an int"),
    ],
)
def test_probe_json_scalar_contract_is_strict(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    probe_dir, _baseline = _write_probe_pack(tmp_path)
    path = probe_dir / "large-body.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    errors = validate_probe_artifacts(
        _relative(tmp_path, probe_dir), repo_root=tmp_path
    )

    assert any("large-body" in error and expected in error for error in errors)


def test_baseline_response_correctness_requires_complete_object_equality(
    tmp_path: Path,
) -> None:
    probe_dir, baseline = _write_probe_pack(tmp_path)
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    payload["module_benchmark"]["scenarios"][0]["response_correctness"][
        "schema_extension"
    ] = "not allowed"
    baseline.write_text(json.dumps(payload), encoding="utf-8")

    errors = validate_probe_artifacts(
        _relative(tmp_path, probe_dir),
        baseline=_relative(tmp_path, baseline),
        repo_root=tmp_path,
    )

    assert any(
        "plain-small" in error
        and "exactly equal" in error
        and "extra=schema_extension" in error
        for error in errors
    )


def test_malformed_or_non_object_probe_json_fails(tmp_path: Path) -> None:
    probe_dir, _baseline = _write_probe_pack(tmp_path)
    (probe_dir / "plain-small.json").write_text("{broken", encoding="utf-8")
    (probe_dir / "chunked-medium.json").write_text("[]", encoding="utf-8")

    errors = validate_probe_artifacts(
        _relative(tmp_path, probe_dir), repo_root=tmp_path
    )

    assert any("plain-small" in error and "invalid JSON" in error for error in errors)
    assert any("chunked-medium" in error and "not an object" in error for error in errors)


def test_finalized_response_correctness_mismatch_fails(tmp_path: Path) -> None:
    probe_dir, baseline = _write_probe_pack(tmp_path)
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    payload["module_benchmark"]["scenarios"][0]["response_correctness"][
        "tail_token_count"
    ] = 99
    baseline.write_text(json.dumps(payload), encoding="utf-8")

    errors = validate_probe_artifacts(
        _relative(tmp_path, probe_dir),
        baseline=_relative(tmp_path, baseline),
        repo_root=tmp_path,
    )

    assert any("plain-small" in error and "tail_token_count" in error for error in errors)


def test_absolute_and_traversal_probe_paths_are_rejected(tmp_path: Path) -> None:
    probe_dir, _baseline = _write_probe_pack(tmp_path)

    absolute_errors = validate_probe_artifacts(
        str(probe_dir), repo_root=tmp_path
    )
    traversal_errors = validate_probe_artifacts(
        "perf/baselines/../outside", repo_root=tmp_path
    )

    assert any("repository-relative" in error for error in absolute_errors)
    assert any(".." in error for error in traversal_errors)


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    link = tmp_path / "perf" / "baselines" / "escaped-probes"
    link.parent.mkdir(parents=True)
    link.symlink_to(Path("/tmp"), target_is_directory=True)

    errors = validate_probe_artifacts(
        "perf/baselines/escaped-probes", repo_root=tmp_path
    )

    assert any("escapes validation root" in error for error in errors)


def test_cli_rejects_absolute_probe_path(tmp_path: Path) -> None:
    assert main(["--probe-dir", str(tmp_path / "probes")]) == 1


def test_response_field_contract_stays_explicit() -> None:
    assert EXPECTED_RESPONSE_FIELDS == (
        "http_status",
        "headers",
        "content_type",
        "content_encoding",
        "body_bytes",
        "body_sha256",
        "heading_present",
        "tail_token_present",
        "tail_token_count",
        "verdict",
        "failure_reason",
        "curl_exit_code",
        "header_artifact",
        "body_artifact",
    )
