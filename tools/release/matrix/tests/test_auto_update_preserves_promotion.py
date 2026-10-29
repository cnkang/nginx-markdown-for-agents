"""Regression tests: auto-discovery must preserve maintainer decisions.

A promoted row is a reviewed decision backed by evidence.  The weekly updater
runs unattended, so it must not downgrade that row when it discovers a new
patch, and it must not delete or rewrite a fixed distro row.
"""

from __future__ import annotations

from tools.release.matrix import update_matrix as um


def _promoted_row(version: str, arch: str = "x86_64") -> dict:
    return {
        "nginx": version,
        "os_type": "glibc",
        "arch": arch,
        "support_tier": "full",
        "os": "linux",
        "artifact_type": "dynamic-module",
        "nginx_version": version,
        "nginx_channel": "stable",
        "verification_state": "verified",
        "support_stage": "primary",
        "release_blocking": True,
    }


def _manual_ubuntu_row() -> dict:
    return {
        "nginx_version": "1.24.0",
        "os": "ubuntu-24.04",
        "libc": "glibc",
        "arch": "x86_64",
        "artifact_type": "deb-package",
        "support_tier": "supported",
        "support_stage": "primary",
        "verification_state": "verified",
        "release_blocking": True,
        "managed_by": "manual",
        "source": "ubuntu-default-repo",
        "provenance": {
            "kind": "ubuntu-default-repo",
            "reference": "https://launchpad.net/ubuntu/+source/nginx/1.24.0-2ubuntu7.19",
        },
        "date": "2026-10-29",
    }


def _run_updater_merge(existing_entries: list[dict], discovered: list[str]) -> dict:
    """Run the updater's merge and rewrite stages over existing entries."""
    data = {"schema_version": 1, "entries": existing_entries}
    auto_entries, manual_entries = um._split_matrix_entries(
        existing_entries, um.MATRIX_PATH
    )
    desired_auto = um.compute_matrix(discovered, ["glibc"], ["x86_64"])
    merged = um.merge_matrix(desired_auto, manual_entries)
    diff = um.diff_matrix(auto_entries, desired_auto)
    effective = um._mark_new_versions_pending(merged, diff)
    um._replace_canonical_dynamic_entries(data, effective)
    return data


def test_promoted_row_survives_a_new_patch_discovery() -> None:
    existing = [_promoted_row("1.30.6")]
    data = _run_updater_merge(existing, ["1.30.6", "1.30.7"])
    rows = {
        entry["nginx_version"]: entry
        for entry in data["entries"]
        if entry.get("artifact_type") == "dynamic-module"
    }
    assert rows["1.30.6"]["support_tier"] == "supported"
    assert rows["1.30.6"]["release_blocking"] is True
    assert rows["1.30.6"]["verification_state"] == "verified"
    assert rows["1.30.7"]["support_tier"] == "best-effort"
    assert rows["1.30.7"]["release_blocking"] is False


def test_manual_distro_row_is_never_dropped_or_rewritten() -> None:
    existing = [_promoted_row("1.30.6"), _manual_ubuntu_row()]
    data = _run_updater_merge(existing, ["1.30.6", "1.30.7"])
    manual = [
        entry for entry in data["entries"] if entry.get("managed_by") == "manual"
    ]
    assert len(manual) == 1
    assert manual[0] == _manual_ubuntu_row()


def test_generated_rows_are_not_promoted_by_discovery() -> None:
    data = _run_updater_merge([_promoted_row("1.30.6")], ["1.30.6", "1.30.7"])
    for entry in data["entries"]:
        if entry["nginx_version"] == "1.30.7":
            assert entry["support_stage"] == "best-effort"
            assert entry["verification_state"] == "pending"
