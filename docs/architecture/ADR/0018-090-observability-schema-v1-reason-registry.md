# ADR-0018: 0.9.0 Observability Schema v1 and Reason Code Registry

## Status

Accepted (0.9.0 contract freeze — initial contract phase)

## Context

Historically there were **two** parallel reason registries: a Rust `ReasonCode`
enum and a C-side set of `SKIP_*` / `ELIGIBLE_*` / `STREAMING_*` codes. Metrics
labels, diagnostics JSON, and response headers had grown without a stability
contract. The frozen contract now converges them through one registry and
generated projections.

## Decision

### Single reason-code source of truth

`components/rust-converter/reason_registry.toml` is the **single source**.
The generator emits the Rust reason code projection, C metadata,
release artifacts, and reverse lookup data. C consumes the generated metadata.
There is no hand-maintained C string table or parallel public reason list.
Naming rules:

- Wire/label form is `lower_snake_case` (for example `streaming_block_full_cache_validation`).
- Discriminants are **never reused**, deprecated codes keep their slot.
- 1.0+ is **additive-only**: add variants, never remove/renumber.

The 0.9.0 registry adds the cross-cutting decision codes the consistency review
identified, including: `forwarded_header_untrusted`, `forwarded_header_trusted`,
`trusted_proxies_not_configured`, `streaming_block_full_cache_validation`,
`streaming_block_small_body`, `streaming_block_inflight_limit`,
`bypass_range_request`, `bypass_no_transform`, `bypass_content_encoding`, and
`streaming_mid_flight_error` (post-commit). The logger writes streaming
transition details separately in the structured `event=` field.

### Metrics label whitelist (no high cardinality)

Allowed labels: `reason`, `profile`, `path_mode`, `cache_validation` — all
low-cardinality enumerations. **Forbidden** as labels: URL, path, host, IP, User
-Agent, raw header values, or any unbounded request-derived string. Per-path
metrics keep their existing cardinality cap (`markdown_metrics_per_path_cardinality`).

### Diagnostics JSON schema v1

Diagnostics JSON carries `schema_version: 1`. The field set is frozen as the v1
contract. 1.0+ changes are additive-only. Diagnostics output desensitizes:
the module reports forwarded-header decisions by reason code, never by echoing raw
untrusted header values.

### Response header stability contract

Frozen response-header behavior: `Content-Type: text/markdown` on conversion,
`Vary: Accept` (deduped), `Content-Length` invalidated for streaming,
`Last-Modified` preserved from source. ETag only as defined by ADR-0017
(full-buffer transformed ETag under `cache_validation full`, none in streaming).

### Single-source enforcement

A docs-sync test asserts that the reason-code registry, Prometheus output, and
the C diagnostics renderer stay synchronized with the canonical registry and
its generated projections (no unvalidated parallel public list).
`python3 tools/reason-codegen/generate.py --check` and `make check-headers`
cover generated and FFI drift.

## Consequences

### Positive

- One reason registry, no C/Rust semantic fork.
- Bounded metric cardinality protects SHM and scrape cost.
- Versioned diagnostics schema enables safe consumer evolution.

### Negative

- C call sites that report outcomes must use canonical names. Streaming
  transition details belong in the separate `event=` field.
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
