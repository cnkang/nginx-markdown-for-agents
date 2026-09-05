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
explicitly breaking, **no alias compatibility** exists: removed directives
become reject-only stubs that fail `nginx -t` with a migration hint. NGINX's
unknown-directive handling cannot emit a hint, so the stub parser entries must
remain to produce actionable errors.

## Decision

### Stable 0.9.0 directives (1.0 contract surface)

New consolidating directives (additive-only after 1.0):

| Directive | Replaces | Grammar |
|-----------|----------|---------|
| `markdown_limits` | `markdown_max_size`, `markdown_memory_budget`, `markdown_timeout`, `markdown_streaming_budget`, `markdown_stream_threshold`, `markdown_large_body_threshold`, `markdown_decompress_max_size` | `conversion_timeout=30s parser_timeout=10s conversion_memory=64m parser_memory=32m streaming_buffer=2m decompressed_size=10m decompression_ratio=100 max_inflight=64` (space-separated key/value entries; each key is optional and inherits independently; duplicate/unknown keys, zero, malformed values, and overflow are rejected). `markdown_decompress_max_size` maps to the `decompressed_size` key; the legacy directive is reject-only with a migration hint, exactly like the other removed directives |
| `markdown_accept` | `markdown_on_wildcard` | `strict\|wildcard\|force` |
| `markdown_cache_validation` | `markdown_conditional_requests`, `markdown_etag` | `off\|ims_only\|full` |
| `markdown_streaming` | existing directive; policy split from engine | `off\|auto\|force` |
| `markdown_error_policy` | `markdown_on_error`, `markdown_streaming_on_error` | `pass\|fail_closed\|status <code>` (codes 429/503 only; `status 502` rejected, use `fail_closed`) |
| `markdown_trusted_proxies` | `markdown_trust_forwarded_headers`, `markdown_forwarded_headers` | `<CIDR>...\|off`, **http context only** (see ADR-0016) |
| `markdown_profile` | (new) | `strict_cache\|balanced\|streaming_first` |

Retained EXISTING stable directives keep their names and semantics, notably:
`markdown_filter on|off` (the module enable directive — there is **no**
`markdown on|off`), `markdown_streaming` (the sole processing selector),
`markdown_content_types`, `markdown_stream_types`, and the
metrics/diagnostics/otel/parser-budget families.

The 0.9.1 contract treated `markdown_streaming_engine` as reject-only. Version
0.9.2 removed it, so it is not a stable 1.0 configuration directive. The
reject-only parser entry described below applies only to directives retained
by the 0.9.0 migration contract.

### Reject-only legacy stubs (no aliases)

Every directive removed as part of the 0.9.0 migration contract keeps a parser
entry whose **only** behavior is
`NGX_CONF_ERROR` + a migration hint pointing at the replacement and the 0.9.0
migration guide (`docs/guides/MIGRATION-0.9.0.md`). There are **no transition
aliases** in 0.9.0. Canonical error shape:

```
nginx: [emerg] "markdown_trust_forwarded_headers" directive has been removed in
0.9.0; use "markdown_trusted_proxies <CIDR>..." instead
(see docs/guides/MIGRATION-0.9.0.md)
```

Stub set: `markdown_on_wildcard`, `markdown_etag`,
`markdown_conditional_requests`, `markdown_on_error`,
`markdown_streaming_on_error`, `markdown_trust_forwarded_headers`,
`markdown_forwarded_headers`, `markdown_etag_policy`, `markdown_max_size`,
`markdown_memory_budget`, `markdown_timeout`, `markdown_streaming_budget`,
`markdown_stream_threshold`, `markdown_large_body_threshold`,
`markdown_decompress_max_size` (maps to the `decompressed_size` key of
`markdown_limits`. It is reject-only with a migration hint, exactly like the
other removed directives).

### Cross-directive conflict rules (configuration-time errors and request-time warnings)

Some conflicts fail configuration parsing with `nginx -t`. Others pass
`nginx -t` and surface only at request time. Each rule below names its
validation boundary.

- `markdown_cache_validation full` + `markdown_streaming force` → **error**
  (streaming cannot generate a strong ETag for chunked output, headers commit
  before the transformed body is known — see ADR-0017).
- `markdown_cache_validation full` + `markdown_streaming auto` → **runtime
  warning** (not an `nginx -t` configuration error): the header-filter phase
  detects the conflict at request time, blocks streaming for that request, and
  uses full-buffer conversion with full validation, emitting reason code
  `streaming_block_full_cache_validation` (ADR-0018).
- `markdown_accept force` + `markdown_auth_policy deny` → **warning** (dangerous).

### Dynconf schema version

Dynconf JSON gains an explicit `schema_version` field. 0.9.0 = `"0.9"`.
Missing/unknown version → error. Static config and dynconf share one Rust
validator core.

## Consequences

### Positive Consequences

- Small, auditable 1.0 config surface, clear breaking boundary.
- `nginx -t` fails fast with actionable migration hints instead of silent
  unknown-directive errors.
- One validator core for static + dynconf eliminates drift.

### Negative Consequences

- Operators must rewrite 0.8.x configs (mitigated by the 0.9.0 migration guide).
- Reject-only stubs add parser entries that exist only to error (removed in 1.0).
- No in-band rollback command is part of the config schema. For dynamic
  configuration, 0.9.2 restores a prior valid file by atomic replacement and
  reuses the normal watcher validation path, the worker-consistency decision
  appears in [ADR-0026](0026-dynconf-file-restore-contract.md).

## Alternatives Considered

- **Transition aliases**: rejected — silent aliasing blurs the breaking boundary
  and creates compatibility debt into 1.0.
- **Drop directives with no stub (rely on NGINX unknown-directive error)**:
  rejected — NGINX cannot emit a migration hint, producing poor operator UX.
- **Per-location `markdown_trusted_proxies`**: rejected — per-location trust
  creates local trust-bypass risk, http-only is auditable (ADR-0016).

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
| 0.9.2 | 2026-08-15 | Kang | Clarified that cache_validation full combined with markdown_streaming auto is a runtime warning, not a config error; markdown_streaming force combined with cache_validation full remains an nginx -t configuration error |
| 0.9.0 | 2026-06-30 | Kang | Initial ADR — Config V2 grammar freeze, reject-only stub policy, conflict rules, dynconf schema_version |
