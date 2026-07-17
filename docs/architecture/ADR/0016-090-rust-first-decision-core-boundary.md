# ADR-0016: 0.9.0 Rust-First Decision Core and C/Rust Boundary

## Status

Accepted (0.9.0 contract freeze)

## Context

[ADR-0010](0010-v070-rust-first-boundary-evolution.md) established the Rust-first
strategy: new pure logic lands in Rust, C retains NGINX-coupled responsibilities.
0.9.0 completes the migration of the remaining **pure decision logic** that still
lives in C — Accept negotiation glue, content-type/method/status eligibility,
base-URL / forwarded-header decision, conditional-request decision, streaming
eligibility, reason selection, and error classification.

The risk is over-correction: a single giant
`markdown_decide_request(ngx_http_request_t *)` FFI that swallows all request
state would couple Rust to NGINX internals, defeat unit testing, and create an
unstable ABI. 0.9.0 therefore freezes a **small-API** decision boundary.

## Decision

### Small decision APIs (no giant aggregate FFI)

The decision core is exposed as small, independently testable Rust functions
operating on normalized inputs (byte slices, primitive fields, normalized config
snapshots), never on `ngx_http_request_t`:

| Decision API | Maps to | 0.9.0 status |
|--------------|---------|--------------|
| `decide_accept` | `negotiator.rs` | exists; C `ngx_http_markdown_accept.c` becomes thin wrapper |
| `decide_eligibility` | new Rust `eligibility` | migrate from `ngx_http_markdown_eligibility.c` |
| `decide_base_url` / `decide_forwarded_headers` | `security.rs` + new | migrate forwarded-header/base-URL pure logic |
| `decide_conditional` | `conditional.rs` | exists; C `ngx_http_markdown_conditional.c` becomes thin wrapper |
| `decide_streaming` | streaming eligibility (pure parts) | migrate pure parts; lifecycle stays C |
| `select_reason` | `decision/reason_code.rs` | single source (ADR-0018) |
| `classify_error` | `error.rs` | pure classification |

Any aggregation layer MUST be a thin wrapper over these small APIs, not a new
request-swallowing FFI entry point. New `FFIDecisionResult`-style structs are
permitted but must be `repr(C)`, additive-only, and initialized via helper
constructors (Rule 15).

### C side after migration (NGINX glue only)

C retains: module/command registration, config parse+merge, filter hooks, pool
allocation, chain forwarding, header-list mutation (via HeaderPlan, ADR-0017),
SHM/slab metrics, dynconf snapshot, diagnostics subrequest, request
finalization. Migrated C pure-logic files become **thin wrappers** that marshal
inputs, call the small Rust API, and apply the result — with **no business
branching**. New C pure business branches are forbidden in 0.9.0+ without an
ADR-justified exception.

### Trusted proxies — http-only CIDR trust

Source IP is taken from `r->connection->sockaddr` (after the realip module,
if configured). `markdown_trusted_proxies` is **http context only**, CIDR-based
(IPv4 + IPv6, parsed at config time), and `Forwarded` takes precedence over
`X-Forwarded-*` with right-most value selection on multi-hop chains (the
right-most element is the one appended by the trusted proxy; the left-most
is client-controlled and must not be trusted). `proto` is
restricted to `http`/`https`. Untrusted sources produce a reason code and never
leak raw header values into logs/metrics.

### C complexity reduction acceptance (per migrated file)

Each migration must produce: before/after C function inventory; P0 migration
status (migrated / thin-wrapper / documented exception); Rust unit coverage; a C
integration **parity test** (Rust decision == legacy C decision for identical
input) retained until the C logic is deleted; `tools/harness/detect_c_pure_logic.sh`
passing; no duplicate C/Rust business logic; no direct `headers_out` mutation
outside HeaderPlan exceptions (ADR-0017).

## Consequences

### Positive Consequences

- Decision logic is unit-testable without NGINX; property tests apply.
- Stable, additive small-API ABI; no request-state coupling.
- Single source of truth for accept/eligibility/conditional/reason/error.

### Negative Consequences

- More small FFI symbols to maintain (mitigated by helpers + `make check-headers`).
- Parity tests add temporary duplication until C deletion.
- Coordinated C+Rust landing per Rule 15.

## Alternatives Considered

- **Giant `decide_request(r)` FFI**: rejected — couples Rust to NGINX internals,
  untestable, unstable ABI.
- **Keep pure logic in C**: rejected — repeats the 0.5–0.6 pattern of subtle C
  pure-logic bugs; contradicts ADR-0010.

## References

- [ADR-0010: v0.7.0 Rust-First Boundary Evolution](0010-v070-rust-first-boundary-evolution.md)
- [ADR-0015: Config V2 Breaking Migration](0015-090-config-v2-breaking-migration.md)
- [ADR-0017: HeaderPlan Atomic Apply](0017-090-headerplan-atomic-apply.md)
- [ADR-0018: Observability Schema v1 and Reason Code Registry](0018-090-observability-schema-v1-reason-registry.md)
- [FFI Migration Contract](../FFI_MIGRATION_CONTRACT.md), [FFI ABI Compatibility](../FFI_ABI_COMPATIBILITY.md)
- AGENTS.md Rule 15, 17, 46, 53

## Date

2026-06-30

## Authors

Kang

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.0 | 2026-06-30 | Kang | Initial ADR — small-API decision boundary freeze, trusted-proxies model, C complexity reduction acceptance |
