"""Contract extraction and comparison unit tests.

These tests read the real repository sources.  They are the regression net for
the extractor: a parser change that silently stops reading a field would show
up here as a mismatch against the checked-in inventory or as a digest change
against a frozen release tag.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools.compatibility.compare import (
    ADDITIVE,
    BREAKING,
    OK,
    REVIEW_REQUIRED,
    UNVERIFIED,
    Comparison,
    apply_approvals,
    compare_contracts,
)
from tools.compatibility.extract import extract_surfaces
from tools.compatibility.model import SURFACES, contract_digest, surface_digests
from tools.compatibility.sources import (
    SourceReader,
    repo_root_for,
    resolve_tag,
    resolve_worktree,
)

REPO = Path(__file__).resolve().parents[3]
INDEX = REPO / "tools" / "compatibility" / "contracts" / "INDEX.json"


@pytest.fixture(scope="module")
def worktree_surfaces():
    """Extract the working tree contract once for the module."""
    reader = SourceReader(str(REPO), resolve_worktree(str(REPO)))
    return extract_surfaces(reader)


@pytest.fixture(scope="module")
def index_document():
    """Load the anchor index."""
    with open(INDEX, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _anchor_manifest(index: dict, tag: str) -> dict:
    """Load one anchor manifest from the index."""
    anchor = [item for item in index["anchors"] if item["tag"] == tag][0]
    with open(INDEX.parent / anchor["manifest"], "r", encoding="utf-8") as handle:
        return json.load(handle)


def test_every_surface_is_extracted(worktree_surfaces):
    """The manifest covers every declared contract surface."""
    assert sorted(worktree_surfaces) == sorted(SURFACES)


def test_directives_match_the_checked_in_inventory(worktree_surfaces):
    """The extractor agrees with the inventory the drift gate already owns."""
    with open(
        REPO / "docs" / "harness" / "public-surface-inventory.json", "r", encoding="utf-8"
    ) as handle:
        inventory = json.load(handle)
    reference = {entry["name"]: entry for entry in inventory["directives"]}
    extracted = {entry["name"]: entry for entry in worktree_surfaces["directives"]["entries"]}
    assert sorted(extracted) == sorted(reference)
    for name, entry in extracted.items():
        for field in ("syntax", "args", "default", "context"):
            assert entry[field] == reference[name][field], "{} {}".format(name, field)
    assert worktree_surfaces["directives"]["count"] == inventory["directive_count"]


def test_metrics_emitted_set_matches_the_registry(worktree_surfaces):
    """Every declared family is emitted with the declared type."""
    metrics = worktree_surfaces["metrics"]
    declared = {entry["name"]: entry["type"] for entry in metrics["families"]}
    assert metrics["emitted"]["families"] == declared
    assert len(declared) == 10


def test_reasons_are_contiguous_and_complete(worktree_surfaces):
    """Reason discriminants form one contiguous frozen range."""
    reasons = worktree_surfaces["reasons"]
    discriminants = [entry["discriminant"] for entry in reasons["entries"]]
    assert discriminants == list(range(reasons["count"]))
    assert len({entry["key"] for entry in reasons["entries"]}) == reasons["count"]


def test_diagnostics_has_seven_frozen_top_level_fields(worktree_surfaces):
    """The diagnostics wire schema keeps its frozen top-level field set."""
    diagnostics = worktree_surfaces["diagnostics"]
    names = {field["name"] for field in diagnostics["fields"]}
    assert diagnostics["schema_version"] == 3
    assert diagnostics["additional_properties"] is False
    assert len(diagnostics["required"]) == 7
    assert "extensions" in names
    assert "extensions" not in diagnostics["required"]
    assert diagnostics["methods"] == ["GET", "HEAD"]


def test_ffi_header_exports_match_the_inventory(worktree_surfaces):
    """The generated header export set matches the checked-in inventory."""
    with open(
        REPO / "docs" / "harness" / "public-surface-inventory.json", "r", encoding="utf-8"
    ) as handle:
        inventory = json.load(handle)
    expected = {entry["name"] for entry in inventory["ffi_exports"]}
    actual = {entry["name"] for entry in worktree_surfaces["ffi"]["symbols"]}
    assert actual == expected
    assert worktree_surfaces["ffi"]["abi_version"] == inventory["ffi_abi_version"]


def test_anchor_manifests_reproduce_from_their_tags(index_document):
    """A frozen manifest must equal a fresh extraction of its release tag."""
    for anchor in index_document["anchors"]:
        stored = _anchor_manifest(index_document, anchor["tag"])
        fresh = extract_surfaces(
            SourceReader(str(REPO), resolve_tag(str(REPO), anchor["tag"]))
        )
        assert contract_digest(fresh) == anchor["contract_digest"]
        assert contract_digest(fresh) == stored["contract_digest"]
        assert surface_digests(fresh) == stored["surface_digests"]


def test_frozen_contract_surfaces_are_stable_between_anchors(index_document):
    """The five frozen surfaces did not move between the two anchors."""
    baseline = _anchor_manifest(index_document, "v0.9.2")["surface_digests"]
    candidate = _anchor_manifest(index_document, "v0.9.4")["surface_digests"]
    for surface in ("directives", "metrics", "reasons", "diagnostics", "ffi"):
        assert baseline[surface] == candidate[surface], surface


def test_candidate_matches_the_current_baseline(index_document, worktree_surfaces):
    """The working tree preserves the newest anchor's contract."""
    baseline = _anchor_manifest(index_document, index_document["baseline"])
    result = compare_contracts(
        baseline,
        {"surfaces": worktree_surfaces},
        index_document["baseline"],
        "worktree",
    )
    assert result.verdict() == OK
    assert result.blocking() == []


def test_declared_historical_deltas_still_occur(index_document):
    """Every documented historical delta is still observable."""
    record = index_document["comparisons"][0]
    baseline = _anchor_manifest(index_document, record["from"])
    candidate = _anchor_manifest(index_document, record["to"])
    result = compare_contracts(baseline, candidate, record["from"], record["to"])
    observed = {
        finding.finding_id
        for finding in result.findings
        if finding.verdict not in (OK, ADDITIVE)
    }
    assert observed == set(record["documented_delta"])


def _synthetic_pair(surface: str, baseline: dict, candidate: dict):
    """Compare two synthetic single-surface contracts."""
    return compare_contracts(
        {"surfaces": {surface: baseline}},
        {"surfaces": {surface: candidate}},
        "baseline",
        "candidate",
    )


def test_compare_reports_unverified_when_a_surface_is_absent():
    """A missing surface is never treated as a pass."""
    result = compare_contracts(
        {"surfaces": {}},
        {"surfaces": {}},
        "baseline",
        "candidate",
    )
    assert result.verdict() == UNVERIFIED
    assert {finding.verdict for finding in result.findings} == {UNVERIFIED}


def test_compare_classifies_context_widening_as_additive():
    """Widening a directive context is additive."""
    baseline = {
        "count": 1,
        "reject_only_count": 0,
        "entries": [
            {
                "name": "markdown_filter",
                "context": ["http", "server", "location"],
                "documented_context": ["http", "server", "location"],
                "args": "1",
                "handler": "handler",
                "conf_offset": "offset",
                "source_flags": "flags",
                "syntax": "on|off",
                "default": "off",
                "documentation": "doc",
            }
        ],
    }
    candidate = json.loads(json.dumps(baseline))
    candidate["entries"][0]["context"] = ["http", "server", "location", "if"]
    result = _synthetic_pair("directives", baseline, candidate)
    verdicts = {finding.finding_id: finding.verdict for finding in result.findings}
    assert verdicts["directives:context-widened:markdown_filter"] == ADDITIVE
    assert BREAKING not in verdicts.values()


def test_approvals_waive_review_but_never_break_candidates():
    """An approval converts REVIEW_REQUIRED only."""
    result = Comparison(baseline_label="a", candidate_label="b")
    result.add("http", REVIEW_REQUIRED, "test-changed:x", "review me")
    result.add("directives", BREAKING, "default-changed:y", "break")
    approvals = {
        "schema": 1,
        "exceptions": [
            {
                "finding_id": "http:test-changed:x",
                "reason": "non-behavioural test edit",
                "approved_by": "maintainer",
                "approved_at": "2026-11-23",
            },
            {
                "finding_id": "directives:default-changed:y",
                "reason": "attempted waiver",
                "approved_by": "maintainer",
                "approved_at": "2026-11-23",
            },
        ],
    }
    apply_approvals(result, approvals, "approvals.json")
    verdicts = {finding.finding_id: finding.verdict for finding in result.findings}
    assert verdicts["http:test-changed:x"] == OK
    assert verdicts["directives:default-changed:y"] == BREAKING
    assert result.verdict() == BREAKING


def test_approval_file_requires_named_approver():
    """An approval without a reviewer is rejected."""
    result = Comparison(baseline_label="a", candidate_label="b")
    with pytest.raises(ValueError):
        apply_approvals(
            result,
            {
                "schema": 1,
                "exceptions": [{"finding_id": "http:test-changed:x", "reason": "r"}],
            },
            "approvals.json",
        )


def test_cli_check_passes_on_the_working_tree():
    """The blocking gate passes on the current checkout."""
    from tools.compatibility.cli import main

    assert main(["check", "--index", str(INDEX), "--repo-root", str(REPO)]) == 0


def test_cli_rejects_an_unknown_surface():
    """An unknown surface name fails closed."""
    from tools.compatibility.model import ContractError

    reader = SourceReader(str(REPO), resolve_worktree(str(REPO)))
    with pytest.raises(ContractError):
        extract_surfaces(reader, ["directives", "nonsense"])


def test_repo_root_is_discovered_from_the_contracts_directory():
    """The default repository root resolves to this checkout."""
    assert os.path.realpath(repo_root_for(str(INDEX.parent))) == os.path.realpath(REPO)
