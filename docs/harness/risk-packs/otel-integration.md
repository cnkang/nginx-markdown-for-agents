# OTel Integration Pack

> **ARCHIVED.** The 0.9.2 release removed the OTel subsystem
> (`markdown_otel`, `markdown_otel_endpoint`, and the
> `ngx_http_markdown_otel*` sources). This pack stays as a historical
> record of the pre-0.9.2 risk surface. It has no active triggers. See
> [ADR-0027](../../architecture/ADR/0027-otel-removal-reintroduction-conditions.md)
> for the reintroduction conditions.

## Historical scope

There are no active triggers or source paths for this pack. The entries below
describe the risks behind subsystem removal. They are not
requirements of the 0.9.2 production contract. A future implementation must
first satisfy ADR-0027 and create a new routing entry as part of its review.

## Risks

- Span export blocking the request path (must be async/non-blocking)
- Span containing request/response body content (security violation)
- Ring buffer overflow causing silent span loss without logging
- OTLP protobuf encoding errors producing invalid spans
- Collector unavailability affecting conversion behavior (must be fail-safe)

## Common Supporting Packs

- `observability-metrics` when OTel changes affect metrics export surface
- `nginx-protocol-safety` when OTel interacts with filter chain lifecycle

## Historical sync points

The former C header, tracing feature document, directive names, and
Superseded ADRs and migration material retain the former trace-context
behavior. Do not cite those materials as current source or configuration
contracts.

## Minimum Verification

```bash
make test-nginx-unit
make test-nginx-unit-sanitize-smoke
make docs-check
```

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-08 | Kang | Marked pack archived (OTel subsystem removed in 0.9.2) |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.6.0 | 2026-05-03 | Codex | Covered implementation-header and config/doc routing for OTel changes |
| 0.6.0 | 2026-05-03 | Codex | Added trace-context header-list traversal sync point |
| 0.6.0 | 2026-04-28 | v0.6.0-planning | Initial pack definition |
