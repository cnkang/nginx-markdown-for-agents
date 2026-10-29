"""Unit tests for the nginx.org evidence composer."""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from tools.release.matrix.evidence_contract import validate_record
from tools.release.matrix import record_nginx_org_evidence as composer

COMMIT = "a" * 40
SOURCE_SHA = "b" * 64


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create the minimum tree the composer reads from."""
    root = tmp_path / "repo"
    (root / "packaging").mkdir(parents=True)
    (root / "tools" / "build_release").mkdir(parents=True)
    (root / "artifacts" / "release" / "0.9.3").mkdir(parents=True)
    (root / "packaging" / "checksums.sha256").write_text(
        f"{SOURCE_SHA}  nginx-1.30.6\n", encoding="utf-8"
    )
    (root / "rust-toolchain.toml").write_text(
        'channel = "1.98.1"\n', encoding="utf-8"
    )
    (root / "tools" / "build_release" / "Dockerfile.glibc").write_text(
        "FROM scratch\n", encoding="utf-8"
    )
    (root / "artifacts" / "release" / "0.9.3"
     / "official-build-feature-manifest.json").write_text(
        json.dumps({"prune_noise_regions": True, "streaming": True}),
        encoding="utf-8",
    )
    return root


def _module_and_ledger(tmp_path: pathlib.Path, module_bytes: bytes = b"module"):
    build = tmp_path / "build"
    build.mkdir()
    module = build / "ngx_http_markdown_filter_module.so"
    module.write_bytes(module_bytes)
    digest = hashlib.sha256(module_bytes).hexdigest()
    ledger = build / "ledger.json"
    ledger.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_kind": "nginx.org",
                "nginx_version": "1.30.6",
                "libc": "glibc",
                "arch": "arm64",
                "module_sha256": digest,
                "tests": [
                    {"name": "build", "status": "PASS", "detail": "x"},
                    {"name": "load", "status": "PASS", "detail": "nginx -t"},
                    {"name": "request_markdown", "status": "PASS", "detail": "200"},
                    {"name": "request_passthrough", "status": "PASS", "detail": "200"},
                    {"name": "lifecycle", "status": "PASS", "detail": "reload"},
                ],
                "pass": 5,
                "fail": 0,
                "skip": 0,
            }
        ),
        encoding="utf-8",
    )
    return build, ledger


def _compose(
    root: pathlib.Path,
    build: pathlib.Path,
    ledger: pathlib.Path,
    *,
    source_sha: str = COMMIT,
    **kw,
):
    return composer.compose(
        build_dir=build,
        ledger_path=ledger,
        version="1.30.6",
        libc="glibc",
        arch="arm64",
        verified_by="local:test",
        workflow_run_url="",
        repo_root=root,
        source_sha=source_sha,
        date="2026-10-29",
        **kw,
    )


def test_compose_produces_a_contract_valid_record(tmp_path):
    root = _repo(tmp_path)
    build, ledger = _module_and_ledger(tmp_path)

    record = _compose(root, build, ledger)

    assert validate_record(record) == []
    assert record["target_identity"] == "nginx.org:1.30.6:linux:glibc:arm64"
    assert record["module_sha256"] == hashlib.sha256(b"module").hexdigest()
    assert record["source_digest"] == f"sha256:{SOURCE_SHA}"
    assert record["commit_sha"] == COMMIT
    assert record["rust_version"] == "1.98.1"
    assert record["build_args_digest"].startswith("sha256:")


def test_module_digest_mismatch_is_rejected(tmp_path):
    root = _repo(tmp_path)
    build, ledger = _module_and_ledger(tmp_path)
    # Replace the module after the ledger was written.
    (build / "ngx_http_markdown_filter_module.so").write_bytes(b"tampered")

    with pytest.raises(composer.ComposeError, match="module digest"):
        _compose(root, build, ledger)


def test_ledger_for_a_different_architecture_is_rejected(tmp_path):
    root = _repo(tmp_path)
    build, ledger = _module_and_ledger(tmp_path)
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    payload["arch"] = "amd64"
    ledger.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(composer.ComposeError, match="libc or architecture"):
        _compose(root, build, ledger)


def test_missing_checksum_entry_is_rejected(tmp_path):
    root = _repo(tmp_path)
    build, ledger = _module_and_ledger(tmp_path)
    (root / "packaging" / "checksums.sha256").write_text("", encoding="utf-8")

    with pytest.raises(composer.ComposeError, match="no entry"):
        _compose(root, build, ledger)


def test_build_args_digest_binds_the_dockerfile(tmp_path):
    root = _repo(tmp_path)
    build, ledger = _module_and_ledger(tmp_path)
    first = _compose(root, build, ledger)["build_args_digest"]

    (root / "tools" / "build_release" / "Dockerfile.glibc").write_text(
        "FROM scratch\n# changed\n", encoding="utf-8"
    )
    second = _compose(root, build, ledger)["build_args_digest"]

    assert first != second


def test_invalid_source_sha_is_rejected(tmp_path):
    root = _repo(tmp_path)
    build, ledger = _module_and_ledger(tmp_path)

    with pytest.raises(composer.ComposeError, match="40 lowercase hex"):
        _compose(root, build, ledger, source_sha="not-a-sha")
