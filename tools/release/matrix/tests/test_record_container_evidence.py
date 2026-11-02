"""Unit tests for the container-registry evidence composer."""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from tools.release.matrix import record_container_evidence as composer
from tools.release.matrix.evidence_contract import record_covers_entry, validate_record

COMMIT = "a" * 40
BASE_DIGEST = "sha256:" + "4" * 64
BUILT_ID = "sha256:" + "5" * 64
RUN_URL = "https://github.com/cnkang/nginx-markdown-for-agents/actions/runs/42"


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "repo"
    (root / "examples" / "docker").mkdir(parents=True)
    (root / "rust-toolchain.toml").write_text('channel = "1.98.1"\n', encoding="utf-8")
    (root / composer.DOCKERFILE).write_text("FROM scratch\n", encoding="utf-8")
    return root


def _module_and_ledger(tmp_path: pathlib.Path, *, libc: str = "glibc", arch: str = "amd64"):
    build = tmp_path / "module"
    build.mkdir()
    (build / "ngx_http_markdown_filter_module.so").write_bytes(b"module")
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps(
            {
                "nginx_version": "1.26.3",
                "libc": libc,
                "arch": arch,
                "module_sha256": hashlib.sha256(b"module").hexdigest(),
                "tests": [
                    {"name": "load", "status": "PASS", "detail": "nginx -t"},
                    {"name": "request_markdown", "status": "PASS", "detail": "200"},
                    {"name": "request_passthrough", "status": "PASS", "detail": "200"},
                    {"name": "lifecycle", "status": "PASS", "detail": "reload"},
                    {"name": "static_brotli", "status": "PASS", "detail": "none"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return build, ledger


def _compose(root, build, ledger, **overrides):
    target = {
        "distro": "debian12",
        "image_reference": "nginx:1.26.3",
        "image_digest": BASE_DIGEST,
        "built_image_id": BUILT_ID,
    }
    for key in list(overrides):
        if key in target:
            target[key] = overrides.pop(key)
    kwargs = {
        "module_dir": build,
        "ledger_path": ledger,
        "identity": ("1.26.3", "glibc", "amd64"),
        "target": composer.ContainerTarget(**target),
        "source_sha": COMMIT,
        "verified_by": "ci:official-nginx-docker/build-and-verify",
        "workflow_run_url": RUN_URL,
        "repo_root": root,
    }
    kwargs.update(overrides)
    return composer.compose(**kwargs)


def _row() -> dict:
    return {
        "nginx_version": "1.26.3",
        "os": "debian12",
        "libc": "glibc",
        "arch": "amd64",
        "artifact_type": "docker-image",
        "source": "container-registry",
        "image_ref": "nginx:1.26.3",
        "image_digest": BASE_DIGEST,
    }


def test_compose_produces_a_valid_record_that_covers_its_row(tmp_path):
    root = _repo(tmp_path)
    build, ledger = _module_and_ledger(tmp_path)
    record = _compose(root, build, ledger)

    assert validate_record(record) == []
    assert record["source_kind"] == "container-registry"
    assert record["image_digest"] == BASE_DIGEST
    assert record["built_image_id"] == BUILT_ID
    assert record["commit_sha"] == COMMIT
    assert record["workflow_run_url"] == RUN_URL
    assert record["tests"][0]["name"] == "build"
    assert record_covers_entry(record, _row())


def test_build_args_digest_binds_the_dockerfile_and_base(tmp_path):
    root = _repo(tmp_path)
    build, ledger = _module_and_ledger(tmp_path)
    first = _compose(root, build, ledger)["build_args_digest"]
    moved = _compose(root, build, ledger, image_digest="sha256:" + "6" * 64)[
        "build_args_digest"
    ]
    (root / composer.DOCKERFILE).write_text("FROM scratch\n# x\n", encoding="utf-8")
    changed = _compose(root, build, ledger)["build_args_digest"]
    assert len({first, moved, changed}) == 3


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"image_digest": "nginx:1.26.3"}, "image digest"),
        ({"built_image_id": "latest"}, "built image ID"),
        ({"source_sha": "main"}, "source SHA"),
        ({"distro": "Debian 12"}, "distro"),
    ],
)
def test_mutable_or_malformed_identity_is_rejected(tmp_path, override, message):
    root = _repo(tmp_path)
    build, ledger = _module_and_ledger(tmp_path)
    with pytest.raises(composer.ComposeError, match=message):
        _compose(root, build, ledger, **override)


def test_module_digest_mismatch_is_rejected(tmp_path):
    root = _repo(tmp_path)
    build, ledger = _module_and_ledger(tmp_path)
    (build / "ngx_http_markdown_filter_module.so").write_bytes(b"tampered")
    with pytest.raises(composer.ComposeError, match="module digest"):
        _compose(root, build, ledger)


def test_skipped_check_is_not_composed(tmp_path):
    root = _repo(tmp_path)
    build, ledger = _module_and_ledger(tmp_path)
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    payload["tests"][-1]["status"] = "SKIP"
    ledger.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(composer.ComposeError, match="non-passing"):
        _compose(root, build, ledger)


def test_main_refuses_an_outside_root_output(tmp_path, capsys):
    build, ledger = _module_and_ledger(tmp_path)
    out = tmp_path / "outside.json"
    rc = composer.main(
        [
            "--module-dir", str(build),
            "--ledger", str(ledger),
            "--nginx-version", "1.26.3",
            "--libc", "glibc",
            "--arch", "amd64",
            "--distro", "debian12",
            "--image-reference", "nginx:1.26.3",
            "--image-digest", BASE_DIGEST,
            "--built-image-id", BUILT_ID,
            "--out", str(out),
            "--source-sha", COMMIT,
            "--verified-by", "ci:test",
            "--workflow-run-url", RUN_URL,
        ]
    )
    assert rc == 1
    assert "escapes root" in capsys.readouterr().err
    assert not out.exists()
