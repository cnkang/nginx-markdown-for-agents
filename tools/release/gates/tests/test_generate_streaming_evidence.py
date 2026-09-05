"""Tests for the machine-readable streaming evidence producer."""

from __future__ import annotations

import json

import pytest

from tools.release.gates.generate_streaming_evidence import MARKER, _parse_marker


def _marker(**overrides: object) -> str:
    payload: dict[str, object] = {
        "total_comparisons": 2,
        "identical_count": 1,
        "known_difference_count": 1,
        "unknown_difference_count": 0,
        "error_parity_mismatch_count": 0,
        "known_difference_ids": ["DIFF-001"],
        "known_difference_observation_ids": ["DIFF-001"],
    }
    payload.update(overrides)
    return MARKER + json.dumps(payload)


def test_parse_marker_accepts_harness_partition() -> None:
    assert _parse_marker(_marker())["known_difference_ids"] == ["DIFF-001"]


def test_parse_marker_rejects_repeated_observation_for_one_registry_id() -> None:
    output = _marker(
        total_comparisons=3,
        identical_count=1,
        known_difference_count=2,
        known_difference_observation_ids=["DIFF-001", "DIFF-001"],
    )
    with pytest.raises(ValueError):
        _parse_marker(output)


@pytest.mark.parametrize(
    "output",
    [
        "no marker",
        _marker() + "\n" + _marker(),
        _marker(known_difference_count=2),
        _marker(total_comparisons=1),
        _marker(known_difference_ids=[True]),
        _marker(known_difference_observation_ids=[True]),
        _marker(
            known_difference_count=2,
            known_difference_observation_ids=["DIFF-001"],
        ),
        _marker(
            known_difference_ids=["DIFF-001", "DIFF-001"],
            known_difference_count=2,
        ),
    ],
)
def test_parse_marker_rejects_untrusted_or_inconsistent_output(output: str) -> None:
    with pytest.raises(ValueError):
        _parse_marker(output)
