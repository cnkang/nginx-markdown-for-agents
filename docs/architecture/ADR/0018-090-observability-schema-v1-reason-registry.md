# ADR-0018: 0.9.0 Observability Schema v1 and Reason Code Registry

## Status

Accepted (0.9.0 contract freeze — Wave 1)

## Context

Today there are **two** parallel reason registries: a Rust `ReasonCode` enum (18
variants) and a C-side set (32+ `SKIP_*` / `ELIGIBLE_*` / `STREAMING_*` codes).
Metrics labels, diagnostics JSON, and response headers have grown without a
stability contract. 0.9.0 is the last chance before 1.0 to converge these into a
single, additive-only observability schema.

## Decision

### Single reason-code source of truth

The Rust `decision/reason_code.rs` `ReasonCode` enum is the **single source**.
C consumes it via FFI (`select_reason`, ADR-0016); the C-side string table and
parallel codes are removed or become FFI shims. Naming rules:

- Wire/label form is `lower_snake_case` (e.g. `streaming_block_full_cache_validation`).
- Discriminants are **never reused**; deprecated codes keep their slot.
- 1.0+ is **additive-only**: add variants, never remove/renumber.

The 0.9.0 registry adds the cross-cutting decision codes the consistency review
identified, including: `forwarded_header_untrusted`, `forwarded_header_trusted`,
`trusted_proxies_not_configured`, `streaming_block_full_cache_validation`,
`streaming_block_small_body`, `streaming_block_inflight_limit`,
`bypass_range_request`, `bypass_no_transform`, `bypass_content_encoding`, and
`streaming_mid_flight_error` (post-commit; C internal `STREAMING_FAIL_POSTCOMMIT`
is an implementation detail mapped to this canonical name).

### Metrics label whitelist (no high cardinality)

Allowed labels: `reason`, `profile`, `path_mode`, `cache_validation` — all
low-cardinality enumerations. **Forbidden** as labels: URL, path, host, IP, User
-Agent, raw header values, or any unbounded request-derived string. Per-path
metrics keep their existing cardinality cap (`markdown_metrics_per_path_cardinality`).

### Diagnostics JSON schema v1

Diagnostics JSON carries `schema_version: 1`. The field set is frozen as the v1
contract; 1.0+ changes are additive-only. Diagnostics output is desensitized:
forwarded-header decisions are reported by reason code, never by echoing raw
untrusted header values.

### Response header stability contract

Frozen response-header behavior: `Content-Type: text/markdown` on conversion;
`Vary: Accept` (deduped); `Content-Length` invalidated for streaming;
`Last-Modified` preserved from source; ETag only as defined by ADR-0017
(full-buffer transformed ETag under `cache_validation full`; none in streaming).

### Single-source enforcement

A docs-sync test asserts that the reason-code registry, the Prometheus golden
output, and the diagnostics schema all derive from the Rust enum (no
hand-maintained parallel list). `make check-headers` covers FFI drift.

## Consequences

### Positive

- One reason registry; no C/Rust semantic fork.
- Bounded metric cardinality protects SHM and scrape cost.
- Versioned diagnostics schema enables safe consumer evolution.

### Negative

- C call sites that referenced C-only codes must map to canonical names.
- Adding a reason code now touches a registry + golden tests + docs (intended
  friction).

## Alternatives Considered

- **Keep dual registries with a mapping table**: rejected — perpetuates drift and
  doubles the surface to keep in sync.
- **Allow free-form metric labels**: rejected — high cardinality is a known SHM
  and Prometheus-scrape hazard.

## References

- [ADR-0015: Config V2 Breaking Migration](0015-090-config-v2-breaking-migration.md)
- [ADR-0016: Rust-First Decision Core and C/Rust Boundary](0016-090-rust-first-decision-core-boundary.md)
- [ADR-0017: HeaderPlan Atomic Apply](0017-090-headerplan-atomic-apply.md)
- AGENTS.md Rule 7, 8, 23

## Date

2026-06-30

## Authors

Kang

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.0 | 2026-06-30 | Kang | Initial ADR — single reason registry, metrics label whitelist, diagnostics schema v1, response-header contract |
