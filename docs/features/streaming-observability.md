# Streaming Observability

**Status**: 0.9.2 production contract

The frozen Prometheus and diagnostics contracts represent the 0.9.2 streaming
lifecycle. The former 0.8/0.9 streaming-specific metric families
and diagnostics sections are not emitted.

## Metrics

The `markdown_metrics` directive enables the endpoint, which emits Prometheus text
format 0.0.4. The complete family catalog lives in
[Prometheus Metrics](../guides/prometheus-metrics.md). Streaming-specific
observability comes through:

| Family | Labels | Meaning |
|---|---|---|
| `nginx_markdown_streaming_events_total` | `transition`, `reason` | Streaming lifecycle events, including start, completion, resume, and failure transitions. |
| `nginx_markdown_conversion_attempts_total` | `engine` | At-most-once conversion attempts. |
| `nginx_markdown_conversion_deliveries_total` | `engine` | Successful downstream delivery only. |
| `nginx_markdown_output_bytes_total` | none | Converted bytes accepted downstream. |
| `nginx_markdown_inflight_requests` | none | Requests still in the conversion pipeline. |
| `nginx_markdown_requests_total` | `outcome`, `stage`, `reason` | Exactly one terminal outcome per decision-chain request. |

The renderer emits a fixed transition allowlist: `commit`, `fallback`,
`safe_finish_start`, `abort_start`, `resume_success`, and `resume_failure`.
The `reason` label is a fixed compile-time binding in the C streaming
renderer. It is not looked up dynamically from the registry at render time.
The internal C path-selection enum is not used for this family.

The table above is the production label inventory. Lifecycle events use the
`transition`/`reason` labels. The terminal request family uses the
`outcome`/`stage`/`reason` labels. The `engine` label is the sole intentional
label on the conversion-attempt and conversion-delivery families. Output bytes and
inflight requests are intentionally unlabelled to keep series cardinality
bounded. Rust helper-event names and enums are implementation details and do
not expand this Prometheus label contract.

The six series currently map to the following snapshot counters:

| Transition | Fixed reason | Snapshot source | Contract note |
|---|---|---|---|
| `commit` | `converted` | `streaming.commit_total` | Counts committed streaming responses. |
| `fallback` | `precommit_html_error` | `streaming.fallback_total` | Counts pre-commit fallback decisions. |
| `safe_finish_start` | `converted` | `streaming_failure_postcommit_safe_finish` | Counts entry into safe-finish handling, not converted deliveries. |
| `abort_start` | `streaming_mid_flight_error` | `streaming_failure_postcommit_abort` | Counts protocol-safe abort attempts. |
| `resume_success` | `converted` | `perf.backpressure_resume_total` | Counts successful downstream resumes after backpressure. |
| `resume_failure` | `streaming_mid_flight_error` | `streaming.failed_total` | Independent from `abort_start`; it is not an abort counter. |

Raw paths, URIs, hosts, users, and profile names are never emitted.

The counters follow these conservation rules:

- `conversion_attempts_total` increments at most once per request.
- `conversion_deliveries_total` and output bytes increment only after the
  downstream filter accepts the converted response.
- failed-open, failed-closed, terminal abort, and client-abort paths do not
  count as successful deliveries.
- `inflight_requests` returns to zero after cleanup and quiescence.
- `NGX_AGAIN` records pending work, not delivery.

## Diagnostics

The diagnostics handler returns the strict Schema v2 response documented in
[Observability Contract v2](../architecture/observability-schema-v2.md). It has
no streaming-only top-level section. The frozen families provide runtime visibility via
worker-local `runtime` counters, bounded `recent_decisions` entries, and the
optional `runtime.module_metrics` evidence counters. Those counters are read
directly by the benchmark harness. They are not inferred from engine labels.

The endpoint accepts only GET and HEAD. HEAD computes the complete response length but
sends no body. Other methods return 405. Native NGINX allow/deny/auth
directives can narrow access further but cannot broaden the handler's built-in
loopback-only boundary.

## Decision reasons and troubleshooting

Streaming decisions use the canonical
`components/rust-converter/reason_registry.toml` registry. Operator-visible
keys are lowercase snake_case. Unknown numeric values map to
`internal_unknown` and get logged as an error.

When fallback or resume-failure events increase:

1. Inspect the `reason` label and corresponding decision log entry.
2. Check cache-validation, content-encoding, content-type, and configured
   `markdown_limits` values.
3. Verify that the downstream filter is not applying sustained backpressure.
4. Keep `markdown_error_policy` explicit when fail-open versus fail-closed
   behavior matters.

See [Streaming Compatibility](STREAMING_COMPATIBILITY.md) for the supported
request-path behavior and [Prometheus Metrics](../guides/prometheus-metrics.md)
for the full family and histogram contract.
