# ADR-0006: OpenTelemetry Integration Architecture

**Status**: Accepted
**Date**: 2026-04-28
**Context**: v0.6.0 Production Readiness Release

## Context

v0.6.0 requires distributed tracing capability. The existing Prometheus-compatible metrics endpoint provides aggregated counters and gauges, but cannot trace per-request latency distribution across the conversion pipeline. Operators need to correlate conversion latency with upstream response time and downstream delivery to identify bottlenecks.

## Decision

Self-implement minimal OTLP HTTP/protobuf span encoder within the NGINX C module. Do not introduce third-party OpenTelemetry SDK dependencies.

## Rationale

1. NGINX module C code operates under strict dependency constraints. Large third-party libraries (e.g., opentelemetry-c) add build complexity, version coupling, and ABI stability risk.
2. The OTLP HTTP protocol is simple enough for manual span encoding. A span requires: trace_id, span_id, parent_id, name, kind, start/end timestamps, and attributes — all fixed-format protobuf fields.
3. Introducing an OTel SDK on the Rust side would add FFI boundary calls for span creation/export, increasing cross-language overhead and complexity.
4. The project's installation experience is zero-runtime-dependency. Adding an OTel SDK would break this.

## Consequences

- **Positive**: No new external dependencies. Build remains self-contained. Span export latency is controllable (async HTTP POST via NGINX event-driven model).
- **Negative**: Only OTLP HTTP transport is supported (not gRPC). Protobuf encoding must be manually maintained. No automatic semantic convention validation.
- **Mitigation**: Protobuf message structure for ResourceSpans/ScopeSpans/Span is fixed and small (<500 lines C). gRPC support can be added later without breaking the HTTP path.

## Implementation Sketch

- New C files: `ngx_http_markdown_otel.c`, `ngx_http_markdown_otel.h`
- Per-worker lock-free ring buffer for span storage
- NGINX timer-based async HTTP POST to collector endpoint
- Configuration: `markdown_otel_tracing`, `markdown_otel_metrics`, `markdown_otel_endpoint`, `markdown_otel_service_name`, `markdown_otel_span_buffer_size`, `markdown_otel_export_timeout`
- Span attributes: `nginx.markdown.engine`, `nginx.markdown.result`, `nginx.markdown.reason_code`, `nginx.markdown.duration_ms`
