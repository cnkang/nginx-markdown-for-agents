# 0.7.x Eligibility & Policy Check Audit (Streaming Security)

| Field | Value |
|-------|-------|
| Version | 0.7.16 |
| Created | 2026-06-05 |
| Purpose | Enumerate every eligibility/policy check that gates HTML→Markdown conversion |

---

## Overview

This document inventories every check in the 0.7.x codebase that determines
whether an HTTP response is eligible for conversion and what resource limits
apply during conversion. The streaming engine (v0.8.0 streaming architecture) must enforce
**all** of these checks before evaluating a response as a streaming candidate
(Pre-streaming Policy Gate component).

---

## 1. Header-Filter Policy Checks (Pre-conversion Gate)

These checks execute in `ngx_http_markdown_header_filter()` in
`ngx_http_markdown_request_impl.h`. They run **in sequence**; the first
failure short-circuits to passthrough.

### 1.1 `markdown_filter` Enablement (Config Scope)

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_request_impl.h` |
| Function | `ngx_http_markdown_header_filter` via `ngx_http_markdown_is_enabled()` |
| Directive | `markdown_filter on\|off\|$variable` |
| What | Resolves the enabled state (static or complex expression) once per request |
| On failure | Passthrough — `NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG` |
| Metrics | `requests_entered++`, `skips.config++`, `conversions_bypassed++` |

### 1.2 Request Method

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_eligibility.c` |
| Function | `ngx_http_markdown_check_method()` |
| What | Only `GET` and `HEAD` are eligible (FR-02.1) |
| On failure | Passthrough — `NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD` |

### 1.3 Response Status Code

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_eligibility.c` |
| Function | `ngx_http_markdown_check_status()` |
| What | Only HTTP 200 OK is eligible (FR-02.2). 206 routes to `INELIGIBLE_RANGE`. |
| On failure | Passthrough — `NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS` or `INELIGIBLE_RANGE` |

### 1.4 Range Request Detection

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_eligibility.c` |
| Function | `ngx_http_markdown_has_range_header()` |
| What | Range header present in request — ineligible (partial HTML invalid for conversion) |
| On failure | Passthrough — `NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE` |

### 1.5 Hard Exclusions (Streaming Content-Types)

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_eligibility.c` |
| Function | `ngx_http_markdown_is_streaming()` |
| What | Built-in: `text/event-stream`. User-configured: `markdown_stream_types` array. Case-insensitive prefix + boundary-char match. |
| On failure | Passthrough — `NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING` |

**Additional hard exclusions (v0.8.0 streaming path)**:

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_eligibility.c` |
| Function | `ngx_http_markdown_stream_type_excluded()` |
| What | Built-in hard exclusions that **cannot be removed by user config**: `text/event-stream` (17 chars), `application/x-ndjson` (20 chars), `application/stream+json` (23 chars). Plus user-configured `stream.excluded_types` array. Matching is case-insensitive and ignores Content-Type parameters (strips after `;`). |
| On failure | Passthrough (not eligible for streaming conversion) |
| Requirement | Streaming exclusion Req 5 AC 2–4; Hard Exclusions Req 4 |

### 1.6 Content-Type Allowlist

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_eligibility.c` |
| Function | `ngx_http_markdown_check_content_type()` |
| What | If `markdown_content_types` is configured, matches against that array with prefix + boundary-char semantics. Default: `text/html` only. Case-insensitive. |
| On failure | Passthrough — `NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE` |

### 1.7 Response Size Limit (Content-Length known)

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_eligibility.c` |
| Function | `ngx_http_markdown_check_size_limit()` |
| Directive | `markdown_max_size` (default: 10 MiB) |
| What | If `Content-Length` is present and exceeds `conf->max_size`, ineligible. If `Content-Length` is absent (chunked), passes here and is enforced during buffering. |
| On failure | Passthrough — `NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE` |

### 1.8 Auth Policy

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_request_impl.h` (header filter) + `ngx_http_markdown_auth.c` |
| Function | `ngx_http_markdown_is_authenticated()` |
| Directive | `markdown_auth_policy deny` + `markdown_auth_cookies` patterns |
| What | When `auth_policy == DENY`, checks for Authorization header or configured cookie patterns. If authenticated then ineligible. |
| On failure | Passthrough — `NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH` |
| Order | Runs **after** core eligibility, **before** Accept negotiation |

### 1.9 Accept Header Negotiation

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_accept.c` |
| Function | `ngx_http_markdown_should_convert()` via FFI `markdown_negotiate_accept()` |
| What | RFC 7231 section 5.3.2 q-value comparison with RFC 9110 tie-break rules. Client must prefer `text/markdown` over `text/html`. |
| On failure | Passthrough — `skips.accept++` with reason sub-codes: `NO_ACCEPT`, `LOWER_Q`, `EXPLICIT_REJECT`, `MALFORMED` |
| Order | **Last** check before conversion attempt |
| Directive | `markdown_on_wildcard on\|off` (controls `*/*` interpretation) |

---

## 2. Content-Encoding Handling

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_decompression.c` |
| Function | `ngx_http_markdown_detect_compression()` |
| Directive | `markdown_auto_decompress on\|off` (default: on) |
| What | Reads `Content-Encoding` header. Detects: `gzip`, `deflate`, `br` (brotli). Unknown formats produce `COMPRESSION_UNKNOWN`. |
| Location | Header filter, after eligibility passes, before context init completes |
| On UNKNOWN | Fail-open (pass original) or reject per `on_error` policy |
| On known format | Sets `ctx->decompression.needed = 1`; decompression occurs in body filter |

---

## 3. Resource Limits (Body-Filter Enforcement)

These checks apply **during buffering/conversion** in the body filter path.

### 3.1 `markdown_max_size` (Buffering Budget)

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_payload_impl.h` |
| Function | `ngx_http_markdown_handle_buffer_append_failure()` |
| Directive | `markdown_max_size` (default: 10 MiB) |
| What | Buffer initialized with `max_size` as capacity cap. Each append checks `buffer.size + chunk_size > max_size`. |
| On exceeded | `on_error=pass` then fail-open (forward original HTML). `on_error=reject` then `NGX_ERROR` (502). |
| Metrics | `conversions_failed++`, `failures_resource_limit++` |

**Streaming path enforcement**:

| File | `ngx_http_markdown_streaming_impl.h` (line ~1792) |
|------|---|
| What | `ctx->streaming.total_input_bytes` tracked on every feed; checked against `conf->max_size` |
| On exceeded | Pre-commit: fallback to full-buffer. Post-commit: safe_finish or abort. |

### 3.2 `markdown_decompress_max_size` (Decompression Budget)

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_decompression.c` + `ngx_http_markdown_payload_impl.h` |
| Functions | `ngx_http_markdown_grow_output_buffer()`, `ngx_http_markdown_decompress_gzip()`, Rust FFI `markdown_decompress_bounded()` |
| Directive | `markdown_decompress_max_size` (default: same as `max_size`) |
| What | Independent budget for decompressed output size. Caps decompression output buffer growth. Prevents decompression bombs. |
| On exceeded | Error code `ERROR_DECOMPRESSION_BUDGET_EXCEEDED` (9). Classified as `RESOURCE_LIMIT`. Applies `on_error` policy. |
| Rust FFI | `markdown_decompress_bounded()` receives the budget as a parameter; returns `DECOMP_CATEGORY_BUDGET_EXCEEDED` (101) if output exceeds it. |

### 3.3 `markdown_parser_budget` (Parser Memory Budget)

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_conversion_impl.h` passes to Rust converter |
| Function | Passed as `options->parser_memory_budget` to `markdown_convert()` / `markdown_streaming_new()` |
| Directive | `markdown_parser_budget` (default: 64 MiB) |
| What | Maximum memory the HTML parser may allocate. Enforced inside the Rust parser. |
| On exceeded | Error code `ERROR_PARSE_BUDGET_EXCEEDED` (11). Classified as `RESOURCE_LIMIT`. |
| Enforcement | Rust-side parser tracks allocations against this ceiling. |

### 3.4 `markdown_memory_budget` (Unified Memory Budget)

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_conversion_impl.h`, `ngx_http_markdown_dynconf_impl.h` |
| Function | `ngx_http_markdown_effective_memory_budget()` populates `options->memory_budget` |
| Directive | `markdown_memory_budget` (default: NGX_CONF_UNSET_SIZE = not set) |
| What | Unified cap for both streaming and full-buffer paths. Currently enforced by Rust streaming/incremental converters; full-buffer relies on NGINX-side `max_size`. |
| Priority | explicit per-engine > unified memory_budget > compiled default |
| On exceeded | `ERROR_MEMORY_LIMIT` (4) or `ERROR_BUDGET_EXCEEDED` (6, streaming). |

### 3.5 `markdown_streaming_budget` (Streaming Working-Set Budget)

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_streaming_impl.h`, Rust `streaming/budget.rs` |
| Directive | `markdown_streaming_budget` (default: 2 MiB) |
| What | Caps streaming converter working-set memory. Enforced by `MemoryBudget` struct in Rust with per-stage caps (state_stack, output_buffer, lookahead, total). |
| Priority | explicit streaming_budget > memory_budget > 2 MiB default |
| On exceeded | `ERROR_BUDGET_EXCEEDED` (6). Pre-commit: fallback. Post-commit: abort. |

### 3.6 Replay Buffer / Pre-Commit Buffer

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_stream_replay.h`, `ngx_http_markdown_streaming_impl.h` |
| Functions | `ngx_http_markdown_stream_replay_init()`, `ngx_http_markdown_stream_replay_append()`, `ngx_http_markdown_stream_replay_available()` |
| Directive | `markdown_stream_precommit_buffer` (default: 256 KiB) |
| What | Stores original upstream bytes during pre-commit streaming so fail-open can replay them. Capacity is `conf->stream.precommit_buffer`. |
| On init failure | `precommit_error` state — cannot guarantee fail-open data integrity (Rule 38). |
| On overflow | `REPLAY_OVERFLOW` event — state machine transitions to `PRE_COMMIT_REPLAY_UNAVAILABLE`; decision engine decides fallback or commit. |
| Streaming path | Also: `ctx->streaming.failopen_replay_buf` (legacy streaming path) with same semantics. |

### 3.7 `markdown_parse_timeout` (Parse Deadline)

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_conversion_impl.h` passes to Rust converter |
| Directive | `markdown_parse_timeout` (default: 30 s) |
| What | Deadline for HTML parsing phase. Passed as `options->parse_timeout_ms`. |
| On exceeded | `ERROR_PARSE_TIMEOUT` (10). Classified as `RESOURCE_LIMIT`. |

### 3.8 `markdown_timeout` (Overall Conversion Timeout)

| Field | Value |
|-------|-------|
| File | `ngx_http_markdown_conversion_impl.h` passes to Rust converter |
| Directive | `markdown_timeout` (default: 5000 ms) |
| What | Overall conversion deadline passed to Rust. |
| On exceeded | `ERROR_TIMEOUT` (3). Applies `on_error` policy. |

---

## 4. Decision Chain Order

The documented decision chain (Requirement 2.1) enforces this sequence:

```
scope (config enabled)
  -> method (GET/HEAD)
    -> status (200 only)
      -> range (no Range header / not 206)
        -> streaming exclusion (SSE, NDJSON, stream+json, configured stream_types)
          -> content-type allowlist (text/html or configured list)
            -> size limit (Content-Length vs max_size)
              -> auth policy (deny authenticated if configured)
                -> Accept negotiation (client prefers text/markdown)
                  -> [ELIGIBLE -- proceed to conversion]
```

All checks run in the **header filter** before any body buffering begins.
The streaming candidate evaluation (v0.8.0 streaming) must execute **after** all
the above checks pass.

---

## 5. Error Policy Semantics

| Directive | Value | Behavior |
|-----------|-------|----------|
| `markdown_on_error` | `pass` (default) | Conversion failure: return original HTML (fail-open) |
| `markdown_on_error` | `reject` | Conversion failure: return 502 Bad Gateway |

These semantics apply to:
- Buffer size exceeded
- Decompression budget exceeded
- Parser budget exceeded
- Timeout exceeded
- Unsupported compression format
- Context allocation failure

---

## 6. Summary Table

| # | Check | File | Directive | On Failure |
|---|-------|------|-----------|------------|
| 1 | Config scope | request_impl.h | `markdown_filter` | passthrough |
| 2 | Method | eligibility.c | — | passthrough |
| 3 | Status code | eligibility.c | — | passthrough |
| 4 | Range request | eligibility.c | — | passthrough |
| 5 | Hard exclusions (SSE/NDJSON/stream+json) | eligibility.c | `markdown_stream_types`, `stream.excluded_types` | passthrough |
| 6 | Content-Type | eligibility.c | `markdown_content_types` | passthrough |
| 7 | Size limit (header) | eligibility.c | `markdown_max_size` | passthrough |
| 8 | Auth policy | auth.c + request_impl.h | `markdown_auth_policy`, `markdown_auth_cookies` | passthrough |
| 9 | Accept negotiation | accept.c via Rust FFI | `markdown_on_wildcard` | passthrough |
| 10 | Content-Encoding | decompression.c | `markdown_auto_decompress` | fail-open/reject |
| 11 | Body size (buffering) | payload_impl.h | `markdown_max_size` | fail-open/reject |
| 12 | Decompression budget | decompression.c | `markdown_decompress_max_size` | fail-open/reject |
| 13 | Parser budget | Rust converter | `markdown_parser_budget` | fail-open/reject |
| 14 | Memory budget | Rust converter | `markdown_memory_budget` | fail-open/reject |
| 15 | Streaming budget | Rust streaming | `markdown_streaming_budget` | fallback/abort |
| 16 | Replay buffer | stream_replay.h | `markdown_stream_precommit_buffer` | precommit_error |
| 17 | Parse timeout | Rust converter | `markdown_parse_timeout` | fail-open/reject |
| 18 | Conversion timeout | Rust converter | `markdown_timeout` | fail-open/reject |

---

## 7. Implications for Streaming (Security & Resource Limits)

The Pre-streaming Policy Gate (Design Component 1) must ensure:

1. **Checks 1-9** all run in the header filter **before** streaming candidate evaluation.
2. **Check 10** (Content-Encoding) must route compressed responses to full-buffer or passthrough (Requirement 3: Compression Handling).
3. **Hard exclusions** (check 5) must use `ngx_http_markdown_stream_type_excluded()` which is parameter-aware and case-insensitive — matching the Requirement 4: Hard Exclusions requirements.
4. **Checks 11-18** are enforced incrementally during the body filter streaming path via the Budget Tracker (Design Component 2).

No check from the 0.7.x full-buffer path may be bypassed by the streaming path.
