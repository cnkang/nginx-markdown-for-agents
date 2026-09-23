"""Regression tests for the v0.7.x release-gate validator.

The Gate 3 dependency-edge cases drive the validator from small YAML documents
instead of the raw workflow text, so each test states one structural rule the
check must respect: which job owns the ``needs`` list, which YAML spelling
carries it, and which near-miss names must not satisfy it.
"""

from __future__ import annotations

import sys

from tools.release.gates import validate_release_gates_070 as gates
from tools.release.gates.validate_release_gates_070 import _gate_2_items

TAG_CONDITION = "github.ref_type == 'tag'"


def _gate_3_item(release_packages: str) -> bool:
    """The 'tag package workflow gate' verdict for a workflow document."""
    checks = gates._gate_3_items(release_packages)
    return dict(checks)["tag package workflow gate"]


def _release_gate_job(needs_block: str) -> str:
    """A minimal workflow whose release-gate job carries ``needs_block``."""
    upstream = (
        "  prepare:\n    runs-on: ubuntu-24.04\n"
        "  smoke-test:\n    runs-on: ubuntu-24.04\n"
        "  fuzz-qualification:\n    runs-on: ubuntu-24.04\n"
    )
    return (
        "jobs:\n"
        + upstream
        + "  release-gate:\n"
        + "    if: " + TAG_CONDITION + "\n"
        + needs_block
    )


def _reason_code_check(contract: str) -> bool:
    checks = _gate_2_items("", contract, "", "", "", "", "", "")
    return dict(checks)["reason code source"]


def test_reason_code_gate_accepts_canonical_ffi_contract():
    """The gate checks the documented source and its production exports."""

    contract = """
    components/rust-converter/src/decision/reason_code.rs
    markdown_reason_code_str
    markdown_reason_code_metric_key
    markdown_reason_code_count
    """

    assert _reason_code_check(contract)


def test_reason_code_gate_rejects_unrelated_reason_text():
    """A generic reason phrase must not satisfy the source contract gate."""

    assert not _reason_code_check("Error/reason is documented here")


def test_gate_three_items_accepts_needs_in_any_order() -> None:
    """`needs` order carries no meaning: the same set passes either way."""
    assert _gate_3_item(
        _release_gate_job("    needs: [prepare, smoke-test, fuzz-qualification]\n")
    )
    assert _gate_3_item(
        _release_gate_job("    needs: [fuzz-qualification, smoke-test, prepare]\n")
    )


def test_gate_three_items_accepts_block_sequence_needs() -> None:
    """A block-sequence `needs` is the same dependency edge as a flow list."""
    assert _gate_3_item(
        _release_gate_job(
            "    needs:\n"
            "      - prepare\n"
            "      - smoke-test\n"
            "      - fuzz-qualification\n"
        )
    )


def test_gate_three_items_accepts_quoted_needs_items() -> None:
    """Quoted job names (flow and block form) declare the same edges."""
    quoted_flow = (
        "    needs: ['prepare', \"smoke-test\", fuzz-qualification]\n"
    )
    quoted_block = (
        "    needs:\n"
        "      - 'prepare'\n"
        "      - \"smoke-test\"\n"
        "      - 'fuzz-qualification'\n"
    )
    assert _gate_3_item(_release_gate_job(quoted_flow))
    assert _gate_3_item(_release_gate_job(quoted_block))


def test_gate_three_items_rejects_prefixed_and_sibling_names() -> None:
    """Only exact job names satisfy the edge: prefixes and siblings do not."""
    for stand_in in ("prepare-x", "smoke-testify", "fuzz-qualification-record"):
        assert not _gate_3_item(
            _release_gate_job(
                f"    needs: [prepare, smoke-test, {stand_in}]\n"
            )
        )
    # A prefixed name must never answer for the real job it starts with.
    assert not _gate_3_item(
        _release_gate_job("    needs: [prepare-x, smoke-test, fuzz-qualification]\n")
    )
    # Each required job is load-bearing on its own.
    for missing in ("prepare", "smoke-test", "fuzz-qualification"):
        remaining = gates.RELEASE_GATE_REQUIRED_NEEDS - {missing}
        listed = ", ".join(sorted(remaining))
        assert not _gate_3_item(_release_gate_job(f"    needs: [{listed}]\n"))
    # Dropping the fuzz job from the list must fail the gate.
    assert not _gate_3_item(_release_gate_job("    needs: [prepare, smoke-test]\n"))


def test_gate_three_items_scopes_needs_to_the_release_gate_job() -> None:
    """Another job's dependency list must never answer for release-gate."""
    workflow = (
        "jobs:\n"
        "  prepare:\n    runs-on: ubuntu-24.04\n"
        "  other-job:\n    needs: [prepare, smoke-test, fuzz-qualification]\n"
        "  release-gate:\n"
        "    if: " + TAG_CONDITION + "\n"
        "    needs: [prepare, smoke-test]\n"
    )
    assert not _gate_3_item(workflow)


def test_gate_three_items_ignores_commented_out_needs() -> None:
    """A commented-out dependency list is not a dependency edge."""
    workflow = (
        "jobs:\n"
        "  prepare:\n    runs-on: ubuntu-24.04\n"
        "  smoke-test:\n    runs-on: ubuntu-24.04\n"
        "  fuzz-qualification:\n    runs-on: ubuntu-24.04\n"
        "  release-gate:\n"
        "    if: " + TAG_CONDITION + "\n"
        "    # needs: [prepare, smoke-test, fuzz-qualification]\n"
    )
    assert not _gate_3_item(workflow)


def test_gate_three_items_fails_closed_on_unreadable_workflow() -> None:
    """Malformed YAML, no jobs map, no release-gate job, scalar `needs`.

    Each fixture carries the gate's other preconditions as text, so only the
    dependency reading decides the verdict.
    """
    # A single scalar `needs` (``needs: prepare``) is a valid YAML spelling of
    # one dependency, never all three.
    assert not _gate_3_item(_release_gate_job("    needs: prepare\n"))
    # A list whose entries are not all job-name strings stays unreadable, so
    # non-string entries cannot be silently dropped from the comparison.
    assert not _gate_3_item(
        _release_gate_job("    needs: [prepare, smoke-test, 123]\n")
    )
    # Unterminated flow sequence: the document does not parse at all.
    assert not _gate_3_item(
        "release-gate:\nif: " + TAG_CONDITION + "\njobs: [\n"
    )
    # Parses, but there is no `jobs` mapping to read.
    assert not _gate_3_item("release-gate:\n  if: " + TAG_CONDITION + "\n")
    # Parses with a jobs mapping, but no release-gate job in it; the marker
    # and condition appear only as a comment, which cannot create the edge.
    assert not _gate_3_item(
        "# release-gate: if: " + TAG_CONDITION + "\n"
        "jobs:\n  prepare:\n    runs-on: ubuntu-24.04\n"
    )


def test_gate_three_items_resolves_aliased_needs() -> None:
    """A YAML alias carries the same dependency edge as the anchor it reuses."""
    workflow = (
        "jobs:\n"
        "  other-job:\n"
        "    needs: &shared [prepare, smoke-test, fuzz-qualification]\n"
        "  release-gate:\n"
        "    if: " + TAG_CONDITION + "\n"
        "    needs: *shared\n"
    )
    assert _gate_3_item(workflow)


def test_gate_three_items_fails_closed_without_pyyaml(monkeypatch) -> None:
    """An unimportable PyYAML leaves the edge unreadable, so the gate fails."""
    valid = _release_gate_job(
        "    needs: [prepare, smoke-test, fuzz-qualification]\n"
    )
    assert _gate_3_item(valid)
    monkeypatch.setitem(sys.modules, "yaml", None)
    assert not _gate_3_item(valid)
    assert gates._release_gate_needs(valid) is None


def test_gate_three_items_accepts_the_release_packages_workflow() -> None:
    """Positive control: the real workflow satisfies the gate as written."""
    release_packages = gates.read(gates.RELEASE_PACKAGES_WORKFLOW)
    assert release_packages
    assert _gate_3_item(release_packages)
    parsed = gates._release_gate_needs(release_packages)
    assert parsed is not None
    assert gates.RELEASE_GATE_REQUIRED_NEEDS <= parsed
