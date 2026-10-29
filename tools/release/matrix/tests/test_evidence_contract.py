"""Tests for the compatibility evidence contract.

The contract is the only place that decides whether a record may support a
release promise, so each rejection path gets its own test.
"""

from __future__ import annotations

from tools.release.matrix.evidence_contract import (
    RECORD_REQUIRED_FIELDS,
    is_promotable,
    record_covers_entry,
    validate_record,
)


def _tests() -> list[dict[str, str]]:
    return [
        {"name": "build", "status": "PASS"},
        {"name": "load", "status": "PASS"},
        {"name": "request_markdown", "status": "PASS"},
        {"name": "request_passthrough", "status": "PASS"},
        {"name": "lifecycle", "status": "PASS"},
    ]


def _record(**overrides) -> dict:
    record = {
        "schema_version": 1,
        "target_identity": "nginx.org:1.30.6:linux:amd64",
        "source_kind": "nginx.org",
        "nginx_version": "1.30.6",
        "nginx_package_revision": "1.30.6",
        "distro": "linux",
        "distro_version": "",
        "arch": "amd64",
        "libc": "glibc",
        "abi_provide": "",
        "source_reference": "https://nginx.org/download/nginx-1.30.6.tar.gz",
        "source_digest": "sha256:" + "a" * 64,
        "build_args_digest": "sha256:" + "b" * 64,
        "module_sha256": "c" * 64,
        "rust_version": "1.98.1",
        "features": ["streaming", "prune_noise_regions"],
        "tests": _tests(),
        "pass": 5,
        "fail": 0,
        "skip": 0,
        "workflow_run_url": "https://github.com/cnkang/nginx-markdown-for-agents/actions/runs/1",
        "commit_sha": "d" * 40,
        "date": "2026-10-29",
        "verified_by": "ci:release-packages",
    }
    record.update(overrides)
    return record


def _ubuntu_record(**overrides) -> dict:
    record = _record(
        target_identity="ubuntu-default-repo:1.24.0-2ubuntu7.19:ubuntu-24.04:amd64",
        source_kind="ubuntu-default-repo",
        nginx_version="1.24.0",
        nginx_package_revision="1.24.0-2ubuntu7.19",
        distro="ubuntu-24.04",
        distro_version="24.04",
        abi_provide="nginx-abi-1.24.0-1",
        source_reference="https://launchpad.net/ubuntu/+source/nginx/1.24.0-2ubuntu7.19",
        tests=_tests() + [{"name": "negative_abi_rejected", "status": "PASS"}],
        **{"pass": 6},
    )
    record.update(overrides)
    return record


def test_complete_nginx_org_record_is_valid() -> None:
    assert validate_record(_record()) == []
    assert is_promotable(_record())


def test_complete_ubuntu_record_requires_negative_control() -> None:
    assert validate_record(_ubuntu_record()) == []
    without_negative = _ubuntu_record(
        tests=_tests(), **{"pass": 5}
    )
    assert any(
        "negative_abi_rejected" in error
        for error in validate_record(without_negative)
    )


def test_every_required_field_is_enforced() -> None:
    for field in RECORD_REQUIRED_FIELDS:
        record = _record()
        record.pop(field)
        errors = validate_record(record)
        assert any(
            field in error for error in errors
        ), f"missing {field} was accepted"


def test_count_mismatch_is_rejected() -> None:
    errors = validate_record(_record(skip=1))
    assert any("skip" in error for error in errors)


def test_failed_or_skipped_test_is_not_promotable() -> None:
    tests = _tests()
    tests[1]["status"] = "SKIP"
    record = _record(tests=tests, **{"pass": 4, "skip": 1})
    assert validate_record(record) == []
    assert not is_promotable(record)


def test_duplicate_test_name_is_rejected() -> None:
    tests = _tests() + [{"name": "load", "status": "PASS"}]
    errors = validate_record(_record(tests=tests, **{"pass": 6}))
    assert any("duplicate test name" in error for error in errors)


def test_ubuntu_revision_shape_is_enforced() -> None:
    errors = validate_record(
        _ubuntu_record(nginx_package_revision="1.24.0")
    )
    assert any("nginx_package_revision" in error for error in errors)


def test_ubuntu_abi_provide_is_required() -> None:
    errors = validate_record(_ubuntu_record(abi_provide=""))
    assert any("abi_provide" in error for error in errors)


def test_ubuntu_distro_must_match_version() -> None:
    errors = validate_record(_ubuntu_record(distro="ubuntu-26.04"))
    assert any("distro must equal" in error for error in errors)


def test_non_https_source_reference_is_rejected() -> None:
    errors = validate_record(
        _record(source_reference="http://nginx.org/download/nginx-1.30.6.tar.gz")
    )
    assert any("https" in error for error in errors)


def test_boolean_count_is_rejected() -> None:
    errors = validate_record(_record(**{"pass": True}))
    assert any("pass must be an integer" in error for error in errors)


def test_record_covers_matching_row_only() -> None:
    row = {
        "nginx_version": "1.30.6",
        "os": "linux",
        "libc": "glibc",
        "arch": "amd64",
    }
    assert record_covers_entry(_record(), row)
    assert not record_covers_entry(_record(), {**row, "arch": "arm64"})
    assert not record_covers_entry(_record(), {**row, "libc": "musl"})
    assert not record_covers_entry(_record(), {**row, "os": "debian12"})


def test_record_covers_target_triple_row() -> None:
    row = {
        "nginx_version": "1.30.6",
        "os": "linux",
        "libc": "glibc",
        "target": "aarch64-unknown-linux-gnu",
    }
    assert not record_covers_entry(_record(), row)
    assert record_covers_entry(_record(arch="arm64"), row)


def test_ubuntu_record_covers_only_its_suite() -> None:
    row = {
        "nginx_version": "1.24.0",
        "os": "ubuntu-24.04",
        "libc": "glibc",
        "arch": "amd64",
    }
    assert record_covers_entry(_ubuntu_record(), row)
    assert not record_covers_entry(
        _ubuntu_record(), {**row, "os": "ubuntu-26.04"}
    )
