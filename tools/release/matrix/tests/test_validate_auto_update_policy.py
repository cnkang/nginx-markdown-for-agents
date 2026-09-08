"""Negative and positive tests for the NGINX auto-discovery policy."""

from __future__ import annotations

from tools.release.matrix.validate_auto_update_policy import validate_policy


def _matrix(*entries: dict) -> dict:
    return {"entries": list(entries)}


def _entry(**overrides) -> dict:
    entry = {
        "nginx_version": "1.32.0",
        "support_tier": "best-effort",
        "verification_state": "pending",
        "support_stage": "best-effort",
    }
    entry.update(overrides)
    return entry


def test_no_new_versions_is_a_clean_noop() -> None:
    assert validate_policy(_matrix(_entry(support_tier="supported")), {}) == []


def test_new_version_pending_best_effort_is_accepted() -> None:
    diff = {"added_versions": ["1.32.0"]}
    assert validate_policy(_matrix(_entry()), diff) == []


def test_new_version_supported_is_rejected() -> None:
    diff = {"added_versions": ["1.32.0"]}
    violations = validate_policy(
        _matrix(_entry(support_tier="supported")), diff
    )
    assert any("support_tier=best-effort" in item for item in violations)


def test_new_version_without_pending_state_is_rejected() -> None:
    diff = {"added_versions": ["1.32.0"]}
    violations = validate_policy(
        _matrix(_entry(verification_state="verified", support_stage="primary")),
        diff,
    )
    assert any("verification_state=pending" in item for item in violations)
    assert any("support_stage" in item for item in violations)


def test_malformed_diff_is_rejected() -> None:
    violations = validate_policy(_matrix(_entry()), {"added_versions": "1.32.0"})
    assert violations == ["matrix diff added_versions must be a list of non-empty strings"]
