# Prometheus Metrics Guide

This guide describes the 0.9.2 production metrics contract. The endpoint emits
only Prometheus text exposition format 0.0.4 and exactly the eleven families
listed below. The checked-in metrics registry is the machine-readable source
for names, types, labels, and help text; the public inventory documents the
operator-facing surface.

## Enable the endpoint

Configure the endpoint in a location block. The handler is disabled unless
`markdown_metrics` is present and accepts only `GET` and `HEAD`.

```nginx
location = /markdown-metrics {
    markdown_metrics;
    allow 127.0.0.1;
    allow ::1;
    deny all;
}
```

The handler enforces loopback access itself; the `allow`/`deny` rules can
narrow access further but cannot broaden it. Scrapers should request:

```sh
curl --fail-with-body \
  -H 'Accept: text/plain; version=0.0.4' \
  http://127.0.0.1/markdown-metrics
```

`markdown_metrics_format` and the legacy JSON/plain-text negotiation are not
part of the 0.9.2 directive or wire contract.

## Frozen family catalog

All names use the `nginx_markdown_` prefix. Label names and values are bounded;
path, URI, host, profile, and per-path dimensions are not emitted.

| Family | Type | Labels | Meaning |
|---|---|---|---|
| `nginx_markdown_requests_total` | counter | `outcome`, `stage`, `reason` | One terminal outcome per request in the module decision chain. |
| `nginx_markdown_conversion_attempts_total` | counter | `engine` | Conversion engine selection committed and the request attempt latch changed from 0 to 1. |
| `nginx_markdown_conversion_deliveries_total` | counter | `engine` | Successful terminal delivery of converted Markdown only. |
| `nginx_markdown_conversion_duration_seconds` | histogram | `engine` | Conversion duration with ten fixed boundaries, `_bucket`, `_sum`, and `_count`. |
| `nginx_markdown_input_bytes_total` | counter | — | Input bytes read for conversion. |
| `nginx_markdown_output_bytes_total` | counter | — | Converted bytes successfully delivered downstream. |
| `nginx_markdown_inflight_requests` | gauge | — | Requests currently undergoing conversion. |
| `nginx_markdown_streaming_events_total` | counter | `transition`, `reason` | Closed streaming lifecycle transitions. |
| `nginx_markdown_decompression_events_total` | counter | `encoding`, `outcome`, `reason` | Decompression completion and failure events. |
| `nginx_markdown_dynconf_reloads_total` | counter | `outcome`, `reason` | Dynamic-configuration reload attempts. |
| `nginx_markdown_build_info` | gauge | `version`, `nginx_version`, `features` | Build identity; value is always `1`. |

The engine values are `full_buffer` and `streaming`. The streaming transition
allowlist is `commit`, `fallback`, `safe_finish_start`, `abort_start`,
`resume_success`, and `resume_failure`. Decompression encodings are `gzip`,
`deflate`, and `brotli`; its outcomes are `success` and `failure`. Dynconf
outcomes are `success` and `failure`.

The conversion histogram boundaries are `0.001`, `0.005`, `0.01`, `0.025`,
`0.05`, `0.1`, `0.25`, `0.5`, `1.0`, and `5.0` seconds, followed by `+Inf`.

## Semantics and conservation

The frozen event model is:

1. Every request entering the decision chain receives exactly one terminal
   outcome.
2. A request starts at most one conversion attempt.
3. A successful attempt produces at most one request-level successful delivery.
4. `inflight_requests` returns to zero after quiescence.

Therefore, after the system is quiescent:

```text
sum(requests_total) == requests entering the decision chain
sum(conversion_attempts_total) <= sum(requests_total)
sum(conversion_deliveries_total) <= sum(conversion_attempts_total)
conversion_duration_seconds_count <= sum(conversion_attempts_total)
inflight_requests == 0
```

HTML passthrough, failed-open HTML, failed-closed responses, and abort-terminal
responses do not increment `conversion_deliveries_total`. They are represented
by their terminal request outcome instead.

## Scrape configuration

```yaml
scrape_configs:
  - job_name: nginx-markdown
    scrape_interval: 15s
    metrics_path: /markdown-metrics
    static_configs:
      - targets: ['127.0.0.1:80']
```

Keep the endpoint on a management listener or behind an internal access
policy. Do not expose it directly to the public network.

## Verify the contract

From a repository checkout, the static and schema gates are:

```sh
python3 tools/release/gates/validate_metrics_registry.py
python3 tools/release/gates/validate_schema_drift.py
python3 tools/harness/detect_public_surface_drift.py
```

The C unit target `unit-prometheus_renderer` verifies HELP/TYPE lines,
histogram completeness, bounded labels, and known-value rendering. The
registry gate fails if a renderer family is missing or an unregistered family
appears.

## Reason-code labels

The canonical reason source is
[`components/rust-converter/reason_registry.toml`](../../components/rust-converter/reason_registry.toml).
Generated C and Rust artifacts must be refreshed with:

```sh
python3 tools/reason-codegen/generate.py
python3 tools/reason-codegen/generate.py --check
```

Operator-visible reason keys are lowercase `snake_case`, including
`encoding_header_invalid` for malformed `Content-Encoding` grammar. Unknown
numeric reason codes map to `internal_unknown` and are logged as errors.

## Migration from earlier metric surfaces

The 0.9.2 freeze removes legacy conversion, passthrough, per-path, streaming
debug, last-observed gauge, JSON, and multi-format families. Update dashboards
and alerts to the eleven families above; do not carry old family names into a
new 0.9.2 deployment. The detailed public compatibility inventory is
[`docs/architecture/PUBLIC_SURFACE_INVENTORY.md`](../architecture/PUBLIC_SURFACE_INVENTORY.md).

## Stability policy

The eleven-family set is frozen for 0.9.2. A future 1.x family addition
requires a documented operator use case and a backward-compatible schema
review. New labels must remain bounded and must not introduce path, URI, host,
or other unbounded cardinality.

| Version | Change |
|---|---|
| 0.9.2 | Replaced legacy multi-format metrics with the eleven-family Prometheus v1 contract. |
| 0.9.1 | Previous release-line metric migration guidance. |
