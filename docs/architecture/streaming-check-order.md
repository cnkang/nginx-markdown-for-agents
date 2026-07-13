# Streaming Security Check Order Verification

| Field | Value |
|-------|-------|
| Scope | Streaming Security, Resource Limits, and Compression |
| Task | 1.2 — Verify each runs before streaming candidate |
| Date | 2026-06-05 |
| Property | No Bypass (Design Property 1) |
| Status | **PASS** — all security checks confirmed to run before streaming candidate evaluation |

---

## Summary

Every eligibility and policy check from the 0.7.x full-buffer path executes
BEFORE the streaming candidate evaluation point
(`ngx_http_markdown_select_processing_path`). The streaming path cannot bypass
any security check.

---

## Code Path Traced

The header filter (`ngx_http_markdown_header_filter` in
`ngx_http_markdown_request_impl.h`) is the single entry point for both
full-buffer and streaming paths. The decision chain executes in strict
sequence:

```text
header_filter entry
  |
  +-- 1. conf == NULL check -> passthrough
  +-- 2. Build effective_conf (snapshot binding)
  +-- 3. filter_enabled check (markdown_filter directive) -> passthrough if disabled
  +-- 4. ngx_http_markdown_check_eligibility() -> passthrough on any failure
  |      +-- config enabled
  |      +-- method (GET/HEAD only)
  |      +-- status_code (200 only, 206->range)
  |      +-- range header
  |      +-- unbounded streaming (text/event-stream + stream_types)
  |      +-- content_type (allowlist check)
  |      +-- size_limit (Content-Length vs max_size)
  +-- 5. auth_policy check -> passthrough if deny+authenticated
  +-- 6. Accept negotiation (ngx_http_markdown_should_convert) -> passthrough if no MD preference
  +-- 7. Context allocation
  +-- 8. Decompression detection (compression strategy)
  |
  +-- 9. *** STREAMING CANDIDATE EVALUATION ***
         ngx_http_markdown_select_processing_path()
```

The streaming candidate evaluation (step 9) only runs AFTER steps 1-8 all pass.
Any failure at steps 1-6 results in `return ngx_http_next_header_filter(r)` --
the request passes through without ever reaching the streaming path selector.

---

## Per-Check Verification

### auth_policy (Requirement 2.1) -- PASS

- **Location**: `ngx_http_markdown_request_impl.h`, header_filter, after
  `check_eligibility()` returns ELIGIBLE.
- **Code**: `if (conf->policy.auth_policy == DENY && is_authenticated(r, conf))`
- **Ordering**: Executes at step 5, well before streaming candidate (step 9).
- **Verdict**: CONFIRMED -- runs before streaming.

### content_type (Requirement 2.1) -- PASS

- **Location**: `ngx_http_markdown_eligibility.c`, inside
  `ngx_http_markdown_check_eligibility()`.
- **Code**: `ngx_http_markdown_check_content_type(r, conf)` -- allowlist match
  against `text/html` (default) or `markdown_content_types`.
- **Ordering**: Executes at step 4, inside eligibility check.
- **Verdict**: CONFIRMED -- runs before streaming.

### status_code (Requirement 2.1) -- PASS

- **Location**: `ngx_http_markdown_eligibility.c`, inside
  `ngx_http_markdown_check_eligibility()`.
- **Code**: `ngx_http_markdown_check_status(r)` -- only 200 OK eligible.
- **Ordering**: Executes at step 4, inside eligibility check.
- **Verdict**: CONFIRMED -- runs before streaming.

### Accept negotiation (Requirement 2.1) -- PASS

- **Location**: `ngx_http_markdown_request_impl.h`, header_filter, after
  auth_policy check.
- **Code**: `ngx_http_markdown_should_convert(r, conf, &accept_reason)` -- FFI
  call to Rust Accept header negotiator.
- **Ordering**: Executes at step 6, before streaming candidate (step 9).
- **Verdict**: CONFIRMED -- runs before streaming.

### Hard exclusions -- text/event-stream (Requirement 4) -- PASS

- **Location**: `ngx_http_markdown_eligibility.c`, inside
  `ngx_http_markdown_is_streaming()` called from `check_eligibility()`.
- **Code**: Hardcoded prefix match against `text/event-stream`.
- **Ordering**: Executes at step 4, inside eligibility check.
- **Additional**: Also checked in `select_processing_path()` Rule 6 as defense-in-depth.
- **Verdict**: CONFIRMED -- runs before streaming (double-guarded).

### Hard exclusions -- application/x-ndjson, application/stream+json (Requirement 4) -- PASS

- **Location**: These types are excluded by the content_type allowlist check
  (step 4) since only `text/html` (or user-configured types) passes.
- **Defense-in-depth**: `ngx_http_markdown_stream_type_excluded()` provides an
  explicit hard-exclusion function covering all three types. The streaming
  engine selector (Rule 7 in `select_processing_path`) also checks
  `stream_types` exclusion list.
- **Ordering**: The content_type allowlist rejects them at step 4. Even if
  a user configures `markdown_content_types application/x-ndjson`, the
  `ngx_http_markdown_is_streaming()` check (step 4) catches
  user-configured `stream_types`, and the streaming engine selector
  (step 9, Rule 7) provides a final defense layer.
- **Verdict**: CONFIRMED -- implicitly excluded by content_type allowlist
  before streaming; explicitly excluded by `stream_type_excluded()` for
  defense-in-depth.

### Resource limits -- size_limit (Requirement 1) -- PASS

- **Location**: `ngx_http_markdown_eligibility.c`, inside
  `ngx_http_markdown_check_eligibility()`.
- **Code**: `ngx_http_markdown_check_size_limit(r, conf)` -- checks
  Content-Length against `markdown_limits memory=<size>`.
- **Ordering**: Executes at step 4, inside eligibility check.
- **Note**: When Content-Length is absent (chunked), the check passes and size
  enforcement is deferred to the body filter (budget tracking during streaming
  feed calls -- covered by Requirement 1 / Task 2).
- **Verdict**: CONFIRMED -- static limit checked before streaming; dynamic
  budget enforced during streaming (separate concern).

### Compression handling (Requirement 3) -- PASS

- **Location**: `ngx_http_markdown_request_impl.h`, header_filter, step 8
  (after Accept negotiation, before streaming candidate).
- **Code**: `ngx_http_markdown_detect_compression(r)` -- detects Content-Encoding.
  If UNKNOWN format -> fail-open passthrough immediately (never reaches
  streaming). If supported -> sets `decompression.needed` flag for body filter.
- **Ordering**: Executes at step 8, before streaming candidate (step 9).
- **Verdict**: CONFIRMED -- unsupported compression rejected before streaming;
  supported compression flagged for controlled decompression in body filter.

---

## Streaming Engine Selector Internal Checks

`ngx_http_markdown_select_processing_path()` (step 9) applies additional
streaming-specific guards AFTER all security checks pass:

1. Engine == off -> full-buffer
2. HEAD request -> full-buffer
3. 304 Not Modified -> full-buffer
4. conditional_requests full_support -> full-buffer
5. text/event-stream -> full-buffer (defense-in-depth, already excluded at step 4)
6. stream_types exclusion -> full-buffer (defense-in-depth)
7. Engine == on -> streaming
8. Auto mode threshold/chunked logic

These are path-selection decisions, not security gates -- the security gates
all executed earlier in the pipeline.

---

## Streaming Body Filter Confirmation

The streaming body filter (`ngx_http_markdown_streaming_body_filter`) is only
reached when:
1. `ctx != NULL` (header filter set up context -- all checks passed)
2. `ctx->eligible` is true (eligibility confirmed)
3. `ctx->processing_path == PATH_STREAMING` (selector chose streaming)

No eligibility or policy re-evaluation happens in the streaming body filter.
The body filter trusts the header filter's cached decision (per NGINX best
practice: avoid header/body phase inconsistency for dynamic variables).

---

## Conclusion

**Property 1 (No Bypass) is satisfied.** The code structure guarantees that:

1. All security checks execute in `ngx_http_markdown_header_filter` BEFORE
   the streaming candidate evaluation point.
2. The streaming path has NO alternate entry point that could skip these checks.
3. A request can only enter the streaming body filter after passing every
   eligibility gate, auth policy check, and Accept negotiation.
4. Hard-excluded types are doubly guarded: by the content-type allowlist AND
   by explicit streaming exclusion checks.
5. Compression is handled before path selection -- unsupported formats never
   reach streaming.

No gaps found. No remediation needed for this property.

---

## Task 1.3 Remediation Assessment

| Field | Value |
|-------|-------|
| Date | 2026-06-05 |
| Result | **No code changes needed** |
| Reviewer | Agent (streaming security enforcement task 1.3) |

### Function: `ngx_http_markdown_stream_type_excluded()`

Verified in `components/nginx-module/src/ngx_http_markdown_eligibility.c` (lines 364-441):

1. **Case-insensitive matching** — uses `ngx_strncasecmp()` (length-bounded,
   case-insensitive NGINX helper) for all comparisons.
2. **Content-Type parameter stripping** — strips everything after first `;`
   via `ngx_strlchr()`, plus trims trailing whitespace before the semicolon.
3. **All three hard-coded types** — `text/event-stream` (17 chars),
   `application/x-ndjson` (20 chars), `application/stream+json` (23 chars)
   checked with exact-length plus case-insensitive prefix match.

### AGENTS.md Rule Compliance

- **Rule 12 (security)**: Function is read-only comparison; no interpolation
  of untrusted values.
- **Rule 27 (sanitization)**: No output emission; purely boolean gate.
- **Rule 30 (NUL-terminate)**: Uses `ngx_strncasecmp` (length-bounded) and
  `ngx_strlchr` (pointer-bounded); no C standard library string calls that
  require NUL termination.

### Existing Test Coverage

`streaming_config_contract_test.c` provides comprehensive coverage including
all three hard-coded types, case variations, Content-Type parameters, trailing
whitespace, partial match rejection, NULL/empty inputs, and user-configured
exclusions alongside hard exclusions.

**Conclusion**: No gaps found. The function satisfies Requirements 4 (AC 1-4)
as documented in the eligibility audit.
