"""Unit tests for the immutable release-tag ruleset contract."""

from __future__ import annotations

from tools.release.gates.verify_tag_ref_protection import (
    REQUIRED_INCLUDE_PATTERN,
    _flatten_pages,
    _ruleset_protects_release_tags,
)


def _ruleset(**overrides: object) -> dict[str, object]:
    ruleset: dict[str, object] = {
        "target": "tag",
        "enforcement": "active",
        "conditions": {
            "ref_name": {"include": [REQUIRED_INCLUDE_PATTERN]},
        },
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
        ],
    }
    ruleset.update(overrides)
    return ruleset


def test_active_release_tag_ruleset_satisfies_contract() -> None:
    """The complete active v* tag ruleset is accepted."""
    assert _ruleset_protects_release_tags(_ruleset()) is True


def test_ruleset_requires_active_tag_target_and_exact_include() -> None:
    """Branch rulesets, inactive rulesets, and broad patterns do not qualify."""
    assert _ruleset_protects_release_tags(_ruleset(target="branch")) is False
    assert _ruleset_protects_release_tags(_ruleset(enforcement="disabled")) is False
    assert _ruleset_protects_release_tags(
        _ruleset(
            conditions={"ref_name": {"include": ["refs/tags/*"]}},
        )
    ) is False


def test_ruleset_requires_both_immutable_update_rules() -> None:
    """A tag rule that omits either deletion or update protection is rejected."""
    for rule_type in ("deletion", "non_fast_forward"):
        rules = [{"type": rule_type}]
        assert _ruleset_protects_release_tags(_ruleset(rules=rules)) is False


def test_flatten_pages_ignores_non_object_entries() -> None:
    """Paginated API responses are normalized without trusting malformed rows."""
    payload = [
        [{"id": 1}, "not-a-ruleset"],
        {"id": 2},
        "not-a-page",
    ]

    assert _flatten_pages(payload) == [{"id": 1}, {"id": 2}]
