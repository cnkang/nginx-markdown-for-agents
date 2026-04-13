from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))

from harness import resolve_spec


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
