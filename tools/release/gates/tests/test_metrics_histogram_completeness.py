"""Validate the histogram contract against the production C renderer."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tools.release.gates.generate_schema_artifacts import DEFAULT_VERSION

REPO_ROOT = Path(__file__).resolve().parents[4]
METRICS_CONTRACT = json.loads(
    (REPO_ROOT / "schemas" / "metrics-v1.registry.json").read_text(
        encoding="utf-8"
    )
)
RENDERER_PATH = (
    REPO_ROOT
    / "components/nginx-module/src/ngx_http_markdown_metrics_v1_renderer.h"
)
HISTOGRAM = next(
    (
        family
        for family in METRICS_CONTRACT["families"]
        if family["type"] == "histogram"
    ),
    None,
)
if HISTOGRAM is None:
    raise AssertionError("metrics-v1 registry has no histogram family")
HISTOGRAM_FAMILY = HISTOGRAM["name"]


def _renderer_source() -> str:
    assert RENDERER_PATH.is_file(), f"production renderer missing: {RENDERER_PATH}"
    return RENDERER_PATH.read_text(encoding="utf-8")


def _histogram_function(source: str) -> str:
    start = source.find("ngx_http_markdown_metrics_v1_render_histogram(")
    assert start != -1, "production histogram renderer function is missing"
    # Walk forward from the signature balancing braces, so the extraction
    # does not depend on exact whitespace or a following static declaration.
    brace_depth = 0
    i = start
    while i < len(source):
        ch = source[i]
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth == 0:
                return source[start : i + 1]
        i += 1
    raise AssertionError("histogram renderer function body is unbalanced")


def _registry_artifact() -> dict:
    """Load the generated metrics registry artifact once per test call."""
    registry_path = (
        REPO_ROOT / "artifacts" / "release" / DEFAULT_VERSION
        / "metrics-registry.json"
    )
    if not registry_path.is_file():
        raise AssertionError(
            f"metrics registry artifact missing: {registry_path}"
        )
    return json.loads(registry_path.read_text(encoding="utf-8"))


def test_renderer_boundaries_match_registry() -> None:
    """The C renderer's actual bucket array must match the public registry."""
    source = _histogram_function(_renderer_source())
    match = re.search(
        r"static const char \*bucket_le\[.*?\]\s*=\s*\{(.*?)\};",
        source,
        flags=re.DOTALL,
    )
    assert match is not None, "renderer bucket boundary array is missing"
    bucket_body = match.group(1)
    boundaries = [float(v) for v in re.findall(r'"([0-9.]+)"', bucket_body)]
    assert boundaries == [float(value) for value in HISTOGRAM["bucket_boundaries"]]
    assert len(boundaries) == HISTOGRAM["bucket_count"]


def test_renderer_emits_complete_histogram_families() -> None:
    """The production renderer emits buckets, +Inf, sum, and count."""
    source = _renderer_source()
    function = _histogram_function(source)
    assert "cumulative += histogram->buckets[i]" in function
    assert "i < NGX_HTTP_MARKDOWN_METRICS_V1_BUCKET_COUNT" in function
    assert 'le=\\"+Inf\\"' in function
    assert f"{HISTOGRAM_FAMILY}_sum" in function
    assert f"{HISTOGRAM_FAMILY}_count" in function

    families = re.search(
        r"static u_char \*\s*ngx_http_markdown_metrics_v1_render_families_4_to_7\("
        r".*?(?=\n}\n\nstatic )",
        source,
        flags=re.DOTALL,
    )
    assert families is not None
    body = families.group(0)
    assert body.count("ngx_http_markdown_metrics_v1_render_histogram(") == 2
    assert '"full_buffer"' in body
    assert '"streaming"' in body


def test_v1_mapping_does_not_put_gt_1000ms_in_finite_5s_bucket() -> None:
    """The fifth-second bucket is finite; >1000ms belongs only in +Inf."""
    source = (REPO_ROOT / "components/nginx-module/src/ngx_http_markdown_metrics_impl.h").read_text(
        encoding="utf-8"
    )
    mapping = source[source.index("ngx_http_markdown_metrics_to_v1"):]
    # buckets[9] is the finite le=5s boundary (see the v1 histogram band
    # contract).  >1000ms latency must not be placed in a dedicated
    # >1000ms bucket; the renderer folds everything past the last finite
    # boundary into +Inf.  The positive assertions cover the legacy->v1
    # band copy (buckets[8]=le_1000ms, buckets[9]=le_5000ms).
    assert "duration_full_buffer.buckets[9]" not in mapping
    assert "duration_streaming.buckets[9]" not in mapping
    assert "destination->buckets[9] = source->le_5000ms;" in source
    assert "destination->buckets[8] = source->le_1000ms;" in source


def test_registry_histogram_has_correct_boundaries() -> None:
    registry = _registry_artifact()
    histograms = [
        family for family in registry.get("families", [])
        if family.get("type") == "histogram"
    ]
    assert len(histograms) == 1
    assert histograms[0]["name"] == HISTOGRAM_FAMILY
    assert histograms[0]["bucket_count"] == HISTOGRAM["bucket_count"]
    assert histograms[0]["bucket_boundaries"] == HISTOGRAM["bucket_boundaries"]


def test_registry_histogram_is_only_histogram() -> None:
    registry = _registry_artifact()
    names = [
        family["name"] for family in registry.get("families", [])
        if family.get("type") == "histogram"
    ]
    expected = [
        family["name"]
        for family in METRICS_CONTRACT["families"]
        if family["type"] == "histogram"
    ]
    assert names == expected
