# ADR-0006: OpenTelemetry Integration Architecture

**Status**: Accepted
**Date**: 2026-04-28
**Context**: v0.6.0 Production Readiness Release

## Context

v0.6.0 requires distributed tracing capability. The existing Prometheus-compatible metrics endpoint provides aggregated counters and gauges, but cannot trace per-request latency distribution across the conversion pipeline. Operators need to correlate conversion latency with upstream response time and downstream delivery to identify bottlenecks.

## Decision

Self-implement minimal OTLP HTTP/JSON span encoder within the NGINX C module. Do not introduce third-party OpenTelemetry SDK dependencies.

The current implementation uses OTLP HTTP/JSON encoding. OTLP HTTP/protobuf is a future consideration that would reduce payload size and improve encoding efficiency. The team defers it until a protobuf schema maintenance burden proves justified by production scale.

## Rationale

1. NGINX module C code operates under strict dependency constraints. Large third-party libraries (for example opentelemetry-c) add build complexity, version coupling, and ABI stability risk.
2. The OTLP HTTP/JSON protocol is simple enough for manual span encoding. A span requires: trace_id, span_id, parent_id, name, kind, start/end timestamps, and attributes — all fixed-format JSON fields. JSON encoding avoids the complexity of protobuf wire format while remaining compatible with OTLP HTTP collectors.
3. Introducing an OTel SDK on the Rust side would add FFI boundary calls for span creation/export. This increases cross-language overhead and complexity.
4. The project's installation experience is zero-runtime-dependency. Adding an OTel SDK would break this.

## Consequences

- **Positive**: No new external dependencies. Build remains self-contained. Span export latency is controllable (async HTTP POST via NGINX event-driven model).
- **Negative**: Only OTLP HTTP transport works (not gRPC). JSON encoding produces larger payloads than protobuf. No automatic semantic convention validation.
- **Mitigation**: JSON message structure for ResourceSpans/ScopeSpans/Span stays fixed and small. The team can add protobuf encoding later without breaking the JSON HTTP path. It can add gRPC support later without breaking the HTTP path.

## Implementation Sketch

- New C files: `ngx_http_markdown_otel.c`, `ngx_http_markdown_otel.h`
- Request-scoped span storage. Ring-buffer and batch export defer until
  the OTel surface moves beyond its experimental status
- Nonblocking NGINX internal-subrequest HTTP POST to the configured endpoint
- The request pool owns span state and export subrequests. The module
  creates no worker-owned OTel thread, queue, timer, socket, or pending batch,
  so worker exit has no OTel cleanup or flush operation to perform.
- Configuration: `markdown_otel on|off` and
  `markdown_otel_endpoint <internal-uri>`
- Span attributes: `nginx.markdown.engine`, `nginx.markdown.result`, `nginx.markdown.reason_code`, `nginx.markdown.duration_ms`
