# Review Report: 0.9.2 Pre-Code-Freeze — Comment Consistency

**Date**: 2026-08-07
**Branch**: `dev/wip-0.9.2-harness` (base: `dev/wip-0.9.2`)
**Scope**: Git-tracked C sources under `components/nginx-module/` (61 files reviewed; 57 production files fully, tests sampled)
**Method**: Per-file verification of comments against implementation; cross-language (FFI header) spot-checks; no files modified during review.

## Summary

- Findings: 27 (High 2 / Medium 12 / Low 13)
- Categories: stale/outdated 9, misleading 8, signature/return-code mismatch 4, FFI boundary mismatch 3, duplicate/orphan blocks 2, placeholder 1
- Tests (tests/unit) sampled clean; no independent high/medium comment drift found in test files.

## Findings

### C-H1 (High) — `components/nginx-module/src/ngx_http_markdown_filter_module.h:591-629`
- **Issue**: "Configuration defaults" doc block is entirely stale. Lists `max_size: 10MB`, `timeout: 5000ms`, `parse_timeout: 30000ms`, `parser_budget: 64MB`, `decompress_max_size: same as max_size`, `large_body_threshold`, `stream.budget` — none of which exist in the 0.9.2 unified limits model.
- **Evidence**: `config_core_impl.h:692-715` bridges `NGX_HTTP_MARKDOWN_LIMITS_*_DEFAULT`; actual defaults (`filter_module.h:518-529`): conversion_timeout=30000ms, parser_timeout=10000ms, conversion_memory=64MB, parser_memory=32MB, streaming_buffer=2MB, decompressed_size=10MB, decompression_ratio=100, max_inflight=64.
- **Fix**: Rewrite the defaults list to the 0.9.2 unified `limits` values referencing the `NGX_HTTP_MARKDOWN_LIMITS_*_DEFAULT` macro names; drop removed fields.
- **Verify**: `make test-nginx-unit`; grep for stale names (`max_size`, `parser_budget`) in the block.

### C-H2 (High) — `components/rust-converter/src/ffi/abi.rs:792,818` + generated `components/nginx-module/src/markdown_converter.h:1428-1431`
- **Issue**: `error_policy` doc says "0=pass, 1=fail_closed; 255=not set" — wrong encoding. Actual `ErrorPolicy` (`config/profile.rs:66-73`): `Pass=0, Status=1, FailClosed=2`.
- **Evidence**: C-side `filter_module.h:439-443` correctly documents 0/1/2. The generated header inherits the stale Rust doc.
- **Fix**: Correct both Rust doc comments to "0=pass, 1=status, 2=fail_closed" and regenerate the FFI header (`make rust-lib` + `make copy-headers`; then `make check-headers`).
- **Verify**: `make check-headers`; grep header for updated text.

### C-M1 (Medium) — `components/nginx-module/src/ngx_http_markdown_streaming_impl.h:45-60`
- **Issue**: Orphan doc block ("Streaming body filter main entry point … `in` chain …") dangles above `ngx_http_markdown_streaming_process_chunk(r, ctx, conf, buf)`; describes a nonexistent entry (`in` param, return semantics mismatch). The real entry is at line 4844; correct process_chunk doc at 61-78.
- **Fix**: Delete the orphan block at 45-60.

### C-M2 (Medium) — `components/nginx-module/src/ngx_http_markdown_streaming_impl.h:132-166`
- **Issue**: "they are cleared in step()" — no `step()` function exists in the file. Latches are cleared by `finalize_request` (3453), `send_deferred_lastbuf` (1465), `record_pending_terminal_success`/`resume_failure` (1589/1613).
- **Fix**: Replace "cleared in step()" with "cleared by their owning helpers at the respective finalization stage".

### C-M3 (Medium) — `components/nginx-module/src/ngx_http_markdown_streaming_impl.h:326-345 vs 1449-1456`
- **Issue**: Forward declaration return-code list omits `NGX_DONE`; definition site lists `NGX_OK, NGX_DONE, NGX_AGAIN, NGX_ERROR`.
- **Fix**: Unify both to the four-value list.

### C-M4 (Medium) — zero-copy path comment contradiction
- **Issue**: `output_decision_impl.h:11,22` says "Zero-copy was removed in 0.9.2 (directive deleted); always pool-copy", while `streaming_impl.h:2362-2441` (and 1076-1077, 1557-1561) document an active zero-copy path (`send_zero_copy_feed_output`, `save_pending(zero_copy=1)`, `perf.zero_copy_output_total`), and `metrics_impl.h:1170-1171` still counts "Zero-Copy Output Total". Decision functions always return POOL_COPY today.
- **Fix**: Keep the retained path but annotate it as retained dead/legacy path: update comments in `output_decision_impl.h` to say the code path is retained but never selected since 0.9.2, and clarify in `streaming_impl.h` zero-copy comments that the path is non-active (decision always POOL_COPY). Leave code untouched (pre-freeze).

### C-M5 (Medium) — `components/nginx-module/src/markdown_converter.h:1086-1089` (FFI doc, op_type 2)
- **Issue**: "the C caller must substitute the actual ETag value from MarkdownResult.etag" — C side (`header_plan.c:639-645`) treats op_type 2 as a no-op zero entry; ETag is set independently via `fullcov_prepare_etag`; tests (`header_plan_apply_test.c:756-778`) assert no mutation.
- **Fix**: Reword to "op_type 2 is an all-zero placeholder entry; C treats it as no-op; actual ETag is set by the C side".

### C-M6 (Medium) — `components/nginx-module/src/ngx_http_markdown_dynconf_impl.h:68-75`
- **Issue**: `NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN 72` documented as "64 hex chars + NUL" (=65). Actual layout (`copy_digest` 2558-2570): "sha256:" prefix (7) + 64 hex + NUL = 72.
- **Fix**: "72 = 'sha256:' prefix (7) + 64 hex chars + NUL".

### C-M7 (Medium) — `components/nginx-module/src/ngx_http_markdown_payload_impl.h:1269-1275`
- **Issue**: "On every failure class the function follows a fail-open strategy" — false for `on_error == REJECT`, which finalizes with `conf->error_status` (fail-closed).
- **Fix**: "Routed by error policy: pass → fail-open original forwarding; fail_closed → configured error status".

### C-M8 (Medium) — `components/nginx-module/src/ngx_http_markdown_stream_replay.c:231-242`
- **Issue**: "points the buffer at the replay data" — implementation (276-283) is `ngx_palloc` + `ngx_memcpy` copy into the request pool.
- **Fix**: "copies the replay data into the request pool and points the buffer at that copy".

### C-M9 (Medium) — `components/nginx-module/src/ngx_http_markdown_conversion_impl.h:1175-1188`
- **Issue**: `ngx_http_markdown_record_per_path_metrics` is a no-op shell since 0.9.2 (1195-1199), but the doc fully describes the removed RB-tree behavior.
- **Fix**: Replace doc with "no-op placeholder since 0.9.2 (per-path metrics removed)".

### C-M10 (Medium) — `components/nginx-module/src/ngx_http_markdown_lifecycle_impl.h:100-103, 187-195`
- **Issue**: init_worker doc still lists per-path metrics cardinality wiring as a duty; the wiring is `#ifdef MARKDOWN_METRICS_PER_PATH_DEBUG` (unreachable in production).
- **Fix**: Remove per-path wiring from init_worker doc; drop the residual block at 187-191.

### C-M11 (Medium) — `components/nginx-module/src/ngx_http_markdown_request_impl.h:892-897`
- **Issue**: "FFI contract failure: treat as malformed (fail closed)" — `handle_encoding_header_invalid` routes by `on_error` (REJECT → error_status; PASS → forward + failopen count).
- **Fix**: "route like MALFORMED (decision governed by on_error policy)".

### C-M12 (Medium) — `components/nginx-module/src/ngx_http_markdown_headers_impl.h:467-512`
- **Issue**: COMMIT PHASE list includes "Cache-Control value pointer swap"; implementation does all Cache-Control rewrites in prepare (P5), commit (`ngx_http_markdown_fullcov_commit`) never touches Cache-Control.
- **Fix**: Remove Cache-Control from commit list; note in-place rewrite under prepare.

### C-M13 (Medium) — `components/nginx-module/src/ngx_http_markdown_stream_postcommit.c:375-388`
- **Issue**: "will be enhanced when the body filter is wired" — body filter is long wired; HTML signature scan is the final implementation.
- **Fix**: Drop the placeholder sentence.

### C-M14 (Medium) — `components/nginx-module/src/ngx_http_markdown_streaming_decomp_impl.h:1602-1612`
- **Issue**: `ngx_http_markdown_streaming_decomp_feed` return doc omits typed codes BUDGET_EXCEEDED/FORMAT_ERROR/TRUNCATED_INPUT/IO_ERROR (-100..-103).
- **Fix**: Add the typed code list.

### C-L1 (Low) — `filter_module.h:960` — path selection doc lists only FULLBUFFER/INCREMENTAL; add `PATH_STREAMING` (2).

### C-L2 (Low) — `metrics_impl.h:2174` — "Negotiate the response format" → "Select the frozen response format" (format frozen in 0.9.2; select_format ignores Accept).

### C-L3 (Low) — `stream_postcommit.c:493-514` — doc says only `b->last_buf` read; `latch_terminal` also reads `last_in_chain` and may set `subrequest_terminal_sent`.

### C-L4 (Low) — `stream_postcommit.c:702-706` — "send_terminal is the only path allowed when ctx is NULL" contradicts immediate NGX_ERROR return; reword to "send_terminal is the only caller that may receive NULL ctx; guarded here so acquire never sees NULL".

### C-L5 (Low) — `auth.c:1252-1266` — duplicate doc block for `ngx_http_markdown_rewrite_public_entries` (first copy dangles above trampoline); delete the first copy.

### C-L6 (Low) — `header_plan.c:47-53` — C names op 2 `PLAN_OP_MODIFY`; FFI header names it `set-etag-placeholder`; add alias note.

### C-L7 (Low) — `eligibility.c:17-18` — "ineligible: status not 200" → mention 206→range (enum at filter_module.h:899).

### C-L8 (Low) — `stream_error.c:128-134` — broken indentation in REJECT_STATUS branch comment.

### C-L9 (Low) — `decompression.c:1193,1379` — "ctx->buffer allocator family" — no `ctx` in scope; reword to "the same ngx_alloc/ngx_free allocator family as ctx->buffer (Rule 43)".

### C-L10 (Low) — `stream_state.c:139-144` — terminal-state comment "stays in the current state" ambiguous; add "(action remains the terminal action)".

### C-L11 (Low) — `streaming_decomp_impl.h:556-566` — `expand_buf` return doc omits `NGX_HTTP_MARKDOWN_DECOMP_OVERFLOW_ERROR` (-105).

## Verification method (post-fix)

- `make test-nginx-unit` (C compile + unit tests)
- `grep -rnE "10MB|5000ms|parser_budget|large_body_threshold" components/nginx-module/src/ngx_http_markdown_filter_module.h` → no stale matches
- `make check-headers` (FFI header in sync after Rust doc fix)
- `make complexity-check` (comment edits must not alter complexity)

---

## Closeout (2026-08-07)

All 27 findings remediated on `dev/wip-0.9.2-harness`:

| ID | Status |
|----|--------|
| C-H1..C-H2, C-M1..C-M14, C-L1..C-L11 | **fixed** — comment/doc-only edits across 19 C files; FFI header regenerated via cbindgen and `make check-headers` in sync |

Verification (fresh runs, all green):
- `make test-nginx-unit` — 1428 tests passed
- `make check-headers` — in sync
- `make complexity-check` — PASS
- `make docs-check` — PASS
