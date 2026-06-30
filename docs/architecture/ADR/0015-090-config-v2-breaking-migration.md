# ADR-0015: 0.9.0 Config V2 Breaking Migration

## Status

Accepted (0.9.0 contract freeze)

## Context

0.9.0 is the **Production Readiness Breaking Release** before 1.0.0. The 0.8.x
configuration surface accreted dozens of directives with overlapping
responsibilities: seven `markdown_stream_*` knobs, two error directives
(`markdown_on_error` + `markdown_streaming_on_error`), a boolean trust model
(`markdown_trust_forwarded_headers`), and split conditional/ETag directives
(`markdown_conditional_requests` + `markdown_etag`). This sprawl is not a stable
1.0 contract.

0.9.0 collapses these into a small, auditable Config V2 grammar. Because 0.9.0 is
explicitly breaking, **no alias compatibility** is provided: removed directives
become reject-only stubs that fail `nginx -t` with a migration hint. NGINX's
unknown-directive handling cannot emit a hint, so the stub parser entries must
remain to produce actionable errors.

## Decision

### Stable 0.9.0 directives (1.0 contract surface)

New consolidating directives (additive-only after 1.0):

| Directive | Replaces | Grammar |
|-----------|----------|---------|
| `markdown_limits` | `markdown_max_size`, `markdown_timeout`, `markdown_streaming_budget`, `markdown_large_body_threshold` | `memory=<size> timeout=<time> streaming_buffer=<size> max_inflight=<N>` (space-separated keys; duplicate/unknown/zero key → error; per-key inheritance) |
| `markdown_accept` | `markdown_on_wildcard` | `strict\|wildcard\|force` |
| `markdown_cache_validation` | `markdown_conditional_requests`, `markdown_etag` | `off\|ims_only\|full` |
| `markdown_streaming` | (policy split from engine) | `off\|auto\|force` |
| `markdown_error_policy` | `markdown_on_error`, `markdown_streaming_on_error` | `pass\|fail_closed\|status <code>` (codes 429/502/503 only) |
| `markdown_trusted_proxies` | `markdown_trust_forwarded_headers`, `markdown_forwarded_headers` | `<CIDR>...\|off`, **http context only** (see ADR-0016) |
| `markdown_profile` | (new) | `strict_cache\|balanced\|streaming_first` |

Retained EXISTING stable directives keep their names and semantics, notably:
`markdown_filter on|off` (the module enable directive — there is **no**
`markdown on|off`), `markdown_streaming_engine` (implementation selector,
distinct from the `markdown_streaming` policy enum — **not** an alias),
`markdown_content_types`, `markdown_stream_types`, and the
metrics/diagnostics/otel/parser-budget families.

### Reject-only legacy stubs (no aliases)

Every removed directive keeps a parser entry whose **only** behavior is
`NGX_CONF_ERROR` + a migration hint pointing at the replacement and the 0.9.0
migration guide (`docs/guides/MIGRATION-0.9.md`). There are **no transition
aliases** in 0.9.0. Canonical error shape:

```
nginx: [emerg] "markdown_trust_forwarded_headers" directive has been removed in
0.9.0; use "markdown_trusted_proxies <CIDR>..." instead
(see docs/guides/MIGRATION-0.9.md)
```

Stub set: `markdown_on_wildcard`, `markdown_etag`,
`markdown_conditional_requests`, `markdown_on_error`,
`markdown_streaming_on_error`, `markdown_trust_forwarded_headers`,
`markdown_forwarded_headers`, `markdown_etag_policy`, `markdown_max_size`,
`markdown_timeout`, `markdown_streaming_budget`, `markdown_large_body_threshold`.

### Cross-directive conflict rules (validated at `nginx -t`)

- `markdown_cache_validation full` + `markdown_streaming force` → **error**
  (streaming cannot generate a strong ETag for chunked output; headers commit
  before the transformed body is known — see ADR-0017).
- `markdown_cache_validation full` + `markdown_streaming auto` → **warning**;
  at runtime streaming is blocked and the request uses full-buffer with full
  validation, reason code `streaming_block_full_cache_validation` (ADR-0018).
- `markdown_accept force` + `markdown_auth_policy deny` → **warning** (dangerous).

### Dynconf schema version

Dynconf JSON gains an explicit `schema_version` field; 0.9.0 = `"0.9"`.
Missing/unknown version → error. Static config and dynconf share one Rust
validator core.

## Consequences

### Positive Consequences

- Small, auditable 1.0 config surface; clear breaking boundary.
- `nginx -t` fails fast with actionable migration hints instead of silent
  unknown-directive errors.
- One validator core for static + dynconf eliminates drift.

### Negative Consequences

- Operators must rewrite 0.8.x configs (mitigated by the 0.9.0 migration guide).
- Reject-only stubs add parser entries that exist only to error (removed in 1.0).
- No rollback-by-config; rollback is by reverting the module version.

## Alternatives Considered

- **Transition aliases**: rejected — silent aliasing blurs the breaking boundary
  and creates compatibility debt into 1.0.
- **Drop directives with no stub (rely on NGINX unknown-directive error)**:
  rejected — NGINX cannot emit a migration hint, producing poor operator UX.
- **Per-location `markdown_trusted_proxies`**: rejected — per-location trust
  creates local trust-bypass risk; http-only is auditable (ADR-0016).

## References

- [ADR-0016: Rust-First Decision Core and C/Rust Boundary](0016-090-rust-first-decision-core-boundary.md)
- [ADR-0017: HeaderPlan Atomic Apply](0017-090-headerplan-atomic-apply.md)
- [ADR-0018: Observability Schema v1 and Reason Code Registry](0018-090-observability-schema-v1-reason-registry.md)
- [Configuration Guide](../../guides/CONFIGURATION.md)
- AGENTS.md Rule 35 (dynconf), Rule 45 (effective_conf), Rule 55 (version consistency)

## Date

2026-06-30

## Authors

Kang

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.0 | 2026-06-30 | Kang | Initial ADR — Config V2 grammar freeze, reject-only stub policy, conflict rules, dynconf schema_version |
