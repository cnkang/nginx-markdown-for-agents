"""Tests for the spec resolver.

Validates spec resolution via explicit names, active-spec pointers, and
free-text hints, including graceful handling of malformed pointers,
missing specs, and ambiguous hint matches.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.harness import resolve_spec


def test_resolve_returns_skip_when_specs_absent(tmp_path):
    result = resolve_spec.resolve_spec(specs_root=tmp_path / ".kiro" / "specs")
    assert result.status == resolve_spec.SKIP_NOT_PRESENT


def test_resolve_explicit_spec_match(tmp_path):
    specs_root = _write_specs(tmp_path)
    result = resolve_spec.resolve_spec(
        explicit_specs=["14-nginx-streaming-runtime-and-ffi"],
        specs_root=specs_root,
        pointer_candidates=[],
    )
    assert result.status == resolve_spec.PASS
    assert result.chosen["dir_name"] == "14-nginx-streaming-runtime-and-ffi"


def test_resolve_uses_active_pointer(tmp_path):
    specs_root = _write_specs(tmp_path)
    pointer = tmp_path / ".kiro" / "active-spec.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps({"spec": "streaming-performance-evidence-and-release-001"}),
        encoding="utf-8",
    )
    result = resolve_spec.resolve_spec(
        specs_root=specs_root,
        pointer_candidates=[pointer],
    )
    assert result.status == resolve_spec.PASS
    assert result.chosen["dir_name"] == "18-streaming-performance-evidence-and-release"


def test_resolve_warns_on_pointer_mismatch(tmp_path):
    specs_root = _write_specs(tmp_path)
    pointer = tmp_path / ".kiro" / "active-spec.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({"spec": "non-existent-spec"}), encoding="utf-8")

    result = resolve_spec.resolve_spec(
        specs_root=specs_root,
        pointer_candidates=[pointer],
    )

    assert result.status == resolve_spec.WARN_NEEDS_AUTHOR_REVIEW
    assert result.chosen is None
    candidate_dirs = {candidate["dir_name"] for candidate in result.candidates}
    assert candidate_dirs == {
        "14-nginx-streaming-runtime-and-ffi",
        "16-streaming-parity-diff-testing",
        "18-streaming-performance-evidence-and-release",
    }


def test_resolve_warns_on_explicit_spec_without_match(tmp_path):
    specs_root = _write_specs(tmp_path)
    result = resolve_spec.resolve_spec(
        explicit_specs=["non-existent-spec"],
        specs_root=specs_root,
        pointer_candidates=[],
    )
    assert result.status == resolve_spec.WARN_NEEDS_AUTHOR_REVIEW
    assert "explicit spec does not match any local spec" in result.reason
    assert "non-existent-spec" in result.reason
    assert result.chosen is None
    assert {candidate["dir_name"] for candidate in result.candidates} == {
        "14-nginx-streaming-runtime-and-ffi",
        "16-streaming-parity-diff-testing",
        "18-streaming-performance-evidence-and-release",
    }


def test_resolve_skips_malformed_pointer_json_and_uses_hints(tmp_path):
    specs_root = _write_specs(tmp_path)
    pointer = tmp_path / ".kiro" / "active-spec.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text("{not-json", encoding="utf-8")

    result = resolve_spec.resolve_spec(
        hints=["streaming parity diff"],
        specs_root=specs_root,
        pointer_candidates=[pointer],
    )

    assert result.status == resolve_spec.PASS
    assert result.chosen["dir_name"] == "16-streaming-parity-diff-testing"


def test_resolve_skips_malformed_config_files(tmp_path):
    specs_root = _write_specs(tmp_path)
    bad_dir = specs_root / "99-bad-spec"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / ".config.kiro").write_text("{bad-json", encoding="utf-8")

    result = resolve_spec.resolve_spec(
        hints=["streaming performance evidence"],
        specs_root=specs_root,
        pointer_candidates=[],
    )

    assert result.status == resolve_spec.PASS
    assert result.chosen["dir_name"] == "18-streaming-performance-evidence-and-release"


def test_resolve_best_hint_match(tmp_path):
    specs_root = _write_specs(tmp_path)
    result = resolve_spec.resolve_spec(
        hints=["work on streaming parity diff failure triage"],
        specs_root=specs_root,
        pointer_candidates=[],
    )
    assert result.status == resolve_spec.PASS
    assert result.chosen["dir_name"] == "16-streaming-parity-diff-testing"


def test_resolve_warns_when_hints_are_ambiguous(tmp_path):
    specs_root = _write_specs(tmp_path)
    result = resolve_spec.resolve_spec(
        hints=["continue streaming work"],
        specs_root=specs_root,
        pointer_candidates=[],
    )
    assert result.status == resolve_spec.WARN_NEEDS_AUTHOR_REVIEW
    assert len(result.candidates) > 1


def _write_specs(tmp_path: Path) -> Path:
    specs_root = tmp_path / ".kiro" / "specs"
    specs = {
        "14-nginx-streaming-runtime-and-ffi": "nginx-streaming-runtime-and-ffi-001",
        "16-streaming-parity-diff-testing": "streaming-parity-diff-testing-001",
        "18-streaming-performance-evidence-and-release": "streaming-performance-evidence-and-release-001",
    }
    for dir_name, spec_id in specs.items():
        spec_dir = specs_root / dir_name
        spec_dir.mkdir(parents=True, exist_ok=True)
        (spec_dir / ".config.kiro").write_text(
            json.dumps(
                {
                    "specId": spec_id,
                    "workflowType": "requirements-first",
                    "specType": "feature",
                }
            ),
            encoding="utf-8",
        )
    return specs_root
