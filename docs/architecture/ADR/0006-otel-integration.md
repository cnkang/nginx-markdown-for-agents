# ADR-0006: OpenTelemetry Integration Architecture

**Status**: Superseded by ADR-0027
**Date**: 2026-04-28
**Context**: v0.6.0 Production Readiness Release

> Historical proposal only. The 0.9.2 production contract includes no OTel
> subsystem, current implementation, transport, or OTLP field contract. The
> sections below preserve the superseded pre-freeze discussion and are not
> implementation requirements.

## Context

The v0.6.0 planning context requested distributed tracing capability because
the Prometheus-compatible metrics endpoint did not provide per-request trace
correlation. This context explains why the proposal was written. It does not
describe a requirement for 0.9.2.

## Decision

The superseded proposal selected a minimal OTLP HTTP/JSON encoder in the NGINX
C module and no third-party SDK. The project shipped neither that encoder nor
an OTLP HTTP/JSON transport, and 0.9.2 does not revive either design.

## Rationale

The historical rationale was dependency minimization, a small proposed
encoding surface, and avoidance of a new Rust/C export lifecycle. None of the
proposed span fields or transport details became a live compatibility contract.

## Consequences

- **Positive (historical)**: The proposal avoided new external dependencies.
- **Negative (superseded)**: No transport, span export, or semantic convention
  validation shipped.
- **Mitigation**: No mitigation is active. Any future implementation must satisfy
  ADR-0027 instead of extending this proposal.

## Historical Implementation Sketch (not shipped)

The following entries preserve historical design notes only. They were not
shipped. Do not copy them into the 0.9.2 command table or request
path: proposed request-scoped spans, a nonblocking internal subrequest,
`markdown_otel`/`markdown_otel_endpoint`, and proposed engine/result/reason
attributes. Any future design must satisfy ADR-0027 in full.
