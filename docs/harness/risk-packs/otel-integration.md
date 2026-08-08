# OTel Integration Pack

> **ARCHIVED.** The 0.9.2 release removed the OTel subsystem
> (`markdown_otel`, `markdown_otel_endpoint`, and the
> `ngx_http_markdown_otel*` sources). This pack stays as a historical
> record of the pre-0.9.2 risk surface. It has no active triggers. See
> [ADR-0027](../../architecture/ADR/0027-otel-removal-reintroduction-conditions.md)
> for the reintroduction conditions.

## Triggers

Changes to `ngx_http_markdown_otel*`, OTel configuration directives, OTLP export logic, or span attribute definitions.

## Risks

- Span export blocking the request path (must be async/non-blocking)
- Span containing request/response body content (security violation)
- Ring buffer overflow causing silent span loss without logging
- OTLP protobuf encoding errors producing invalid spans
- Collector unavailability affecting conversion behavior (must be fail-safe)

## Common Supporting Packs

- `observability-metrics` when OTel changes affect metrics export surface
- `nginx-protocol-safety` when OTel interacts with filter chain lifecycle

## Sync Points

- C header `ngx_http_markdown_otel.h` must define all span attribute constants
- `docs/features/otel-tracing.md` must document all configuration directives
- ADR or operator examples must stay aligned with the actual directive names
  and internal URI/subrequest contract.
- W3C trace-context extraction must traverse all NGINX header list parts and
  handle absent/malformed values without affecting conversion behavior.
- AGENTS.md Rule 23 (observability contract) applies to all new OTel metrics
- SHM zone layout version must bump when OTel state struct changes

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
