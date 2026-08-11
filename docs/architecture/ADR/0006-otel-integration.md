# ADR-0006: OpenTelemetry Integration Architecture

**Status**: Superseded by ADR-0027
**Date**: 2026-04-28
**Context**: v0.6.0 Production Readiness Release

> Historical proposal only. The 0.9.2 production contract includes no OTel
> subsystem, current implementation, or OTLP field contract. Its design
> statements below describe the superseded
> pre-freeze proposal.

## Context

v0.6.0 requires distributed tracing capability. The existing Prometheus-compatible metrics endpoint provides aggregated counters and gauges, but cannot trace per-request latency distribution across the conversion pipeline. Operators need to correlate conversion latency with upstream response time and downstream delivery to identify bottlenecks.

## Decision

Self-implement minimal OTLP HTTP/JSON span encoder within the NGINX C module. Do not introduce third-party OpenTelemetry SDK dependencies.

The proposal selected OTLP HTTP/JSON encoding. OTLP HTTP/protobuf was a future
consideration, but the current module shipped neither transport.

## Rationale

1. NGINX module C code operates under strict dependency constraints. Large third-party libraries (for example opentelemetry-c) add build complexity, version coupling, and ABI stability risk.
2. The proposed OTLP HTTP/JSON protocol was simple enough for manual span
   encoding. A proposed span would have required trace_id, span_id, parent_id,
   name, kind, start/end timestamps, and attributes. The project never adopted
   these details as a live compatibility contract.
3. Introducing an OTel SDK on the Rust side would add FFI boundary calls for span creation/export. This increases cross-language overhead and complexity.
4. The project's installation experience is zero-runtime-dependency. Adding an OTel SDK would break this.

## Consequences

- **Positive (historical)**: The proposal avoided new external dependencies.
- **Negative (superseded)**: No transport, span export, or semantic convention
  validation shipped.
- **Mitigation**: No mitigation is active. Any future implementation must satisfy
  ADR-0027 instead of extending this proposal.

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
