"""Regression tests for const-correctness detector token boundaries."""

from tools.harness import detect_const_correctness as detector


def test_mutator_tokens_do_not_match_inside_identifiers() -> None:
    for identifier in ("offset", "appendix", "freeze"):
        assert detector.INTENTIONAL_MUTATOR_RE.search(identifier) is None


def test_actual_mutator_tokens_still_match() -> None:
    for identifier in ("set_value", "append_node", "mark_header_reject"):
        assert detector.INTENTIONAL_MUTATOR_RE.search(identifier) is not None
