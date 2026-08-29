# ADR-0017: 0.9.0 HeaderPlan Atomic Apply (Prepare/Commit)

## Status

Accepted (0.9.0 contract freeze — initial contract phase)

## Context

`ngx_http_markdown_header_plan.c` already exists with SET/DELETE/MODIFY/DELETE-ALL
operations and rollback support. However, several response-header mutations still
happen **outside** HeaderPlan (in-place on `r->headers_out`), and the existing
apply path interleaves allocation with mutation. Because NGINX pool allocation
**cannot roll back**, an allocation failure mid-apply can leave headers
partially mutated — a correctness and fail-open hazard (AGENTS.md Rule 39).

The local 0.9.0 working inventory records exceptions from the current
scattered-mutation map and the documented full-response-synthesis contract.
This ADR freezes the resulting contract.

## Decision

### Two-phase prepare/commit contract

All core wire-critical in-place mutation of a converted **upstream** response
goes through HeaderPlan in two strictly separated phases. Other response
metadata and explicitly documented post-plan operations remain outside this
atomic scope:

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
- The C-side post-plan commit boundary handles `status` and
  `last_modified_time`. These fields remain explicitly outside the HeaderPlan
  atomic field set.
- Multi-step modification is atomic: abort on first prepare failure, no partial
  apply (Rule 39).

ETag, `Vary: Accept`, token, and authentication-header operations are **not**
handled in the HeaderPlan commit. They are C-side post-plan operations (see
the atomic scope boundary below). This keeps them consistent with the
rationale: ETag set/clear and Vary add execute in C after the plan commits.
The HeaderPlan atomicity invariant therefore applies **only to the core
wire-critical fields Content-Type, Content-Encoding, and Content-Length**.
Status and `last_modified_time`, along with ETag, Vary, token, and
authentication-header operations, are explicitly exempt via the atomic-scope
exception below.

HeaderPlan is the sole authority for invalidating an upstream
`Content-Length`: it removes the stale list entries and clears the scalar
field together. The later C-side scalar assignment is a separate,
infallible operation that records the length of the newly generated
representation after the plan has committed. It is not a second invalidation
path, and must run before `ngx_http_send_header()`. The C-side implementation
omits it when the new representation length is not known.

### Streaming vs full-buffer header matrix

- **Streaming**: deletes/omits `Content-Length`, generates **no** ordinary ETag
  (headers commit before the transformed body is known), `If-None-Match` not
  supported, `If-Modified-Since` uses preserved `Last-Modified`.
- **Full-buffer**: when `markdown_cache_validation full` and a transformed
  representation is computable, generates a transformed ETag.
- HEAD / 304 / no-body / error-status paths follow a documented matrix.

### Documented exceptions (NOT in-place mutation → bypass allowed)

Two categories of exceptions exist and remain distinct. Do not conflate
them.

**Category 1 — Full-response synthesis (no upstream response to mutate):**
These paths build a complete response from scratch. There is no upstream
`headers_out` to mutate, so HeaderPlan does not apply. Each exception carries a justification. The table below lists each one:

| Path | Justification |
|------|---------------|
| Metrics endpoint (`metrics_impl.h`) | full-response synthesis (subrequest) |
| Diagnostics endpoint (`diagnostics.c`) | full-response synthesis (subrequest) |

**Post-commit terminal exception (separate category):**
`stream_postcommit.c` does not synthesize a full response and is therefore
not a Category 1 exception. It is a distinct post-commit terminal category:
after headers are already committed, the streaming post-commit error path
may send a terminal closing chain (empty `last_buf` or safe-finish closing
bytes) over the already-committed response. It MUST NOT synthesize a new
error body, MUST NOT replay the original content, MUST NOT replace the
status, and MUST NOT call `ngx_http_send_header()` a second time.
Pre-commit errors never enter this path — they follow
`markdown_error_policy` instead.

**Category 2 — Post-plan mutation of an upstream response:**
These operations mutate headers on an existing upstream response after the
HeaderPlan commit. They are not full-response synthesis. They execute after
the core plan commits and fall within the atomic scope boundary below
(pre-send best-effort with hard abort):

- ETag set/clear
- `Vary: Accept` add
- `X-Markdown-Tokens` header
- Auth `Cache-Control` modify

Any **new** exception requires ADR justification and an entry here. No other code may mutate `headers_out` in place outside HeaderPlan **for core fields** (Content-Type, Content-Encoding, Content-Length). The post-plan operations listed above (status, `last_modified_time`, ETag, Vary, X-Markdown-Tokens, Auth Cache-Control) are explicitly exempt from the HeaderPlan atomicity invariant. They execute after the plan commits under the pre-send best-effort boundary.

### Post-commit error boundary

Once commit succeeds and headers are sent, a streaming post-commit error
**cannot** count as pre-commit: it does not follow `markdown_error_policy`
pass/fail_closed/status selection. Allowed: stop output, close the downstream
connection, log reason code `streaming_mid_flight_error` (ADR-0018), emit
metrics. Forbidden: pass original content, return 200 with truncated body, or
"reliably" rewrite HTTP status.

### Atomic scope boundary (0.9.0 pragmatic contract)

The HeaderPlan prepare/commit covers the **core** header mutations that
directly affect wire-level correctness:

- Content-Type (set via dedicated field + stale list-entry delete-all)
- Content-Encoding (delete-all)
- Content-Length (delete-all — stale original invalidated atomically)

The following **post-plan operations** execute after the plan commits
successfully. They are **pre-send best-effort with hard abort**: a failure in
any of them returns `NGX_ERROR` before the module calls `ngx_http_send_header()`, so
no partially-mutated headers reach the wire. However, they fall outside
the plan's rollback guarantee — a failure here leaves the already-committed
core mutations in place (which is safe: Content-Type/Content-Encoding/old
Content-Length are already correct, and the request will error out before
headers are sent).

| Post-plan operation | Failure handling |
|---------------------|------------------|
| ETag set/clear | `NGX_ERROR` — abort before send |
| `Vary: Accept` add | `NGX_ERROR` — abort before send |
| Generated Content-Length set (new value after plan commit) | Scalar assignment — cannot fail |
| `X-Markdown-Tokens` header | `NGX_ERROR` — abort before send |
| `Accept-Ranges` removal | Scalar assignment — cannot fail |
| Auth `Cache-Control` modify | `NGX_ERROR` — abort before send |
| `Status` line / `Last-Modified` timestamp | Scalar field assignments — cannot fail; applied outside the HeaderPlan atomicity boundary, together with the other post-plan operations before send |

**Rationale**: The atomicity invariant above applies only to core in-place
mutations covered by HeaderPlan (Content-Type, Content-Encoding, and
Content-Length). Expanding the plan to cover these post-plan operations would
require adding ETag set/clear, Vary add, and token header to the Rust
`markdown_build_header_plan` FFI, increasing the FFI surface and coupling Rust
to NGINX-specific list-push semantics. The current design keeps Rust
plan-building pure (core wire-critical mutations only) and handles
NGINX-lifecycle-specific header operations (ETag, Vary, token, and
authentication headers) in C post-plan. This is a pragmatic
0.9.0 contract, if future requirements demand full atomicity for these
operations, they should migrate into the Rust plan with corresponding FFI
expansion.

## Consequences

### Positive

- No partial mutation occurs within the HeaderPlan-covered fields. The commit
  is allocation-free and cannot half-apply.
- Content-Type, Content-Encoding, and Content-Length edge cases handled in
  one atomic place. ETag and Vary remain explicit post-plan operations under
  the scope boundary above.
- A post-plan failure can leave the core mutations applied, but it occurs
  before `ngx_http_send_header()` and therefore cannot expose those partial
  headers on the wire.
- Honest streaming post-commit semantics (no false 502/rewrite promises).

### Negative

- Prepare must over-allocate worst-case header entries up front.
- The implementation must migrate existing scattered mutations into HeaderPlan.
- Fault-injection test surface grows.

## Alternatives Considered

- **Allocate-during-commit with rollback**: rejected — NGINX pool allocations are
  not reversible, rollback of allocation is impossible.
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
| 0.9.2 | 2026-08-20 | Hermes | Scope HeaderPlan atomicity invariant to core fields (Content-Type/Content-Encoding/Content-Length); move stream_postcommit into a distinct post-commit terminal exception category |
| 0.9.2 | 2026-08-19 | Hermes | Clarify stream_postcommit exception: post-commit terminal closing only, never a synthesized error body, replay, status replacement, or second send_header |
| 0.9.0 | 2026-07-02 | Kang | Documented atomic scope boundary: core plan vs post-plan best-effort with hard abort |
| 0.9.0 | 2026-06-30 | Kang | Initial ADR — prepare/commit split, special-field atomicity, exception table, post-commit boundary |
