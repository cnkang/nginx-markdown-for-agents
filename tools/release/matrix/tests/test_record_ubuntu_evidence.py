"""Unit tests for the Ubuntu-native evidence composer."""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from tools.release.matrix.evidence_contract import validate_record
from tools.release.matrix import record_ubuntu_evidence as composer

COMMIT = "c" * 40
MODULE = b"ubuntu-module"


def _build(tmp_path: pathlib.Path, **overrides) -> pathlib.Path:
    payload = {
        "schema_version": 1,
        "source_kind": "ubuntu-default-repo",
        "distro": "ubuntu-24.04",
        "distro_version": "24.04",
        "arch": "amd64",
        "libc": "glibc",
        "nginx_package_revision": "1.24.0-2ubuntu7.19",
        "nginx_version": "1.24.0",
        "abi_provide": "nginx-abi-1.24.0-1",
        "source_reference": "https://launchpad.net/ubuntu/+source/nginx/1.24.0-2ubuntu7.19",
        "source_digest": "sha256:" + "d" * 64,
        "build_args_digest": "sha256:" + "e" * 64,
        "module_sha256": hashlib.sha256(MODULE).hexdigest(),
        "rust_version": "1.98.1",
        "features": ["streaming", "prune_noise_regions"],
        "brotli_version": "1.1.0-2ubuntu0.1",
        "brotli_linkage": "dynamic",
    }
    payload.update(overrides)
    path = tmp_path / "ubuntu-native-build.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _ledger(tmp_path: pathlib.Path, **overrides) -> pathlib.Path:
    tests = [
        {"name": "load", "status": "PASS", "detail": "nginx -t"},
        {"name": "request_passthrough", "status": "PASS", "detail": "HTTP 200"},
        {"name": "request_markdown", "status": "PASS", "detail": "HTTP 200"},
        {"name": "lifecycle", "status": "PASS", "detail": "HEAD 200, reload PASS"},
        {"name": "negative_abi_rejected", "status": "PASS", "detail": "rejected"},
    ]
    payload = {
        "schema_version": 1,
        "source_kind": "ubuntu-default-repo",
        "distro": "ubuntu-24.04",
        "arch": "amd64",
        "libc": "glibc",
        "nginx_package_revision": "1.24.0-2ubuntu7.19",
        "nginx_version": "1.24.0",
        "module_sha256": hashlib.sha256(MODULE).hexdigest(),
        "tests": tests,
        "pass": len(tests),
        "fail": 0,
        "skip": 0,
    }
    payload.update(overrides)
    path = tmp_path / "ubuntu-native-ledger.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _compose(build, ledger, **kw):
    return composer.compose(
        build_path=build,
        ledger_path=ledger,
        verified_by="local:test",
        workflow_run_url="",
        source_sha=COMMIT,
        **kw,
    )


def test_compose_adds_the_build_class_and_recomputes_totals(tmp_path):
    record = _compose(_build(tmp_path), _ledger(tmp_path))

    assert validate_record(record) == []
    assert [test["name"] for test in record["tests"]][0] == "build"
    assert record["pass"] == 6
    assert record["fail"] == 0
    assert record["skip"] == 0
    assert record["commit_sha"] == COMMIT
    assert record["target_identity"] == (
        "ubuntu-default-repo:1.24.0-2ubuntu7.19:ubuntu-24.04:amd64"
    )
    assert record["brotli_version"] == "1.1.0-2ubuntu0.1"
    assert record["brotli_linkage"] == "dynamic"


def test_failed_test_in_the_ledger_is_refused(tmp_path):
    ledger = _ledger(tmp_path)
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    payload["tests"][0]["status"] = "FAIL"
    ledger.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(composer.ComposeError, match="non-passing"):
        _compose(_build(tmp_path), ledger)


def test_missing_verifier_class_is_refused(tmp_path):
    ledger = _ledger(tmp_path)
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    payload["tests"] = [t for t in payload["tests"] if t["name"] != "lifecycle"]
    ledger.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(composer.ComposeError, match="missing required test classes"):
        _compose(_build(tmp_path), ledger)


def test_build_and_ledger_revision_disagreement_is_refused(tmp_path):
    ledger = _ledger(tmp_path, nginx_package_revision="1.24.0-2ubuntu7.18")

    with pytest.raises(composer.ComposeError, match="disagree on nginx_package_revision"):
        _compose(_build(tmp_path), ledger)


def test_module_digest_disagreement_is_refused(tmp_path):
    ledger = _ledger(tmp_path, module_sha256="f" * 64)

    with pytest.raises(composer.ComposeError, match="module digest"):
        _compose(_build(tmp_path), ledger)


def test_declared_totals_are_not_copied(tmp_path):
    """A ledger that under-reports PASS is corrected from its own test list."""
    ledger = _ledger(tmp_path, **{"pass": 1})

    record = _compose(_build(tmp_path), ledger)

    assert record["pass"] == 6


def test_writer_refuses_an_output_path_outside_the_repository(tmp_path):
    """The composer must not write outside the checkout."""
    with pytest.raises(ValueError, match="escapes root"):
        composer.validate_write_path_within_root(
            tmp_path / "outside.json", root=composer.REPO_ROOT
        )


def test_writer_refuses_a_traversal_output_path():
    with pytest.raises(ValueError, match="traversal"):
        composer.validate_write_path_within_root(
            "docs/../outside.json", root=composer.REPO_ROOT
        )


def test_a_failed_build_entry_is_not_filtered_into_a_pass(tmp_path):
    """The composer supplies the build class, so a reported failure must win."""
    ledger = _ledger(tmp_path)
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    payload["tests"].append({"name": "build", "status": "FAIL", "detail": "compile"})
    ledger.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(composer.ComposeError, match="non-passing build"):
        _compose(_build(tmp_path), ledger)
