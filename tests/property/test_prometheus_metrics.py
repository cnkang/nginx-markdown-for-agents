# Feature: prometheus-module-metrics — Property-based tests for Prometheus
# text exposition format renderer.
"""Property-based tests for the Prometheus metrics renderer.

These tests use a Python reference model of the Prometheus renderer and
verify properties of the output using the prometheus_client text parser.
The reference model must stay in sync with the C implementation in
ngx_http_markdown_prometheus_impl.h.

Feature: prometheus-module-metrics
Validates: Requirements 1.3, 6.2, 6.4, 7.2, 7.4, 12.1, 12.2, 12.3,
           12.4, 13.1, 13.2, 13.3, 13.4, 17.1, 17.2, 19.1, 19.3, 19.4

Run:
    python3 -m pytest tests/property/test_prometheus_metrics.py -v
"""

import re
from dataclasses import dataclass, field

import hypothesis.strategies as st
from hypothesis import given, settings
from prometheus_client.parser import text_string_to_metric_families


# -------------------------------------------------------------------
# Python model of the metrics snapshot
# -------------------------------------------------------------------

@dataclass
class DecompressionSnapshot:
    """Decompression sub-struct counters."""
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    gzip: int = 0
    deflate: int = 0
    brotli: int = 0


@dataclass
class SkipsSnapshot:
    """Skip counters by reason code."""
    config: int = 0
    method: int = 0
    status: int = 0
    content_type: int = 0
    size: int = 0
    streaming: int = 0
    auth: int = 0
    range: int = 0
    accept: int = 0


@dataclass
class MetricsSnapshot:
    """Mirrors ngx_http_markdown_metrics_snapshot_t."""
    conversions_attempted: int = 0
    conversions_succeeded: int = 0
    conversions_failed: int = 0
    conversions_bypassed: int = 0
    failures_conversion: int = 0
    failures_resource_limit: int = 0
    failures_system: int = 0
    conversion_time_sum_ms: int = 0
    input_bytes: int = 0
    output_bytes: int = 0
    conversion_latency_le_10ms: int = 0
    conversion_latency_le_100ms: int = 0
    conversion_latency_le_1000ms: int = 0
    conversion_latency_gt_1000ms: int = 0
    decompressions: DecompressionSnapshot = field(
        default_factory=DecompressionSnapshot
    )
    fullbuffer_path_hits: int = 0
    incremental_path_hits: int = 0
    requests_entered: int = 0
    skips: SkipsSnapshot = field(default_factory=SkipsSnapshot)
    failopen_count: int = 0
    estimated_token_savings: int = 0


# -------------------------------------------------------------------
# Python reference renderer (mirrors C implementation)
# -------------------------------------------------------------------

def render_prometheus(snap: MetricsSnapshot) -> str:
    """Render a MetricsSnapshot as Prometheus text exposition format.

    This is the Python reference model that must stay in sync with
    ngx_http_markdown_metrics_write_prometheus() in the C code.
    """
    lines = []

    def emit_family(name, typ, help_text, entries):
        """Emit HELP, TYPE, and all value lines for a family."""
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {typ}")
        for entry in entries:
            if entry[1] is None:
                lines.append(f"{name} {entry[0]}")
            else:
                lbl = ",".join(
                    f'{k}="{v}"' for k, v in entry[1].items()
                )
                lines.append(f"{name}{{{lbl}}} {entry[0]}")
        lines.append("")

    emit_family(
        "nginx_markdown_requests_total", "counter",
        "Total requests entering the module decision chain.",
        [(snap.requests_entered, None)],
    )

    emit_family(
        "nginx_markdown_conversions_total", "counter",
        "Successful HTML-to-Markdown conversions.",
        [(snap.conversions_succeeded, None)],
    )

    emit_family(
        "nginx_markdown_passthrough_total", "counter",
        "Requests not converted (skipped or failed-open).",
        [(snap.conversions_bypassed + snap.failopen_count,
          None)],
    )

    emit_family(
        "nginx_markdown_skips_total", "counter",
        "Requests skipped by reason.",
        [
            (snap.skips.method,
             {"reason": "SKIP_METHOD"}),
            (snap.skips.status,
             {"reason": "SKIP_STATUS"}),
            (snap.skips.content_type,
             {"reason": "SKIP_CONTENT_TYPE"}),
            (snap.skips.size,
             {"reason": "SKIP_SIZE"}),
            (snap.skips.streaming,
             {"reason": "SKIP_STREAMING"}),
            (snap.skips.auth,
             {"reason": "SKIP_AUTH"}),
            (snap.skips.range,
             {"reason": "SKIP_RANGE"}),
            (snap.skips.accept,
             {"reason": "SKIP_ACCEPT"}),
            (snap.skips.config,
             {"reason": "SKIP_CONFIG"}),
        ],
    )

    emit_family(
        "nginx_markdown_failures_total", "counter",
        "Conversion failures by stage.",
        [
            (snap.failures_conversion,
             {"stage": "FAIL_CONVERSION"}),
            (snap.failures_resource_limit,
             {"stage": "FAIL_RESOURCE_LIMIT"}),
            (snap.failures_system,
             {"stage": "FAIL_SYSTEM"}),
        ],
    )

    emit_family(
        "nginx_markdown_failopen_total", "counter",
        "Conversions failed with original HTML served "
        "(fail-open).",
        [(snap.failopen_count, None)],
    )

    emit_family(
        "nginx_markdown_large_response_path_total", "counter",
        "Requests routed to incremental processing path.",
        [(snap.incremental_path_hits, None)],
    )

    emit_family(
        "nginx_markdown_input_bytes_total", "counter",
        "Cumulative HTML input bytes from successful "
        "conversions.",
        [(snap.input_bytes, None)],
    )

    emit_family(
        "nginx_markdown_output_bytes_total", "counter",
        "Cumulative Markdown output bytes from successful "
        "conversions.",
        [(snap.output_bytes, None)],
    )

    emit_family(
        "nginx_markdown_estimated_token_savings_total",
        "counter",
        "Estimated cumulative token savings "
        "(requires markdown_token_estimate on).",
        [(snap.estimated_token_savings, None)],
    )

    emit_family(
        "nginx_markdown_decompressions_total", "counter",
        "Decompression operations by format.",
        [
            (snap.decompressions.gzip,
             {"format": "gzip"}),
            (snap.decompressions.deflate,
             {"format": "deflate"}),
            (snap.decompressions.brotli,
             {"format": "brotli"}),
        ],
    )

    emit_family(
        "nginx_markdown_decompression_failures_total",
        "counter",
        "Failed decompression attempts.",
        [(snap.decompressions.failed, None)],
    )

    le_10 = snap.conversion_latency_le_10ms
    le_100 = le_10 + snap.conversion_latency_le_100ms
    le_1000 = le_100 + snap.conversion_latency_le_1000ms
    le_inf = le_1000 + snap.conversion_latency_gt_1000ms

    emit_family(
        "nginx_markdown_conversion_duration_seconds", "gauge",
        "Cumulative conversion count per latency bucket "
        "(not a native Prometheus histogram; no _sum/_count).",
        [
            (le_10, {"le": "0.01"}),
            (le_100, {"le": "0.1"}),
            (le_1000, {"le": "1.0"}),
            (le_inf, {"le": "+Inf"}),
        ],
    )

    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------
# Hypothesis strategies
# -------------------------------------------------------------------

counter_value = st.integers(min_value=0, max_value=2**32 - 1)


@st.composite
def snapshot_strategy(draw):
    """Generate a random MetricsSnapshot with non-negative values."""
    return MetricsSnapshot(
        conversions_attempted=draw(counter_value),
        conversions_succeeded=draw(counter_value),
        conversions_failed=draw(counter_value),
        conversions_bypassed=draw(counter_value),
        failures_conversion=draw(counter_value),
        failures_resource_limit=draw(counter_value),
        failures_system=draw(counter_value),
        conversion_time_sum_ms=draw(counter_value),
        input_bytes=draw(counter_value),
        output_bytes=draw(counter_value),
        conversion_latency_le_10ms=draw(counter_value),
        conversion_latency_le_100ms=draw(counter_value),
        conversion_latency_le_1000ms=draw(counter_value),
        conversion_latency_gt_1000ms=draw(counter_value),
        decompressions=DecompressionSnapshot(
            attempted=draw(counter_value),
            succeeded=draw(counter_value),
            failed=draw(counter_value),
            gzip=draw(counter_value),
            deflate=draw(counter_value),
            brotli=draw(counter_value),
        ),
        fullbuffer_path_hits=draw(counter_value),
        incremental_path_hits=draw(counter_value),
        requests_entered=draw(counter_value),
        skips=SkipsSnapshot(
            config=draw(counter_value),
            method=draw(counter_value),
            status=draw(counter_value),
            content_type=draw(counter_value),
            size=draw(counter_value),
            streaming=draw(counter_value),
            auth=draw(counter_value),
            range=draw(counter_value),
            accept=draw(counter_value),
        ),
        failopen_count=draw(counter_value),
        estimated_token_savings=draw(counter_value),
    )


# -------------------------------------------------------------------
# Metric catalog constants
# -------------------------------------------------------------------

EXPECTED_FAMILIES = {
    "nginx_markdown_requests_total",
    "nginx_markdown_conversions_total",
    "nginx_markdown_passthrough_total",
    "nginx_markdown_skips_total",
    "nginx_markdown_failures_total",
    "nginx_markdown_failopen_total",
    "nginx_markdown_large_response_path_total",
    "nginx_markdown_input_bytes_total",
    "nginx_markdown_output_bytes_total",
    "nginx_markdown_estimated_token_savings_total",
    "nginx_markdown_decompressions_total",
    "nginx_markdown_decompression_failures_total",
    "nginx_markdown_conversion_duration_seconds",
}

ALLOWED_LABEL_KEYS = {"reason", "stage", "format", "le"}

FORBIDDEN_LABELS = {
    "path", "url", "host", "ua", "query",
    "referer", "remote_addr",
}

REASON_VALUES = {
    "SKIP_CONFIG", "SKIP_METHOD", "SKIP_STATUS",
    "SKIP_CONTENT_TYPE", "SKIP_SIZE", "SKIP_STREAMING",
    "SKIP_AUTH", "SKIP_RANGE", "SKIP_ACCEPT",
}

STAGE_VALUES = {
    "FAIL_CONVERSION", "FAIL_RESOURCE_LIMIT", "FAIL_SYSTEM",
}

FORMAT_VALUES = {"gzip", "deflate", "brotli"}

LE_VALUES = {"0.01", "0.1", "1.0", "+Inf"}

SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

EXPECTED_TIME_SERIES_COUNT = 28


# -------------------------------------------------------------------
# Helper: parse Prometheus text into a dict of metric values
# -------------------------------------------------------------------

def parse_prometheus(text):
    """Parse Prometheus text format into a dict.

    Returns a dict mapping (metric_name, frozenset(labels))
    to the numeric value.
    """
    result = {}
    families = list(text_string_to_metric_families(text))
    for family in families:
        for sample in family.samples:
            key = (
                sample.name,
                frozenset(sample.labels.items()),
            )
            result[key] = sample.value
    return result, families


# -------------------------------------------------------------------
# Property 1: Prometheus output round-trip
# -------------------------------------------------------------------
# **Validates: Requirements 19.4, 19.1, 19.3, 1.3**


@given(snap=snapshot_strategy())
@settings(max_examples=100)
def test_property1_round_trip(snap):
    """Property 1: Round-trip — generate random snapshots, render
    via reference model, parse with prometheus_client text parser,
    verify values match.

    **Validates: Requirements 19.4, 19.1, 19.3, 1.3**
    """
    text = render_prometheus(snap)
    parsed, _ = parse_prometheus(text)

    def check(name, expected, labels=None):
        lbl = frozenset(labels.items()) if labels else frozenset()
        key = (name, lbl)
        assert key in parsed, (
            f"Missing metric {name} with labels {labels}"
        )
        assert parsed[key] == float(expected), (
            f"{name}{labels or ''}: expected {expected}, "
            f"got {parsed[key]}"
        )

    check("nginx_markdown_requests_total",
          snap.requests_entered)
    check("nginx_markdown_conversions_total",
          snap.conversions_succeeded)
    check("nginx_markdown_passthrough_total",
          snap.conversions_bypassed + snap.failopen_count)

    for reason, val in [
        ("SKIP_METHOD", snap.skips.method),
        ("SKIP_STATUS", snap.skips.status),
        ("SKIP_CONTENT_TYPE", snap.skips.content_type),
        ("SKIP_SIZE", snap.skips.size),
        ("SKIP_STREAMING", snap.skips.streaming),
        ("SKIP_AUTH", snap.skips.auth),
        ("SKIP_RANGE", snap.skips.range),
        ("SKIP_ACCEPT", snap.skips.accept),
        ("SKIP_CONFIG", snap.skips.config),
    ]:
        check("nginx_markdown_skips_total", val,
              {"reason": reason})

    for stage, val in [
        ("FAIL_CONVERSION", snap.failures_conversion),
        ("FAIL_RESOURCE_LIMIT",
         snap.failures_resource_limit),
        ("FAIL_SYSTEM", snap.failures_system),
    ]:
        check("nginx_markdown_failures_total", val,
              {"stage": stage})

    check("nginx_markdown_failopen_total",
          snap.failopen_count)
    check("nginx_markdown_large_response_path_total",
          snap.incremental_path_hits)
    check("nginx_markdown_input_bytes_total",
          snap.input_bytes)
    check("nginx_markdown_output_bytes_total",
          snap.output_bytes)
    check("nginx_markdown_estimated_token_savings_total",
          snap.estimated_token_savings)

    for fmt, val in [
        ("gzip", snap.decompressions.gzip),
        ("deflate", snap.decompressions.deflate),
        ("brotli", snap.decompressions.brotli),
    ]:
        check("nginx_markdown_decompressions_total", val,
              {"format": fmt})

    check("nginx_markdown_decompression_failures_total",
          snap.decompressions.failed)

    le_10 = snap.conversion_latency_le_10ms
    le_100 = le_10 + snap.conversion_latency_le_100ms
    le_1000 = le_100 + snap.conversion_latency_le_1000ms
    le_inf = le_1000 + snap.conversion_latency_gt_1000ms

    for le_val, val in [
        ("0.01", le_10),
        ("0.1", le_100),
        ("1.0", le_1000),
        ("+Inf", le_inf),
    ]:
        check("nginx_markdown_conversion_duration_seconds",
              val, {"le": le_val})


# -------------------------------------------------------------------
# Property 2: Metric naming compliance
# -------------------------------------------------------------------
# **Validates: Requirements 12.1, 12.2, 12.3, 12.4**


@given(snap=snapshot_strategy())
@settings(max_examples=100)
def test_property2_metric_naming_compliance(snap):
    """Property 2: Metric naming compliance — all names start with
    nginx_markdown_, use snake_case, counter-typed end with _total.

    **Validates: Requirements 12.1, 12.2, 12.3, 12.4**

    Note: prometheus_client strips _total from family.name for
    counters, so we check sample.name instead for the _total
    suffix requirement.
    """
    text = render_prometheus(snap)
    _, families = parse_prometheus(text)

    for family in families:
        for sample in family.samples:
            name = sample.name
            assert name.startswith("nginx_markdown_"), (
                f"Metric '{name}' does not start with "
                f"nginx_markdown_"
            )
            assert SNAKE_CASE_RE.match(name), (
                f"Metric '{name}' is not snake_case"
            )
            if family.type == "counter":
                assert name.endswith("_total"), (
                    f"Counter metric '{name}' does not "
                    f"end with _total"
                )


# -------------------------------------------------------------------
# Property 3: Label boundary enforcement
# -------------------------------------------------------------------
# **Validates: Requirements 13.1, 13.2, 13.3, 6.2, 6.4, 7.2, 7.4,
#              17.1, 17.2**


@given(snap=snapshot_strategy())
@settings(max_examples=100)
def test_property3_label_boundary_enforcement(snap):
    """Property 3: Label boundary enforcement — all label keys in
    {reason, stage, format, le}, no forbidden labels, values in
    bounded sets.

    **Validates: Requirements 13.1, 13.2, 13.3, 6.2, 6.4, 7.2,
    7.4, 17.1, 17.2**
    """
    text = render_prometheus(snap)
    _, families = parse_prometheus(text)

    for family in families:
        for sample in family.samples:
            for key, value in sample.labels.items():
                assert key in ALLOWED_LABEL_KEYS, (
                    f"Forbidden label key '{key}' on "
                    f"metric '{sample.name}'"
                )
                assert key not in FORBIDDEN_LABELS, (
                    f"Forbidden label key '{key}'"
                )
                assert value not in FORBIDDEN_LABELS, (
                    f"Forbidden label value '{value}'"
                )

                if key == "reason":
                    assert value in REASON_VALUES, (
                        f"Unknown reason value '{value}'"
                    )
                elif key == "stage":
                    assert value in STAGE_VALUES, (
                        f"Unknown stage value '{value}'"
                    )
                elif key == "format":
                    assert value in FORMAT_VALUES, (
                        f"Unknown format value '{value}'"
                    )
                elif key == "le":
                    assert value in LE_VALUES, (
                        f"Unknown le value '{value}'"
                    )


# -------------------------------------------------------------------
# Property 6: HELP and TYPE annotation completeness
# -------------------------------------------------------------------
# **Validates: Requirements 1.3, 19.1**


@given(snap=snapshot_strategy())
@settings(max_examples=100)
def test_property6_help_and_type_completeness(snap):
    """Property 6: HELP and TYPE completeness — every metric family
    has exactly one HELP and one TYPE line.

    **Validates: Requirements 1.3, 19.1**
    """
    text = render_prometheus(snap)

    found_families = set()
    help_counts = {}
    type_counts = {}

    for line in text.splitlines():
        if line.startswith("# HELP "):
            parts = line.split(" ", 3)
            name = parts[2]
            found_families.add(name)
            help_counts[name] = help_counts.get(name, 0) + 1
        elif line.startswith("# TYPE "):
            parts = line.split(" ", 3)
            name = parts[2]
            type_counts[name] = type_counts.get(name, 0) + 1

    assert found_families == EXPECTED_FAMILIES, (
        f"Family mismatch.\n"
        f"Missing: {EXPECTED_FAMILIES - found_families}\n"
        f"Extra: {found_families - EXPECTED_FAMILIES}"
    )

    for name in EXPECTED_FAMILIES:
        assert help_counts.get(name, 0) == 1, (
            f"Expected exactly 1 HELP for '{name}', "
            f"got {help_counts.get(name, 0)}"
        )
        assert type_counts.get(name, 0) == 1, (
            f"Expected exactly 1 TYPE for '{name}', "
            f"got {type_counts.get(name, 0)}"
        )


# -------------------------------------------------------------------
# Property 7: Time series count is bounded
# -------------------------------------------------------------------
# **Validates: Requirements 13.4**


@given(snap=snapshot_strategy())
@settings(max_examples=100)
def test_property7_time_series_count_bounded(snap):
    """Property 7: Time series count is bounded — exactly 28 unique
    time series lines (9 unlabeled counters + 9 reason labels +
    3 stage labels + 3 format labels + 4 le buckets).

    **Validates: Requirements 13.4**
    """
    text = render_prometheus(snap)
    parsed, _ = parse_prometheus(text)

    assert len(parsed) == EXPECTED_TIME_SERIES_COUNT, (
        f"Expected {EXPECTED_TIME_SERIES_COUNT} unique time "
        f"series, got {len(parsed)}"
    )


# -------------------------------------------------------------------
# Property 5: Content negotiation selects correct format
# -------------------------------------------------------------------
# **Validates: Requirements 2.4, 2.3, 2.2**

OUTPUT_TEXT = 0
OUTPUT_JSON = 1
OUTPUT_PROMETHEUS = 2

METRICS_FORMAT_AUTO = 0
METRICS_FORMAT_PROMETHEUS = 1


def _prefers_prometheus(accept: str | None) -> bool:
    """Check if Accept explicitly requests Prometheus format.

    Matches application/openmetrics-text, or text/plain with
    version=0.0.4.  Bare version=0.0.4 without text/plain
    does not match (avoids false positives from unrelated
    media types).
    """
    if accept is None:
        return False
    lower = accept.lower()
    if "application/openmetrics-text" in lower:
        return True
    if "text/plain" in lower and "version=0.0.4" in lower:
        return True
    return False


def select_format(metrics_format: int, accept: str | None) -> int:
    """Python reference model of the content negotiation state
    machine from the design document.

    Mirrors ngx_http_markdown_metrics_select_format().
    """
    if accept is not None and "application/json" in accept.lower():
        return OUTPUT_JSON

    if (metrics_format == METRICS_FORMAT_PROMETHEUS
            and _prefers_prometheus(accept)):
        return OUTPUT_PROMETHEUS

    return OUTPUT_TEXT


# Strategy for Accept header values covering all branches
accept_header_strategy = st.one_of(
    st.just(None),
    st.just("application/json"),
    st.just("text/plain"),
    st.just("text/plain; version=0.0.4"),
    st.just("application/openmetrics-text"),
    st.just("*/*"),
    st.just("text/html"),
    st.just("application/xml; version=0.0.4"),
    st.just("version=0.0.4"),
    st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N", "P", "Z"),
            whitelist_characters="/;,=",
        ),
        min_size=0,
        max_size=100,
    ),
)

metrics_format_strategy = st.sampled_from(
    [METRICS_FORMAT_AUTO, METRICS_FORMAT_PROMETHEUS]
)


@given(
    metrics_format=metrics_format_strategy,
    accept=accept_header_strategy,
)
@settings(max_examples=200)
def test_property5_content_negotiation(metrics_format, accept):
    """Property 5: Content negotiation selects correct format.

    For any combination of metrics_format setting and Accept
    header value, the format selection matches the state machine:
    - JSON when Accept contains application/json
    - Prometheus when format is prometheus and Accept does not
      contain application/json
    - Plain text when format is auto and Accept does not
      contain application/json

    **Validates: Requirements 2.4, 2.3, 2.2**
    """
    result = select_format(metrics_format, accept)

    has_json = (
        accept is not None
        and "application/json" in accept.lower()
    )

    is_prom_accept = _prefers_prometheus(accept)

    if has_json:
        assert result == OUTPUT_JSON, (
            f"Expected JSON for Accept={accept!r}, "
            f"got {result}"
        )
    elif (metrics_format == METRICS_FORMAT_PROMETHEUS
          and is_prom_accept):
        assert result == OUTPUT_PROMETHEUS, (
            f"Expected PROMETHEUS for format=prometheus, "
            f"Accept={accept!r}, got {result}"
        )
    else:
        assert result == OUTPUT_TEXT, (
            f"Expected TEXT for format={metrics_format}, "
            f"Accept={accept!r}, got {result}"
        )

# -------------------------------------------------------------------
# Property 4: Counter accounting invariant
# -------------------------------------------------------------------
# **Validates: Requirements 3.1, 4.1, 5.1, 5.2, 8.1, 8.2**


@st.composite
def constrained_snapshot_strategy(draw):
    """Generate a MetricsSnapshot where the accounting invariants
    hold by construction.

    The invariants are:
    - passthrough_total == sum(skips_total) + failopen_total
    - requests_total >= conversions_total + passthrough_total
    - sum(failures_total) >= failopen_total

    We build the snapshot bottom-up so the invariants hold.
    """
    cv = st.integers(min_value=0, max_value=10000)

    skip_config = draw(cv)
    skip_method = draw(cv)
    skip_status = draw(cv)
    skip_content_type = draw(cv)
    skip_size = draw(cv)
    skip_streaming = draw(cv)
    skip_auth = draw(cv)
    skip_range = draw(cv)
    skip_accept = draw(cv)

    total_skips = (
        skip_config + skip_method + skip_status
        + skip_content_type + skip_size + skip_streaming
        + skip_auth + skip_range + skip_accept
    )

    failopen_count = draw(cv)

    #
    # passthrough_total is derived in the renderer as
    # conversions_bypassed + failopen_count.
    # conversions_bypassed tracks only skip-path requests
    # at runtime; the renderer adds failopen to produce
    # the spec-compliant passthrough value.
    #
    # passthrough = total_skips + failopen
    #
    passthrough = total_skips + failopen_count

    conversions_succeeded = draw(cv)

    #
    # requests_entered >= conversions + passthrough.
    # Add a non-negative surplus for in-flight requests.
    #
    surplus = draw(cv)
    requests_entered = conversions_succeeded + passthrough + surplus

    #
    # sum(failures) >= failopen.
    # failures_total includes both fail-open and fail-closed.
    #
    extra_failures = draw(cv)
    total_failures = failopen_count + extra_failures

    # Distribute failures across categories
    fail_conv = draw(
        st.integers(min_value=0, max_value=total_failures)
    )
    remaining = total_failures - fail_conv
    fail_res = draw(
        st.integers(min_value=0, max_value=remaining)
    )
    fail_sys = remaining - fail_res

    return MetricsSnapshot(
        conversions_attempted=draw(cv),
        conversions_succeeded=conversions_succeeded,
        conversions_failed=total_failures,
        conversions_bypassed=total_skips,
        failures_conversion=fail_conv,
        failures_resource_limit=fail_res,
        failures_system=fail_sys,
        conversion_time_sum_ms=draw(cv),
        input_bytes=draw(cv),
        output_bytes=draw(cv),
        conversion_latency_le_10ms=draw(cv),
        conversion_latency_le_100ms=draw(cv),
        conversion_latency_le_1000ms=draw(cv),
        conversion_latency_gt_1000ms=draw(cv),
        decompressions=DecompressionSnapshot(
            attempted=draw(cv),
            succeeded=draw(cv),
            failed=draw(cv),
            gzip=draw(cv),
            deflate=draw(cv),
            brotli=draw(cv),
        ),
        fullbuffer_path_hits=draw(cv),
        incremental_path_hits=draw(cv),
        requests_entered=requests_entered,
        skips=SkipsSnapshot(
            config=skip_config,
            method=skip_method,
            status=skip_status,
            content_type=skip_content_type,
            size=skip_size,
            streaming=skip_streaming,
            auth=skip_auth,
            range=skip_range,
            accept=skip_accept,
        ),
        failopen_count=failopen_count,
        estimated_token_savings=draw(cv),
    )


@given(snap=constrained_snapshot_strategy())
@settings(max_examples=200)
def test_property4_counter_accounting_invariant(snap):
    """Property 4: Counter accounting invariant.

    For constrained snapshots where the arithmetic invariants
    hold by construction, render to Prometheus, parse, and
    verify:
    - passthrough_total == sum(skips_total) + failopen_total
    - requests_total >= conversions_total + passthrough_total
    - sum(failures_total) >= failopen_total

    **Validates: Requirements 3.1, 4.1, 5.1, 5.2, 8.1, 8.2**
    """
    text = render_prometheus(snap)
    parsed, _ = parse_prometheus(text)

    def get(name, labels=None):
        lbl = frozenset(labels.items()) if labels else frozenset()
        return parsed[(name, lbl)]

    requests = get("nginx_markdown_requests_total")
    conversions = get("nginx_markdown_conversions_total")
    passthrough = get("nginx_markdown_passthrough_total")
    failopen = get("nginx_markdown_failopen_total")

    sum_skips = sum(
        get("nginx_markdown_skips_total", {"reason": r})
        for r in REASON_VALUES
    )

    sum_failures = sum(
        get("nginx_markdown_failures_total", {"stage": s})
        for s in STAGE_VALUES
    )

    assert passthrough == sum_skips + failopen, (
        f"passthrough ({passthrough}) != "
        f"sum(skips) ({sum_skips}) + "
        f"failopen ({failopen})"
    )

    assert requests >= conversions + passthrough, (
        f"requests ({requests}) < "
        f"conversions ({conversions}) + "
        f"passthrough ({passthrough})"
    )

    assert sum_failures >= failopen, (
        f"sum(failures) ({sum_failures}) < "
        f"failopen ({failopen})"
    )
