# Automatic Decompression - Test Suite Documentation

## Overview

This document describes the comprehensive test suite for the automatic decompression feature in the nginx markdown filter module. The feature automatically detects and decompresses upstream compressed content (gzip, deflate, brotli) to enable HTML-to-Markdown conversion.

This is a coverage map and command reference. Performance numbers gathered from these tests are local comparison points, not product guarantees. Record stable benchmark runs in [PERFORMANCE_BASELINES.md](PERFORMANCE_BASELINES.md).

## Test Coverage

### Unit Tests

#### 1. Compression Detection Tests (`test_decompression_detect.c`)

**Status:** ✅ Implemented

**Coverage:**
- NULL Content-Encoding header → NONE
- Empty Content-Encoding header → NONE
- "gzip" (lowercase) → GZIP
- "GZIP" (uppercase) → GZIP (case-insensitive)
- "GzIp" (mixed case) → GZIP (case-insensitive)
- "deflate" → DEFLATE
- "DEFLATE" (uppercase) → DEFLATE (case-insensitive)
- "br" → BROTLI
- "BR" (uppercase) → BROTLI (case-insensitive)
- "compress" (unknown) → UNKNOWN
- "identity" (unknown) → UNKNOWN

**How to Run:**
```bash
cd components/nginx-module/tests
make -C components/nginx-module/tests unit
```

#### 2. Gzip/Deflate Decompression Tests (`test_gzip_deflate_decompression.c`)

**Status:** ✅ Implemented

**Coverage:**
- Valid gzip data decompression
- Valid deflate data decompression
- Corrupted gzip data (error handling)
- Size limit exceeded (resource protection)
- Empty input data (validation)

**How to Run:**
```bash
cd components/nginx-module/tests
make -C components/nginx-module/tests unit
```

#### 3. Brotli Decompression Tests (`test_brotli_standalone.c`)

**Status:** ✅ Implemented

**Coverage:**
- Valid brotli data decompression
- Brotli module availability check
- Fallback when brotli not available

**How to Run:**
```bash
cd components/nginx-module/tests
make -C components/nginx-module/tests unit
```

#### 4. Input Validation Tests (`test_input_validation.c`)

**Status:** ✅ Implemented

**Coverage:**
- Empty input data validation
- Input buffer allocation validation
- inflateInit2 error code validation
- Output buffer allocation validation
- Z_FINISH flag usage (prevents infinite loops)
- inflate return code validation (Z_STREAM_END)
- Size limit enforcement (before and after decompression)
- Output buffer creation validation
- Chain link allocation validation
- Brotli empty input validation
- Brotli error code validation
- Brotli size limit enforcement
- Nginx memory pool usage (prevents buffer overflow)

**How to Run:**
```bash
cd components/nginx-module/tests
make -C components/nginx-module/tests unit-input_validation
```

#### 5. Security Measures Tests (`test_security_measures.c`)

**Status:** ✅ Implemented

**Coverage:**
- Size limit checks (pre and post decompression)
- Buffer overflow protection during decompression
- Input validation and boundary checks
- Memory allocation failure handling
- Proper cleanup on all error paths
- Decompression bomb protection
- Malicious data protection
- Resource exhaustion protection

**How to Run:**
```bash
cd components/nginx-module/tests
./test_security_measures
```

#### 6. Configuration Tests (`test_config_*.c`)

**Status:** ✅ Implemented

**Coverage:**
- Configuration parsing helpers used by standalone tests
- Merged defaults for decompression-related fields
- Configuration inheritance and merging behavior
- Validation of internal decompression-related settings used by the runtime

**How to Run:**
```bash
cd components/nginx-module/tests
make -C components/nginx-module/tests unit-config_merge
```

### Integration Tests

#### 7. Deflate Trailing-Data Integrity Tests

**Status:** ✅ Implemented (0.9.1)

**Coverage:**
- zlib-wrapped deflate + trailing garbage in same chunk → `FORMAT_ERROR`
- raw deflate + trailing garbage in same chunk → `FORMAT_ERROR`
- deflate complete in one chunk, trailing garbage in next chunk → `FORMAT_ERROR`
- deflate complete, empty subsequent chunk → safe no-op (OK)
- clean deflate (no trailing data) still succeeds (zlib-wrapped and raw)
- gzip concatenated members still succeed (anti-regression, one-feed and cross-feed)
- full-buffer zlib-wrapped deflate + trailing garbage → `FORMAT_ERROR`
- full-buffer raw deflate + trailing garbage → `FORMAT_ERROR`
- full-buffer clean deflate still succeeds (zlib-wrapped and raw)
- full-buffer gzip concatenated members still succeed (anti-regression)

**How to Run:**
```bash
make -C components/nginx-module/tests unit-streaming_decomp
make -C components/nginx-module/tests unit-decompression_production
```

#### 8. Brotli Streaming Decompression Tests (`streaming_decomp_brotli`)

**Status:** ✅ Implemented (0.9.1)

**Coverage:**
- Valid Brotli stream, single chunk → correct output
- Multi-chunk streaming with arbitrary boundary splits → correct reassembly
- Byte-by-byte input (every-byte chunk split) → correct output
- Empty valid Brotli stream handling
- Same-feed SUCCESS + trailing bytes → `FORMAT_ERROR` with byte count
- Next-feed trailing bytes after SUCCESS → `FORMAT_ERROR` without decoder invocation
- Empty feed after SUCCESS → `NGX_OK` without modification
- Truncated input at finish → `TRUNCATED_INPUT`
- Malformed Brotli input → `FORMAT_ERROR` + diagnostic log
- Budget enforcement (exact budget, budget+1 byte, cumulative across calls)
- Exact-budget probing (Scenario A and B)
- No-progress guard detection → `FORMAT_ERROR` with `brotli_no_progress`
- Allocation failure handling (pre-decode and post-decode)
- Cleanup idempotency (double cleanup + pool cleanup → safe)
- Error code classification: FORMAT (-1 to -17), ALLOCATION (-21 to -30), INTERNAL (-18 to -20, -31, unknown)
- Routing matrix: Brotli + NGX_HTTP_BROTLI + streaming eligible → STREAMING
- Feature gate toggle: NGX_HTTP_BROTLI defined vs undefined

**How to Run:**
```bash
make -C components/nginx-module/tests unit-streaming_decomp_brotli
```

#### 9. Brotli Streaming Property Tests

**Status:** ✅ Implemented (0.9.1)

**Coverage:**
- **Property 2 — Trailing Data Rejection** (`brotli_trailing_property`): valid Brotli + random trailing bytes → FORMAT_ERROR
- **Property 4 — Truncation Detection** (`brotli_trailing_property`): Brotli truncated at random offset → TRUNCATED_INPUT at finish
- **Property 6 — Streaming-vs-Full-Buffer Equivalence** (`streaming_equiv_brotli_property`): random data compressed/split into random chunks → byte-identical output
- **Property 7 — Cumulative Budget Enforcement** (`brotli_budget_property`): payloads exceeding max → BUDGET_EXCEEDED
- **Property 9 — Error Code Propagation** (`brotli_error_prop_property`): injected error conditions propagate exact codes through `brotli_loop()`
- **Property 11 — No-Progress Guard No False Positives** (`brotli_noprogress_property`): valid streams never trigger no-progress guard

**How to Run:**
```bash
make -C components/nginx-module/tests unit-brotli_trailing_property
make -C components/nginx-module/tests unit-brotli_budget_property
make -C components/nginx-module/tests unit-brotli_error_prop_property
make -C components/nginx-module/tests unit-brotli_noprogress_property
make -C components/nginx-module/tests unit-streaming_equiv_brotli_property
```

#### Integration Test Suite (`test_decompression_integration.sh`)

**Status:** ✅ Implemented

**Test Scenarios:**

1. **Gzip Decompression with Automatic Detection**
   - Upstream returns gzip-compressed HTML
   - Module automatically detects and decompresses
   - Converts to Markdown
   - Removes Content-Encoding header

2. **Deflate Decompression**
   - Upstream returns deflate-compressed HTML
   - Module automatically detects and decompresses
   - Converts to Markdown

3. **Brotli Decompression (if supported)**
   - Upstream returns brotli-compressed HTML
   - Module detects and decompresses (if brotli available)
   - Falls back gracefully if not available

4. **Uncompressed Content (Fast Path)**
   - Upstream returns uncompressed HTML
   - Module processes without decompression overhead
   - Preserves the no-decompression fast path

5. **Corrupted Compressed Data (Error Handling)**
   - Upstream returns corrupted gzip data
   - Module detects error and applies fail-open strategy
   - Returns the original eligible HTML response (200 OK)
   - Logs error with category=conversion

6. **Size Limit Enforcement**
   - Upstream returns large compressed content
   - Decompressed size exceeds `markdown_limits memory=<size>`
   - Module applies fail-open strategy
   - Logs error with category=resource_limit

**How to Run:**
```bash
cd components/nginx-module/tests
./test_decompression_integration.sh
```

**Prerequisites:**
- NGINX compiled with markdown filter module
- Python 3 with `brotli` module
- curl

### End-to-End Tests

#### E2E Test Suite (`test_decompression_e2e.sh`)

**Status:** ✅ Implemented

**Test Scenarios:**

1. **CDN Forced Gzip Compression**
   - Simulates CDN that ignores Accept-Encoding
   - Always returns gzip-compressed content
   - Module automatically handles decompression
   - Converts to Markdown successfully
   - **Real-world scenario:** Cloudflare, Fastly forcing compression

2. **CDN Forced Brotli Compression**
   - Simulates CDN forcing brotli compression
   - Module handles brotli if available
   - Falls back gracefully if not
   - **Real-world scenario:** Modern CDNs preferring brotli

3. **Complex HTML Conversion After Decompression**
   - Tests decompression + conversion pipeline
   - Verifies semantic structure preservation
   - Checks headings, lists, links, code blocks
   - **Validates:** Complete workflow

4. **Large Page Handling**
   - Tests 50KB+ HTML pages
   - Verifies decompression of large content
   - Checks memory usage
   - **Validates:** Scalability

5. **Performance Baseline - Compressed vs Uncompressed**
   - Measures latency for compressed content
   - Measures latency for uncompressed content
   - Calculates decompression overhead
   - Compare runs on the same machine and workload

6. **Concurrent Requests**
   - Sends 10 concurrent requests
   - Verifies no race conditions
   - Checks resource management
   - Exercises concurrent request handling

7. **CDN Cache Headers Preserved**
   - Verifies Cache-Control preserved
   - Verifies X-CDN-Cache preserved
   - Checks only Content-Encoding removed

**How to Run:**
```bash
cd components/nginx-module/tests
./test_decompression_e2e.sh
```

**Prerequisites:**
- NGINX compiled with markdown filter module
- Python 3 with `brotli` module
- curl

#### Brotli Streaming E2E Test (`verify_brotli_streaming_e2e.sh`)

**Status:** ✅ Implemented (0.9.1)

**Test Scenarios:**

1. **Normal Brotli Streaming Operation**
   - `Content-Encoding: br` with valid compressed response
   - Streaming decompression produces correct Markdown output
   - Small and large responses verified

2. **Chunked Delivery**
   - Response without Content-Length (chunked transfer)
   - Streaming decompression handles arbitrary chunk boundaries

3. **Error Conditions**
   - Malformed, truncated, and trailing Brotli streams are handled without
     worker failure under the configured error policy
   - Unit tests assert the exact typed error classification for each case
   - Budget exceeded preserves the original Brotli response

4. **Configuration Gates**
   - `markdown_auto_decompress off` → passthrough
   - `markdown_cache_validation full` → routed to full-buffer

**How to Run:**
```bash
make verify-brotli-streaming-e2e
```

**Prerequisites:**
- NGINX compiled with markdown filter module and `NGX_MARKDOWN_BROTLI_STREAMING=on`
- `libbrotlidec` runtime library installed
- Rust toolchain for the repository-pinned version

The compatibility shell entrypoint delegates to the canonical
`brotli-streaming` Rust harness scenario. The scenario owns deterministic
compressed fixtures, single-byte upstream chunks, full-buffer routing checks,
and slow-reader backpressure evidence.

### Performance Tests

#### Performance Test Suite (`test_decompression_performance.sh`)

**Status:** ✅ Implemented

**Test Scenarios:**

1. **Latency - Small Pages**
   - Measures latency for small pages (<1KB)
   - Compares compressed vs uncompressed

2. **Latency - Medium Pages**
   - Measures latency for medium pages (~10KB)
   - Compares compressed vs uncompressed

3. **Latency - Large Pages**
   - Measures latency for large pages (~100KB)
   - Compares compressed vs uncompressed

4. **Throughput Comparison**
   - Uses Apache Bench (ab) for load testing
   - 1000 requests, concurrency 10
   - Measures requests per second

5. **Memory Usage**
   - Measures memory before and after load
   - Generates 100 concurrent requests
   - Checks memory increase

**How to Run:**
```bash
cd components/nginx-module/tests
./test_decompression_performance.sh
```

**Prerequisites:**
- NGINX compiled with markdown filter module
- Python 3
- curl
- Apache Bench (ab) - optional, for throughput tests

Use these tests for relative comparison and regression spotting. Persist notable measured results in [PERFORMANCE_BASELINES.md](PERFORMANCE_BASELINES.md) with machine, workload, and concurrency context.

## Test Execution Summary

### Quick Test (Unit Tests Only)
```bash
cd components/nginx-module/tests
make -C components/nginx-module/tests unit
make -C components/nginx-module/tests unit
make -C components/nginx-module/tests unit
./test_security_measures
./test_input_validation
```

**Time:** ~30 seconds

### Brotli Streaming Tests (requires libbrotlidec + libbrotlienc)
```bash
# Unit tests (Brotli-enabled build)
make -C components/nginx-module/tests unit-streaming_decomp_brotli

# Property tests
make -C components/nginx-module/tests unit-brotli_trailing_property
make -C components/nginx-module/tests unit-brotli_budget_property
make -C components/nginx-module/tests unit-brotli_error_prop_property
make -C components/nginx-module/tests unit-brotli_noprogress_property
make -C components/nginx-module/tests unit-streaming_equiv_brotli_property

# E2E (requires NGINX binary with Brotli streaming enabled)
make verify-brotli-streaming-e2e
```

### Full Test Suite (All Tests)
```bash
cd components/nginx-module/tests

# Unit tests
make -C components/nginx-module/tests unit
make -C components/nginx-module/tests unit
make -C components/nginx-module/tests unit
./test_security_measures
./test_input_validation

# Integration tests
./test_decompression_integration.sh

# E2E tests
./test_decompression_e2e.sh

# Performance tests
./test_decompression_performance.sh
```

**Time:** ~5-10 minutes

## Brotli Streaming Backpressure Coverage Analysis

**Status:** Covered by existing codec-agnostic tests (no Brotli-specific E2E needed)

The downstream backpressure mechanism (NGX_AGAIN handling, pending output save,
resume drain) is **codec-agnostic** — it operates on the outgoing Markdown chain
after decompression, not on compressed input. Once the decompressor produces
bytes and those are converted to Markdown, the downstream delivery path is
identical regardless of codec (gzip, deflate, or Brotli).

**Existing coverage that proves backpressure correctness for Brotli:**

| Layer | Test | What It Proves |
|-------|------|----------------|
| E2E | `verify_brotli_streaming_e2e.sh` | Real Brotli streaming through the native module with a constrained slow reader; asserts complete output, a positive backpressure metric delta, and worker survival |
| E2E | `verify_chunked_streaming_native_e2e.sh` Case 4d | Real downstream backpressure (slow reader, throttled socket) with gzip streaming — exercises the codec-agnostic pending output save/resume path, asserts exact output equivalence, terminal-once, and backpressure metrics |
| E2E | `verify_failopen_backpressure_e2e.sh` | Fail-open passthrough under backpressure (codec-agnostic) |
| Property | `streaming_equiv_brotli_property_test.c` | Brotli streaming produces byte-identical output to full-buffer across arbitrary random chunk splits (100+ iterations) |
| Unit | `streaming_test.c` §14.4 | Codec-agnostic buffered flag management, deferred finalize resume |
| Unit | `streaming_decomp_test.c` (multi-chunk) | Brotli multi-chunk feeding with 2-byte-at-a-time splits, verifying correct reassembly without data loss or duplication |
| Unit | Backpressure integration tests | Brotli integration with the backpressure state machine — input consumed exactly once, pending output save, resume drain, terminal chain at-most-once |

**Key invariant:** Under backpressure, the module saves pending OUTPUT (outgoing
Markdown chain), not compressed input. Consumed compressed bytes are never
re-fed to the decoder. This invariant is codec-independent and verified by:
- The gzip E2E test (Case 4d) proving the mechanism works end-to-end
- The Brotli property test proving streaming correctness across chunk splits
- The phase 9 unit tests proving Brotli specifically integrates with the backpressure state machine

**Conclusion:** The native Brotli scenario now provides codec-specific
slow-reader evidence in addition to the existing gzip proof of the shared
pending-output/resume mechanism. Multi-chunk unit tests and the streaming
equivalence property test independently cover decoder chunk boundaries.

## Coverage Summary

This suite is most useful for confirming:

- compression-type detection
- gzip, deflate, and brotli decompression paths
- error handling on corrupt or oversized compressed payloads
- preservation of headers and conversion behavior after decompression
- local regression comparison for decompression overhead

## Test Maintenance

### Adding New Tests

1. **Unit Tests:** Add to appropriate `test_*.c` file or create new file
2. **Integration Tests:** Add scenario to `test_decompression_integration.sh`
3. **E2E Tests:** Add scenario to `test_decompression_e2e.sh`
4. **Performance Tests:** Add metric to `test_decompression_performance.sh`

### Updating Tests

When modifying the decompression implementation:
1. Run unit tests first to catch basic issues
2. Run integration tests to verify end-to-end flow
3. Run E2E tests to validate real-world scenarios
4. Run performance tests to ensure no regression

## Troubleshooting

### Common Issues

**Issue:** Backend server fails to start
**Solution:** Check if port is already in use: `lsof -i :9990`

**Issue:** Brotli tests fail
**Solution:** Install brotli module: `pip3 install brotli`

**Issue:** Performance tests show high overhead
**Solution:** This is normal on busy systems. Run on idle system for accurate baselines.

**Issue:** Integration tests timeout
**Solution:** Increase timeout in test scripts or check NGINX error log

## References

- Implementation: `components/nginx-module/src/ngx_http_markdown_decompression.c`
- Performance Baselines: `docs/testing/PERFORMANCE_BASELINES.md`


## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.1 | 2026-07-18 | Kiro | Added Brotli streaming decompression test sections: streaming_decomp_brotli unit tests, property tests (trailing, budget, error propagation, no-progress, streaming equivalence), E2E verify_brotli_streaming_e2e.sh, backpressure coverage analysis |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, added mermaid diagrams where applicable, verified directive accuracy against code, added update tracking section |
