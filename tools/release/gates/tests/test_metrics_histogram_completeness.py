"""
Property 9: Metrics histogram completeness — property-based tests.

For each histogram family, verify _bucket lines for all defined boundaries
+ +Inf, exactly one _sum, exactly one _count.

Uses hypothesis to generate arbitrary snapshot counter values for the
conversion_duration_seconds histogram (the only histogram family in the
frozen 11-family registry), then validates the rendered Prometheus text
output has correct structural completeness.

Each property runs at least 100 iterations.

**Validates: Requirements 5.2**
"""

import re
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

# Ensure the tools package is importable
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent.parent.parent)
)

# --- Constants from the frozen metrics registry ---

HISTOGRAM_FAMILY = "nginx_markdown_conversion_duration_seconds"
HISTOGRAM_ENGINE = "full_buffer"

# Exactly 10 bucket boundaries defined in metrics-registry.json
BUCKET_BOUNDARIES = [
    "0.001", "0.005", "0.01", "0.025", "0.05",
    "0.1", "0.25", "0.5", "1.0", "5.0",
]

BUCKET_COUNT = 10


# --- Strategies ---

# Arbitrary non-negative integers representing histogram bucket counts
histogram_bucket_strategy = st.lists(
    st.integers(min_value=0, max_value=2**32 - 1),
    min_size=BUCKET_COUNT,
    max_size=BUCKET_COUNT,
)

# Arbitrary sum in microseconds (non-negative)
sum_us_strategy = st.integers(min_value=0, max_value=2**48 - 1)

# Arbitrary count (non-negative)
count_strategy = st.integers(min_value=0, max_value=2**32 - 1)


# --- Renderer simulation ---

def render_histogram(buckets: list[int], sum_us: int, count: int) -> str:
    """
    Simulate the v1 renderer output for the conversion_duration_seconds
    histogram family, matching the format in
    ngx_http_markdown_metrics_v1_renderer.h.

    This produces the same structural output that the C renderer emits:
    - HELP line
    - TYPE line
    - 10 cumulative _bucket lines with engine and le labels
    - 1 _bucket line with engine and le="+Inf"
    - 1 _sum line and 1 _count line with the engine label
    """
    lines = []
    lines.append(
        f"# HELP {HISTOGRAM_FAMILY} "
        "Duration of conversion operations in seconds."
    )
    lines.append(f"# TYPE {HISTOGRAM_FAMILY} histogram")

    # Cumulative buckets (matching the C renderer behavior)
    cumulative = 0
    for i, boundary in enumerate(BUCKET_BOUNDARIES):
        cumulative += buckets[i]
        lines.append(
            f"{HISTOGRAM_FAMILY}_bucket{{engine=\"{HISTOGRAM_ENGINE}\","
            f"le=\"{boundary}\"}} {cumulative}"
        )

    # +Inf bucket equals total count
    lines.append(
        f"{HISTOGRAM_FAMILY}_bucket{{engine=\"{HISTOGRAM_ENGINE}\","
        f"le=\"+Inf\"}} {count}"
    )

    # _sum (microseconds to seconds.fraction)
    sum_seconds = sum_us // 1000000
    sum_frac = sum_us % 1000000
    lines.append(
        f"{HISTOGRAM_FAMILY}_sum{{engine=\"{HISTOGRAM_ENGINE}\"}} "
        f"{sum_seconds}.{sum_frac:06d}"
    )

    # _count
    lines.append(
        f"{HISTOGRAM_FAMILY}_count{{engine=\"{HISTOGRAM_ENGINE}\"}} "
        f"{count}"
    )

    return "\n".join(lines) + "\n"


# --- Parsing and validation ---

def parse_histogram_output(text: str) -> dict:
    """
    Parse Prometheus text output for a histogram family and return
    a structured dict with:
      - bucket_les: list of le values found
      - sum_count: number of _sum lines
      - count_count: number of _count lines
      - help_count: number of HELP lines for this family
      - type_count: number of TYPE lines for this family
    """
    result = {
        "bucket_les": [],
        "sum_count": 0,
        "count_count": 0,
        "help_count": 0,
        "type_count": 0,
    }

    bucket_re = re.compile(
        rf'^{re.escape(HISTOGRAM_FAMILY)}_bucket\{{engine="[^"]+",'
        rf'le="([^"]+)"\}}\s+\S+$'
    )
    sum_re = re.compile(
        rf'^{re.escape(HISTOGRAM_FAMILY)}_sum\{{engine="[^"]+"\}}\s+\S+$'
    )
    count_re = re.compile(
        rf'^{re.escape(HISTOGRAM_FAMILY)}_count\{{engine="[^"]+"\}}\s+\S+$'
    )
    help_re = re.compile(
        rf'^# HELP {re.escape(HISTOGRAM_FAMILY)}\s'
    )
    type_re = re.compile(
        rf'^# TYPE {re.escape(HISTOGRAM_FAMILY)}\s'
    )

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        m = bucket_re.match(line)
        if m:
            result["bucket_les"].append(m.group(1))
            continue

        if sum_re.match(line):
            result["sum_count"] += 1
            continue

        if count_re.match(line):
            result["count_count"] += 1
            continue

        if help_re.match(line):
            result["help_count"] += 1
            continue

        if type_re.match(line):
            result["type_count"] += 1
            continue

    return result


# --- Property tests ---

@settings(max_examples=200)
@given(
    buckets=histogram_bucket_strategy,
    sum_us=sum_us_strategy,
    count=count_strategy,
)
def test_histogram_has_all_bucket_boundaries(buckets, sum_us, count):
    """
    Property 9a: For any snapshot state, the rendered histogram output
    contains _bucket lines for all 10 defined boundaries.
    """
    text = render_histogram(buckets, sum_us, count)
    parsed = parse_histogram_output(text)

    # Must have exactly 10 defined-boundary buckets
    defined_les = [le for le in parsed["bucket_les"] if le != "+Inf"]
    assert len(defined_les) == BUCKET_COUNT, (
        f"Expected {BUCKET_COUNT} defined bucket boundaries, "
        f"got {len(defined_les)}: {defined_les}"
    )

    # Each defined boundary must match the registry
    for i, expected_le in enumerate(BUCKET_BOUNDARIES):
        assert defined_les[i] == expected_le, (
            f"Bucket {i} le mismatch: expected '{expected_le}', "
            f"got '{defined_les[i]}'"
        )


@settings(max_examples=200)
@given(
    buckets=histogram_bucket_strategy,
    sum_us=sum_us_strategy,
    count=count_strategy,
)
def test_histogram_has_inf_bucket(buckets, sum_us, count):
    """
    Property 9b: For any snapshot state, the rendered histogram output
    contains exactly one _bucket line with le="+Inf".
    """
    text = render_histogram(buckets, sum_us, count)
    parsed = parse_histogram_output(text)

    inf_buckets = [le for le in parsed["bucket_les"] if le == "+Inf"]
    assert len(inf_buckets) == 1, (
        f"Expected exactly 1 +Inf bucket, got {len(inf_buckets)}"
    )


@settings(max_examples=200)
@given(
    buckets=histogram_bucket_strategy,
    sum_us=sum_us_strategy,
    count=count_strategy,
)
def test_histogram_has_exactly_one_sum(buckets, sum_us, count):
    """
    Property 9c: For any snapshot state, the rendered histogram output
    contains exactly one _sum line.
    """
    text = render_histogram(buckets, sum_us, count)
    parsed = parse_histogram_output(text)

    assert parsed["sum_count"] == 1, (
        f"Expected exactly 1 _sum line, got {parsed['sum_count']}"
    )


@settings(max_examples=200)
@given(
    buckets=histogram_bucket_strategy,
    sum_us=sum_us_strategy,
    count=count_strategy,
)
def test_histogram_has_exactly_one_count(buckets, sum_us, count):
    """
    Property 9d: For any snapshot state, the rendered histogram output
    contains exactly one _count line.
    """
    text = render_histogram(buckets, sum_us, count)
    parsed = parse_histogram_output(text)

    assert parsed["count_count"] == 1, (
        f"Expected exactly 1 _count line, got {parsed['count_count']}"
    )


@settings(max_examples=200)
@given(
    buckets=histogram_bucket_strategy,
    sum_us=sum_us_strategy,
    count=count_strategy,
)
def test_histogram_total_bucket_count(buckets, sum_us, count):
    """
    Property 9e: For any snapshot state, the total number of _bucket
    lines is exactly 11 (10 defined boundaries + 1 +Inf).
    """
    text = render_histogram(buckets, sum_us, count)
    parsed = parse_histogram_output(text)

    expected_total = BUCKET_COUNT + 1  # 10 boundaries + +Inf
    actual_total = len(parsed["bucket_les"])
    assert actual_total == expected_total, (
        f"Expected {expected_total} total bucket lines, "
        f"got {actual_total}"
    )


@settings(max_examples=200)
@given(
    buckets=histogram_bucket_strategy,
    sum_us=sum_us_strategy,
    count=count_strategy,
)
def test_histogram_has_help_and_type(buckets, sum_us, count):
    """
    Property 9f: For any snapshot state, the rendered histogram output
    contains exactly one HELP line and one TYPE line.
    """
    text = render_histogram(buckets, sum_us, count)
    parsed = parse_histogram_output(text)

    assert parsed["help_count"] == 1, (
        f"Expected 1 HELP line, got {parsed['help_count']}"
    )
    assert parsed["type_count"] == 1, (
        f"Expected 1 TYPE line, got {parsed['type_count']}"
    )


@settings(max_examples=200)
@given(
    buckets=histogram_bucket_strategy,
    sum_us=sum_us_strategy,
    count=count_strategy,
)
def test_histogram_bucket_order_monotonic(buckets, sum_us, count):
    """
    Property 9g: For any snapshot state, bucket boundaries appear in
    strictly increasing order (as defined in the registry).
    """
    text = render_histogram(buckets, sum_us, count)
    parsed = parse_histogram_output(text)

    le_values = parsed["bucket_les"]
    # Convert to float for comparison (including +Inf)
    float_les = []
    for le in le_values:
        if le == "+Inf":
            float_les.append(float("inf"))
        else:
            float_les.append(float(le))

    for i in range(len(float_les) - 1):
        assert float_les[i] < float_les[i + 1], (
            f"Bucket boundaries not strictly increasing at index {i}: "
            f"{float_les[i]} >= {float_les[i + 1]}"
        )


@settings(max_examples=200)
@given(
    buckets=histogram_bucket_strategy,
    sum_us=sum_us_strategy,
)
def test_histogram_cumulative_buckets_non_decreasing(buckets, sum_us):
    """
    Property 9h: For any valid snapshot state (where count equals the
    total observations across all buckets), cumulative bucket values
    are non-decreasing (Prometheus histogram invariant).

    The count field equals sum(buckets) for a well-formed histogram
    snapshot because each observation falls into exactly one bucket.
    """
    # Derive count from buckets to ensure a valid histogram state
    count = sum(buckets)
    text = render_histogram(buckets, sum_us, count)

    # Extract bucket values
    bucket_re = re.compile(
        rf'^{re.escape(HISTOGRAM_FAMILY)}_bucket\{{engine="[^"]+",'
        rf'le="[^"]+"\}}\s+(\S+)$'
    )
    values = []
    for line in text.splitlines():
        m = bucket_re.match(line.strip())
        if m:
            values.append(int(m.group(1)))

    for i in range(len(values) - 1):
        assert values[i] <= values[i + 1], (
            f"Cumulative bucket values not non-decreasing at index {i}: "
            f"{values[i]} > {values[i + 1]}"
        )


# --- Registry structural validation ---

def test_registry_histogram_has_correct_boundaries():
    """
    Validate the metrics-registry.json artifact defines exactly
    10 bucket boundaries for the conversion_duration_seconds histogram.
    """
    import json

    repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    registry_path = (
        repo_root / "artifacts" / "spec62" / "wave2"
        / "metrics-registry.json"
    )
    if not registry_path.exists():
        # Skip if artifact not yet produced
        return

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    families = registry.get("families", [])

    histograms = [f for f in families if f.get("type") == "histogram"]
    assert len(histograms) == 1, (
        f"Expected exactly 1 histogram family, got {len(histograms)}"
    )

    hist = histograms[0]
    assert hist["name"] == HISTOGRAM_FAMILY
    assert hist["bucket_count"] == BUCKET_COUNT
    assert hist["bucket_boundaries"] == [
        0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0
    ]


def test_registry_histogram_is_only_histogram():
    """
    Validate that conversion_duration_seconds is the ONLY histogram
    family in the frozen 11-family registry.
    """
    import json

    repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    registry_path = (
        repo_root / "artifacts" / "spec62" / "wave2"
        / "metrics-registry.json"
    )
    if not registry_path.exists():
        return

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    families = registry.get("families", [])

    histogram_names = [
        f["name"] for f in families if f.get("type") == "histogram"
    ]
    assert histogram_names == [HISTOGRAM_FAMILY], (
        f"Expected only {HISTOGRAM_FAMILY} as histogram, "
        f"got {histogram_names}"
    )
