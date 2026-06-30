# ADR-0017: 0.9.0 HeaderPlan Atomic Apply (Prepare/Commit)

## Status

Accepted (0.9.0 contract freeze — Wave 1)

## Context

`ngx_http_markdown_header_plan.c` already exists with SET/DELETE/MODIFY/DELETE-ALL
operations and rollback support. However, several response-header mutations still
happen **outside** HeaderPlan (in-place on `r->headers_out`), and the existing
apply path interleaves allocation with mutation. Because NGINX pool allocation
**cannot be rolled back**, an allocation failure mid-apply can leave headers
partially mutated — a correctness and fail-open hazard (AGENTS.md Rule 39).

The current scattered-mutation map and the documented full-response-synthesis
exceptions are recorded in the local 0.9.0 working inventory; this ADR freezes
the resulting contract.

## Decision

### Two-phase prepare/commit contract

All in-place mutation of a converted **upstream** response goes through HeaderPlan
in two strictly separated phases:

**Prepare** (may allocate, may fail, **MUST NOT** mutate `r->headers_out`):
parse operations, allocate `ngx_table_elt_t` entries and copy string values from
the pool, validate operation legality, check list capacity, save rollback state.
On failure: return error, no mutation has occurred.

**Commit** (**MUST NOT** allocate, must not fail on allocation, only mutates):
apply prepared SET/DELETE/MODIFY/DELETE-ALL ops and special-field updates using
memory already allocated in prepare. Invariant: **if prepare succeeds, commit
succeeds.**

### Special-field atomicity (handled together in commit)

- Delete `Content-Length`: invalidate **both** `content_length_n` and the
  `content_length` header entry (`hash = 0`, Rule 40).
- Set `Content-Type`: update `content_type`, `content_type_len`,
  `content_type_lowcase` (NULL), and `charset` (clear) together.
- Delete `ETag`: clear `etag` pointer + `hash = 0`.
- `Vary: Accept`: dedup (no duplicate append).
- `status`, `last_modified_time` handled in commit.
- Multi-step modification is atomic: abort on first prepare failure, no partial
  apply (Rule 39).

### Streaming vs full-buffer header matrix

- **Streaming**: deletes/omits `Content-Length`; generates **no** ordinary ETag
  (headers commit before the transformed body is known); `If-None-Match` not
  supported; `If-Modified-Since` uses preserved `Last-Modified`.
- **Full-buffer**: when `markdown_cache_validation full` and a transformed
  representation is computable, generates a transformed ETag.
- HEAD / 304 / no-body / error-status paths follow a documented matrix.

### Documented exception table (NOT in-place mutation → bypass allowed)

These synthesize a **complete** response (no upstream response to mutate) and are
permitted exceptions, each justified and listed:

| Path | Justification |
|------|---------------|
| Metrics endpoint (`metrics_impl.h`) | full-response synthesis (subrequest) |
| Diagnostics endpoint (`diagnostics.c`) | full-response synthesis (subrequest) |
| Stream error response (`stream_error.c`) | full-response synthesis (error path) |

Any **new** exception requires ADR justification and an entry here. New in-place
`headers_out` mutation outside HeaderPlan is forbidden.

### Post-commit error boundary

Once commit succeeds and headers are sent, a streaming post-commit error
**cannot** be treated as pre-commit: it does not follow `markdown_error_policy`
pass/fail_closed/status selection. Allowed: stop output, close the downstream
connection, log reason code `streaming_mid_flight_error` (ADR-0018), emit
metrics. Forbidden: pass original content, return 200 with truncated body, or
"reliably" rewrite HTTP status.

## Consequences

### Positive

- No partial header mutation; commit is allocation-free and cannot half-apply.
- All `Content-Type`/`Content-Length`/`ETag`/`Vary` edge cases handled in one
  atomic place.
- Honest streaming post-commit semantics (no false 502/rewrite promises).

### Negative

- Prepare must over-allocate worst-case header entries up front.
- Existing scattered mutations must be migrated into HeaderPlan.
- Fault-injection test surface grows.

## Alternatives Considered

- **Allocate-during-commit with rollback**: rejected — NGINX pool allocations are
  not reversible; rollback of allocation is impossible.
- **Leave scattered mutations as-is**: rejected — they are the partial-mutation
  hazard this ADR closes.

## References

- [ADR-0015: Config V2 Breaking Migration](0015-090-config-v2-breaking-migration.md)
- [ADR-0016: Rust-First Decision Core and C/Rust Boundary](0016-090-rust-first-decision-core-boundary.md)
- [ADR-0018: Observability Schema v1 and Reason Code Registry](0018-090-observability-schema-v1-reason-registry.md)
- AGENTS.md Rule 39, 40, 47, 51

## Date

2026-06-30

## Authors

Kang

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.0 | 2026-06-30 | Kang | Initial ADR — prepare/commit split, special-field atomicity, exception table, post-commit boundary |
