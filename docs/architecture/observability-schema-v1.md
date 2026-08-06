# Observability Contract v1 (Internal)

**Status**: repository-internal model for 0.9.2

This document records the ownership boundaries for the frozen observability
surfaces. It is not an external Rust SDK or an additional wire-schema source.

The authoritative production surfaces are:

- Diagnostics JSON: `components/nginx-module/src/ngx_http_markdown_diagnostics.c`,
  validated by `schemas/diagnostics.schema.json`.
- Prometheus text: `components/nginx-module/src/ngx_http_markdown_metrics_v1_renderer.h`,
  documented by [Prometheus Metrics](../guides/prometheus-metrics.md).
- Reason taxonomy: `components/rust-converter/reason_registry.toml`, with
  generated Rust/C accessors checked by `make reason-codegen-check`.
- The combined public inventory:
  [Public Surface Inventory](PUBLIC_SURFACE_INVENTORY.md).

Changes to these surfaces and their tests/documentation must remain
synchronized. Internal Rust helpers must not add undocumented operator fields,
metric families, labels, or configuration directives.

## Diagnostics Schema v1

The NGINX C renderer is the single implementation of the live diagnostics
endpoint. The response has exactly these seven top-level fields:

- `schema_version`: integer constant `1`
- `product_version`
- `worker`: `pid` and `scope="worker-local"`
- `build`: source SHA, NGINX/Rust versions, and feature list
- `configuration`: static digest, Dynconf state, effective values, and
  per-field sources
- `runtime`: worker-local `inflight` and `pending_output`
- `recent_decisions`: bounded worker-local decision entries

The schema rejects unknown fields and malformed types. The handler is
read-only: GET and HEAD are accepted, HEAD computes the complete body length
without sending a body, and other methods return 405. Native NGINX
allow/deny/auth directives can further restrict access; they cannot broaden
the handler's built-in internal access boundary.

Legacy `config_snapshot`, profile, streaming, duplicated metrics, and
rollback-mutation fields are not part of v1.

## Prometheus Metrics v1

The endpoint emits Prometheus text format 0.0.4 and exactly these twelve
families:

```text
nginx_markdown_requests_total
nginx_markdown_conversion_attempts_total
nginx_markdown_conversion_deliveries_total
nginx_markdown_conversion_duration_seconds
nginx_markdown_input_bytes_total
nginx_markdown_output_bytes_total
nginx_markdown_inflight_requests
nginx_markdown_streaming_events_total
nginx_markdown_decompression_events_total
nginx_markdown_dynconf_reloads_total
nginx_markdown_build_info
```

Labels are bounded allowlists. Raw paths, URIs, hosts, users, profiles, and
per-path dimensions are not emitted. Histogram rendering includes
`_bucket`, `_sum`, `_count`, and `+Inf`. Delivery and output-byte
counters advance only after downstream acceptance; request terminal outcomes
and inflight cleanup are accounted for independently.

## Internal Rust helpers and ABI

Rust metrics-label helpers, generated C headers, FFI structs/functions, and
numeric discriminants are internal implementation boundaries. They do not
constitute a supported third-party SDK or an append-only public ABI promise.
The C/Rust/header changeset and ABI handshake remain covered by the generated
header, layout, ownership, panic-containment, and feature-build checks.
