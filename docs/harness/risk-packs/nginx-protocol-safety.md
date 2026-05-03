# NGINX Protocol Safety Pack

Use this as the primary pack when NGINX request lifecycle, authentication,
cache-control, conditional request, status, or header semantics change.

## Triggers

- touched auth, conditional request, request lifecycle, filter module, or C test
  files under the NGINX module
- touched E2E or CI scripts that verify real NGINX protocol behavior
- keywords like `auth`, `Cache-Control`, `If-None-Match`,
  `If-Modified-Since`, `ETag`, `HEAD`, `206`, `304`, or `header`

## Common Supporting Packs

- `observability-metrics` when the protocol branch emits a reason code,
  decision log, metric, or operator-visible status
- `docs-tooling-drift` when operator docs, configuration docs, or release gates
  describe the protocol behavior
- `runtime-streaming` when conditional request behavior changes streaming vs
  full-buffer routing

## Sync Points

- headers are finalized before body data and are not emitted twice
- auth-denied, auth-allowed, and authenticated cache-control branches are all
  covered by tests
- repeated header fields are evaluated across every `ngx_list_part_t`, not only
  the first list part or first matching value
- cache-control decisions aggregate all relevant values before applying
  precedence, especially `public` with `private`/`no-store` combinations
- auth/cache-bypass cookie patterns match full cookie-name boundaries and do
  not create substring false positives
- conditional request modes preserve the intended full-buffer vs streaming path
- status-specific eligibility, especially `206` and `304`, maps to the intended
  reason code under normal and malformed upstream responses
- HEAD requests and empty-body artifacts are verified with `curl --head` or an
  equivalent protocol-correct check
- protocol docs use exact config names, reason codes, and retrievable metrics

## Minimum Verification

```bash
make harness-check
make test-nginx-unit
make test-nginx-integration
```

For changes involving real NGINX, source-build CI, or conditional request E2E
behavior, also run the matching focused E2E or `make harness-check-full`.

## Canonical References

- [../../architecture/REQUEST_LIFECYCLE.md](../../architecture/REQUEST_LIFECYCLE.md)
- [../../guides/CONFIGURATION.md](../../guides/CONFIGURATION.md)
- [../../../AGENTS.md](../../../AGENTS.md)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.5 | 2026-04-24 | Codex | Added 60-day protocol safety routing |
| 0.6.0 | 2026-05-03 | Codex | Added multi-header and cookie-boundary sync points |
