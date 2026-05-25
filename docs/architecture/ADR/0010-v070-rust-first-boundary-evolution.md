# ADR-0010: v0.7.0 Rust-First Boundary Evolution

## Status

Accepted

## Context

The module uses a dual-language architecture (C + Rust) with an FFI boundary,
established in [ADR-0001](0001-use-rust-for-conversion.md). As the project
matured through v0.5.0 and v0.6.x, the boundary remained largely static: Rust
owned HTML→Markdown conversion while C owned everything else.

In v0.7.0, several new capabilities are required—Accept negotiation, conditional
request handling, URL/Host validation, a decision engine, header plan
construction, and reason-code/metrics normalization. These are all pure logic
with no dependency on NGINX APIs, making them natural candidates for Rust
ownership.

At the same time, runtime correctness fixes (bounded decompression, replay
buffer hardening, output ordering) highlighted that the FFI boundary needs
stronger contracts: explicit struct initialization helpers, CI-enforced header
drift checks, and layout tests.

The question: should new pure logic continue to be implemented in C (closer to
NGINX), or should the project adopt a deliberate "Rust-first" strategy for all
new logic that does not require NGINX API access?

## Decision

In v0.7.0, the project adopts a **Rust-first** strategy: all new pure logic is
implemented in Rust and exposed to the C module via FFI. The C side retains only
NGINX-coupled responsibilities.

### C Side Retains

- Module registration (`ngx_module_t` / `ngx_command_t`)
- Directive entry (configuration parsing and merge)
- Filter hooks (`header_filter` / `body_filter` registration)
- Pool allocation (`ngx_palloc` / `ngx_pnalloc` / `ngx_pool_cleanup`)
- Chain forwarding (`ngx_output_chain` / `ngx_next_filter`)
- Header list modification (`ngx_list_push` / `ngx_hash_init`)
- SHM/slab/rbtree (shared memory operations)
- Log adapter (`ngx_log_error` / `ngx_log_debug`)
- Request finalization (`ngx_http_finalize_request`)
- Dynconf reload trigger (HUP signal handling / inotify / kqueue)

### Rust Side Owns

- HTML→Markdown conversion (streaming + full-buffer engine)
- Accept negotiation (q-value comparison, MIME type matching)
- Bounded decompression (gzip/deflate/br + budget enforcement)
- Conditional request / ETag parsing (If-None-Match, If-Modified-Since, ETag
  generation)
- URL/Host validation (control character rejection, X-Forwarded-* parsing)
- Config normalization / validation (merge + syntax/semantic validation)
- Conversion decision pure logic (`make_decision(context) → decision +
  reason_code`)
- Error category generation (Rust error enum → FFI error code)
- Header plan construction (response header change plan)
- Token/ETag/front-matter/pruning logic
- Schema and release-gate validators

### Key Decisions Made in v0.7.0

| ID | Decision | Task Reference |
|----|----------|---------------|
| D1 | Reason code enum is single source of truth in Rust | B06.1 |
| D2 | FFI struct helpers required for all struct initialization | A07.4 |
| D3 | cbindgen header drift check enforced in CI | A07.1 |
| D4 | Decision engine is a pure function in Rust | B05 |
| D5 | Accept negotiation implemented in Rust | A05 |
| D6 | Conditional request / ETag matching in Rust | B02 |
| D7 | URL/Host validation in Rust | B03 |
| D8 | Header plan construction in Rust | B04 |

### Boundary Rules

1. Rust receives byte slices and normalized config snapshots
2. Rust returns explicit enums, reason codes, plans, and owned buffers
3. C uses NGINX API to apply plans
4. C owns NGINX memory and lifecycle
5. Rust-owned memory must have explicit release functions
6. C must not parse Rust error text
7. Worker request path must not contain hidden blocking operations
8. All cross-boundary structs use `repr(C)`
9. New FFI fields are appended to struct tail (non-breaking ABI change)

## Consequences

### Positive Consequences

- **Testability**: Pure Rust logic is trivially unit-testable without NGINX
  infrastructure; property-based tests cover negotiation and decision paths.
- **Safety**: Memory-safe Rust eliminates classes of bugs in parsing, validation,
  and decision logic.
- **Single source of truth**: Reason codes, error categories, and decision logic
  have one canonical definition—no C/Rust semantic forks.
- **CI enforcement**: Header drift checks and layout tests catch ABI
  incompatibilities before merge.
- **Incremental migration**: Existing stable FFI functions remain unchanged;
  new functions are additive.

### Negative Consequences

- **FFI surface growth**: More FFI functions and structs to maintain; mitigated
  by helper constructors and CI drift checks.
- **Build dependency**: All contributors need the Rust toolchain; mitigated by
  existing CI infrastructure.
- **Coordination cost**: C and Rust changes for the same feature must land
  together; mitigated by Rule 15 (FFI cross-language boundary) enforcement.
- **Debugging complexity**: Cross-language stack traces are harder to read;
  mitigated by structured logging at the FFI boundary.

## Alternatives Considered

### Keep Pure Logic in C

**Pros:** No FFI overhead for new functions; single-language debugging.

**Cons:** Loses memory safety for parsing/validation; harder to test without
NGINX; duplicates logic that Rust already handles well.

**Why not chosen:** The v0.5.0–v0.6.x experience showed that C-side pure logic
accumulates subtle bugs (buffer overflows, missing error paths) that Rust's type
system prevents at compile time.

### Move All Logic to Rust (Including NGINX-Coupled)

**Pros:** Maximizes Rust safety coverage.

**Cons:** Requires unsafe Rust wrappers around all NGINX APIs; loses NGINX
idiom familiarity; massive migration scope.

**Why not chosen:** NGINX API usage is inherently unsafe and C-idiomatic. The
cost of wrapping every NGINX function in safe Rust abstractions exceeds the
benefit for v0.7.0 scope.

## References

- [ADR-0001: Use Rust for HTML-to-Markdown Conversion](0001-use-rust-for-conversion.md)
- [FFI Migration Contract](../FFI_MIGRATION_CONTRACT.md)
- [FFI ABI Compatibility](../FFI_ABI_COMPATIBILITY.md)
- v0.7.0 Technical Design: C/Rust Boundary Contract (§2)
- AGENTS.md Rule 15 (FFI cross-language boundary)

## Date

2026-05-17

## Authors

Kang

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.7.0 | 2026-05-17 | Kang | Initial ADR for v0.7.0 Rust-first boundary evolution |
