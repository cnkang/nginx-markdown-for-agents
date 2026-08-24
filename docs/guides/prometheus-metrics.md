# Prometheus Metrics Guide

This guide describes the 0.9.2 production metrics contract. The endpoint emits
only Prometheus text exposition format 0.0.4 and exactly the twelve families
listed below. The checked-in metrics registry is the machine-readable source
for names, types, labels, and help text. The public inventory documents the
operator-facing surface.

## Enable the endpoint

Configure the endpoint in a location block. The handler stays disabled unless
`markdown_metrics` is present and accepts only `GET` and `HEAD`.

```nginx
location = /markdown-metrics {
    markdown_metrics;
    allow 127.0.0.1;
    allow ::1;
    deny all;
}
```

The handler enforces loopback access itself. The `allow`/`deny` rules can
narrow access further but cannot broaden it. Scrapers should request:

```sh
curl --fail-with-body \
  -H 'Accept: text/plain; version=0.0.4' \
  http://127.0.0.1/markdown-metrics
```

`markdown_metrics_format` and the legacy JSON/plain-text negotiation are not
part of the 0.9.2 directive or wire contract.

## Frozen family catalog

All names use the `nginx_markdown_` prefix. Label names and values stay bounded.
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
| `nginx_markdown_streaming_peak_memory_bytes` | gauge | — | Peak working-set estimate from the most recent streaming conversion; not process RSS. |
| `nginx_markdown_streaming_events_total` | counter | `transition`, `reason` | Closed streaming lifecycle transitions. |
| `nginx_markdown_decompression_events_total` | counter | `encoding`, `outcome`, `reason` | Decompression completion and failure events. |
| `nginx_markdown_dynconf_reloads_total` | counter | `outcome`, `reason` | Dynamic-configuration reload attempts. |
| `nginx_markdown_build_info` | gauge | `version`, `nginx_version`, `features` | Build identity; value is always `1`. |

The engine values are `full_buffer` and `streaming`. The streaming transition
allowlist is `commit`, `fallback`, `safe_finish_start`, `abort_start`,
`resume_success`, and `resume_failure`. Decompression encodings are `gzip`,
`deflate`, and `brotli`. Its outcomes are `success` and `failure`. Dynconf
outcomes are `success` and `failure`.

The conversion histogram boundaries are `0.001`, `0.005`, `0.01`, `0.025`,
`0.05`, `0.1`, `0.25`, `0.5`, `1.0`, and `5.0` seconds, followed by `+Inf`.

The engine delivery series use direct counters: `full_buffer` counts successful
full-buffer terminal deliveries and `streaming` counts successful streaming
terminal deliveries. They are not derived by subtracting one engine from the
aggregate. In `streaming_events_total`, `commit` counts successful streaming
header commits, while `resume_success` counts successful downstream resume
drains after backpressure.

## Semantics and conservation

The frozen event model is:

1. Every request entering the decision chain receives exactly one terminal
   outcome.
2. A request starts at most one conversion attempt.
3. A successful attempt produces at most one request-level successful delivery.
4. `inflight_requests` returns to zero after quiescence.

Therefore, after the system is quiescent (no in-flight requests, so the
counters have stopped advancing), the conservation equations hold on
**deltas measured from one shared counter baseline** taken before the
observation window (for example the previous scrape):

```text
delta(sum(requests_total)) == requests entering the decision chain during the window
delta(sum(conversion_attempts_total)) <= delta(sum(requests_total))
delta(sum(conversion_deliveries_total)) <= delta(sum(conversion_attempts_total))
delta(conversion_duration_seconds_count) <= delta(sum(conversion_attempts_total))
inflight_requests == 0
```

Compare deltas from the same baseline for every family. Raw cumulative
values from different points in time are not comparable because the
counters never reset.

HTML passthrough, failed-open HTML, failed-closed responses, and abort-terminal
responses do not increment `conversion_deliveries_total`. They appear
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

The metrics endpoint accepts only loopback clients (`allow 127.0.0.1; allow ::1;
deny all;`), so the `targets: ['127.0.0.1:80']` example works only when
Prometheus shares NGINX's network namespace (same host or same pod). For remote
or separately containerized Prometheus, use a local agent, a sidecar, or a
loopback relay that forwards to the management listener instead of changing the
endpoint's loopback restriction.

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
The developer must refresh generated C and Rust artifacts with:

```sh
python3 tools/reason-codegen/generate.py
python3 tools/reason-codegen/generate.py --check
```

The Rust reason registry uses lowercase `snake_case` for operator-visible
request reasons, including `encoding_header_invalid` for malformed
`Content-Encoding` grammar. The C-only streaming transition labels
`ENGINE_STREAMING` and `STREAMING_CONVERT` are canonical uppercase values in
the streaming event/log surface. They are not Rust registry variants. Unknown
numeric reason codes map to `internal_unknown` and get logged as errors.

## Migration from earlier metric surfaces

The 0.9.2 freeze removes legacy conversion, passthrough, per-path, streaming
debug, JSON, and multi-format families. Update dashboards and alerts to the
twelve families above. Do not carry old family names into a
new 0.9.2 deployment. The detailed public compatibility inventory is
[`docs/architecture/PUBLIC_SURFACE_INVENTORY.md`](../architecture/PUBLIC_SURFACE_INVENTORY.md).

The four legacy decompression family names below merged into
`nginx_markdown_decompression_events_total` with the `reason` label.
Update any dashboard or alert that references a name in the left column.

| Legacy family name | Merged into |
|---|---|
| `nginx_markdown_decompression_budget_exceeded_total` | `nginx_markdown_decompression_events_total` |
| `nginx_markdown_decompression_format_error_total` | `nginx_markdown_decompression_events_total` |
| `nginx_markdown_decompression_truncated_input_total` | `nginx_markdown_decompression_events_total` |
| `nginx_markdown_decompression_io_error_total` | `nginx_markdown_decompression_events_total` |

The parse and replay families (`nginx_markdown_parse_timeouts_total`,
`nginx_markdown_parse_budget_exceeded_total`, and
`nginx_markdown_replay_buffer_errors_total`) no longer appear in the
Prometheus output. Remove dashboards and alerts that query them.

The 0.9.1 release renamed the conversion latency histogram to
`nginx_markdown_conversion_duration_seconds` with `_bucket`, `_sum`, and
`_count` suffixes. The legacy name `nginx_markdown_conversion_latency`
and its `_bucket_total` series are gone. Update dashboards and alerts
that reference the old name.

## Stability policy

The twelve-family set is frozen for 0.9.2. A future 1.x family addition
requires a documented operator use case and a backward-compatible schema
review. New labels must remain bounded and must not introduce path, URI, host,
or other unbounded cardinality.

| Version | Change |
|---|---|
| 0.9.2 | Replaced legacy multi-format metrics with the twelve-family Prometheus v1 contract. |
| 0.9.1 | Previous release-line metric migration guidance. |
