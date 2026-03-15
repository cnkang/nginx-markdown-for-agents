"""Property-based tests for report JSON roundtrip consistency and schema conformance.

Properties tested:
  - Property 4: Report JSON roundtrip consistency — exercises the real
    ``threshold_engine`` parse/serialize path (``load_json`` / ``_write_json``)
    on both generated and binary-produced reports.
  - Property 3: Report schema consistency (local vs CI reports share same schema).

Run:
    python3 -m pytest tools/perf/tests/test_report_roundtrip.py -v
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from threshold_engine import _write_json, load_json  # noqa: E402

# ---------------------------------------------------------------------------
# Schema constants (from perf/metrics-schema.json)
# ---------------------------------------------------------------------------

SAMPLE_TIERS = ["small", "medium", "medium-front-matter", "large-1m"]

CORE_METRICS = [
    "p50_ms", "p95_ms", "p99_ms",
    "peak_memory_bytes", "req_per_s", "input_mb_per_s",
]

STAGE_BREAKDOWN_KEYS = [
    "parse_pct", "convert_pct", "etag_pct", "token_pct",
]

VERDICT_VALUES = ["pass", "warn", "fail"]

# ---------------------------------------------------------------------------
# Hypothesis strategies for generating random report structures
# ---------------------------------------------------------------------------

metric_value = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)
pct_value = st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)
pos_int = st.integers(min_value=1, max_value=100000)


@st.composite
def tier_data(draw):
    """Generate a single tier's measurement data."""
    return {
        "html_bytes": draw(pos_int),
        "markdown_bytes_avg": draw(pos_int),
        "token_estimate_avg": draw(pos_int),
        "p50_ms": draw(metric_value),
        "p95_ms": draw(metric_value),
        "p99_ms": draw(metric_value),
        "peak_memory_bytes": draw(pos_int),
        "req_per_s": draw(metric_value),
        "input_mb_per_s": draw(metric_value),
        "stage_breakdown": {
            "parse_pct": draw(pct_value),
            "convert_pct": draw(pct_value),
            "etag_pct": draw(pct_value),
            "token_pct": draw(pct_value),
        },
        "iterations": draw(st.integers(min_value=1, max_value=10000)),
        "warmup": draw(st.integers(min_value=0, max_value=100)),
    }


@st.composite
def measurement_report(draw):
    """Generate a random Measurement Report."""
    tiers = {}
    for name in SAMPLE_TIERS:
        tiers[name] = draw(tier_data())
    return {
        "schema_version": "1.0.0",
        "report_type": "measurement",
        "timestamp": "2026-03-15T10:00:00Z",
        "git_commit": draw(st.text(alphabet="0123456789abcdef", min_size=7, max_size=40)),
        "platform": draw(st.sampled_from(["linux-x86_64", "darwin-arm64"])),
        "tiers": tiers,
    }


@st.composite
def verdict_comparison_metric(draw):
    """Generate a single metric comparison entry."""
    return {
        "baseline": draw(metric_value),
        "current": draw(metric_value),
        "deviation_pct": round(draw(st.floats(min_value=-100, max_value=500, allow_nan=False, allow_infinity=False)), 4),
        "verdict": draw(st.sampled_from(VERDICT_VALUES)),
    }


@st.composite
def verdict_report(draw):
    """Generate a random Verdict Report."""
    comparison_tiers = {}
    for tier_name in SAMPLE_TIERS:
        tier_comp = {}
        for metric in CORE_METRICS:
            tier_comp[metric] = draw(verdict_comparison_metric())
        comparison_tiers[tier_name] = tier_comp

    return {
        "schema_version": "1.0.0",
        "report_type": "verdict",
        "timestamp": "2026-03-15T11:00:00Z",
        "git_commit": draw(st.text(alphabet="0123456789abcdef", min_size=7, max_size=40)),
        "platform": draw(st.sampled_from(["linux-x86_64", "darwin-arm64"])),
        "overall_verdict": draw(st.sampled_from(["pass", "warn", "fail", "skipped"])),
        "comparison": {
            "baseline_commit": draw(st.text(alphabet="0123456789abcdef", min_size=7, max_size=40)),
            "baseline_timestamp": "2026-03-14T10:00:00Z",
            "tiers": comparison_tiers,
        },
    }


# ---------------------------------------------------------------------------
# Property 4: Report JSON roundtrip via real threshold_engine I/O
# ---------------------------------------------------------------------------

@given(report=measurement_report())
@settings(max_examples=200)
def test_property4_measurement_report_roundtrip(report):
    """Write via _write_json → read via load_json → write again → read again.

    Exercises the real serialization/deserialization path used by the
    threshold engine, not bare json.dumps/json.loads.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        path_a = os.path.join(tmpdir, "measurement_a.json")
        path_b = os.path.join(tmpdir, "measurement_b.json")

        # First roundtrip: write → read
        _write_json(report, path_a)
        loaded_a = load_json(path_a)

        # Second roundtrip: write loaded data → read again
        _write_json(loaded_a, path_b)
        loaded_b = load_json(path_b)

        assert loaded_a == loaded_b, "Measurement Report roundtrip diverged after second write/read"

        # Byte-level stability: both files should be identical
        raw_a = Path(path_a).read_text(encoding="utf-8")
        raw_b = Path(path_b).read_text(encoding="utf-8")
        assert raw_a == raw_b, "Measurement Report serialization not byte-stable across roundtrips"


@given(report=verdict_report())
@settings(max_examples=200)
def test_property4_verdict_report_roundtrip(report):
    """Write via _write_json → read via load_json → write again → read again."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path_a = os.path.join(tmpdir, "verdict_a.json")
        path_b = os.path.join(tmpdir, "verdict_b.json")

        _write_json(report, path_a)
        loaded_a = load_json(path_a)

        _write_json(loaded_a, path_b)
        loaded_b = load_json(path_b)

        assert loaded_a == loaded_b, "Verdict Report roundtrip diverged after second write/read"

        raw_a = Path(path_a).read_text(encoding="utf-8")
        raw_b = Path(path_b).read_text(encoding="utf-8")
        assert raw_a == raw_b, "Verdict Report serialization not byte-stable across roundtrips"


# ---------------------------------------------------------------------------
# Property 4 (extended): Roundtrip on real binary-produced report
# ---------------------------------------------------------------------------

def _find_perf_binary():
    """Locate the perf_baseline binary (release preferred, then debug)."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    base = repo_root / "components" / "rust-converter" / "target"
    for profile in ("release", "debug"):
        candidate = base / profile / "examples" / "perf_baseline"
        if candidate.exists():
            return str(candidate), str(repo_root)
    return None, None


def test_property4_real_binary_measurement_roundtrip():
    """Roundtrip a Measurement Report produced by the real perf_baseline binary.

    Invokes the binary for the ``small`` tier, then runs the report through
    _write_json → load_json → _write_json → load_json and asserts stability.
    """
    binary, repo_root = _find_perf_binary()
    if binary is None:
        import pytest
        pytest.skip("perf_baseline binary not built; run cargo build --release --example perf_baseline")

    with tempfile.TemporaryDirectory() as tmpdir:
        original = os.path.join(tmpdir, "original.json")
        result = subprocess.run(
            [binary, "--single", "small", "--json-output", original],
            cwd=repo_root,
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"perf_baseline failed: {result.stderr.decode()}"
        )

        # First roundtrip
        loaded_a = load_json(original)
        path_b = os.path.join(tmpdir, "roundtrip_b.json")
        _write_json(loaded_a, path_b)
        loaded_b = load_json(path_b)

        assert loaded_a == loaded_b, "Real binary report diverged after roundtrip"

        # Second roundtrip for byte stability
        path_c = os.path.join(tmpdir, "roundtrip_c.json")
        _write_json(loaded_b, path_c)
        raw_b = Path(path_b).read_text(encoding="utf-8")
        raw_c = Path(path_c).read_text(encoding="utf-8")
        assert raw_b == raw_c, "Real binary report serialization not byte-stable"


# ---------------------------------------------------------------------------
# Property 3: Report schema consistency (local and CI share same structure)
# ---------------------------------------------------------------------------

MEASUREMENT_REQUIRED_TOP_KEYS = {
    "schema_version", "report_type", "timestamp", "git_commit", "platform", "tiers",
}

MEASUREMENT_TIER_REQUIRED_KEYS = {
    "html_bytes", "markdown_bytes_avg", "token_estimate_avg",
    "p50_ms", "p95_ms", "p99_ms", "peak_memory_bytes",
    "req_per_s", "input_mb_per_s", "stage_breakdown",
    "iterations", "warmup",
}

VERDICT_REQUIRED_TOP_KEYS = {
    "schema_version", "report_type", "timestamp", "git_commit", "platform",
    "overall_verdict", "comparison",
}

VERDICT_COMPARISON_REQUIRED_KEYS = {
    "baseline_commit", "baseline_timestamp", "tiers",
}

VERDICT_METRIC_REQUIRED_KEYS = {
    "baseline", "current", "deviation_pct", "verdict",
}


@given(report=measurement_report())
@settings(max_examples=100)
def test_property3_measurement_schema_conformance(report):
    """Every generated Measurement Report has the required schema structure."""
    assert set(report.keys()) >= MEASUREMENT_REQUIRED_TOP_KEYS
    assert report["report_type"] == "measurement"
    for tier_name in SAMPLE_TIERS:
        assert tier_name in report["tiers"], f"Missing tier: {tier_name}"
        tier = report["tiers"][tier_name]
        assert set(tier.keys()) >= MEASUREMENT_TIER_REQUIRED_KEYS
        assert set(tier["stage_breakdown"].keys()) >= set(STAGE_BREAKDOWN_KEYS)


@given(report=verdict_report())
@settings(max_examples=100)
def test_property3_verdict_schema_conformance(report):
    """Every generated Verdict Report has the required schema structure."""
    assert set(report.keys()) >= VERDICT_REQUIRED_TOP_KEYS
    assert report["report_type"] == "verdict"
    comp = report["comparison"]
    assert set(comp.keys()) >= VERDICT_COMPARISON_REQUIRED_KEYS
    for tier_name in SAMPLE_TIERS:
        assert tier_name in comp["tiers"], f"Missing tier: {tier_name}"
        for metric in CORE_METRICS:
            assert metric in comp["tiers"][tier_name], f"Missing metric: {metric}"
            assert set(comp["tiers"][tier_name][metric].keys()) >= VERDICT_METRIC_REQUIRED_KEYS
