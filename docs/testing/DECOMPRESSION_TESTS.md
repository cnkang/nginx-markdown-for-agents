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
   - Decompressed size exceeds `markdown_max_size`
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
