#!/usr/bin/env python3
"""Compatibility evidence contract for the release matrix.

A matrix row states a support promise.  An evidence record states what was
actually built, loaded, and served.  This module keeps the two separate so a
promise can never be read as proof.

Records live under ``docs/evidence/<release>/compat/`` as one JSON object per
verified target.  The contract is intentionally narrow: it accepts only the
fields the release gates can re-check, and it fails closed on a missing,
mistyped, or internally inconsistent value.

Exit codes of the command-line wrapper in
``validate_compatibility_evidence.py`` are documented there.
"""

from __future__ import annotations

import datetime as _datetime
import json
import pathlib
import re
from typing import Any, Iterable

SCHEMA_VERSION = 1

# Test classes every promotable record must carry.  A record may carry more,
# but a release gate that accepts a subset would let a build-only run stand in
# for a load-and-serve run.
REQUIRED_TEST_CLASSES: tuple[str, ...] = (
    "build",
    "load",
    "request_markdown",
    "request_passthrough",
    "lifecycle",
)

# Distro-native rows must also prove that an incompatible module is rejected,
# because their ABI binding is the whole claim.
DISTRO_REQUIRED_TEST_CLASSES: tuple[str, ...] = ("negative_abi_rejected",)

STATUS_VALUES = frozenset({"PASS", "FAIL", "SKIP"})

SOURCE_KINDS = frozenset({"nginx.org", "ubuntu-default-repo"})

_REQUIRED_FIELDS: tuple[tuple[str, type], ...] = (
    ("schema_version", int),
    ("target_identity", str),
    ("source_kind", str),
    ("nginx_version", str),
    ("nginx_package_revision", str),
    ("distro", str),
    ("distro_version", str),
    ("arch", str),
    ("libc", str),
    ("abi_provide", str),
    ("source_reference", str),
    ("source_digest", str),
    ("build_args_digest", str),
    ("module_sha256", str),
    ("rust_version", str),
    ("features", list),
    ("tests", list),
    ("pass", int),
    ("fail", int),
    ("skip", int),
    ("workflow_run_url", str),
    ("commit_sha", str),
    ("date", str),
    ("verified_by", str),
)

# Public field-name list for tests and documentation tooling.
RECORD_REQUIRED_FIELDS: tuple[str, ...] = tuple(name for name, _ in _REQUIRED_FIELDS)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_DISTRO_VERSION_RE = re.compile(r"^\d{2}\.\d{2}$")
_UBUNTU_REVISION_RE = re.compile(r"^\d+\.\d+\.\d+-\d+ubuntu[\d.]+$")
_ABI_PROVIDE_RE = re.compile(r"^nginx-abi-\d+\.\d+\.\d+-\d+$")
_NGINX_RPM_PROVIDE_RE = re.compile(r"^nginx-r\d+\.\d+\.\d+$")
_HTTPS_RE = re.compile(r"^https://[^\s]+$")


class EvidenceError(ValueError):
    """Raised when a record cannot be read as evidence at all."""


def load_record(path: pathlib.Path) -> dict[str, Any]:
    """Read one evidence record from disk."""
    try:
        with path.open(encoding="utf-8") as handle:
            record = json.load(handle)
    except OSError as exc:
        raise EvidenceError(f"cannot read evidence record {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"invalid JSON in evidence record {path}: {exc}") from exc
    if not isinstance(record, dict):
        raise EvidenceError(f"evidence record {path} must be a JSON object")
    return record


def _validate_identity(record: dict[str, Any], errors: list[str]) -> None:
    """Validate the target identity, source kind, and version fields."""
    if record.get("source_kind") not in SOURCE_KINDS:
        errors.append(f"source_kind must be one of {sorted(SOURCE_KINDS)}")
    if record.get("arch") not in {"amd64", "arm64"}:
        errors.append("arch must be amd64 or arm64")
    if record.get("libc") not in {"glibc", "musl"}:
        errors.append("libc must be glibc or musl")
    version = record.get("nginx_version")
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        errors.append("nginx_version must be MAJOR.MINOR.PATCH")
    identity = record.get("target_identity")
    if isinstance(identity, str) and version and version not in identity:
        errors.append("target_identity must contain nginx_version")
    distro = record.get("distro")
    if not isinstance(distro, str) or not re.match(r"^[a-z0-9][a-z0-9.-]*$", distro):
        errors.append("distro must be a lowercase distro slug")


def _validate_nginx_org_source(record: dict[str, Any], errors: list[str]) -> None:
    """Validate source binding for an official nginx.org build."""
    provide = record.get("abi_provide", "")
    if provide and not _NGINX_RPM_PROVIDE_RE.match(provide):
        errors.append("nginx.org abi_provide must look like nginx-rX.Y.Z")
    reference = record.get("source_reference", "")
    if not _HTTPS_RE.match(reference):
        errors.append("source_reference must be an https URL")
    if "nginx.org" not in reference:
        errors.append("nginx.org records must reference nginx.org")


def _validate_ubuntu_source(record: dict[str, Any], errors: list[str]) -> None:
    """Validate the distro revision, suite, and ABI binding."""
    revision = record.get("nginx_package_revision", "")
    if not _UBUNTU_REVISION_RE.match(revision):
        errors.append(
            "ubuntu nginx_package_revision must look like "
            "X.Y.Z-NubuntuREVISION"
        )
    if not _ABI_PROVIDE_RE.match(record.get("abi_provide", "")):
        errors.append("ubuntu abi_provide must look like nginx-abi-X.Y.Z-N")
    distro_version = record.get("distro_version", "")
    if not _DISTRO_VERSION_RE.match(distro_version):
        errors.append("distro_version must look like 24.04")
    if record.get("distro") != f"ubuntu-{distro_version}":
        errors.append("distro must equal ubuntu-<distro_version>")
    if record.get("libc") != "glibc":
        errors.append("ubuntu native records are glibc only")
    reference = record.get("source_reference", "")
    if not _HTTPS_RE.match(reference):
        errors.append("source_reference must be an https URL")


def _validate_hashes(record: dict[str, Any], errors: list[str]) -> None:
    """Validate module, source, and build-argument digests."""
    module_sha = record.get("module_sha256", "")
    if not _SHA256_RE.match(module_sha):
        errors.append("module_sha256 must be 64 lowercase hex characters")
    digest = record.get("source_digest", "")
    if not digest.startswith("sha256:") or not _SHA256_RE.match(digest[7:]):
        errors.append("source_digest must be sha256:<64 hex>")
    build_digest = record.get("build_args_digest", "")
    if not build_digest.startswith("sha256:") or not _SHA256_RE.match(
        build_digest[7:]
    ):
        errors.append("build_args_digest must be sha256:<64 hex>")


def _validate_provenance(record: dict[str, Any], errors: list[str]) -> None:
    """Validate commit, date, and run provenance."""
    if not _COMMIT_RE.match(record.get("commit_sha", "")):
        errors.append("commit_sha must be 40 lowercase hex characters")
    # A local pre-verification run has no workflow run to point at. It must
    # say so in verified_by, so the release stage can reject it.
    url = record.get("workflow_run_url", "")
    verifier = record.get("verified_by", "")
    if url:
        if not _HTTPS_RE.match(url):
            errors.append("workflow_run_url must be an https URL")
    elif not verifier.startswith("local"):
        errors.append(
            "workflow_run_url is required unless verified_by starts with 'local'"
        )
    if not verifier:
        errors.append("verified_by must name the verifier")
    date = record.get("date", "")
    try:
        _datetime.date.fromisoformat(date)
    except ValueError:
        errors.append("date must be an ISO 8601 calendar date")


def _validate_features(record: dict[str, Any], errors: list[str]) -> None:
    """Validate the recorded Cargo feature set."""
    features = record.get("features")
    if not isinstance(features, list) or not features:
        errors.append("features must be a non-empty list")
        return
    if not all(isinstance(item, str) and item for item in features):
        errors.append("features must contain non-empty strings")


def _validate_tests(record: dict[str, Any], errors: list[str]) -> None:
    """Validate the test ledger and its declared totals."""
    tests = record.get("tests")
    if not isinstance(tests, list) or not tests:
        errors.append("tests must be a non-empty list")
        return
    seen: set[str] = set()
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for index, test in enumerate(tests):
        if not isinstance(test, dict):
            errors.append(f"tests[{index}] must be an object")
            continue
        name = test.get("name")
        status = test.get("status")
        if not isinstance(name, str) or not name:
            errors.append(f"tests[{index}] name must be a non-empty string")
            continue
        if status not in STATUS_VALUES:
            errors.append(f"tests[{index}] status must be PASS, FAIL, or SKIP")
            continue
        if name in seen:
            errors.append(f"duplicate test name {name}")
        seen.add(name)
        counts[status] += 1

    required = set(REQUIRED_TEST_CLASSES)
    if record.get("source_kind") == "ubuntu-default-repo":
        required |= set(DISTRO_REQUIRED_TEST_CLASSES)
    missing = sorted(required - seen)
    if missing:
        errors.append(f"missing required test classes: {', '.join(missing)}")

    for key, expected in (
        ("pass", counts["PASS"]),
        ("fail", counts["FAIL"]),
        ("skip", counts["SKIP"]),
    ):
        if record.get(key) != expected:
            errors.append(f"{key} is {record.get(key)!r} but the tests list has {expected}")


def validate_record(record: dict[str, Any]) -> list[str]:
    """Return every contract violation in one evidence record."""
    errors: list[str] = []
    for field, expected_type in _REQUIRED_FIELDS:
        if field not in record:
            errors.append(f"missing required field {field}")
            continue
        value = record[field]
        # bool is a subclass of int; a boolean count is a malformed record.
        if expected_type is int and isinstance(value, bool):
            errors.append(f"{field} must be an integer")
            continue
        if not isinstance(value, expected_type):
            errors.append(f"{field} must be {expected_type.__name__}")
    if errors:
        return errors

    if record["schema_version"] != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")

    _validate_identity(record, errors)
    if record.get("source_kind") == "ubuntu-default-repo":
        _validate_ubuntu_source(record, errors)
    else:
        _validate_nginx_org_source(record, errors)
    _validate_hashes(record, errors)
    _validate_provenance(record, errors)
    _validate_features(record, errors)
    _validate_tests(record, errors)
    return errors


def is_promotable(record: dict[str, Any]) -> bool:
    """Return whether a record may support a release-blocking promise."""
    return (
        not validate_record(record)
        and record["fail"] == 0
        and record["skip"] == 0
        and record["pass"] > 0
    )


def target_key(record: dict[str, Any]) -> tuple[str, str, str, str]:
    """Return the matrix identity a record can bind: version, libc, arch, kind."""
    return (
        record["nginx_version"],
        record["libc"],
        record["arch"],
        record["source_kind"],
    )


def _normalized_arch(entry: dict[str, Any]) -> str | None:
    """Return a matrix row's architecture in package vocabulary."""
    raw = entry.get("arch") or entry.get("target")
    if not isinstance(raw, str) or not raw:
        return None
    if raw.startswith("x86_64"):
        return "amd64"
    if raw.startswith("aarch64"):
        return "arm64"
    return raw


def record_covers_entry(record: dict[str, Any], entry: dict[str, Any]) -> bool:
    """Return whether one record verifies one matrix entry.

    The match is deliberately strict on version, libc, architecture, and host
    OS.  A generic Linux glibc row is verified by an nginx.org record whose
    host is ``linux``; a distro row is verified only by a record from that
    exact suite.
    """
    if entry.get("nginx_version") != record["nginx_version"]:
        return False
    if entry.get("libc") != record["libc"]:
        return False
    if _normalized_arch(entry) != record["arch"]:
        return False
    return entry.get("os") == record["distro"]


def iter_records(directory: pathlib.Path) -> Iterable[tuple[pathlib.Path, dict[str, Any]]]:
    """Yield every JSON evidence record in a directory, sorted by name."""
    for path in sorted(directory.glob("*.json")):
        yield path, load_record(path)
