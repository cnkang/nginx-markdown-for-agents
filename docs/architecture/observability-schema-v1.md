# Rust Observability Model v1 (Internal)

**Status**: repository-internal model for 0.9.1

This document describes Rust-owned observability helpers. It is not the
operator-facing NGINX diagnostics or Prometheus wire contract.

Authoritative production surfaces are documented in
[Public Surface Inventory](PUBLIC_SURFACE_INVENTORY.md):

- the diagnostics JSON endpoint is rendered by
  `ngx_http_markdown_diagnostics_build_json()` in
  `components/nginx-module/src/ngx_http_markdown_diagnostics.c`;
- Prometheus text is rendered by the NGINX C module in
  `components/nginx-module/src/ngx_http_markdown_prometheus_impl.h`;
- the response-header contract is documented in the configuration and
  feature guides.

Changes to those C renderers and their public documentation must be made in
the same change set. Rust helpers must not be used to infer undocumented
production fields, metric families, or label rules.

## Reason Registry

`components/rust-converter/src/decision/reason_code.rs` owns the internal
`ReasonCode` registry. Each entry has a stable numeric discriminant for the
bundled C/Rust boundary, a snake-case string, a metric-category key, and a
documented decision-log call site.

The bundled NGINX module reads this registry through these internal FFI
accessors:

| Function | Purpose |
|----------|---------|
| `markdown_reason_code_str` | Return the snake-case reason string |
| `markdown_reason_code_metric_key` | Return the reason's internal metric-category key |
| `markdown_reason_code_count` | Return the registry size |

The metric-category key is metadata used by the internal reason model. It is
not a declaration that the production C renderer exposes exactly those metric
families or labels. The production inventory is the source of truth for that
wire surface.

## Rust Metrics Helpers

`components/rust-converter/src/metrics/labels.rs` defines a bounded label
policy for proposed Rust-owned metrics. Its four keys are `reason`, `profile`,
`path_mode`, and `cache_validation`; it also provides label normalization and
high-cardinality rejection helpers.

These helpers are not the production Prometheus label registry. In particular,
the C renderer has its own bounded labels and supports an explicitly enabled,
cardinality-capped `path` label. See
[Prometheus Metrics](../guides/prometheus-metrics.md) for the actual endpoint.

## Diagnostics contract ownership

The NGINX C renderer is the single implementation of the live diagnostics
endpoint. Its documented top-level sections are:

`schema_version` is the integer `1` for this contract.

- `schema_version`
- `config_snapshot`
- `recent_decisions`
- `metrics_snapshot`
- `streaming_metrics`
- `dynconf_state`
- `streaming_config`
- `profile`
- `overridden_fields`
- `forced_fields`
- `effective_config`

Both `streaming_config.on_error` and `effective_config.error_policy` preserve
the complete unified error-policy value: `pass`, `fail_closed`, `status 429`,
or `status 503`.

The former Rust default specimen and its two zero-consumer FFI accessors were
removed before the v1 freeze because they could drift without observing live
NGINX state. Tests for public endpoint changes assert the C response directly
and update this document in the same change.

## Compatibility

The Rust metrics helpers described above are repository-internal and are not a
supported external SDK.

After the public 1.0 observability freeze, production diagnostics and metrics
follow the compatibility policy recorded in the public surface inventory.
Internal Rust helpers remain subordinate to those operator-facing contracts.
