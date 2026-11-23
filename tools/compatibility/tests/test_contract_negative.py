"""Negative fixtures for the cross-release compatibility gate.

Each case mutates one contract input inside an isolated worktree and asserts
that the gate fails.  The coordinated-drift case is the important one: it edits
the implementation and the current inventory together, which the existing
public-surface drift gate accepts, and asserts the historical anchor still
fails the candidate.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .conftest import REPO_ROOT, _run, run_compat_check, sanitized_environment

DIRECTIVES = (
    "components/nginx-module/src/ngx_http_markdown_config_directives_impl.h"
)
DIRECTIVE_NAMES = "components/nginx-module/src/ngx_http_markdown_directive_names.h"
METRICS_REGISTRY = "schemas/metrics-v1.registry.json"
DIAGNOSTICS_SCHEMA = "schemas/diagnostics.schema.json"
REASON_REGISTRY = "components/rust-converter/reason_registry.toml"
STATUS_E2E = "tools/e2e/verify_status_codes_e2e.sh"
CONDITIONAL_E2E = "tools/e2e/verify_conditional_requests_e2e.sh"
INVENTORY = "docs/harness/public-surface-inventory.json"
MANIFEST_092 = "tools/compatibility/contracts/nginx-markdown-0.9.2.json"
MANIFEST_094 = "tools/compatibility/contracts/nginx-markdown-0.9.4.json"
INDEX = "tools/compatibility/contracts/INDEX.json"


def _read(worktree: Path, relative: str) -> str:
    """Read one file from the isolated worktree."""
    return (worktree / relative).read_text(encoding="utf-8")


def _write(worktree: Path, relative: str, text: str) -> None:
    """Write one file in the isolated worktree."""
    path = worktree / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _replace(worktree: Path, relative: str, old: str, new: str) -> None:
    """Replace exactly one occurrence of ``old``."""
    text = _read(worktree, relative)
    assert old in text, "fixture anchor not found in {}".format(relative)
    _write(worktree, relative, text.replace(old, new, 1))


def _assert_fails(worktree: Path, *finding_ids: str) -> str:
    """Run the gate in the worktree and assert it fails with the given ids."""
    completed = run_compat_check(worktree)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    output = completed.stdout + completed.stderr
    for finding_id in finding_ids:
        assert finding_id in output, "expected {} in:\n{}".format(finding_id, output)
    return output


DEFAULT_ANCHOR = (
    "     * Also supports per-request toggle via nginx variables/complex values.\n"
    "     * Default: off\n"
)


def _flip_filter_default(worktree: Path) -> None:
    """Change the documented default of markdown_filter from off to on."""
    _replace(
        worktree,
        DIRECTIVES,
        DEFAULT_ANCHOR,
        DEFAULT_ANCHOR.replace("Default: off", "Default: on"),
    )


def _fingerprint_for(output: str, finding_id: str) -> str:
    """Read the evidence fingerprint the report printed for one finding."""
    marker = "{} (fingerprint ".format(finding_id)
    start = output.find(marker)
    assert start >= 0, "no fingerprint for {} in:\n{}".format(finding_id, output)
    start += len(marker)
    end = output.find(")", start)
    return output[start:end]


def test_clean_worktree_passes(isolated_worktree):
    """Positive control: the unmodified isolated worktree passes."""
    completed = run_compat_check(isolated_worktree)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "PASS" in completed.stdout


def test_negative_directive_default_changed(isolated_worktree):
    """1. Changing an active directive default must fail."""
    _flip_filter_default(isolated_worktree)
    _assert_fails(isolated_worktree, "directives:default-changed:markdown_filter")


def test_negative_stable_directive_renamed(isolated_worktree):
    """2. Renaming a stable directive must fail as a removal."""
    _replace(
        isolated_worktree, DIRECTIVE_NAMES, '"markdown_flavor"', '"markdown_flavour"'
    )
    _assert_fails(
        isolated_worktree,
        "directives:removed:markdown_flavor",
        "directives:added:markdown_flavour",
    )


def test_negative_metric_type_changed(isolated_worktree):
    """3a. Changing a family type must fail."""
    text = _read(isolated_worktree, METRICS_REGISTRY)
    document = json.loads(text)
    for family in document["families"]:
        if family["name"] == "nginx_markdown_conversion_attempts_total":
            family["type"] = "gauge"
    _write(isolated_worktree, METRICS_REGISTRY, json.dumps(document, indent=2) + "\n")
    _assert_fails(
        isolated_worktree,
        "metrics:type-changed:nginx_markdown_conversion_attempts_total",
    )


def test_negative_metric_label_removed(isolated_worktree):
    """3b. Removing a stable label must fail."""
    text = _read(isolated_worktree, METRICS_REGISTRY)
    document = json.loads(text)
    for family in document["families"]:
        if family["name"] == "nginx_markdown_requests_total":
            family["labels"] = [
                label for label in family["labels"] if label["name"] != "stage"
            ]
    _write(isolated_worktree, METRICS_REGISTRY, json.dumps(document, indent=2) + "\n")
    _assert_fails(
        isolated_worktree, "metrics:label-removed:nginx_markdown_requests_total:stage"
    )


def test_negative_diagnostics_required_field_removed(isolated_worktree):
    """4. Dropping a required diagnostics field must fail."""
    document = json.loads(_read(isolated_worktree, DIAGNOSTICS_SCHEMA))
    document["required"] = [name for name in document["required"] if name != "worker"]
    _write(
        isolated_worktree,
        DIAGNOSTICS_SCHEMA,
        json.dumps(document, indent=2) + "\n",
    )
    _assert_fails(isolated_worktree, "diagnostics:required-removed")


def test_negative_diagnostics_field_type_changed(isolated_worktree):
    """4b. Changing a diagnostics field type must fail."""
    document = json.loads(_read(isolated_worktree, DIAGNOSTICS_SCHEMA))
    document["properties"]["product_version"]["type"] = "integer"
    _write(
        isolated_worktree,
        DIAGNOSTICS_SCHEMA,
        json.dumps(document, indent=2) + "\n",
    )
    _assert_fails(isolated_worktree, "diagnostics:type-changed:product_version")


def test_negative_reason_discriminant_changed(isolated_worktree):
    """5. Renumbering a stable reason discriminant must fail."""
    text = _read(isolated_worktree, REASON_REGISTRY)
    assert 'discriminant = 1\nkey = "skipped_accept"' in text
    assert 'discriminant = 2\nkey = "skipped_no_accept"' in text
    text = text.replace(
        'discriminant = 1\nkey = "skipped_accept"',
        'discriminant = 2\nkey = "skipped_accept"',
        1,
    )
    text = text.replace(
        'discriminant = 2\nkey = "skipped_no_accept"',
        'discriminant = 1\nkey = "skipped_no_accept"',
        1,
    )
    _write(isolated_worktree, REASON_REGISTRY, text)
    _assert_fails(
        isolated_worktree,
        "reasons:discriminant-changed:skipped_accept",
        "reasons:discriminant-changed:skipped_no_accept",
    )


def test_negative_reason_key_removed(isolated_worktree):
    """5b. Removing a stable reason key must fail."""
    text = _read(isolated_worktree, REASON_REGISTRY)
    block = '[[reasons]]\ndiscriminant = 23\nkey = "bypass_no_transform"'
    assert block in text
    text = text.replace(block, '[[reasons]]\ndiscriminant = 23\nkey = "bypass_renamed"', 1)
    _write(isolated_worktree, REASON_REGISTRY, text)
    _assert_fails(
        isolated_worktree,
        "reasons:removed:bypass_no_transform",
        "reasons:added:bypass_renamed",
    )


def test_negative_http_contract_test_changed(isolated_worktree):
    """6. Editing an HTTP contract test must fail."""
    text = _read(isolated_worktree, CONDITIONAL_E2E)
    _write(
        isolated_worktree,
        CONDITIONAL_E2E,
        text + "\n# compatibility fixture edit\n",
    )
    _assert_fails(
        isolated_worktree, "http:test-changed:{}".format(CONDITIONAL_E2E)
    )


def test_negative_missing_http_fixture(isolated_worktree):
    """A missing behavioural fixture fails closed."""
    (isolated_worktree / STATUS_E2E).unlink()
    completed = run_compat_check(isolated_worktree)
    assert completed.returncode == 1
    assert "is missing" in completed.stdout + completed.stderr


def test_negative_coordinated_source_and_inventory_change(isolated_worktree):
    """7. Editing the implementation and the inventory together must still fail.

    The public-surface drift gate compares the command table against the
    inventory, so a change applied to both looks consistent.  The historical
    anchor is what makes the coordinated edit visible.
    """
    _flip_filter_default(isolated_worktree)
    inventory = json.loads(_read(isolated_worktree, INVENTORY))
    for entry in inventory["directives"]:
        if entry["name"] == "markdown_filter":
            entry["default"] = "on"
    _write(isolated_worktree, INVENTORY, json.dumps(inventory, indent=2) + "\n")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(isolated_worktree)
    env.pop("VIRTUAL_ENV", None)
    drift = _run(
        [sys.executable, "tools/harness/detect_public_surface_drift.py"],
        isolated_worktree,
        env,
    )
    assert drift.returncode == 0, (
        "the coordinated edit must satisfy the current-surface drift gate:\n"
        + drift.stdout
        + drift.stderr
    )

    _assert_fails(isolated_worktree, "directives:default-changed:markdown_filter")


def test_negative_manifest_tampered(isolated_worktree):
    """8a. Editing a frozen manifest must fail the digest check."""
    document = json.loads(_read(isolated_worktree, MANIFEST_094))
    document["product_version"] = "9.9.9"
    _write(
        isolated_worktree,
        MANIFEST_094,
        json.dumps(document, indent=2, sort_keys=True) + "\n",
    )
    completed = run_compat_check(isolated_worktree)
    assert completed.returncode == 1
    assert "was modified" in completed.stdout + completed.stderr


def test_negative_anchor_commit_tampered(isolated_worktree):
    """8b. Replacing the recorded tag commit must fail."""
    document = json.loads(_read(isolated_worktree, INDEX))
    for anchor in document["anchors"]:
        if anchor["tag"] == "v0.9.4":
            anchor["commit"] = "0" * 40
    _write(isolated_worktree, INDEX, json.dumps(document, indent=2) + "\n")
    completed = run_compat_check(isolated_worktree)
    assert completed.returncode == 1
    assert "source commit" in completed.stdout + completed.stderr


def test_negative_missing_manifest_file(isolated_worktree):
    """A missing frozen snapshot fails closed."""
    (isolated_worktree / MANIFEST_092).unlink()
    completed = run_compat_check(isolated_worktree)
    assert completed.returncode == 1
    assert "missing file" in completed.stdout + completed.stderr


def test_negative_undocumented_historical_delta(isolated_worktree):
    """An undocumented change between two anchors fails the historical audit."""
    document = json.loads(_read(isolated_worktree, INDEX))
    document["comparisons"][0]["documented_delta"] = []
    _write(isolated_worktree, INDEX, json.dumps(document, indent=2) + "\n")
    completed = run_compat_check(isolated_worktree)
    assert completed.returncode == 1
    assert "undocumented change" in completed.stdout + completed.stderr


def test_negative_unused_approval_is_reported(isolated_worktree):
    """A stale approval is reported rather than silently ignored."""
    # The gate only reads inputs inside the repository it audits.
    approvals = isolated_worktree / "approvals.json"
    approvals.write_text(
        json.dumps(
            {
                "schema": 1,
                "exceptions": [
                    {
                        "finding_id": "directives:default-changed:markdown_filter",
                        "evidence_fingerprint": "sha256:" + "0" * 64,
                        "reason": "not applicable",
                        "approved_by": "maintainer",
                        "approved_at": "2026-11-23",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    completed = run_compat_check(
        isolated_worktree, "--approvals", str(approvals)
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "matches no REVIEW_REQUIRED finding" in completed.stdout


def test_negative_approval_cannot_waive_breaking(isolated_worktree):
    """A BREAKING finding stays blocking even with an approval file."""
    _flip_filter_default(isolated_worktree)
    reported = _assert_fails(
        isolated_worktree, "directives:default-changed:markdown_filter"
    )
    fingerprint = _fingerprint_for(
        reported, "directives:default-changed:markdown_filter"
    )
    approvals = isolated_worktree / "approvals.json"
    approvals.write_text(
        json.dumps(
            {
                "schema": 1,
                "exceptions": [
                    {
                        "finding_id": "directives:default-changed:markdown_filter",
                        "evidence_fingerprint": fingerprint,
                        "reason": "attempted waiver",
                        "approved_by": "maintainer",
                        "approved_at": "2026-11-23",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    completed = run_compat_check(isolated_worktree, "--approvals", str(approvals))
    assert completed.returncode == 1


def test_negative_shallow_history_fails_closed(tmp_path):
    """A checkout without release tags fails instead of passing silently."""
    shallow = tmp_path / "shallow"
    clone = subprocess.run(
        ["git", "clone", "--no-tags", "--shared", str(REPO_ROOT), str(shallow)],
        capture_output=True,
        text=True,
        check=False,
        env=sanitized_environment(),
    )
    assert clone.returncode == 0, clone.stderr
    overlay = _run(["git", "checkout", "--detach", "HEAD"], shallow)
    assert overlay.returncode == 0, overlay.stderr
    for relative in (MANIFEST_092, MANIFEST_094, INDEX):
        target = shallow / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((REPO_ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")
    import shutil

    shutil.copytree(
        REPO_ROOT / "tools" / "compatibility",
        shallow / "tools" / "compatibility",
        dirs_exist_ok=True,
    )
    completed = run_compat_check(shallow)
    assert completed.returncode == 1
    assert "git" in (completed.stdout + completed.stderr).lower()
