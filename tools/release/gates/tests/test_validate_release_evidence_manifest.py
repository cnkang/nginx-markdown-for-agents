"""Regression tests for the release evidence manifest gate semantics.

Covers the release-blocking rules that the JSON schema cannot express as
structure: every blocking=true entry must have status=pass, and no blocking
entry may be pending (the real-mode gate must reject such manifests even
when the schema structure itself is valid).
"""

from __future__ import annotations

import copy

from tools.release.gates import validate_release_evidence_manifest as gate

# A minimal structurally-valid manifest exercising every status value.
_BASE_MANIFEST = {
    "schema_version": 1,
    "candidate_sha": "a" * 40,
    "evidence_schema_digest": "sha256:" + "0" * 64,
    "observation_schema_digest": "sha256:" + "0" * 64,
    "generated_at": "2026-08-15T00:00:00Z",
    "run_status": "pass",
    "entries": [
        {
            "category": "coverage",
            "blocking": True,
            "status": "pass",
            "artifact_ref": "workflow:coverage",
        },
        {
            "category": "documentation",
            "blocking": False,
            "status": "skip",
            "artifact_ref": "workflow:docs",
            "justification": "Policy allows skip for documentation.",
            "policy_reference": "release/provenance-policy.json",
        },
    ],
}


def _manifest_with(entries_override=None, **top_override):
    manifest = copy.deepcopy(_BASE_MANIFEST)
    manifest.update(top_override)
    if entries_override is not None:
        manifest["entries"] = entries_override
    return manifest


def test_blocking_entry_with_fail_status_is_rejected() -> None:
    """A blocking entry whose status is not pass must fail the gate."""
    entries = copy.deepcopy(_BASE_MANIFEST["entries"])
    entries[0]["status"] = "fail"
    reasons: list[str] = []
    gate._check_blocking_semantics(_manifest_with(entries), reasons)
    assert any("blocking" in r and "must pass" in r for r in reasons)


def test_blocking_entry_with_pending_status_is_rejected() -> None:
    """A pending blocking entry must fail the gate."""
    entries = copy.deepcopy(_BASE_MANIFEST["entries"])
    entries[0]["status"] = "pending"
    reasons: list[str] = []
    gate._check_blocking_semantics(_manifest_with(entries), reasons)
    assert any("blocking" in r and "must pass" in r for r in reasons)


def test_all_blocking_entries_pass_is_accepted() -> None:
    """A manifest whose blocking entries all pass produces no reasons."""
    reasons: list[str] = []
    gate._check_blocking_semantics(_manifest_with(), reasons)
    assert reasons == []


def test_non_blocking_entries_do_not_trigger_blocking_rule() -> None:
    """Non-blocking entries may carry any status without failing the rule."""
    entries = [
        {
            "domain": "documentation",
            "blocking": False,
            "status": "fail",
            "artifact_ref": "workflow:docs",
        }
    ]
    reasons: list[str] = []
    gate._check_blocking_semantics(_manifest_with(entries), reasons)
    assert reasons == []


def test_missing_entries_array_is_ignored() -> None:
    """The blocking rule silently skips structurally-invalid manifests;
    the schema layer reports structural problems instead."""
    reasons: list[str] = []
    manifest = _manifest_with()
    # Construct a manifest with the entries key explicitly absent.
    # Passing entries_override=None to _manifest_with would preserve the
    # base entries, which does not exercise the missing-key path.
    del manifest["entries"]
    gate._check_blocking_semantics(manifest, reasons)
    assert reasons == []


def test_blocking_rule_reason_names_category() -> None:
    """The rejection reason must name the offending entry for auditing."""
    entries = copy.deepcopy(_BASE_MANIFEST["entries"])
    entries[0]["status"] = "pending"
    reasons: list[str] = []
    gate._check_blocking_semantics(_manifest_with(entries), reasons)
    assert any("category='coverage'" in r for r in reasons)


def test_verify_head_flags_drifted_evidence(monkeypatch) -> None:
    """Committed evidence whose candidate_sha != repository HEAD must be
    flagged as stale (evidence SHA == release candidate SHA)."""
    reasons: list[str] = []
    monkeypatch.setattr(gate, "_git_head_sha", lambda: ("b" * 40, None))
    gate._verify_head_matches("a" * 40, reasons)
    assert any("stale-digest" in r and "repository HEAD" in r for r in reasons)


def test_verify_head_accepts_matching_sha(monkeypatch) -> None:
    """Evidence bound to the current HEAD produces no reason."""
    reasons: list[str] = []
    monkeypatch.setattr(gate, "_git_head_sha", lambda: ("a" * 40, None))
    gate._verify_head_matches("a" * 40, reasons)
    assert reasons == []


def test_verify_head_reports_unresolvable_head(monkeypatch) -> None:
    """An unresolvable repository HEAD fails closed with a clear reason."""
    reasons: list[str] = []
    monkeypatch.setattr(gate, "_git_head_sha", lambda: (None, "test cause"))
    gate._verify_head_matches("a" * 40, reasons)
    assert any("verify-head" in r for r in reasons)
    assert any("test cause" in r for r in reasons)
