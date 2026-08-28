"""Unit tests for the immutable release-tag ruleset contract."""

from __future__ import annotations

from tools.release.gates.verify_tag_ref_protection import (
    REQUIRED_INCLUDE_PATTERN,
    _flatten_pages,
    _repository_from_origin_url,
    _ruleset_protects_release_tags,
    _validate_repo,
)


def _ruleset(**overrides: object) -> dict[str, object]:
    ruleset: dict[str, object] = {
        "target": "tag",
        "enforcement": "active",
        "conditions": {
            "ref_name": {"include": [REQUIRED_INCLUDE_PATTERN]},
        },
        "bypass_actors": [],
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "update"},
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


def test_ruleset_rejects_bypass_actors() -> None:
    """Release tags cannot be mutable through a ruleset bypass."""
    assert _ruleset_protects_release_tags(
        _ruleset(bypass_actors=[{"actor_id": 1}])
    ) is False
    assert _ruleset_protects_release_tags(_ruleset(bypass_actors=None)) is False
    assert _ruleset_protects_release_tags(_ruleset_without_bypass_actors()) is False


def _ruleset_without_bypass_actors() -> dict[str, object]:
    ruleset = _ruleset()
    del ruleset["bypass_actors"]
    return ruleset


def test_ruleset_requires_both_immutable_update_rules() -> None:
    """A tag rule that omits any immutability rule is rejected."""
    for missing_type in ("deletion", "non_fast_forward", "update"):
        rules = [
            {"type": rule_type}
            for rule_type in ("deletion", "non_fast_forward", "update")
            if rule_type != missing_type
        ]
        assert _ruleset_protects_release_tags(_ruleset(rules=rules)) is False


def test_flatten_pages_ignores_non_object_entries() -> None:
    """Paginated API responses are normalized without trusting malformed rows."""
    payload = [
        [{"id": 1}, "not-a-ruleset"],
        {"id": 2},
        "not-a-page",
    ]

    assert _flatten_pages(payload) == [{"id": 1}, {"id": 2}]


def test_repository_argument_is_limited_to_one_safe_path_pair() -> None:
    """Repo input cannot add endpoint components or command-like syntax."""
    assert _validate_repo("cnkang/nginx-markdown-for-agents") == (
        "cnkang/nginx-markdown-for-agents"
    )

    for invalid_repo in (
        "cnkang/nginx-markdown-for-agents/rulesets",
        "cnkang/nginx-markdown-for-agents?x=1",
        "--paginate/evil",
        "cnkang/nginx markdown-for-agents",
    ):
        try:
            _validate_repo(invalid_repo)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted unsafe repository: {invalid_repo}")


def test_github_origin_urls_resolve_to_the_repository_pair() -> None:
    """Supported HTTPS and SSH remotes resolve without hardcoded defaults."""
    for remote_url in (
        "https://github.com/cnkang/nginx-markdown-for-agents.git",
        "git@github.com:cnkang/nginx-markdown-for-agents.git",
        "ssh://git@github.com/cnkang/nginx-markdown-for-agents",
    ):
        assert _repository_from_origin_url(remote_url) == (
            "cnkang/nginx-markdown-for-agents"
        )


def test_origin_url_rejects_non_github_or_ambiguous_paths() -> None:
    """Remote-derived data must not broaden the API path or host."""
    for remote_url in (
        "https://example.com/cnkang/nginx-markdown-for-agents.git",
        "https://github.com/cnkang/nginx-markdown-for-agents/rulesets.git",
        "https://github.com/cnkang/nginx-markdown-for-agents.git?query=1",
    ):
        try:
            _repository_from_origin_url(remote_url)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted unsafe origin URL: {remote_url}")
