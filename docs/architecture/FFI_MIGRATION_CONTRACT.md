# FFI Migration Contract — v0.7.0

## Purpose

This document defines the ownership, migration status, and compatibility
constraints for every FFI function and struct at the Rust↔C boundary.
It serves as the single source of truth for the Rust-first architecture
migration strategy.

## Boundary Overview

The FFI boundary is implemented in:
- **Rust**: `components/rust-converter/src/ffi/` (abi.rs, exports.rs, convert.rs, options.rs, memory.rs, streaming.rs, incremental.rs)
- **C header** (auto-generated): `components/rust-converter/include/markdown_converter.h`
- **C header** (checked-in copy): `components/nginx-module/src/markdown_converter.h`
- **C consumer**: `components/nginx-module/src/ngx_http_markdown_*.c/h`

## FFI Function Registry

| FFI Function | Rust Owner | C Consumer | Migration Status | Compat Constraint |
|-------------|-----------|-----------|-----------------|------------------|
| `markdown_converter_new` | Rust | C | **Stable** | Returns opaque handle |
| `markdown_convert` | Rust | C | **Stable** | Full-buffer conversion |
| `markdown_converter_free` | Rust | C | **Stable** | Releases handle |
| `markdown_result_free` | Rust | C | **Stable** | Releases result buffers |
| `markdown_negotiate_accept` | Rust | C | **v0.7.0 NEW** | Accept header negotiation |
| `markdown_build_header_plan` | Rust | C | **v0.7.0 NEW** | Returns Rust-owned plan + opaque handle |
| `markdown_header_plan_free` | Rust | C | **v0.7.0 NEW** | Releases plan handle and owned buffers |
| `markdown_streaming_new` | Rust | C | **Stable** | Streaming converter handle |
| `markdown_streaming_new_with_code` | Rust | C | **Stable** | Streaming converter handle with error code |
| `markdown_streaming_feed` | Rust | C | **Stable** | Incremental input |
| `markdown_streaming_finalize` | Rust | C | **Stable** | End-of-stream |
| `markdown_streaming_abort` | Rust | C | **Stable** | Error cleanup |
| `markdown_streaming_output_free` | Rust | C | **Stable** | Releases output buffer |
| `markdown_incremental_*` | Rust | C | **Stable** | Incremental API |

## FFI Struct Registry

| Struct | Rust Owner | Size (bytes) | Layout Stability | Migration Status |
|--------|-----------|-------------|-----------------|-----------------|
| `MarkdownOptions` | Rust→C (input) | — | `repr(C)`, tail-append only | **Stable** |
| `MarkdownResult` | Rust→C (output) | 64 | `repr(C)`, tail-append only | **Stable** |
| `FFIAcceptResult` | Rust→C (output) | 2 | `repr(C)` | **v0.7.0 NEW** |
| `FFIHeaderPlan` | Rust→C (output) | ABI-defined | `repr(C)` + opaque handle | **v0.7.0 NEW** |
| `StreamingConverterHandle` | Rust (opaque) | — | Opaque pointer | **Stable** |
| `MarkdownConverterHandle` | Rust (opaque) | — | Opaque pointer | **Stable** |

## Error Code Registry

Reason code ownership follows the same single-source contract: Rust defines the
canonical reason-code values and C consumes/mirrors without introducing
independent semantic forks.

| Code | Constant | Category | Owner | Since |
|------|----------|----------|-------|-------|
| 0 | `ERROR_SUCCESS` | — | Rust | v0.5.0 |
| 1 | `ERROR_PARSE` | conversion | Rust | v0.5.0 |
| 2 | `ERROR_ENCODING` | conversion | Rust | v0.5.0 |
| 3 | `ERROR_TIMEOUT` | resource_limit | Rust | v0.5.0 |
| 4 | `ERROR_MEMORY_LIMIT` | resource_limit | Rust | v0.5.0 |
| 5 | `ERROR_INVALID_INPUT` | conversion | Rust | v0.5.0 |
| 6 | `ERROR_BUDGET_EXCEEDED` | resource_limit | Rust (streaming) | v0.6.0 |
| 7 | `ERROR_STREAMING_FALLBACK` | system | Rust (streaming) | v0.6.0 |
| 8 | `ERROR_POST_COMMIT` | conversion | Rust (streaming) | v0.6.0 |
| 9 | `ERROR_DECOMPRESSION_BUDGET_EXCEEDED` | resource_limit | Rust | v0.7.0 |
| 10 | `ERROR_PARSE_TIMEOUT` | resource_limit | Rust | v0.7.0 |
| 11 | `ERROR_PARSE_BUDGET_EXCEEDED` | resource_limit | Rust | v0.7.0 |
| 99 | `ERROR_INTERNAL` | system | Rust | v0.5.0 |

## Migration Priority

Functions ordered by migration risk and complexity (highest first):

1. **`markdown_convert`** — Core conversion; already Rust-owned. No migration needed.
2. **`markdown_negotiate_accept`** — New in v0.7.0; Rust-first from inception.
3. **Error classification** — C-side `ngx_http_markdown_classify_error()` must stay in sync with Rust `ConversionError::code()`. Adding new Rust variants requires updating C-side switch.
4. **Config option structs** — `MarkdownOptions` fields added at tail only; C-side init sites must be updated.

## Compatibility Rules

1. **Tail-append only**: New fields in `repr(C)` structs MUST be appended after existing fields. Never reorder or insert.
2. **Feature-gated fields**: Streaming/incremental-only fields are `#ifdef`-gated in the C header. Layout drift within a feature gate is a breaking change.
3. **Header sync**: Both copies of `markdown_converter.h` MUST be byte-identical. `make check-headers` enforces this.
4. **Error code uniqueness**: Every `ERROR_*` constant MUST have a unique value. The Rust `layout_tests::test_error_codes_distinct` test enforces this.
5. **Opaque pointers**: `MarkdownConverterHandle` and `StreamingConverterHandle` are opaque to C. C never dereferences them.
6. **Header plan lifetime**: `FFIHeaderPlan.entries` remains valid until `markdown_header_plan_free()`; C must not retain pointers after free.

## Zero/Default Initialization Strategy

### Strategy

All FFI structs crossing the Rust↔C boundary MUST be initialized via their
corresponding `markdown_*_init()` helper function. Direct zero-initialization
(`memset(&s, 0, sizeof(s))`) and C literal initialization (`= {0}`) are
**prohibited** for FFI structs.

### Rationale

When new fields are appended to structs (tail-append ABI evolution per
Compatibility Rule #1), literal initialization may miss newly added fields.
Helper functions guarantee that **all** fields — including future ones added in
later versions — are set to valid, semantically correct defaults. This
eliminates an entire class of bugs where:

1. A new field is appended to a `repr(C)` struct in Rust.
2. C call sites using `= {0}` or `memset` compile without error.
3. The new field receives a zero bit pattern that may not represent a valid
   default (e.g., a zero enum discriminant mapping to an unintended variant,
   or a zero budget meaning "unlimited" instead of "use default").

Helper functions are maintained alongside the struct definition in Rust and
are regenerated by cbindgen, ensuring they always cover every field.

### Available Helpers

| Helper Function | Struct Initialized | Since |
|----------------|-------------------|-------|
| `markdown_options_init()` | `MarkdownOptions` | v0.7.0 |
| `markdown_result_init()` | `MarkdownResult` | v0.7.0 |
| `markdown_accept_result_init()` | `FFIAcceptResult` | v0.7.0 |
| `markdown_conditional_result_init()` | `FFIConditionalResult` | v0.7.0 |
| `markdown_decision_result_init()` | `FFIDecisionResult` | v0.7.0 |
| `markdown_header_plan_init()` | `FFIHeaderPlan` | v0.7.0 |

Each helper:
- Accepts a non-NULL pointer to the target struct.
- Sets every field to its semantically correct default value.
- Is safe to call on already-initialized structs (idempotent).
- Is a no-op when passed NULL (defensive guard).

Corresponding cleanup/free functions release any Rust-owned resources:

| Cleanup Function | Struct | Notes |
|-----------------|--------|-------|
| `markdown_result_free()` | `MarkdownResult` | Frees output buffer; idempotent |
| `markdown_header_plan_free()` | `FFIHeaderPlan` | Frees plan entries and handle |
| `markdown_converter_free()` | `MarkdownConverterHandle` | Releases converter |
| `markdown_streaming_free()` | `StreamingConverterHandle` | Releases streaming handle; pairs with both `markdown_streaming_new` and `markdown_streaming_new_with_code` |
| `markdown_incremental_free()` | `IncrementalConverterHandle` | Releases incremental handle |
| `markdown_streaming_output_free()` | streaming output buffer | Frees (data, len) pair |

### C-side Usage Pattern

**Correct** — use helper initialization:

```c
MarkdownOptions opts;
markdown_options_init(&opts);       /* all fields set to valid defaults */
opts.max_size = conf->max_size;     /* override only what you need */
opts.decompression_budget = conf->decompression_budget;

FFIAcceptResult accept_result;
markdown_accept_result_init(&accept_result);
markdown_negotiate_accept(accept_header.data, accept_header.len,
                          on_wildcard, &accept_result);

FFIHeaderPlan plan;
markdown_header_plan_init(&plan);
markdown_build_header_plan(content_type, content_len, etag,
                           vary, &plan);
/* ... use plan ... */
markdown_header_plan_free(&plan);   /* release Rust-owned resources */
```

**Prohibited** — literal or memset initialization:

```c
/* WRONG: may miss fields added in future versions */
MarkdownOptions opts = {0};
opts.max_size = conf->max_size;

/* WRONG: zero bit pattern may not be a valid default */
FFIHeaderPlan plan;
memset(&plan, 0, sizeof(plan));

/* WRONG: partial initialization */
MarkdownResult result = { .output = NULL, .output_len = 0 };
```

### Enforcement

1. **CI header drift check** (`make check-headers`): Detects when Rust struct
   definitions change but C headers are not regenerated. If a new field is
   added, the helper function is automatically updated by cbindgen.

2. **Layout tests** (Rust `layout_tests` module + C `static_assert`): Verify
   that struct sizes and field offsets match between Rust and C. A mismatch
   indicates the init helper may be out of sync.

3. **Code review convention**: Any C code that initializes an FFI struct
   without calling the corresponding `markdown_*_init()` function should be
   flagged during review. The pattern `StructName varname = {0}` or
   `memset(&varname, 0, sizeof(varname))` for FFI structs is a review
   rejection signal.

4. **Rule 15 (FFI cross-language boundary)**: AGENTS.md Rule 15 requires that
   Rust FFI changes update all C-side init sites. Using helpers instead of
   literal initialization means init sites automatically pick up new field
   defaults without per-site changes.

> **Note**: The CI header drift check and layout tests catch ABI mismatches at
> the binary level, but initialization correctness (ensuring all fields have
> valid semantic defaults) requires disciplined use of helper functions. The
> two mechanisms are complementary.

---

## C-side Pure Logic Audit (v0.7.0)

This section identifies C-side functions that perform pure computation without
NGINX API dependencies (pool alloc, chain ops, header list push, request
finalize, etc.). Per the boundary contract §2.2, these belong on the Rust side
and are candidates for migration.

### Automated First-Pass Detector (advisory)

`tools/harness/detect_c_pure_logic.sh` provides a fast, automated first-pass
signal that complements this manual audit. It scans the C module sources and
flags functions that reference no NGINX API as migration candidates. The
detector runs in advisory mode as part of `make harness-security-checks`
(it prints findings but never blocks CI) and has a `--check` strict mode for
deliberate, scoped enforcement runs. **This audit table — not the detector —
remains the authoritative source for migration decisions** (the detector is a
heuristic and intentionally over-reports, since the contract permits a curated
backlog of known C-side pure-logic functions).

### Audit Methodology


1. Scanned all `.c` files under `components/nginx-module/src/`
2. Classified each function by its dependency on NGINX APIs
3. Functions that only perform computation, string matching, enum mapping, or
   data classification without calling `ngx_palloc`, `ngx_list_push`,
   `ngx_http_send_header`, `ngx_http_finalize_request`, or similar NGINX
   lifecycle APIs are marked as **pure logic**
4. Functions that mix pure logic with NGINX glue are marked as **mixed** with
   notes on extractable portions

### Pure Logic Functions — Migration Candidates

| # | Function | File | Current Responsibility | Pure Logic? | Priority | Complexity |
|---|----------|------|----------------------|-------------|----------|------------|
| 1 | `ngx_http_markdown_classify_error` | `ngx_http_markdown_error.c` | Maps Rust error codes to error categories (conversion/resource_limit/system) | **Yes** — pure switch/case mapping | High | Low |
| 2 | `ngx_http_markdown_error_category_string` | `ngx_http_markdown_error.c` | Returns human-readable string for error category enum | **Yes** — pure enum→string lookup | High | Low |
| 3 | `ngx_http_markdown_check_method` | `ngx_http_markdown_eligibility.c` | Checks if request method is GET/HEAD | **Yes** — reads `r->method` field only (no API call) | Medium | Low |
| 4 | `ngx_http_markdown_check_status` | `ngx_http_markdown_eligibility.c` | Checks if response status is 200 | **Yes** — reads `r->headers_out.status` field only | Medium | Low |
| 5 | `ngx_http_markdown_check_content_type` | `ngx_http_markdown_eligibility.c` | Matches Content-Type against allowlist with prefix+boundary semantics | **Yes** — pure string comparison logic | High | Medium |
| 6 | `ngx_http_markdown_check_size_limit` | `ngx_http_markdown_eligibility.c` | Checks Content-Length against configured max_size | **Yes** — pure numeric comparison | Medium | Low |
| 7 | `ngx_http_markdown_is_streaming` | `ngx_http_markdown_eligibility.c` | Detects unbounded streaming content types (SSE, configured exclusions) | **Yes** — pure string matching against type list | High | Medium |
| 8 | `ngx_http_markdown_check_eligibility` | `ngx_http_markdown_eligibility.c` | Orchestrates all eligibility checks into a decision | **Yes** — pure decision logic composing sub-checks | High | Medium |
| 9 | `ngx_http_markdown_eligibility_string` | `ngx_http_markdown_eligibility.c` | Returns human-readable string for eligibility enum | **Yes** — pure enum→string lookup | Medium | Low |
| 10 | `ngx_http_markdown_reason_from_eligibility` | `ngx_http_markdown_reason.c` | Maps eligibility enum to reason code string | **Mixed** — pure mapping but uses `ngx_log_error` for unknown values | High | Low |
| 11 | `ngx_http_markdown_reason_from_error_category` | `ngx_http_markdown_reason.c` | Maps error category to failure reason code string | **Mixed** — pure mapping but uses `ngx_log_error` for unknown values | High | Low |
| 12 | `ngx_http_markdown_reason_converted` | `ngx_http_markdown_reason.c` | Returns "ELIGIBLE_CONVERTED" reason code | **Yes** — trivial accessor | Medium | Low |
| 13 | `ngx_http_markdown_reason_failed_open` | `ngx_http_markdown_reason.c` | Returns "ELIGIBLE_FAILED_OPEN" reason code | **Yes** — trivial accessor | Medium | Low |
| 14 | `ngx_http_markdown_reason_failed_closed` | `ngx_http_markdown_reason.c` | Returns "ELIGIBLE_FAILED_CLOSED" reason code | **Yes** — trivial accessor | Medium | Low |
| 15 | `ngx_http_markdown_reason_skip_accept` | `ngx_http_markdown_reason.c` | Returns "SKIP_ACCEPT" reason code | **Yes** — trivial accessor | Medium | Low |
| 16 | `ngx_http_markdown_reason_ct_route_default` | `ngx_http_markdown_reason.c` | Returns "CT_ROUTE_DEFAULT" reason code | **Yes** — trivial accessor | Low | Low |
| 17 | `ngx_http_markdown_reason_ct_route_configured` | `ngx_http_markdown_reason.c` | Returns "CT_ROUTE_CONFIGURED" reason code | **Yes** — trivial accessor | Low | Low |
| 18 | `ngx_http_markdown_detect_compression` | `ngx_http_markdown_decompression.c` | Detects compression type from Content-Encoding header value | **Mixed** — reads `r->headers_out.content_encoding` then does pure string matching | High | Low |
| 19 | `ngx_http_markdown_chain_size` | `ngx_http_markdown_decompression.c` | Calculates total size of chain buffers | **Mixed** — iterates NGINX chain struct (pointer arithmetic only, no API) | Low | Low |
| 20 | `ngx_http_markdown_calc_output_size` | `ngx_http_markdown_decompression.c` | Estimates safe decompression output buffer size with budget cap | **Mixed** — pure arithmetic but uses `ngx_log_error` for diagnostics | High | Low |
| 21 | `ngx_http_markdown_const_strncasecmp` | `ngx_http_markdown_conversion_impl.h` | Case-insensitive byte comparison for const-qualified slices | **Yes** — pure byte comparison | Low | Low |
| 22 | `ngx_http_markdown_strncasecmp_const` | `ngx_http_markdown_headers_impl.h` | Case-insensitive comparison mirroring strncasecmp semantics | **Yes** — pure byte comparison | Low | Low |
| 23 | `ngx_http_markdown_path_rbtree_choose_branch` | `ngx_http_markdown_config_core_impl.h` | RB-tree branch direction for path metric nodes | **Yes** — pure comparison logic | Low | Low |
| 24 | `ngx_http_markdown_cookie_matches_pattern` | `ngx_http_markdown_auth.c` | Matches cookie name against configured pattern (glob/prefix) | **Yes** — pure string/pattern matching | Medium | Medium |
| 25 | `ngx_http_markdown_cache_control_token_is_public` | `ngx_http_markdown_auth.c` | Checks if a Cache-Control token is "public" | **Yes** — pure string comparison | Low | Low |
| 26 | `ngx_http_markdown_token_equals_ignore_case` | `ngx_http_markdown_auth.c` | Case-insensitive token comparison | **Yes** — pure byte comparison | Low | Low |
| 27 | `ngx_http_markdown_next_cache_control_token` | `ngx_http_markdown_auth.c` | Tokenizes Cache-Control header value | **Yes** — pure parsing/tokenization | Medium | Medium |
| 28 | `ngx_http_markdown_skip_cache_control_separators` | `ngx_http_markdown_auth.c` | Skips whitespace/comma separators in Cache-Control | **Yes** — pure cursor advancement | Low | Low |
| 29 | `ngx_http_markdown_trim_cache_control_token` | `ngx_http_markdown_auth.c` | Trims whitespace from token boundaries | **Yes** — pure cursor adjustment | Low | Low |
| 30 | Streaming reason code accessors (11 functions) | `ngx_http_markdown_reason.c` | Return static reason code strings for streaming paths | **Yes** — trivial accessors | Low | Low |

### Migration Priority Summary

**High Priority** (should migrate in v0.7.0–v0.8.0):

| Category | Functions | Rationale |
|----------|-----------|-----------|
| Error classification | #1, #2 | Already mirrors Rust enum; C switch must stay in sync manually — single-source in Rust eliminates drift risk (R04, R08) |
| Eligibility/decision logic | #5, #7, #8 | Core decision logic; design §2.2 assigns to Rust |
| Reason code mapping | #10, #11 | REQ-0700-RUST-006 mandates Rust as single source for reason codes |
| Compression detection | #18 | Design §2.2 assigns bounded decompression to Rust |
| Decompression budget calc | #20 | Design §2.2 assigns bounded decompression budget logic to Rust |
| Content-type matching | #5 | Part of conversion decision; should be input to Rust decision engine |

**Medium Priority** (v0.8.0–v0.9.0):

| Category | Functions | Rationale |
|----------|-----------|-----------|
| Simple eligibility checks | #3, #4, #6, #9 | Trivial but part of decision chain; migrate when decision engine absorbs full eligibility |
| Reason code accessors | #12–#15 | Migrate when reason codes become Rust-only enum |
| Auth pattern matching | #24, #27 | Token/cookie validation is pure logic per §2.2 |

**Low Priority** (v0.9.0+):

| Category | Functions | Rationale |
|----------|-----------|-----------|
| String utilities | #21, #22, #23, #25, #26, #28, #29 | Generic helpers; low risk staying in C |
| Streaming reason accessors | #30 | Trivial; migrate with streaming engine consolidation |
| Content-type route codes | #16, #17 | Trivial accessors with no drift risk |

### Functions Confirmed as C-side Retained (Not Migration Candidates)

These functions use NGINX APIs and correctly remain on the C side per §2.1:

| Function | File | NGINX API Dependency |
|----------|------|---------------------|
| `ngx_http_markdown_should_convert` | `ngx_http_markdown_accept.c` | `ngx_list_part_t` iteration, `r->headers_in`, `ngx_log_debug` |
| `ngx_http_markdown_handle_if_none_match` | `ngx_http_markdown_conditional.c` | `ngx_pcalloc`, `ngx_pfree`, FFI calls, `ngx_log_*` |
| `ngx_http_markdown_send_304` | `ngx_http_markdown_conditional.c` | `ngx_list_push`, `ngx_pnalloc`, `ngx_http_send_header`, `ngx_http_finalize_request` |
| `ngx_http_markdown_decompress_gzip` | `ngx_http_markdown_decompression.c` | `ngx_pnalloc`, `ngx_create_temp_buf`, `ngx_alloc_chain_link`, pool ops |
| `ngx_http_markdown_decompress_brotli` | `ngx_http_markdown_decompression.c` | Same pool/chain NGINX APIs |
| `ngx_http_markdown_decompress` | `ngx_http_markdown_decompression.c` | Dispatcher using NGINX logging |
| `ngx_http_markdown_buffer_init` | `ngx_http_markdown_buffer.c` | `ngx_pool_cleanup_add` |
| `ngx_http_markdown_buffer_append` | `ngx_http_markdown_buffer.c` | `ngx_alloc`/`ngx_free` (heap, not pool — but tied to pool cleanup) |
| `ngx_http_markdown_add_private_cache_control_header` | `ngx_http_markdown_auth.c` | `ngx_list_push` |
| `ngx_http_markdown_dynconf_snapshot_*` | `ngx_http_markdown_dynconf_snapshot.c` | `ngx_slprintf`, reads conf struct |
| `ngx_http_markdown_diagnostics_*` | `ngx_http_markdown_diagnostics.c` | NGINX handler registration, pool alloc, chain construction |

### Extractable Pure Logic from Mixed Functions

Some functions mix pure logic with NGINX glue. The pure portions can be
extracted into Rust while the NGINX glue remains in C:

| Function | Extractable Logic | Remaining C Glue |
|----------|------------------|------------------|
| `ngx_http_markdown_detect_compression` | String matching against "gzip"/"deflate"/"br" | Reading `r->headers_out.content_encoding` |
| `ngx_http_markdown_calc_output_size` | Arithmetic: `min(input*10, budget, UINT_MAX)` | Logging warnings |
| `ngx_http_markdown_reason_from_eligibility` | Enum→string mapping | Logging unknown values |
| `ngx_http_markdown_reason_from_error_category` | Enum→string mapping | Logging unknown values |
| `ngx_http_markdown_check_eligibility` | Decision composition | Reading request struct fields |

### Migration Strategy Notes

1. **Phase 1 (v0.7.0)**: The Rust decision engine (`src/decision/mod.rs`)
   already implements `make_decision()` which subsumes eligibility checks #3–#8.
   The C-side eligibility functions remain as a parallel path until the FFI
   integration is complete. No immediate removal needed.

2. **Error classification (#1, #2)**: Should be the first to migrate because
   the Rust `ConversionError` enum is already the source of truth. The C-side
   `classify_error()` switch statement must be manually kept in sync — moving
   it to Rust eliminates this drift vector entirely.

3. **Reason codes (#10–#17, #30)**: REQ-0700-RUST-006 mandates Rust as single
   source. The C-side reason code accessors should be replaced by FFI calls to
   `get_reason_code_string()` once the decision engine is fully integrated.

   **v0.7.0 status (B06.2)**: C-side FFI accessor wrappers are now available in
   `ngx_http_markdown_reason_ffi.c`:
   - `ngx_http_markdown_get_reason_code_str(code, &str)` — wraps `markdown_reason_code_str()`
   - `ngx_http_markdown_get_reason_code_metric_key(code, &str)` — wraps `markdown_reason_code_metric_key()`
   - `ngx_http_markdown_reason_code_total_count()` — wraps `markdown_reason_code_count()`

   The legacy C-side string literals in `ngx_http_markdown_reason.c` are marked
   deprecated.  New code must use the FFI accessors.  Full migration of existing
   callsites will happen when the decision engine integration is complete.

4. **Content-type and streaming detection (#5, #7)**: These are inputs to the
   conversion decision. When the Rust decision engine absorbs the full
   eligibility check, these become internal to the Rust side.

5. **Auth token/cookie matching (#24, #27)**: Design §2.2 lists
   "token/ETag/front-matter/pruning logic" as Rust-owned. Cookie pattern
   matching is a form of token validation and should migrate.

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.7.0-draft | 2026-05-17 | agent | Initial FFI migration contract for v0.7.0 |
| 0.7.0-impl | 2026-05-18 | codex | Add header-plan ownership/lifecycle contract |
| 0.7.0-audit | 2026-05-18 | kiro | C-side pure logic audit: 30 functions identified, prioritized for migration |
| 0.7.0-b06.2 | 2026-05-18 | kiro | B06.2: Document C-side FFI accessor wrappers for Rust reason codes |
| 0.7.0-a07.5 | 2026-05-18 | kiro | A07.5: Add zero/default initialization strategy section |
