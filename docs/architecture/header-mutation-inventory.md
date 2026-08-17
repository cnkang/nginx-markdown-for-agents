# Header Mutation Inventory & Exception Table

| Field | Value |
|-------|-------|
| Release | 0.9.2 |
| Source of truth | `components/nginx-module/src/ngx_http_markdown_header_plan.{c,h}`, `components/nginx-module/src/ngx_http_markdown_headers_impl.h`, `components/nginx-module/src/ngx_http_markdown_stream_commit.c` |

This document records the response-header mutation contract for the
NGINX module: which paths route through the atomic `HeaderPlan`, and which
count as documented exceptions. Any new `r->headers_out` direct write that is
not routed through `HeaderPlan` must enter the Exception Table with
justification (and, for non-trivial cases, an architecture decision
record under `docs/architecture/ADR/`).

---

## 1. HeaderPlan Model (0.9.2 full coverage)

The 0.9.2 header plan provides **full coverage** of all upstream-response
header mutations in the conversion flow. The two-phase prepare/commit
the protocol implements at two levels:

### Level 1: FFI plan (Rust-built, C-applied)

`ngx_http_markdown_apply_header_plan()` applies the Rust-built plan in two
explicit phases:

- **prepare** — performs every fallible step: pool allocation, key/value
  string copies, header lookups, list-capacity checks, and per-operation
  validation. It records a `prepared` operation array. It MUST NOT mutate
  any pre-existing `r->headers_out` field. The sole write it performs is
  initializing a freshly pushed list slot to the inert state (`hash == 0`)
  so that an aborted prepare is observably equivalent to a no-op (the
  invalidated-header filter rule drops `hash == 0` entries everywhere).
- **commit** — applies the already-prepared mutations using pointer/scalar
  assignment only. It performs **no allocation**, no lookup, and no
  validation, and therefore has **no failure path**: once prepare
  succeeds, commit cannot fail.

### Level 2: Full-coverage prepare/commit (C-side)

`ngx_http_markdown_update_headers()` (full-buffer) and
`ngx_http_markdown_stream_commit_headers()` (streaming) implement the
**full-coverage** two-phase protocol that includes ALL header operations:

**Prepare phase** (all fallible operations, returns error on failure with
headers unchanged = no-op):
- FFI plan application (Content-Type stale delete-all, Content-Encoding
  delete-all, Content-Length delete-all)
- ETag header slot allocation and value copy
- Vary: Accept lookup, dedup check, append/new-slot allocation
- X-Markdown-Tokens slot allocation and value formatting
- Cache-Control auth modification (allocation + rewrite)

**Commit phase** (pointer/scalar assignment only, zero allocations,
unconditional success):
- Content-Type dedicated field assignment
- ETag header entry populated from pre-allocated memory
- Vary header entry populated or value pointer swapped
- Content-Length numeric field set
- X-Markdown-Tokens entry populated from pre-allocated memory
- Accept-Ranges invalidation (hash=0, pointer clear)
- Content-Encoding pointer clear

**Nothing occurs between commit and `ngx_http_send_header`.**

**Pre-commit plan failure**: If prepare fails, response headers remain in
their original unmodified state. The module frees Rust-owned plan resources. The
module logs the `header_plan_apply_error` reason code and applies the
configured `markdown_error_policy` while the original response is still
recoverable.

**Atomicity guarantee:** either the module applies every prepared mutation
(commit) or none are (prepare aborted before commit). There is no partial
mutation on any failure path. This replaces the prior 0.9.0 "pragmatic
contract" where post-plan operations were "pre-send best-effort with hard
abort" — all operations are now in the prepare phase.

### Operations modeled by the FFI plan

| op_type | Operation | prepare | commit |
|---------|-----------|---------|--------|
| 0 | `Set` | copy value (overwrite) or push inert slot + copy key/value (new); Content-Type redirects to delete-all of stale list entries | assign value, or assign key/value + `hash = 1` |
| 1 | `Delete` | locate first match | `hash = 0` |
| 2 | `Set-ETag placeholder` | no-op (real ETag allocated by C-side prepare) | no-op |
| 3 | `DeleteAll` | count + collect all matches (no mutation) | `hash = 0` for each match |

### C-side full-coverage operations (beyond FFI plan)

| Operation | Prepare | Commit |
|-----------|---------|--------|
| ETag set | push inert slot, copy value bytes | assign key/value/hash=1, set typed pointer |
| ETag clear | invalidate existing entries | clear typed pointer |
| Vary: Accept (new) | push inert slot | assign key/value/hash=1 |
| Vary: Accept (append) | allocate appended value copy | swap value pointer |
| X-Markdown-Tokens | push inert slot, format value | assign key/value/hash=1 |
| Cache-Control auth | scan + allocate rewrite | pointer swap (commit only) |
| Content-Length set | — | scalar assignment |
| Accept-Ranges remove | — | hash=0, pointer clear |
| Content-Encoding clear | — | pointer clear |

### Fault injection (test builds only)

`#ifdef NGX_MARKDOWN_FAULT_INJECTION` exposes
`ngx_http_markdown_header_plan_set_fault_injection(op_index)`, which forces
the prepare phase to fail before preparing the operation at `op_index`.
Compiled out of production builds. Used by
`components/nginx-module/tests/unit/header_plan_prepare_commit_test.c` to
prove no-partial-mutation when the 1st/2nd/3rd operation fails. A separate
test-only commit-begin hook (`NGX_HTTP_MARKDOWN_HEADER_PLAN_COMMIT_HOOK`)
lets the same test assert that the commit phase performs zero pool
allocation.

---

## 2. Exception Table

Paths that synthesize a **complete** response (body + headers) from
scratch are NOT mutating an upstream response and are legitimate
exceptions to HeaderPlan routing. The exception inventory contains
fewer than 5 entries (Requirement 15.5).

| # | Path | File | Exception? | Justification |
|---|------|------|------------|---------------|
| 1 | Metrics endpoint | `ngx_http_markdown_metrics_impl.h` | YES — documented | Full-response synthesis (self-produced metrics response; no upstream to mutate). |
| 2 | Diagnostics endpoint | `ngx_http_markdown_diagnostics.c` | YES — documented | Full-response synthesis (self-produced JSON runtime state). |

**Total exceptions: 2** (well below the <5 threshold).

**No postcommit HeaderPlan exception**: The conversion path (including
postcommit) has NO HeaderPlan exception. All fallible header work (ETag
computation, Vary append, token header, Cache-Control modification)
MUST complete in the prepare phase. Postcommit body errors do NOT
produce new status/header modifications.

**Post-commit boundary:** once `HeaderPlan` commit succeeds and headers
are sent, a streaming mid-flight error is NOT a pre-commit error. It does
not follow the fail-open/fail-closed status selection because the
downstream headers are already committed and the upstream connection is
typically gone in streaming mode. The streaming post-commit error path
(`ngx_http_markdown_stream_postcommit.c`) is NOT an exception — it does not
mutate committed headers or produce new status/header modifications.

---

## 3. Mutation Site Coverage (0.9.2 status)

All upstream-response header mutation paths in the conversion flow are
now routed through the two-phase prepare/commit protocol.

| Path | File | Mutation | Status |
|------|------|----------|--------|
| Conversion success (full-buffer) | `ngx_http_markdown_headers_impl.h` | Content-Type, Content-Encoding, Content-Length, ETag, Vary: Accept, X-Markdown-Tokens, Accept-Ranges, Cache-Control | **Full-coverage prepare/commit** |
| Conversion success (streaming) | `ngx_http_markdown_stream_commit.c` | Content-Type, Content-Length, Content-Encoding, ETag (cleared), Vary: Accept, Cache-Control | **Full-coverage prepare/commit (snapshot/rollback + infallible commit)** |
|| Streaming: X-Markdown-Tokens / Accept-Ranges | `ngx_http_markdown_stream_commit.c` | **Cleared** — upstream `X-Markdown-Tokens` and `Accept-Ranges` are cleared/recomputed before committing the transformed Markdown response | Streaming commit now clears upstream `X-Markdown-Tokens` (not applicable to generated Markdown) and `Accept-Ranges` (range requests not supported on transformed output) so headers describe the generated Markdown representation |
| Conditional (ETag / Last-Modified) | `ngx_http_markdown_conditional.c` | set ETag, push header entries | Routed through prepare/commit in conditional 304 path |
| Payload | `ngx_http_markdown_payload_impl.h` | set `last_modified_time` | Scalar assignment (infallible, no allocation) — not a HeaderPlan candidate |

### Content-Length / Vary / invalidated-entry contract

1. **Content-Length removal** clears both the numeric field
   (`content_length_n = -1`) and the header-list entry (`hash = 0`).
2. **Vary: Accept** deduplicates: prepare scans existing `Vary`
   entries (comma-split, trimmed, case-insensitive). If `Accept` is
   already present the commit is a no-op, otherwise it appends `Accept`.
3. All lookup/iteration filters invalidated (`hash == 0`) entries —
   already enforced by `ngx_http_markdown_plan_for_each_header_named`.

---

## 4. Read-only access (no action required)

Paths that only read `r->headers_out` (eligibility, decision logging,
option marshaling) do not require HeaderPlan routing.
