# Header Mutation Inventory & Exception Table

| Field | Value |
|-------|-------|
| Release | 0.9.0 (in progress) |
| Source of truth | `components/nginx-module/src/ngx_http_markdown_header_plan.{c,h}` |

This document records the response-header mutation contract for the
NGINX module: which paths route through the atomic `HeaderPlan`, and which
are documented exceptions. Any new `r->headers_out` direct write that is
not routed through `HeaderPlan` must be added to the Exception Table with
justification (and, for non-trivial cases, an architecture decision
record under `docs/architecture/ADR/`).

---

## 1. HeaderPlan Model (current)

`ngx_http_markdown_apply_header_plan()` applies a Rust-built plan in two
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

**Atomicity guarantee:** either every prepared mutation is applied
(commit) or none are (prepare aborted before commit). There is no partial
mutation on any failure path. This replaces the prior 0.8.x rollback model
(allocate-while-mutating + undo), which could not guarantee an
allocation-free, failure-free apply step.

### Operations currently modeled by the FFI plan

| op_type | Operation | prepare | commit |
|---------|-----------|---------|--------|
| 0 | `Set` | copy value (overwrite) or push inert slot + copy key/value (new); Content-Type redirects to delete-all of stale list entries | assign value, or assign key/value + `hash = 1` |
| 1 | `Delete` | locate first match | `hash = 0` |
| 2 | `Set-ETag placeholder` | no-op (real ETag written by caller post-commit) | no-op |
| 3 | `DeleteAll` | count + collect all matches (no mutation) | `hash = 0` for each match |

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
exceptions to HeaderPlan routing.

| Path | File | Exception? | Justification |
|------|------|------------|---------------|
| Metrics endpoint | `ngx_http_markdown_metrics_impl.h` | YES — documented | Full-response synthesis (self-produced metrics response; no upstream to mutate). |
| Diagnostics endpoint | `ngx_http_markdown_diagnostics.c` | YES — documented | Full-response synthesis (self-produced JSON runtime state). |
| Streaming post-commit error | `ngx_http_markdown_stream_error.c` | YES — documented | Headers were already committed/sent downstream; status cannot be reliably rewritten. Allowed behavior: stop output, close connection, log + metrics/diagnostics with a reason code. Forbidden: re-running HeaderPlan, passing original content, or claiming a status rewrite. |

**Post-commit boundary:** once `HeaderPlan` commit succeeds and headers
are sent, a streaming mid-flight error is NOT a pre-commit error. It does
not follow the fail-open/fail-closed status selection because the
downstream headers are already committed and the upstream connection is
typically gone in streaming mode.

---

## 3. Scattered in-place mutation sites — migration status

These paths mutate an upstream response in place and are therefore in
scope for HeaderPlan routing. The 0.9.0 target is to route all of them
through the two-phase plan.

| Path | File | Mutation | Status |
|------|------|----------|--------|
| Conversion success (full-buffer) | `ngx_http_markdown_conversion_impl.h` / `ngx_http_markdown_headers_impl.h` | Content-Type, Content-Encoding (delete-all), Content-Length (delete-all) via plan; ETag + Vary: Accept + Last-Modified outside plan | Partially routed (plan applies CT/CE/CL; ETag/Vary/Last-Modified still outside plan) — **consolidation deferred** |
| Conversion success (streaming) | `ngx_http_markdown_stream_commit.c` | set Content-Type, `content_length_n = -1`, invalidate Content-Length entry, Vary: Accept | **consolidation deferred** |
| Conditional (ETag / Last-Modified) | `ngx_http_markdown_conditional.c` | set ETag, push header entries | **consolidation deferred** |
| Error (pre-commit) | `ngx_http_markdown_stream_error.c` | set status, set Content-Type | **consolidation deferred** |
| Decompression | `ngx_http_markdown_decompression.c` | clear Content-Encoding | **consolidation deferred** |
| Auth | `ngx_http_markdown_auth.c` | push WWW-Authenticate / Cache-Control | **consolidation deferred** |
| Payload | `ngx_http_markdown_payload_impl.h` | set `last_modified_time` | **consolidation deferred** |

### Why consolidation is deferred (and what it requires)

Routing the remaining sites through `HeaderPlan` requires extending the
FFI plan with new operation types (`Append`, `Preserve`, `SetContentType`,
`SetContentLength`, `SetEtag`, `SetStatus`, `SetLastModified`) plus a
Vary: Accept dedup operation. That is an **additive FFI/ABI change**
(`FFIHeaderEntry` op_type expansion) requiring:

- Rust `HeaderOp` enum + builders (markdown conversion, error pre-commit,
  bypass / pass-through, 304 / HEAD / no-body),
- cbindgen regeneration + `make check-headers`,
- migration of the streaming commit / conditional / decompression / auth
  sites, several of which sit on the fail-open and streaming-backpressure
  invariants and require streaming runtime / E2E verification
  (`make verify-chunked-native-e2e-smoke`, which needs a locally-compiled
  NGINX binary).

Because those sites cannot be E2E-verified without a built NGINX binary,
they are tracked as follow-up work rather than landed unverified.

### Content-Length / Vary / invalidated-entry contract (target)

When consolidation lands, the plan MUST:

1. **Content-Length removal** clears both the numeric field
   (`content_length_n = -1`) and the header-list entry (`hash = 0`).
2. **Vary: Accept** is deduplicated: prepare scans existing `Vary`
   entries (comma-split, trimmed, case-insensitive); if `Accept` is
   already present the commit is a no-op, otherwise it appends `Accept`.
3. All lookup/iteration filters invalidated (`hash == 0`) entries —
   already enforced by `ngx_http_markdown_plan_for_each_header_named`.

---

## 4. Read-only access (no action required)

Paths that only read `r->headers_out` (eligibility, decision logging,
option marshaling) do not require HeaderPlan routing.
