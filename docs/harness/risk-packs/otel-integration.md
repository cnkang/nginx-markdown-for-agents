# OTel Integration Pack

## Triggers

Changes to `ngx_http_markdown_otel.c/h`, OTel configuration directives, OTLP export logic, or span attribute definitions.

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
| 0.6.0 | 2026-04-28 | v0.6.0-planning | Initial pack definition |
