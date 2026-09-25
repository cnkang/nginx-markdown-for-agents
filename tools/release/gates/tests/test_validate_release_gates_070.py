"""Regression tests for the v0.7.x release-gate validator.

The Gate 3 dependency-edge cases drive the validator from small YAML documents
instead of the raw workflow text, so each test states one structural rule the
check must respect: which job owns the ``needs`` list, which YAML spelling
carries it, and which near-miss names must not satisfy it.
"""

from __future__ import annotations

import ast
import sys

from tools.release.gates import validate_release_gates_070 as gates
from tools.release.gates.validate_release_gates_070 import _gate_2_items

TAG_CONDITION = (
    "(github.event_name == 'push' && github.ref_type == 'tag') "
    "|| github.event_name == 'workflow_dispatch'"
)


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


def _publish_gate_item(release_packages: str) -> bool:
    checks = gates._gate_3_items(release_packages)
    return dict(checks)["publish waits for release gate"]


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
    condition = gates._release_gate_if_condition(release_packages)
    assert condition is not None
    assert gates._release_gate_tag_condition_gate(release_packages)
    assert gates._publish_waits_for_release_gate(release_packages)


def test_publish_gate_requires_every_dependency_result_guard() -> None:
    """Each publish dependency must explicitly succeed; one guard is not enough."""
    workflow = gates.read(gates.RELEASE_PACKAGES_WORKFLOW)
    assert workflow
    assert _publish_gate_item(workflow)

    publish_prefix, publish_marker, publish_block = workflow.partition("  publish:\n")
    assert publish_marker
    required_success_guards = (
        "release-gate",
        "musl-build",
        "integrity-checksums",
        "official-docker-release-gate",
        "rc-release-gates",
        "fuzz-qualification",
    )
    for dependency in required_success_guards:
        guard = f"      && needs.{dependency}.result == 'success'\n"
        assert publish_block.count(guard) == 1, dependency
        mutant = publish_prefix + publish_marker + publish_block.replace(guard, "", 1)
        assert not _publish_gate_item(mutant), dependency


def test_publish_gate_requires_the_exact_dependency_set() -> None:
    """Missing and unguarded extra dependencies must fail closed."""
    workflow = gates.read(gates.RELEASE_PACKAGES_WORKFLOW)
    assert workflow
    assert _publish_gate_item(workflow)

    needs_line = (
        "    needs: [release-gate, musl-build, integrity-checksums, "
        "integrity-signature, official-docker-release-gate, "
        "rc-release-gates, fuzz-qualification]\n"
    )
    assert workflow.count(needs_line) == 1
    missing = workflow.replace(needs_line, needs_line.replace("musl-build, ", ""), 1)
    extra = workflow.replace(
        needs_line,
        needs_line.replace(
            "fuzz-qualification]", "fuzz-qualification, unverified-job]"
        ),
        1,
    )
    assert not _publish_gate_item(missing)
    assert not _publish_gate_item(extra)


def test_publish_gate_allows_a_skipped_signature_only_on_dispatch() -> None:
    """A skipped signer is the sole dispatch exception, never a tag exception."""
    workflow = gates.read(gates.RELEASE_PACKAGES_WORKFLOW)
    assert workflow
    assert _publish_gate_item(workflow)

    publish_prefix, publish_marker, publish_block = workflow.partition("  publish:\n")
    assert publish_marker
    dispatch_exception = "&& github.event_name == 'workflow_dispatch'))"
    assert publish_block.count(dispatch_exception) == 1
    tag_exception = publish_block.replace(
        dispatch_exception, "&& github.event_name == 'push'))", 1
    )
    mutant = publish_prefix + publish_marker + tag_exception
    assert not _publish_gate_item(mutant)


def test_gate_three_items_reads_the_job_if_structurally() -> None:
    """Only the release-gate job's own `if` may carry the tag predicate."""
    # The job condition is a quoted scalar: the parser resolves it, so the
    # predicate is matched even though the source text differs from the
    # unquoted spelling.
    quoted_condition = (
        _release_gate_job(
            "    needs: [prepare, smoke-test, fuzz-qualification]\n"
        ).replace(
            "    if: " + TAG_CONDITION,
            '    if: "github.event_name == \'push\' && ' + TAG_CONDITION + '"\n',
        )
    )
    assert _gate_3_item(quoted_condition)
    # Another job's condition cannot answer for release-gate.
    other_job = (
        "jobs:\n"
        "  prepare:\n    runs-on: ubuntu-24.04\n"
        "  smoke-test:\n    runs-on: ubuntu-24.04\n"
        "  fuzz-qualification:\n    runs-on: ubuntu-24.04\n"
        "  other-job:\n    if: " + TAG_CONDITION + "\n"
        "  release-gate:\n"
        "    needs: [prepare, smoke-test, fuzz-qualification]\n"
    )
    assert not _gate_3_item(other_job)
    # A scalar `if` that is not a condition (list, number) stays unreadable.
    for spelling in ("[a, b]", "42", "null", "~"):
        assert not _gate_3_item(
            _release_gate_job(
                "    needs: [prepare, smoke-test, fuzz-qualification]\n"
            ).replace("    if: " + TAG_CONDITION, f"    if: {spelling}\n")
        )


def test_gate_three_items_rejects_false_or_negated_tag_conditions() -> None:
    """A tag token cannot satisfy a condition that blocks tag-push execution."""
    invalid_conditions = (
        "false && github.ref_type == 'tag'",
        "github.ref_type != 'tag'",
        "github.ref_type == 'tag' || true",
        "github.event_name == 'workflow_dispatch' && github.ref_type == 'tag'",
        "!github.ref_type == 'tag'",
    )
    needs = "    needs: [prepare, smoke-test, fuzz-qualification]" + chr(10)
    for condition in invalid_conditions:
        workflow = _release_gate_job(needs).replace(
            "if: " + TAG_CONDITION, "if: " + condition
        )
        assert not _gate_3_item(workflow), condition


def test_github_negation_binds_tighter_than_comparison() -> None:
    """Negation AST shape matches Actions precedence; unknown operands fail closed."""
    unparenthesized = gates._github_condition_ast(
        "!github.ref_type != 'tag'"
    )
    assert isinstance(unparenthesized, ast.Compare)
    assert isinstance(unparenthesized.left, ast.UnaryOp)
    assert isinstance(unparenthesized.left.op, ast.Invert)
    assert gates._evaluate_tag_condition(
        unparenthesized, {"github.ref_type": "tag"}
    ) is None

    parenthesized = gates._github_condition_ast(
        "!(github.ref_type == 'tag')"
    )
    assert isinstance(parenthesized, ast.UnaryOp)
    assert isinstance(parenthesized.op, ast.Invert)
    assert gates._evaluate_tag_condition(
        parenthesized, {"github.ref_type": "tag"}
    ) is False
    assert gates._evaluate_tag_condition(
        parenthesized, {"github.ref_type": "branch"}
    ) is True


def test_gate_three_items_requires_the_manual_dispatch_path() -> None:
    """A tag-only condition must not silently drop workflow_dispatch runs."""
    needs = "    needs: [prepare, smoke-test, fuzz-qualification]\n"
    valid = _release_gate_job(needs)
    assert _gate_3_item(valid)
    condition_text = gates._release_gate_if_condition(valid)
    assert condition_text is not None
    condition = gates._github_condition_ast(condition_text)
    assert condition is not None
    assert gates._evaluate_tag_condition(
        condition,
        {"github.event_name": "workflow_dispatch", "github.ref_type": "branch"},
    ) is True

    tag_only = valid.replace(
        TAG_CONDITION,
        "github.event_name == 'push' && github.ref_type == 'tag'",
    )
    assert not _gate_3_item(tag_only)


def test_gate_three_items_rejects_condition_comment_decoy() -> None:
    """Comment text carrying both gate strings must not satisfy the gate.

    The fixture holds a correct `needs` list and mentions ``release-gate:``
    and ``github.ref_type == 'tag'`` only inside YAML comments, so the raw
    substring form of this check (the pre-fix behaviour) would accept it.
    """
    workflow = (
        "# release-gate: github.ref_type == 'tag'\n"
        "jobs:\n"
        "  prepare:\n    runs-on: ubuntu-24.04\n"
        "  smoke-test:\n    runs-on: ubuntu-24.04\n"
        "  fuzz-qualification:\n    runs-on: ubuntu-24.04\n"
        "  release-gate:\n"
        "    # github.ref_type == 'tag'\n"
        "    needs: [prepare, smoke-test, fuzz-qualification]\n"
    )
    # Mutation sensitivity: the retired substring check is satisfied here.
    assert "release-gate:" in workflow
    assert "github.ref_type == 'tag'" in workflow
    assert not _gate_3_item(workflow)
    assert gates._release_gate_if_condition(workflow) is None


def test_gate_three_items_rejects_commented_out_predicate_in_block_scalar() -> None:
    """A commented-out predicate inside the job `if` is still not a condition."""
    workflow = (
        "jobs:\n"
        "  prepare:\n    runs-on: ubuntu-24.04\n"
        "  smoke-test:\n    runs-on: ubuntu-24.04\n"
        "  fuzz-qualification:\n    runs-on: ubuntu-24.04\n"
        "  release-gate:\n"
        "    if: |\n"
        "      # github.ref_type == 'tag'\n"
        "      github.event_name == 'workflow_dispatch'\n"
        "    needs: [prepare, smoke-test, fuzz-qualification]\n"
    )
    assert not _gate_3_item(workflow)
    # The predicate itself survives inside a block scalar once it is real.
    live_block = workflow.replace(
        "# github.ref_type == 'tag'",
        "github.event_name == 'push' && github.ref_type == 'tag' ||",
    )
    assert _gate_3_item(live_block)


def test_publish_gate_reads_the_publish_job_dependency_and_condition() -> None:
    """A complete parsed needs set and success condition pass."""
    workflow = """
jobs:
  publish:
    needs: [release-gate, musl-build, integrity-checksums, integrity-signature,
            official-docker-release-gate, rc-release-gates, fuzz-qualification]
    if: >-
      always() &&
      needs.release-gate.result == 'success' &&
      needs.musl-build.result == 'success' &&
      needs.integrity-checksums.result == 'success' &&
      needs.official-docker-release-gate.result == 'success' &&
      needs.rc-release-gates.result == 'success' &&
      needs.fuzz-qualification.result == 'success' &&
      (needs.integrity-signature.result == 'success' ||
       (needs.integrity-signature.result == 'skipped' &&
        github.event_name == 'workflow_dispatch'))
"""
    assert _publish_gate_item(workflow)


def test_publish_gate_requires_signature_success_for_tag_publication() -> None:
    """The manual-dispatch skip exception cannot replace tag signing success."""
    workflow = """
jobs:
  publish:
    needs: [release-gate, musl-build, integrity-checksums, integrity-signature,
            official-docker-release-gate, rc-release-gates, fuzz-qualification]
    if: >-
      always() &&
      needs.release-gate.result == 'success' &&
      needs.musl-build.result == 'success' &&
      needs.integrity-checksums.result == 'success' &&
      needs.official-docker-release-gate.result == 'success' &&
      needs.rc-release-gates.result == 'success' &&
      needs.fuzz-qualification.result == 'success' &&
      (needs.integrity-signature.result == 'success' ||
       (needs.integrity-signature.result == 'skipped' &&
        github.event_name == 'workflow_dispatch'))
"""
    assert _publish_gate_item(workflow)

    missing_tag_success = workflow.replace(
        "(needs.integrity-signature.result == 'success' ||\n",
        "(false ||\n",
    )
    assert not _publish_gate_item(missing_tag_success)


def test_github_expression_parser_rejects_python_only_operators() -> None:
    """Python's `not` spelling and `and`/`or` words are outside GH grammar."""
    assert gates._github_condition_ast(
        "not (github.ref_type == 'tag')"
    ) is None
    assert gates._github_condition_ast(
        "github.ref_type == 'tag' or github.event_name == 'push'"
    ) is None
    assert gates._github_condition_ast(
        "github.ref_type == 'not tag'"
    ) is not None


def test_github_expression_parser_decodes_doubled_single_quotes() -> None:
    """GH's doubled apostrophe escape stays inside one string literal."""
    expression = gates._github_condition_ast(
        "github.ref_type == 'ta''g'"
    )
    assert isinstance(expression, ast.Compare)
    assert isinstance(expression.comparators[0], ast.Constant)
    assert expression.comparators[0].value == "ta'g"


def test_github_expression_parser_does_not_strip_block_scalar_hashes() -> None:
    """A hash in parsed block-scalar text cannot erase a failed predicate."""
    workflow = """
jobs:
  publish:
    needs: [release-gate, musl-build, integrity-checksums, integrity-signature,
            official-docker-release-gate, rc-release-gates, fuzz-qualification]
    if: >-
      always() &&
      needs.release-gate.result == 'success' &&
      needs.musl-build.result == 'success' &&
      needs.integrity-checksums.result == 'success' &&
      needs.integrity-signature.result == 'success' &&
      needs.official-docker-release-gate.result == 'success' &&
      needs.rc-release-gates.result == 'success' &&
      needs.fuzz-qualification.result == 'success' # && false
"""
    assert not _publish_gate_item(workflow)


def test_github_publish_condition_rejects_unmodeled_order_comparisons() -> None:
    """A Python-parsable but unsupported comparison remains unverifiable."""
    expression = gates._github_condition_ast(
        "needs.release-gate.result < 'success'"
    )
    assert isinstance(expression, ast.Compare)
    assert gates._evaluate_publish_condition(
        expression, {"needs.release_gate.result": "success"}
    ) is None


def test_publish_gate_rejects_comment_decoys_and_nested_or_success() -> None:
    """Comments and a success test nested in OR cannot establish the gate."""
    comment_decoy = """
# needs: [release-gate]
# if: always() && needs.release-gate.result == 'success'
jobs:
  publish:
    needs: [other-job]
    if: always()
"""
    assert not _publish_gate_item(comment_decoy)

    nested_or = """
jobs:
  publish:
    needs: [release-gate]
    if: always() && (needs.release-gate.result == 'success' || true)
"""
    assert not _publish_gate_item(nested_or)


def test_publish_gate_rejects_a_false_conjunct_after_gate_success() -> None:
    """A positive gate-success comparison cannot outweigh a false term."""
    conditions = (
        "always() && needs.release-gate.result == 'success' && false",
        "always() && (needs.release-gate.result == 'success' && false)",
    )
    for condition in conditions:
        workflow = f"""
jobs:
  publish:
    needs: [release-gate]
    if: {condition}
"""
        assert not _publish_gate_item(workflow), condition


def test_publish_condition_evaluator_handles_supported_ast_nodes() -> None:
    """The bounded evaluator covers calls, comparisons, and Boolean ops."""
    context = {"needs.release_gate.result": "success"}
    expression = gates._github_condition_ast(
        "needs.release-gate.result == 'success' && false"
    )
    assert isinstance(expression, ast.BoolOp)
    assert gates._evaluate_publish_boolean_operator(expression, context) is False
    assert isinstance(expression.values[0], ast.Compare)
    assert gates._evaluate_publish_comparison(expression.values[0], context) is True

    always_call = gates._github_condition_ast("always()")
    other_call = gates._github_condition_ast("success()")
    assert isinstance(always_call, ast.Call)
    assert isinstance(other_call, ast.Call)
    assert gates._is_always_condition_call(always_call)
    assert not gates._is_always_condition_call(other_call)

    negated_success = gates._github_condition_ast(
        "!(needs.release-gate.result == 'success')"
    )
    assert isinstance(negated_success, ast.UnaryOp)
    assert isinstance(negated_success.op, ast.Invert)
    assert gates._evaluate_publish_condition(
        negated_success, context
    ) is False
    assert gates._evaluate_publish_condition(
        negated_success, {"needs.release_gate.result": "failure"}
    ) is True


def test_release_workflow_dependency_diagnostic_names_missing_pyyaml(
    monkeypatch, capsys
) -> None:
    """A blocked PyYAML import must surface one named, actionable FAIL row."""
    result = gates.ValidationResult()
    gates.check_release_workflow_dependencies(result)
    assert result.results == []

    monkeypatch.setitem(sys.modules, "yaml", None)
    result = gates.ValidationResult()
    gates.check_release_workflow_dependencies(result)

    assert result.results == [
        ("FAIL", gates.RELEASE_GATE_PYYAML_GATE, gates.RELEASE_GATE_PYYAML_MESSAGE)
    ]
    assert result.has_failures
    assert "PyYAML" in gates.RELEASE_GATE_PYYAML_MESSAGE
    assert "requirements-release.txt" in gates.RELEASE_GATE_PYYAML_MESSAGE


def test_strict_run_fails_with_the_pyyaml_diagnostic(monkeypatch, capsys) -> None:
    """The strict CLI run reports the dependency row and exits nonzero."""
    monkeypatch.setitem(sys.modules, "yaml", None)
    monkeypatch.setattr(
        sys, "argv", ["validate_release_gates_070.py", "--mode", "strict"]
    )
    monkeypatch.setenv("RELEASE_GATE_EXPECTED_CARGO_VERSION", "0.9.2")

    rc = gates.main()

    assert rc == 1
    report = capsys.readouterr().out
    assert f"FAIL  {gates.RELEASE_GATE_PYYAML_GATE}" in report
    assert "requirements-release.txt" in report
    # The dependency row is the actionable cause; the structural checks that
    # need the parser fail closed alongside it rather than passing silently.
    assert "FAIL  Gate 3:tag package workflow gate" in report


def test_strict_run_parses_the_real_workflow_with_pyyaml() -> None:
    """Installed-PyYAML positive control: the real workflow passes strictly."""
    import yaml  # noqa: F401  (the control requires the parser)

    result = gates.ValidationResult()
    gates.check_release_workflow_dependencies(result)
    assert result.results == []

    release_packages = gates.read(gates.RELEASE_PACKAGES_WORKFLOW)
    checks = dict(gates._gate_3_items(release_packages))
    assert checks["tag package workflow gate"]
    assert checks["publish waits for release gate"]
