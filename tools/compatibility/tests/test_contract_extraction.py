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
    compare_diagnostics,
    OK,
    REVIEW_REQUIRED,
    UNVERIFIED,
    Comparison,
    apply_approvals,
    _compare_emitted,
    compare_directives,
    compare_http,
    compare_packaging,
    compare_contracts,
    compare_diagnostics,
)
from tools.compatibility.extract import (
    _collect_series,
    _nfpm_contract,
    _schema_field,
    _unlabelled_samples,
    extract_surfaces,
)
from tools.compatibility.model import (
    ContractError,
    INDEX_SCHEMA,
    MANIFEST_SCHEMA,
    SURFACES,
    contract_digest,
    digest_of_bytes,
    dump_manifest,
    surface_digests,
)
from tools.compatibility.tests.conftest import (
    _changed_paths,
    _overlay,
    _overlay_entry,
    sanitized_environment,
)
from tools.lib.path_validation import validate_read_path
from tools.compatibility.sources import (
    SourceReader,
    resolve_commit,
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
    resolved = validate_read_path(
        INDEX.parent / anchor["manifest"], purpose="anchor manifest"
    )
    with open(resolved, "r", encoding="utf-8") as handle:
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
    """Only the runtime-default evidence changed between the two anchors."""
    baseline_manifest = _anchor_manifest(index_document, "v0.9.2")
    candidate_manifest = _anchor_manifest(index_document, "v0.9.4")
    baseline = baseline_manifest["surfaces"]["directives"]
    candidate = candidate_manifest["surfaces"]["directives"]
    for key in ("count", "declared_names", "entries", "reject_only_count"):
        assert baseline[key] == candidate[key], key
    assert set(baseline["runtime_default_sources"]) == set(
        candidate["runtime_default_sources"]
    )
    changed_sources = {
        path
        for path in baseline["runtime_default_sources"]
        if baseline["runtime_default_sources"][path]
        != candidate["runtime_default_sources"][path]
    }
    assert changed_sources == {
        "components/nginx-module/src/ngx_http_markdown_filter_module.h"
    }
    baseline_digests = baseline_manifest["surface_digests"]
    candidate_digests = candidate_manifest["surface_digests"]
    for surface in ("metrics", "reasons", "diagnostics", "ffi"):
        assert baseline_digests[surface] == candidate_digests[surface], surface


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


def test_compare_reports_a_changed_fixed_metric_value():
    """A registry-pinned sample value is part of the metrics contract."""
    baseline = {
        "families": [
            {
                "name": "nginx_markdown_build_info",
                "type": "gauge",
                "labels": [],
                "value": 1,
            }
        ],
        "emitted": {"families": {}, "label_sets": {}, "series": {}},
    }
    changed = json.loads(json.dumps(baseline))
    changed["families"][0]["value"] = 2
    verdicts = {
        finding.finding_id: finding.verdict
        for finding in _synthetic_pair("metrics", baseline, changed).findings
    }
    assert (
        verdicts["metrics:fixed-value-changed:nginx_markdown_build_info"] == BREAKING
    )

    dropped = json.loads(json.dumps(baseline))
    del dropped["families"][0]["value"]
    assert "metrics:fixed-value-changed:nginx_markdown_build_info" in {
        finding.finding_id
        for finding in _synthetic_pair("metrics", baseline, dropped).findings
    }


def test_compare_reports_a_directive_block_form_change():
    """NGX_CONF_BLOCK changes whether a directive accepts a block."""
    flags = "NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1"
    baseline = _directive_surface(flags)
    blocked = _directive_surface(flags + "|NGX_CONF_BLOCK")

    verdicts = {
        finding.finding_id: finding.verdict
        for finding in _synthetic_pair("directives", baseline, blocked).findings
    }

    assert verdicts["directives:block-form-changed:markdown_filter"] == BREAKING
    reordered = _directive_surface("NGX_CONF_TAKE1|NGX_HTTP_LOC_CONF")
    assert not [
        finding
        for finding in _synthetic_pair("directives", baseline, reordered).findings
        if finding.surface == "directives"
    ]


def _directive_surface(flags: str) -> dict:
    """Return a single-directive contract surface with the given flags."""
    return {
        "count": 1,
        "reject_only_count": 0,
        "entries": [
            {
                "name": "markdown_filter",
                "context": ["location"],
                "documented_context": ["location"],
                "args": "1",
                "handler": "handler",
                "conf_offset": "offset",
                "source_flags": flags,
                "syntax": "on|off",
                "default": "off",
                "documentation": "doc",
            }
        ],
    }


def test_approvals_waive_review_but_never_break_candidates():
    """An approval converts REVIEW_REQUIRED only."""
    result = Comparison(baseline_label="a", candidate_label="b")
    result.add("http", REVIEW_REQUIRED, "test-changed:x", "review me")
    result.add("directives", BREAKING, "default-changed:y", "break")
    by_id = {finding.finding_id: finding for finding in result.findings}
    approvals = {
        "schema": 1,
        "exceptions": [
            {
                "finding_id": "http:test-changed:x",
                "evidence_fingerprint": by_id["http:test-changed:x"].fingerprint,
                "reason": "non-behavioural test edit",
                "approved_by": "maintainer",
                "approved_at": "2026-11-23",
            },
            {
                "finding_id": "directives:default-changed:y",
                "evidence_fingerprint": by_id["directives:default-changed:y"].fingerprint,
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
                "exceptions": [
                    {
                        "finding_id": "http:test-changed:x",
                        "evidence_fingerprint": "sha256:" + "0" * 64,
                        "reason": "r",
                    }
                ],
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


def test_inherited_git_location_cannot_select_another_repository(tmp_path, monkeypatch):
    """GIT_DIR from the environment must not redirect a read to another repo."""
    import subprocess

    from tools.compatibility.sources import resolve_commit, resolve_worktree

    repos = []
    for name in ("selected", "inherited"):
        path = tmp_path / name
        path.mkdir()
        subprocess.run(
            ["git", "init", str(path)],
            check=True,
            capture_output=True,
            env=sanitized_environment(),
        )
        (path / "marker.txt").write_text(name, encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(path), "add", "marker.txt"],
            check=True,
            capture_output=True,
            env=sanitized_environment(),
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-m",
                name,
            ],
            check=True,
            capture_output=True,
            env=sanitized_environment(),
        )
        repos.append(path)

    selected, inherited = repos
    selected_head = resolve_commit(str(selected), "HEAD").commit
    inherited_head = resolve_commit(str(inherited), "HEAD").commit
    assert selected_head != inherited_head

    monkeypatch.setenv("GIT_DIR", str(inherited / ".git"))
    assert resolve_worktree(str(selected)).commit == selected_head
    assert repo_root_for(str(selected / "marker.txt")) == str(selected)


def test_git_environment_disables_replacement_refs(monkeypatch):
    """Compatibility evidence cannot be redirected through replace refs."""
    from tools.compatibility.sources import _git_environment

    monkeypatch.setenv("GIT_NO_REPLACE_OBJECTS", "0")

    assert _git_environment()["GIT_NO_REPLACE_OBJECTS"] == "1"


def test_source_reader_rejects_a_committed_directory(tmp_path):
    """A directory tree cannot masquerade as the contents of a contract file."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "directory").mkdir()
    (repo / "directory" / "child.txt").write_text("child", encoding="utf-8")
    subprocess.run(
        ["git", "init", str(repo)],
        check=True,
        capture_output=True,
        env=sanitized_environment(),
    )
    subprocess.run(
        ["git", "-C", str(repo), "add", "directory"],
        check=True,
        capture_output=True,
        env=sanitized_environment(),
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
        env=sanitized_environment(),
    )
    reader = SourceReader(str(repo), resolve_commit(str(repo), "HEAD"))

    with pytest.raises(ContractError, match="committed file"):
        reader.read_bytes("directory")


def test_changed_untracked_directory_is_overlaid_file_by_file(tmp_path):
    """The fixture overlay sees individual files inside new directories."""
    import subprocess

    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    worktree.mkdir()
    subprocess.run(
        ["git", "init", str(repo)],
        check=True,
        capture_output=True,
        env=sanitized_environment(),
    )
    nested = repo / "new" / "nested.txt"
    nested.parent.mkdir()
    nested.write_text("new", encoding="utf-8")

    assert "new/nested.txt" in _changed_paths(repo)
    _overlay(repo, worktree)
    assert (worktree / "new" / "nested.txt").read_text(encoding="utf-8") == "new"


def test_fixture_overlay_preserves_symlinks(tmp_path):
    """A changed symlink stays a symlink in an isolated fixture."""
    import subprocess

    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    worktree.mkdir()
    subprocess.run(
        ["git", "init", str(repo)],
        check=True,
        capture_output=True,
        env=sanitized_environment(),
    )
    (repo / "target.txt").write_text("target", encoding="utf-8")
    os.symlink("target.txt", repo / "link.txt")

    _overlay(repo, worktree)

    assert (worktree / "link.txt").is_symlink()
    assert os.readlink(worktree / "link.txt") == "target.txt"


def test_extract_http_rejects_invalid_utf8(monkeypatch):
    """HTTP evidence digests require valid UTF-8 source files."""
    from tools.compatibility import extract as extract_module

    class InvalidByteReader(SourceReader):
        def read_bytes(self, path):
            return b"\xff"

    monkeypatch.setattr(
        extract_module, "HTTP_CONTRACT_TESTS", {"invalid": ("fixture.c",)}
    )
    monkeypatch.setattr(
        extract_module, "HTTP_EXECUTION_TARGETS", {"invalid": "unit"}
    )
    reader = InvalidByteReader(str(REPO), resolve_worktree(str(REPO)))

    with pytest.raises(ContractError, match="valid UTF-8"):
        extract_module.extract_http(reader)


def test_compare_reports_an_array_item_field_type_change():
    """A type change inside an array item is a diagnostics difference."""
    baseline = {
        "schema_version": 3,
        "type": "object",
        "additional_properties": False,
        "required": ["recent_decisions"],
        "fields": [
            {
                "name": "recent_decisions",
                "required": True,
                "type": "array",
                "nullable": False,
                "items_type": "object",
                "items_required": ["duration_ms"],
                "items_additional_properties": False,
                "items_properties": [{"name": "duration_ms", "type": "number"}],
            }
        ],
    }
    candidate = json.loads(json.dumps(baseline))
    candidate["fields"][0]["items_properties"][0]["type"] = "string"

    result = Comparison(baseline_label="a", candidate_label="b")
    compare_diagnostics(result, baseline, candidate)
    verdicts = {finding.finding_id: finding.verdict for finding in result.findings}
    assert (
        verdicts["diagnostics:child-type-changed:recent_decisions[]:duration_ms"]
        == BREAKING
    )


def test_diagnostics_constraints_and_mixed_enum_values_are_compared():
    """Array constraints trigger review and enum sorting accepts null values."""
    baseline = {
        "schema_version": 3,
        "type": "object",
        "additional_properties": False,
        "required": [],
        "fields": [
            {
                "name": "state",
                "type": "string",
                "nullable": False,
                "enum": [None, "active"],
            },
            {
                "name": "values",
                "type": "array",
                "nullable": False,
                "items_type": "integer",
                "items_constraints": {"minimum": 1},
            },
        ],
    }
    candidate = json.loads(json.dumps(baseline))
    candidate["fields"][0]["enum"] = [None]
    candidate["fields"][1]["items_constraints"]["minimum"] = 2

    result = Comparison(baseline_label="a", candidate_label="b")
    compare_diagnostics(result, baseline, candidate)
    verdicts = {finding.finding_id: finding.verdict for finding in result.findings}
    assert verdicts["diagnostics:enum-narrowed:state"] == BREAKING
    assert (
        verdicts["diagnostics:schema-constraints-changed:values[]"]
        == REVIEW_REQUIRED
    )


def test_diagnostics_open_extension_cannot_become_closed():
    """Closing an open extension object is a breaking restriction."""
    baseline = {
        "schema_version": 3,
        "type": "object",
        "additional_properties": False,
        "required": [],
        "fields": [
            {
                "name": "extensions",
                "type": "object",
                "nullable": False,
                "additional_properties": True,
                "required_children": [],
                "properties": [],
            }
        ],
    }
    candidate = json.loads(json.dumps(baseline))
    candidate["fields"][0]["additional_properties"] = False
    result = Comparison(baseline_label="a", candidate_label="b")

    compare_diagnostics(result, baseline, candidate)

    assert {
        finding.finding_id: finding.verdict for finding in result.findings
    }["diagnostics:additional-properties-restricted:extensions"] == BREAKING


def test_diagnostics_root_type_change_is_breaking():
    """The comparator checks the schema root type as part of the wire contract."""
    baseline = {
        "schema_version": 3,
        "type": "object",
        "additional_properties": False,
        "required": [],
        "fields": [],
    }
    candidate = dict(baseline, type="array")
    result = Comparison(baseline_label="a", candidate_label="b")

    compare_diagnostics(result, baseline, candidate)

    assert {
        finding.finding_id: finding.verdict for finding in result.findings
    }["diagnostics:root-type-changed"] == BREAKING


def test_boolean_diagnostics_schema_fails_closed():
    """A boolean subschema cannot disappear as an empty object contract."""
    with pytest.raises(ContractError, match="boolean"):
        _schema_field("value", True, False, {})

    with pytest.raises(ContractError, match="boolean"):
        _schema_field("values", {"type": "array", "items": False}, False, {})


def test_schema_extractor_records_array_item_constraints():
    """The projected diagnostics contract retains item-level minimums."""
    field = _schema_field(
        "values",
        {"type": "array", "items": {"type": "integer", "minimum": 1}},
        False,
        {},
    )

    assert field["items_constraints"] == {"minimum": 1}


def test_schema_extractor_preserves_one_of_alternatives():
    """A constrained oneOf union remains part of the diagnostics contract."""
    field = _schema_field(
        "source_sha",
        {
            "oneOf": [
                {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                {"const": "development"},
            ]
        },
        True,
        {},
    )

    assert field["constraints"]["oneOf"] == [
        {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        {"const": "development"},
    ]


def test_metrics_histogram_samples_and_label_order_are_preserved():
    """Histogram components are identified and label order is canonical."""
    family = "nginx_markdown_latency"
    emitted = (
        family
        + r'_bucket{le=\"1\",region=\"west\"} 2\n'
        + family
        + r'_bucket{region=\"west\",le=\"1\"} 2'
    )
    series, labels = _collect_series(emitted, {family: "histogram"})

    assert series[family] == {
        ("bucket", ("le=1", "region=west")),
    }
    assert labels[family] == [["@component=bucket", "le", "region"]]
    assert _unlabelled_samples(
        family + r"_sum 2\n" + family + "_count 2",
        {family: "histogram"},
    ) == [family + ":count", family + ":sum"]


def test_metrics_comparator_detects_removed_histogram_component():
    """A histogram bucket, sum, or count component is part of its contract."""
    family = "nginx_markdown_latency"
    baseline = {
        "emitted": {
            "families": {family: "histogram"},
            "label_sets": {family: [["le"], ["method"]]},
            "series": {
                family: [
                    ["@component=bucket", "le=1"],
                    ["@component=count", "method=GET"],
                    ["@component=sum", "method=GET"],
                ]
            },
        }
    }
    candidate = json.loads(json.dumps(baseline))
    candidate["emitted"]["series"][family].remove(
        ["@component=count", "method=GET"]
    )
    result = Comparison(baseline_label="a", candidate_label="b")

    _compare_emitted(result, baseline, candidate)

    assert any(
        finding.verdict == BREAKING
        and "emitted-series-removed" in finding.finding_id
        and "@component=count" in finding.finding_id
        for finding in result.findings
    )


def test_nfpm_extraction_reads_only_effective_yaml_values():
    """Commented-out package dependencies do not enter the contract."""
    contract = _nfpm_contract(
        '''
name: sample
# overrides:
#   deb:
#     depends:
#       - "nginx (<< ${NGINX_VERSION_CEIL})"
overrides:
  deb:
    depends:
      - "nginx (>= ${NGINX_VERSION})"
  rpm:
    depends:
      - "nginx-r${NGINX_VERSION}"
      - "nginx >= ${NGINX_VERSION}"
contents:
  - src: module.so
    packager: deb
    dst: /usr/lib/nginx/module.so
  - dst: /usr/share/doc/sample/README.md
    packager: rpm
'''
    )

    assert contract["deb_nginx_constraints"] == [[">=", "NGINX_VERSION"]]
    assert contract["rpm_nginx_constraints"] == [
        "-r${NGINX_VERSION}",
        ">=${NGINX_VERSION}",
    ]
    assert contract["deb_install_paths"] == ["/usr/lib/nginx/module.so"]
    assert contract["rpm_install_paths"] == ["/usr/share/doc/sample/README.md"]


def test_support_rows_keep_artifact_identity_and_detect_each_demotion():
    """Distinct artifacts at one target retain their own tier changes."""
    def package_contract(targets):
        return {
            "package_name": "sample",
            "deb_module_path": [],
            "rpm_module_path": [],
            "deb_load_snippet_paths": [],
            "rpm_load_snippet_paths": [],
            "deb_nginx_constraints": [],
            "rpm_nginx_constraints": [],
            "deb_install_paths": [],
            "rpm_install_paths": [],
            "support": {"targets": targets},
        }

    rows = [
        {
            "nginx_minor": "1.26",
            "nginx_version": "1.26.3",
            "artifact_type": "deb-package",
            "os": "debian12",
            "libc": "glibc",
            "arch": "amd64",
            "support_tier": "supported",
            "release_blocking": True,
        },
        {
            "nginx_minor": "1.26",
            "nginx_version": "1.26.3",
            "artifact_type": "docker-image",
            "os": "debian12",
            "libc": "glibc",
            "arch": "amd64",
            "support_tier": "best-effort",
            "release_blocking": True,
        },
    ]
    candidate_rows = json.loads(json.dumps(rows))
    candidate_rows[0]["support_tier"] = "best-effort"
    candidate_rows[1]["support_tier"] = "supported"
    result = Comparison(baseline_label="a", candidate_label="b")

    compare_packaging(
        result, package_contract(rows), package_contract(candidate_rows)
    )
    changed = {
        finding.finding_id: finding.verdict
        for finding in result.findings
        if "row-tier-changed" in finding.finding_id
    }
    assert changed == {
        "packaging:row-tier-changed:1.26.3|deb-package|debian12/glibc/amd64":
            BREAKING,
        "packaging:row-tier-changed:1.26.3|docker-image|debian12/glibc/amd64":
            ADDITIVE,
    }


def test_support_patch_replacement_cannot_drop_supported_blocking_coverage():
    """A supported sibling artifact cannot mask a patch coverage drop."""
    def target(version, blocking, artifact_type):
        return {
            "nginx_minor": "1.30",
            "nginx_version": version,
            "artifact_type": artifact_type,
            "os": "linux",
            "libc": "glibc",
            "arch": "amd64",
            "support_tier": "supported",
            "release_blocking": blocking,
        }

    def package_contract(targets):
        return {
            "package_name": "sample",
            "deb_module_path": [],
            "rpm_module_path": [],
            "deb_load_snippet_paths": [],
            "rpm_load_snippet_paths": [],
            "deb_nginx_constraints": [],
            "rpm_nginx_constraints": [],
            "deb_install_paths": [],
            "rpm_install_paths": [],
            "support": {"targets": targets},
        }

    result = Comparison(baseline_label="a", candidate_label="b")
    compare_packaging(
        result,
        package_contract(
            [
                target("1.30.8", True, "dynamic-module"),
                target("1.30.8", True, "docker-image"),
            ]
        ),
        package_contract(
            [
                target("1.30.9", False, "dynamic-module"),
                target("1.30.8", True, "docker-image"),
            ]
        ),
    )

    findings = {
        finding.finding_id: finding.verdict for finding in result.findings
    }
    assert findings[
        "packaging:support-blocking-coverage-dropped:1.30/dynamic-module/linux/glibc/amd64"
    ] == BREAKING


def test_directive_runtime_default_source_change_requires_review():
    """A change to the runtime merge code cannot hide behind unchanged comments."""
    result = Comparison(baseline_label="a", candidate_label="b")
    compare_directives(
        result,
        {
            "entries": [],
            "reject_only_count": 0,
            "runtime_default_sources": {"merge.h": "old"},
        },
        {
            "entries": [],
            "reject_only_count": 0,
            "runtime_default_sources": {"merge.h": "new"},
        },
    )

    assert result.findings[0].verdict == REVIEW_REQUIRED
    assert result.findings[0].finding_id == (
        "directives:runtime-default-source-changed:merge.h"
    )


def test_http_rebinding_fingerprint_includes_test_digests():
    """A binding finding carries both path-to-digest maps as its evidence."""
    result = Comparison(baseline_label="a", candidate_label="b")
    compare_http(
        result,
        {
            "behaviours": {"status": {"tests": ["old.c"], "executed_by": "unit"}},
            "test_file_digests": {"old.c": "sha256:old"},
        },
        {
            "behaviours": {"status": {"tests": ["new.c"], "executed_by": "unit"}},
            "test_file_digests": {"new.c": "sha256:new"},
        },
    )

    finding = next(item for item in result.findings if "binding-changed" in item.finding_id)
    assert finding.baseline == {"old.c": "sha256:old"}
    assert finding.candidate == {"new.c": "sha256:new"}


def test_approvals_reject_non_object_exception_entries():
    """Malformed exceptions fail with a validation error instead of crashing."""
    result = Comparison(baseline_label="a", candidate_label="b")

    with pytest.raises(ValueError, match="list of JSON objects"):
        apply_approvals(
            result,
            {"schema": 1, "exceptions": [None]},
            "approvals.json",
        )


def test_cli_load_index_rejects_non_object_json(monkeypatch):
    """A JSON array at the index path produces a contract error."""
    from tools.compatibility import cli

    monkeypatch.setattr(cli, "_read_json", lambda *_: [])

    with pytest.raises(ContractError, match="JSON object"):
        cli.load_index("unused.json", str(REPO))


def test_freeze_atomic_write_preserves_existing_manifest(tmp_path, monkeypatch):
    """A failed atomic replace cannot truncate the current manifest."""
    from tools.compatibility import cli

    target = tmp_path / "manifest.json"
    target.write_text("original", encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(cli.os, "replace", fail_replace)
    with pytest.raises(ContractError, match="atomically write"):
        cli._atomic_write_text(str(target), "replacement")

    assert target.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.iterdir()) == [target]


def test_cli_report_verdict_includes_historical_failures(tmp_path, monkeypatch):
    """The top-level JSON verdict reflects failures outside the candidate."""
    from argparse import Namespace

    from tools.compatibility import cli

    report_path = tmp_path / "report.json"
    monkeypatch.setattr(cli, "load_index", lambda *_: {"baseline": "v0.9.4"})
    monkeypatch.setattr(cli, "_print_header", lambda *_: None)
    monkeypatch.setattr(
        cli, "_verify_every_anchor", lambda *_: ([], {"v0.9.4": {}})
    )
    monkeypatch.setattr(
        cli,
        "_audit_historical",
        lambda *_: ([{"status": "UNDOCUMENTED"}], ["historical failure"]),
    )
    monkeypatch.setattr(
        cli,
        "_compare_candidate",
        lambda *_: ({"verdict": "OK", "findings": []}, []),
    )
    args = Namespace(
        index=str(tmp_path / "INDEX.json"),
        repo_root=str(tmp_path),
        json_report=str(report_path),
        candidate="WORKTREE",
        product_version=None,
        approvals=None,
    )

    assert cli.check_command(args) == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["candidate"]["verdict"] == "OK"
    assert report["verdict"] == "FAIL"
    assert report["failures"] == ["historical failure"]


def _write_anchor_fixture(tmp_path, product_version, tag_object):
    from tools.compatibility.cli import _manifest_path

    contracts = tmp_path / "contracts"
    contracts.mkdir()
    surfaces = {name: {} for name in SURFACES}
    manifest = {
        "manifest_schema": MANIFEST_SCHEMA,
        "product_version": product_version,
        "source": {"commit": "fixture-commit", "tag_object": tag_object},
        "surfaces": surfaces,
        "surface_digests": surface_digests(surfaces),
        "contract_digest": contract_digest(surfaces),
    }
    payload = dump_manifest(manifest)
    manifest_path = Path(_manifest_path(str(contracts), "manifest.json"))
    manifest_path.write_text(payload, encoding="utf-8")
    anchor = {
        "tag": "v0.9.4",
        "commit": "fixture-commit",
        "tag_object": tag_object,
        "manifest": "manifest.json",
        "manifest_sha256": digest_of_bytes(payload.encode("utf-8")),
        "contract_digest": manifest["contract_digest"],
    }
    return contracts, anchor


def test_anchor_manifest_product_version_must_match_tag(tmp_path):
    """A valid digest cannot bind the wrong release identity to a tag."""
    from tools.compatibility.cli import verify_anchor

    contracts, anchor = _write_anchor_fixture(tmp_path, "0.9.3", "tag-object")

    with pytest.raises(ContractError, match="product version"):
        verify_anchor(str(tmp_path), anchor, str(contracts))


def test_anchor_verification_checks_lightweight_to_annotated_tag_change(
    tmp_path, monkeypatch
):
    """An anchor without a recorded tag object rejects a newly annotated tag."""
    from types import SimpleNamespace

    from tools.compatibility import cli

    contracts, anchor = _write_anchor_fixture(tmp_path, "0.9.4", None)
    monkeypatch.setattr(
        cli,
        "resolve_tag",
        lambda *_: SimpleNamespace(
            commit="fixture-commit",
            tag_object="new-tag-object",
            label="v0.9.4",
        ),
    )
    monkeypatch.setattr(
        cli,
        "build_manifest",
        lambda *_: {"contract_digest": anchor["contract_digest"]},
    )

    with pytest.raises(ContractError, match="tag object"):
        cli.verify_anchor(str(tmp_path), anchor, str(contracts))


def _init_committed_repo(repo: Path) -> None:
    """Create a git repository whose tracked files are already committed."""
    import subprocess

    subprocess.run(
        ["git", "init", str(repo)],
        check=True,
        capture_output=True,
        env=sanitized_environment(),
    )
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"],
        check=True,
        capture_output=True,
        env=sanitized_environment(),
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
        env=sanitized_environment(),
    )


def test_worktree_reader_rejects_symlinked_contract_inputs(tmp_path):
    """Working-tree reads accept only regular files, as commit mode does."""
    repo = tmp_path / "repo"
    (repo / "real").mkdir(parents=True)
    (repo / "regular.txt").write_text("regular", encoding="utf-8")
    (repo / "real" / "child.txt").write_text("child", encoding="utf-8")
    os.symlink("regular.txt", repo / "link.txt")
    os.symlink("real", repo / "linked")
    _init_committed_repo(repo)
    reader = SourceReader(str(repo), resolve_worktree(str(repo)))

    assert reader.read_bytes("regular.txt") == b"regular"
    assert reader.read_bytes("real/child.txt") == b"child"
    for path in ("link.txt", "linked/child.txt"):
        with pytest.raises(ContractError, match="not a regular file"):
            reader.read_bytes(path)


def test_fixture_overlay_replaces_a_destination_symlink(tmp_path):
    """Copying a regular file replaces the link without writing its target."""
    import subprocess

    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo.mkdir()
    worktree.mkdir()
    subprocess.run(
        ["git", "init", str(repo)],
        check=True,
        capture_output=True,
        env=sanitized_environment(),
    )
    (repo / "link.txt").write_text("overlay", encoding="utf-8")
    (worktree / "target.txt").write_text("original", encoding="utf-8")
    os.symlink("target.txt", worktree / "link.txt")

    _overlay(repo, worktree)

    assert not (worktree / "link.txt").is_symlink()
    assert (worktree / "link.txt").read_text(encoding="utf-8") == "overlay"
    assert (worktree / "target.txt").read_text(encoding="utf-8") == "original"


def _index_with_pair(source: str, target: str) -> dict:
    """Return a minimal valid anchor index with one comparison record."""
    anchors = [
        {
            "tag": tag,
            "commit": "0" * 40,
            "manifest": "nginx-markdown-{}.json".format(tag[1:]),
            "manifest_sha256": "sha256:" + "0" * 64,
            "contract_digest": "sha256:" + "1" * 64,
        }
        for tag in ("v0.9.2", "v0.9.4")
    ]
    return {
        "index_schema": INDEX_SCHEMA,
        "baseline": "v0.9.4",
        "anchors": anchors,
        "comparisons": [{"from": source, "to": target}],
    }


def test_default_index_requires_the_directed_historical_pair(tmp_path, monkeypatch):
    """The checked-in index cannot replace its audit with a vacuous pair."""
    from tools.compatibility import cli

    for source, target in (("v0.9.4", "v0.9.4"), ("v0.9.4", "v0.9.2")):
        monkeypatch.setattr(
            cli, "_read_json", lambda *_, pair=(source, target): _index_with_pair(*pair)
        )
        with pytest.raises(ContractError, match="v0.9.2 -> v0.9.4"):
            cli.load_index(str(INDEX), str(REPO))

    monkeypatch.setattr(
        cli, "_read_json", lambda *_: _index_with_pair("v0.9.2", "v0.9.4")
    )
    assert cli.load_index(str(INDEX), str(REPO))["baseline"] == "v0.9.4"

    monkeypatch.setattr(
        cli, "_read_json", lambda *_: _index_with_pair("v0.9.4", "v0.9.4")
    )
    custom = cli.load_index(str(tmp_path / "custom.json"), str(REPO))
    assert custom["comparisons"] == [{"from": "v0.9.4", "to": "v0.9.4"}]


def test_index_anchor_records_require_identity_fields(tmp_path, monkeypatch, capsys):
    """An anchor without its manifest identity fails as a contract error."""
    from tools.compatibility import cli

    custom = str(tmp_path / "custom.json")
    for field in ("commit", "manifest", "manifest_sha256", "contract_digest"):
        index = _index_with_pair("v0.9.2", "v0.9.4")
        del index["anchors"][0][field]
        monkeypatch.setattr(cli, "_read_json", lambda *_, value=index: value)
        with pytest.raises(ContractError, match=field):
            cli.load_index(custom, str(REPO))
        arguments = ["verify-anchors", "--index", custom, "--repo-root", str(REPO)]
        assert cli.main(arguments) == 1
        assert "FAIL: anchor" in capsys.readouterr().err

    index = _index_with_pair("v0.9.2", "v0.9.4")
    index["anchors"][0]["manifest"] = "nested/other.json"
    monkeypatch.setattr(cli, "_read_json", lambda *_, value=index: value)
    with pytest.raises(ContractError, match="single safe path component"):
        cli.load_index(custom, str(REPO))


def test_default_index_requires_the_maintenance_baseline(tmp_path, monkeypatch):
    """The checked-in index cannot compare against an older anchor."""
    from tools.compatibility import cli

    index = _index_with_pair("v0.9.2", "v0.9.4")
    index["baseline"] = "v0.9.2"
    monkeypatch.setattr(cli, "_read_json", lambda *_: index)
    with pytest.raises(ContractError, match="v0.9.4"):
        cli.load_index(str(INDEX), str(REPO))

    custom = cli.load_index(str(tmp_path / "custom.json"), str(REPO))
    assert custom["baseline"] == "v0.9.2"


def test_report_destination_cannot_alias_a_gate_input(tmp_path):
    """A report that resolves to an input the gate reads is refused."""
    from tools.compatibility import cli

    contracts = tmp_path / "contracts"
    contracts.mkdir()
    index_path = contracts / "INDEX.json"
    manifest_path = contracts / "manifest.json"
    approvals = tmp_path / "approvals.json"
    for path in (index_path, manifest_path, approvals):
        path.write_text("{}", encoding="utf-8")
    index = {"anchors": [{"tag": "v0.9.4", "manifest": "manifest.json"}]}

    for target, label in (
        (str(index_path), "anchor index"),
        (str(manifest_path), "manifest"),
        (str(approvals), "approvals"),
    ):
        with pytest.raises(ContractError, match=label):
            cli._reject_report_aliases(
                target, str(index_path), index, str(contracts), str(approvals)
            )
    cli._reject_report_aliases(
        str(tmp_path / "report.json"), str(index_path), index, str(contracts), ""
    )


def test_report_destination_must_stay_inside_the_repository(tmp_path):
    """The report path keeps its existing repository-root boundary."""
    from argparse import Namespace

    from tools.compatibility import cli

    args = Namespace(json_report=str(tmp_path.parent / "outside.json"))
    with pytest.raises(ContractError, match="escapes root"):
        cli._report_destination(args, str(tmp_path))


def test_check_rejects_an_aliased_report_before_comparing(tmp_path, monkeypatch):
    """The alias check runs before any anchor verification or comparison."""
    from argparse import Namespace

    from tools.compatibility import cli

    index_path = tmp_path / "INDEX.json"
    index_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        cli, "load_index", lambda *_: {"baseline": "v0.9.4", "anchors": []}
    )
    monkeypatch.setattr(
        cli,
        "_compare_candidate",
        lambda *_: pytest.fail("compared before the report path was checked"),
    )
    args = Namespace(
        index=str(index_path),
        repo_root=str(tmp_path),
        json_report=str(index_path),
        candidate="WORKTREE",
        product_version=None,
        approvals=None,
    )

    with pytest.raises(ContractError, match="anchor index"):
        cli.check_command(args)


def test_index_comparison_records_must_name_anchor_tags(tmp_path, monkeypatch, capsys):
    """A malformed comparison record fails through the FAIL diagnostic."""
    from tools.compatibility import cli

    custom = str(tmp_path / "custom.json")
    for record in (
        "v0.9.2",
        {"from": "v0.9.2"},
        {"from": "v0.9.2", "to": 4},
        {"from": "v0.9.1", "to": "v0.9.4"},
    ):
        index = _index_with_pair("v0.9.2", "v0.9.4")
        index["comparisons"] = [record]
        monkeypatch.setattr(cli, "_read_json", lambda *_, value=index: value)
        with pytest.raises(ContractError, match="comparison"):
            cli.load_index(custom, str(REPO))
        arguments = ["verify-anchors", "--index", custom, "--repo-root", str(REPO)]
        assert cli.main(arguments) == 1
        assert "FAIL: anchor index comparison" in capsys.readouterr().err

    index = _index_with_pair("v0.9.2", "v0.9.4")
    index["comparisons"] = [
        {"from": "v0.9.2", "to": "v0.9.4"},
        {"from": "v0.9.2", "to": 4},
    ]
    monkeypatch.setattr(cli, "_read_json", lambda *_: index)
    with pytest.raises(ContractError, match="comparison"):
        cli.load_index(str(INDEX), str(REPO))


def _added_behaviour_finding(executor: str):
    """Return the behaviour-added finding for one new HTTP binding."""
    result = Comparison(baseline_label="a", candidate_label="b")
    compare_http(
        result,
        {"behaviours": {}, "test_file_digests": {}},
        {
            "behaviours": {"status": {"tests": ["a.c"], "executed_by": executor}},
            "test_file_digests": {"a.c": "sha256:a"},
        },
    )
    return next(item for item in result.findings if "behaviour-added" in item.finding_id)


def test_http_addition_fingerprint_includes_the_executor():
    """An executor change invalidates an approval for a new behaviour."""
    first = _added_behaviour_finding("make test-nginx-unit")
    unchanged = _added_behaviour_finding("make test-nginx-unit")
    moved = _added_behaviour_finding("bash tools/e2e/other.sh")

    assert first.candidate == {
        "tests": {"a.c": "sha256:a"},
        "executed_by": "make test-nginx-unit",
    }
    assert first.fingerprint == unchanged.fingerprint
    assert first.fingerprint != moved.fingerprint


def test_nfpm_dependencies_follow_the_effective_override():
    """An override without depends inherits the root dependency list."""
    inherited = _nfpm_contract(
        """
name: sample
depends:
  - "nginx (>= ${NGINX_VERSION})"
overrides:
  deb:
    conflicts:
      - other
contents:
  - dst: /usr/lib/nginx/modules/module.so
"""
    )
    overridden = _nfpm_contract(
        """
name: sample
depends:
  - "nginx (<< ${NGINX_VERSION_CEIL})"
overrides:
  deb:
    depends:
      - "nginx (>= ${NGINX_VERSION})"
contents:
  - dst: /usr/lib/nginx/modules/module.so
"""
    )

    assert inherited["deb_nginx_constraints"] == [[">=", "NGINX_VERSION"]]
    assert overridden["deb_nginx_constraints"] == [[">=", "NGINX_VERSION"]]


def test_nfpm_layout_follows_non_empty_override_contents():
    """A non-empty override contents list replaces the root list."""
    contract = _nfpm_contract(
        """
name: sample
overrides:
  deb:
    contents:
      - dst: /usr/lib/nginx/modules/override.so
  rpm:
    contents: []
contents:
  - dst: /usr/lib64/nginx/modules/root.so
"""
    )

    assert contract["deb_install_paths"] == ["/usr/lib/nginx/modules/override.so"]
    assert contract["deb_module_path"] == ["/usr/lib/nginx/modules/override.so"]
    assert contract["rpm_install_paths"] == ["/usr/lib64/nginx/modules/root.so"]

    with pytest.raises(ContractError, match="no destination"):
        _nfpm_contract(
            """
name: sample
overrides:
  deb:
    contents:
      - src: module.so
contents:
  - dst: /usr/lib/nginx/modules/root.so
"""
        )


def test_nfpm_module_paths_exclude_directory_entries():
    """A directory entry is not an installed module file."""
    contract = _nfpm_contract(
        """
name: sample
contents:
  - dst: /usr/lib/nginx/modules/module.so
    packager: deb
  - dst: /usr/lib/nginx/modules/legacy.so
    type: dir
    packager: deb
"""
    )

    assert contract["deb_module_path"] == ["/usr/lib/nginx/modules/module.so"]
    assert contract["deb_install_paths"] == [
        "/usr/lib/nginx/modules/legacy.so",
        "/usr/lib/nginx/modules/module.so",
    ]


def test_unlabelled_samples_accept_a_tab_separator():
    """A tab after the sample name still identifies the sample."""
    family = "nginx_markdown_latency"

    assert _unlabelled_samples(
        family + r"_sum\t2\n" + family + "_count\t2",
        {family: "histogram"},
    ) == [family + ":count", family + ":sum"]


def test_metrics_label_sets_ignore_histogram_sample_order():
    """Reordered histogram lines keep the label-set contract unchanged."""
    family = "nginx_markdown_latency"
    bucket = family + r'_bucket{engine=\"rust\",le=\"1\"} 2\n'
    count = family + r'_count{engine=\"rust\"} 2\n'
    _, forward = _collect_series(bucket + count, {family: "histogram"})
    _, reverse = _collect_series(count + bucket, {family: "histogram"})
    assert forward == reverse

    def emitted(label_sets):
        return {
            "emitted": {
                "families": {family: "histogram"},
                "label_sets": {family: label_sets},
                "series": {family: []},
            }
        }

    bucket_set = ["@component=bucket", "engine", "le"]
    count_set = ["@component=count", "engine"]
    reordered = Comparison(baseline_label="a", candidate_label="b")
    _compare_emitted(
        reordered, emitted([bucket_set, count_set]), emitted([count_set, bucket_set])
    )
    assert reordered.findings == []

    added = Comparison(baseline_label="a", candidate_label="b")
    _compare_emitted(
        added,
        emitted([count_set]),
        emitted([count_set, ["@component=sum", "engine"]]),
    )
    assert [finding.finding_id for finding in added.findings] == [
        "metrics:emitted-labels-changed:" + family
    ]


def test_schema_ref_allows_annotations_but_rejects_validation_siblings():
    """A sibling constraint cannot overwrite the referenced constraint."""
    defs = {"name": {"type": "string", "minLength": 1}}

    field = _schema_field(
        "value", {"$ref": "#/$defs/name", "description": "doc"}, True, defs
    )
    assert field["constraints"] == {"minLength": 1}

    with pytest.raises(ContractError, match="validation siblings"):
        _schema_field("value", {"$ref": "#/$defs/name", "minLength": 0}, True, defs)


def test_schema_ref_rejects_sibling_additional_properties_scope():
    """Referenced properties stay outside a sibling additionalProperties scope."""
    defs = {
        "entry": {
            "type": "object",
            "properties": {"optional": {"type": "string"}},
        }
    }

    with pytest.raises(ContractError, match="additionalProperties"):
        _schema_field(
            "entry",
            {"$ref": "#/$defs/entry", "additionalProperties": False},
            False,
            defs,
        )


def test_schema_extractor_rejects_prefix_items():
    """Positional array constraints cannot pass as a homogeneous item schema."""
    with pytest.raises(ContractError, match="prefixItems"):
        _schema_field(
            "pair",
            {
                "type": "array",
                "prefixItems": [{"type": "string"}],
                "items": {"type": "integer"},
            },
            False,
            {},
        )


def _nested_configuration(filter_type: str, feature_type: str) -> dict:
    """Return a diagnostics projection with nested object and array children."""
    defs = {
        "effective": {
            "type": "object",
            "additionalProperties": False,
            "required": ["filter"],
            "properties": {"filter": {"type": filter_type, "enum": ["on", "off"]}},
        }
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["effective"],
        "properties": {
            "effective": {"$ref": "#/$defs/effective", "description": "doc"},
            "features": {
                "type": "array",
                "items": {"type": feature_type, "minLength": 1},
            },
        },
    }
    return _schema_field("configuration", schema, True, defs)


def test_schema_projection_records_nested_children():
    """Nested object properties and array items enter the contract."""
    children = {
        child["name"]: child
        for child in _nested_configuration("string", "string")["properties"]
    }

    assert children["effective"]["required_children"] == ["filter"]
    assert children["effective"]["additional_properties"] is False
    assert children["effective"]["properties"] == [
        {"name": "filter", "type": "string", "enum": ["off", "on"]}
    ]
    assert children["features"]["items_type"] == "string"
    assert children["features"]["items_constraints"] == {"minLength": 1}

    with pytest.raises(ContractError, match="nested array"):
        _schema_field(
            "matrix",
            {"type": "array", "items": {"type": "array", "items": {}}},
            False,
            {},
        )


def test_compare_reports_nested_child_type_changes():
    """A type change below a nested object or array child is BREAKING."""
    def surface(field):
        return {
            "schema_version": 3,
            "type": "object",
            "additional_properties": False,
            "required": ["configuration"],
            "fields": [field],
        }

    result = Comparison(baseline_label="a", candidate_label="b")
    compare_diagnostics(
        result,
        surface(_nested_configuration("string", "string")),
        surface(_nested_configuration("integer", "integer")),
    )
    verdicts = {finding.finding_id: finding.verdict for finding in result.findings}

    assert (
        verdicts["diagnostics:child-type-changed:configuration.effective:filter"]
        == BREAKING
    )
    assert verdicts["diagnostics:items-type-changed:configuration.features"] == BREAKING


def _array_child(child_schema: dict) -> dict:
    """Return a diagnostics projection with one nested array child."""
    return _schema_field(
        "build",
        {
            "type": "object",
            "additionalProperties": False,
            "required": [],
            "properties": {"features": child_schema},
        },
        True,
        {},
    )


def _compare_build(baseline: dict, candidate: dict) -> dict:
    """Compare two single-field diagnostics projections by finding id."""
    def surface(field):
        return {
            "schema_version": 3,
            "type": "object",
            "additional_properties": False,
            "required": ["build"],
            "fields": [field],
        }

    result = Comparison(baseline_label="a", candidate_label="b")
    compare_diagnostics(result, surface(baseline), surface(candidate))
    return {finding.finding_id: finding.verdict for finding in result.findings}


def test_nullable_array_children_keep_their_item_schema():
    """A type list that includes array still projects and compares its items."""
    baseline = _array_child({"type": ["array", "null"], "items": {"type": "string"}})
    candidate = _array_child(
        {"type": ["array", "null"], "items": {"type": "integer"}}
    )

    assert baseline["properties"][0]["items_type"] == "string"
    assert (
        _compare_build(baseline, candidate)[
            "diagnostics:items-type-changed:build.features"
        ]
        == BREAKING
    )


def test_array_item_enums_are_projected_and_compared():
    """Item enum changes reach findings, and unrestricted items stay open."""
    def child(enum=None):
        items = {"type": "string"}
        if enum is not None:
            items["enum"] = enum
        return _array_child({"type": "array", "items": items})

    unrestricted = child()
    both = child(["on", "off"])

    assert "items_enum" not in unrestricted["properties"][0]
    assert both["properties"][0]["items_enum"] == ["off", "on"]
    assert _compare_build(unrestricted, unrestricted) == {}
    assert _compare_build(unrestricted, both) == {
        "diagnostics:enum-added:build.features[]": BREAKING
    }
    assert _compare_build(both, unrestricted) == {
        "diagnostics:enum-removed:build.features[]": ADDITIVE
    }
    assert _compare_build(both, child(["on"])) == {
        "diagnostics:enum-narrowed:build.features[]": BREAKING
    }
    assert _compare_build(both, child(["on", "off", "auto"])) == {
        "diagnostics:enum-widened:build.features[]": ADDITIVE
    }


def _diagnostics_surface(*fields) -> dict:
    """Return a minimal diagnostics surface around the given fields."""
    return {
        "schema_version": 3,
        "type": "object",
        "additional_properties": False,
        "required": [],
        "fields": list(fields),
    }


def _diagnostics_verdicts(baseline: dict, candidate: dict) -> dict:
    """Compare two diagnostics surfaces and map finding ids to verdicts."""
    result = Comparison(baseline_label="a", candidate_label="b")
    compare_diagnostics(result, baseline, candidate)
    return {finding.finding_id: finding.verdict for finding in result.findings}


def test_enum_comparison_follows_json_schema_equality():
    """Enums compare by JSON value, including compound members."""
    def surface(top, child):
        return _diagnostics_surface(
            {"name": "state", "type": None, "nullable": False, "enum": top},
            {
                "name": "box",
                "type": "object",
                "nullable": False,
                "additional_properties": False,
                "required_children": [],
                "properties": [{"name": "mode", "type": None, "enum": child}],
            },
        )

    compound = [None, "on", 1, True, [1, 2], {"a": 1, "b": [None]}]
    reordered = [{"b": [None], "a": 1.0}, [1.0, 2], True, 1.0, "on", None]

    assert _diagnostics_verdicts(
        surface(compound, compound), surface(reordered, reordered)
    ) == {}
    assert _diagnostics_verdicts(surface([1], [1]), surface([True], [True])) == {
        "diagnostics:enum-narrowed:state": BREAKING,
        "diagnostics:enum-widened:state": ADDITIVE,
        "diagnostics:child-enum-narrowed:box:mode": BREAKING,
    }


def test_diagnostics_constraints_follow_json_schema_equality():
    """Constraints keep booleans distinct from numbers but not 1 from 1.0."""
    def surface(constraint):
        return _diagnostics_surface(
            {
                "name": "value",
                "type": None,
                "nullable": False,
                "constraints": constraint,
            }
        )

    assert _diagnostics_verdicts(surface({"const": True}), surface({"const": 1})) == {
        "diagnostics:schema-constraints-changed:value": REVIEW_REQUIRED
    }
    assert _diagnostics_verdicts(surface({"minimum": 1}), surface({"minimum": 1.0})) == {}


def test_schema_paths_distinguish_dotted_names_from_nesting():
    """Approving a nested finding leaves a dotted-name finding blocking."""
    def configuration(minimum):
        return {
            "name": "configuration",
            "type": "object",
            "nullable": False,
            "additional_properties": False,
            "required_children": [],
            "properties": [
                {"name": "a.b", "type": "integer", "constraints": {"minimum": minimum}},
                {
                    "name": "a",
                    "type": "object",
                    "additional_properties": False,
                    "required_children": [],
                    "properties": [
                        {"name": "b", "type": "integer", "constraints": {"minimum": minimum}}
                    ],
                },
            ],
        }

    flat = "diagnostics:schema-constraints-changed:configuration.a%2Eb"
    nested = "diagnostics:schema-constraints-changed:configuration.a.b"
    result = Comparison(baseline_label="a", candidate_label="b")
    compare_diagnostics(
        result,
        _diagnostics_surface(configuration(1)),
        _diagnostics_surface(configuration(2)),
    )
    by_id = {finding.finding_id: finding for finding in result.findings}
    assert sorted(by_id) == [flat, nested]

    apply_approvals(
        result,
        {
            "schema": 1,
            "exceptions": [
                {
                    "finding_id": nested,
                    "evidence_fingerprint": by_id[nested].fingerprint,
                    "reason": "reviewed nested constraint",
                    "approved_by": "maintainer",
                    "approved_at": "2026-11-24",
                }
            ],
        },
        "approvals.json",
    )
    assert by_id[nested].verdict == OK
    assert [finding.finding_id for finding in result.blocking()] == [flat]


def test_schema_one_of_rejects_unresolved_references():
    """A oneOf branch cannot record a raw reference instead of its target."""
    defs = {"sha": {"type": "string", "pattern": "^[0-9a-f]{40}$"}}
    for alternatives in (
        [{"$ref": "#/$defs/sha"}, {"const": "development"}],
        [{"type": "object", "properties": {"value": {"$ref": "#/$defs/sha"}}}],
    ):
        with pytest.raises(ContractError, match=r"\$ref"):
            _schema_field("source_sha", {"oneOf": alternatives}, True, defs)


def test_schema_structure_keywords_apply_without_a_type():
    """items, required, and additionalProperties project with type omitted."""
    values = _schema_field("values", {"items": {"type": "integer"}}, False, {})
    assert values["items_type"] == "integer"

    entry = _schema_field(
        "entry", {"required": ["id"], "additionalProperties": False}, False, {}
    )
    assert entry["required_children"] == ["id"]
    assert entry["additional_properties"] is False

    assert _diagnostics_verdicts(
        _diagnostics_surface(values),
        _diagnostics_surface(
            _schema_field("values", {"items": {"type": "string"}}, False, {})
        ),
    ) == {"diagnostics:items-type-changed:values": BREAKING}

    with pytest.raises(ContractError, match="nested array"):
        _schema_field("matrix", {"items": {"items": {}}}, False, {})


def test_nfpm_ghost_and_symlink_entries_are_not_shipped_modules():
    """Only file entries ship a module, and only RPM records a ghost."""
    contract = _nfpm_contract(
        """
name: sample
contents:
  - dst: /usr/lib/nginx/modules/module.so
  - dst: /usr/lib/nginx/modules/ghost.so
    type: ghost
  - src: /usr/lib/nginx/modules/module.so
    dst: /usr/lib/nginx/modules/link.so
    type: symlink
"""
    )

    assert contract["deb_module_path"] == ["/usr/lib/nginx/modules/module.so"]
    assert contract["rpm_module_path"] == ["/usr/lib/nginx/modules/module.so"]
    assert "/usr/lib/nginx/modules/ghost.so" not in contract["deb_install_paths"]
    assert "/usr/lib/nginx/modules/ghost.so" in contract["rpm_install_paths"]

    for content_type in ("tree", "config|tree", "unknown"):
        contents = (
            "name: sample\ncontents:\n  - dst: /usr/lib/nginx/modules\n"
            "    type: {}\n".format(json.dumps(content_type))
        )
        with pytest.raises(ContractError, match="unsupported type"):
            _nfpm_contract(contents)


def test_nfpm_load_snippets_are_recorded_per_package_format():
    """Snippet evidence comes from each format's own file entries."""
    contract = _nfpm_contract(
        """
name: sample
contents:
  - dst: /usr/share/nginx/modules-available/mod.conf
    type: config|noreplace
    packager: deb
  - dst: /usr/share/nginx/modules/mod.conf
    type: config|noreplace
    packager: rpm
  - dst: /usr/share/nginx/modules/legacy.conf
    type: dir
    packager: rpm
"""
    )

    assert contract["deb_load_snippet_paths"] == [
        "/usr/share/nginx/modules-available/mod.conf"
    ]
    assert contract["rpm_load_snippet_paths"] == ["/usr/share/nginx/modules/mod.conf"]
    assert "load_snippet_paths" not in contract


def test_fixture_directory_overlay_preserves_nested_symlinks(tmp_path):
    """A copied directory keeps nested links instead of their targets."""
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "real.txt").write_text("real", encoding="utf-8")
    os.symlink("real.txt", source / "nested" / "link.txt")

    _overlay_entry(source, target)

    assert (target / "nested" / "link.txt").is_symlink()
    assert os.readlink(target / "nested" / "link.txt") == "real.txt"
    assert (target / "nested" / "real.txt").read_text(encoding="utf-8") == "real"
