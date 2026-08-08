# ADR-0027: OpenTelemetry Removal and Reintroduction Conditions

## Status

Accepted

## Date

2026-07-30

## Context

The nginx-markdown-for-agents module included experimental OpenTelemetry (OTel)
integration since v0.8.0. The implementation provided:

- Per-conversion span creation with W3C traceparent propagation
- Span attributes: flavor, engine, content_type, input/output bytes, reason_code
- OTLP/JSON export via NGINX subrequest to an internal endpoint
- Log-level diagnostic fallback when no collector endpoint existed

The OTel surface consisted of two active directives (`markdown_otel on|off` and
`markdown_otel_endpoint <uri>`) plus five reject-only directives for unimplemented
features (tracing, metrics, service_name, span_buffer_size, export_timeout).

As part of the 0.9.2 final pre-v1 breaking freeze, the 0.9.2 release removes
the OTel subsystem entirely because:

1. **Incomplete implementation**: The release implemented only basic span creation.
   Retry buffering, export timeouts, metrics export, and service-name override
   were never implemented (reject-only stubs).
2. **Unproven in production**: No production deployment validated the tracing
   path under load, backpressure, or failure conditions.
3. **Stability risk**: The 1.0 LTS compatibility contract (24-month minimum)
   would freeze an experimental, incomplete tracing API.
4. **Surface reduction**: Removing OTel reduces the 1.0 compatibility surface
   to only well-tested, production-proven subsystems.

## Decision

Remove all OTel implementation from the 0.9.2 release:

- Delete `markdown_otel` and `markdown_otel_endpoint` from the command table
- Delete all OTel implementation code (span creation, export, W3C parsing)
- Remove `otel_enabled` and `otel_endpoint` from the configuration structure
- Remove `otel_span` from the per-request context
- Remove all OTel instrumentation from the conversion and streaming request paths

## Reintroduction Conditions

OTel may return in a future release (1.1+) if ALL of the following
conditions are met:

1. **Stable upstream dependency**: An NGINX-native OTel module
   (e.g., `ngx_otel_module`) reaches stable release status, providing
   standardized span context propagation without per-module reimplementation.

2. **Complete feature scope**: The reintroduced implementation must cover at
   minimum: span creation, W3C context propagation, configurable export
   endpoint, export timeout, retry with bounded buffering, and graceful
   degradation on collector unavailability.

3. **Production validation**: The implementation must pass a minimum 7-day
   production soak test demonstrating: no memory leaks, no request-path
   latency regression (p99 < 1ms overhead), graceful behavior under collector
   outage, and correct span correlation across distributed traces.

4. **No request-path blocking**: OTel export must never block the NGINX event
   loop. Subrequest-based export or async buffered export are acceptable.
   Synchronous HTTP calls to collectors are not.

5. **Feature-gated**: The reintroduced OTel support must be behind a
   compile-time feature flag until the soak criteria above are met in the
   release candidate phase.

6. **Compatibility contract**: The reintroduced directive names and semantics
   must not conflict with any future NGINX-native OTel module. Prefer
   integration with `ngx_otel_module` spans over custom span creation.

## Consequences

- Operators using `markdown_otel on` will see an "unknown directive" error at
  `nginx -t` after upgrading to 0.9.2.
- No observability data loss: the module's built-in metrics endpoint
  (`markdown_metrics`) and diagnostics endpoint (`markdown_diagnostics`)
  continue to provide per-request decision visibility.
- Migration guidance appears in `docs/guides/MIGRATION-0.9.2.md` and
  the CHANGELOG.
