"""Regression tests for the v0.7.x release-gate validator."""

from tools.release.gates.validate_release_gates_070 import _gate_2_items


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
    """`needs` order carries no meaning: every job may be listed any way.

    The boundary is exact: a prefixed name (`prepare-x`) or a longer
    sibling (`fuzz-qualification-record`) must not satisfy the list, and
    every required job must actually be present.
    """
    from tools.release.gates import validate_release_gates_070 as gates

    def item(text: str) -> bool:
        return [
            value
            for name, value in gates._gate_3_items(text)
            if name == "tag package workflow gate"
        ][0]

    header = "release-gate:\n  job:\n    if: github.ref_type == 'tag'\n"
    assert item(
        header + "    needs: [prepare, smoke-test, fuzz-qualification]\n"
    ) is True
    assert item(
        header + "    needs: [fuzz-qualification, smoke-test, prepare]\n"
    ) is True
    # A prefixed name must not stand in for the real job.
    assert item(
        header + "    needs: [prepare-x, smoke-test, fuzz-qualification]\n"
    ) is False
    # A longer sibling name must not stand in either.
    assert item(
        header + "    needs: [prepare, smoke-test, fuzz-qualification-record]\n"
    ) is False
    # Dropping the fuzz job from the list must fail the gate.
    assert item(header + "    needs: [prepare, smoke-test]\n") is False
    crossed = header + "    needs: [prepare]\nother:\n  needs: [smoke-test]\n"
    assert item(crossed) is False
