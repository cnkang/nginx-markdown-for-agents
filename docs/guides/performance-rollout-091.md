# Performance Rollout and Rollback Guide — 0.9.1 Optimizations

## Table of Contents

1. [Overview](#overview)
2. [Optimization Summary](#optimization-summary)
3. [Rollout Stages](#rollout-stages)
4. [Rollback Paths](#rollback-paths)
   - [Zero-Copy Streaming Output](#rollback-zero-copy-streaming-output)
   - [Streaming Decompression](#rollback-streaming-decompression)
   - [Full-Buffer Copy Reduction](#rollback-full-buffer-copy-reduction)
5. [Verification After Rollback](#verification-after-rollback)
6. [Quick Reference Matrix](#quick-reference-matrix)

---

## Overview

The 0.9.1 release introduces three performance optimizations to the NGINX
Markdown filter module. Each optimization has an independent rollback path
that requires only configuration changes and a graceful reload — no binary
rebuild, no NGINX restart, and no downtime.

### Key Principle

Every optimization can be disabled without a binary rebuild:

- **Zero-copy output**: disabled via `markdown_streaming_zero_copy off` + HUP
- **Streaming decompression**: disabled via profile switch or
  `markdown_auto_decompress off`
- **Full-buffer copy reduction**: internal implementation detail with no
  configuration surface; rolled back only via code revert and rebuild

### Related Documents

- [ROLLBACK_GUIDE.md](ROLLBACK_GUIDE.md) — general module rollback procedures
- [streaming-rollout-cookbook.md](streaming-rollout-cookbook.md) — streaming engine rollout
- [CONFIGURATION.md](CONFIGURATION.md) — full directive reference
- [OPERATIONS.md](OPERATIONS.md) — metrics reference

---

## Optimization Summary

| Optimization | Default State | Gate Mechanism | Rollback Method | Binary Rebuild Required |
|--------------|---------------|----------------|-----------------|------------------------|
| Zero-Copy Streaming Output | OFF | `markdown_streaming_zero_copy on` | Set to `off` + HUP reload | No |
| Streaming Decompression | Profile-gated | `streaming_first` profile + `auto_decompress on` | Switch profile or set `auto_decompress off` | No |
| Full-Buffer Copy Reduction | ON (internal) | None (always active) | Code revert + rebuild | Yes |

---

## Rollout Stages

Each optimization follows a staged rollout progression:

| Stage | State | Description |
|-------|-------|-------------|
| 0 | Inactive | Optimization not active; pre-0.9.1 behavior |
| 1 | Opt-in | Operator explicitly enables via directive |
| 2 | Profile-gated | Active only under specific profile |
| 3 | Evidence-gated | Promoted to wider use after benchmark evidence |
| 4 | Default-on | Active by default for all configurations |

### 0.9.1 Stage Assignments

| Optimization | 0.9.1 Stage | Progression Criteria |
|--------------|-------------|---------------------|
| Zero-Copy Streaming Output | Stage 1 (opt-in) | Module benchmark shows latency improvement with no memory regression |
| Streaming Decompression | Stage 2 (profile-gated) | Stable under `streaming_first` workloads; TTFB improvement confirmed |
| Full-Buffer Copy Reduction | Stage 4 (default-on) | Internal detail; no observable behavior change; fail-open equivalence maintained |

---

## Rollback Paths

### Rollback: Zero-Copy Streaming Output

**Trigger conditions:**
- Memory-related errors in NGINX error log
- Unexpected request termination during streaming
- `pending_output_high_watermark_bytes` growing unbounded
- Pool cleanup handler warnings in debug log

**Rollback procedure:**

1. Set `markdown_streaming_zero_copy off` in the affected location block(s):

```nginx
location /docs {
    markdown_filter on;
    markdown_streaming force;
    markdown_streaming_zero_copy off;   # <-- disable zero-copy
    proxy_pass http://backend;
}
```

2. Validate and reload:

```bash
nginx -t && nginx -s reload
```

3. Verify rollback took effect — new requests use pool-copy output:

```bash
# Check metrics: zero_copy_output_total should stop incrementing
curl -s http://localhost/markdown-metrics | \
  grep -E "zero_copy_output_total|copied_output_total"

# Wait 30 seconds, check again
sleep 30
curl -s http://localhost/markdown-metrics | \
  grep -E "zero_copy_output_total|copied_output_total"
# zero_copy_output_total should be unchanged
# copied_output_total should be incrementing
```

**How it works:**

The `markdown_streaming_zero_copy` directive is a location-level `NGX_CONF_FLAG`
that defaults to `off` (0). On HUP reload, NGINX re-reads the configuration
and spawns new worker processes. New requests in the new workers evaluate the
directive at the body-filter entry point. When set to `off`, the hybrid
decision logic unconditionally selects the pool-copy path for all output
chunks, bypassing the buffer factory entirely. In-flight requests on old
workers complete with their existing configuration; no request is interrupted.

**Memory Lifecycle and Safety Invariants:**

To prevent use-after-free and ensure absolute memory safety during asynchronous downstream transmissions, the Rust-owned memory buffers allocated for zero-copy streaming chunks are managed via NGINX request pool cleanup handlers. Consequently, *Rust-allocated buffers are not freed immediately after a single chunk is successfully delivered downstream*; rather, they persist in memory throughout the request duration and are released in batch when the NGINX request pool is destroyed upon request termination.

For long-lived streaming responses with many chunks, this tail retention can cause memory usage to accumulate in the request pool, potentially resulting in a higher worker RSS peak. Due to this characteristic, `markdown_streaming_zero_copy` is kept **disabled by default**, serving as an opt-in optimization under explicit profile selection (such as `streaming_first`) where latency reduction is highly prioritized over strict request-lifetime RSS floors.

**Scope:** Per-location. Different locations can independently enable or
disable zero-copy output.

---

### Rollback: Streaming Decompression

**Trigger conditions:**
- Decompression errors in streaming path (log messages mentioning
  "streaming decompression")
- `decompression_budget_exceeded_total` incrementing rapidly
- Fail-open rate increasing for compressed responses
- Truncated or garbled Markdown output for gzip/deflate responses

**Rollback procedure — Method 1: Profile switch (recommended)**

Switch from `streaming_first` to `balanced` or remove the profile directive:

```nginx
location /docs {
    markdown_filter on;
    markdown_profile balanced;          # <-- was streaming_first
    proxy_pass http://backend;
}
```

Validate and reload:

```bash
nginx -t && nginx -s reload
```

**Rollback procedure — Method 2: Disable auto-decompression**

Keep the profile but disable decompression:

```nginx
location /docs {
    markdown_filter on;
    markdown_profile streaming_first;
    markdown_auto_decompress off;       # <-- disable decompression
    proxy_pass http://backend;
}
```

Validate and reload:

```bash
nginx -t && nginx -s reload
```

**Rollback procedure — Method 3: Disable streaming engine**

Disable the streaming engine entirely (all compressed responses go through
full-buffer path):

```nginx
location /docs {
    markdown_filter on;
    markdown_streaming off;      # <-- disable streaming
    proxy_pass http://backend;
}
```

Validate and reload:

```bash
nginx -t && nginx -s reload
```

**Verification after any method:**

```bash
# Check metrics: decompression_streaming_total should stop incrementing
curl -s http://localhost/markdown-metrics | \
  grep -E "decompression_(streaming|fullbuffer)_total"

# Wait 30 seconds, check again
sleep 30
curl -s http://localhost/markdown-metrics | \
  grep -E "decompression_(streaming|fullbuffer)_total"
# decompression_streaming_total should be unchanged
# decompression_fullbuffer_total should be incrementing (if compressed
# responses are still being processed via full-buffer)
```

**How it works:**

Streaming decompression requires ALL FOUR conditions to be met:
1. `auto_decompress on` (enabled by default)
2. Streaming engine selected for the request
3. `cache_validation` is NOT `full`
4. Encoding supported by streaming decompressor (gzip, zlib-wrapped deflate
   RFC 1950, or raw deflate RFC 1951); Brotli uses bounded full-buffer
   decompression

Switching the profile from `streaming_first` to `balanced` or `strict_cache`
changes the streaming engine selection. The `balanced` profile uses
`streaming_policy: auto` (threshold-based), and `strict_cache` forces
`streaming_policy: off`. Either change means condition (2) is no longer
guaranteed for all requests, so streaming decompression is automatically
disabled for requests that no longer select the streaming path.

Setting `auto_decompress off` directly breaks condition (1), disabling all
decompression routing (compressed responses pass through unconverted or route
to full-buffer decompression depending on engine selection).

New requests evaluate these conditions at header-filter time. In-flight
requests complete with their existing configuration.

**Scope:** Per-location. Each location block has independent profile and
decompression settings.

---

### Rollback: Full-Buffer Copy Reduction

**Trigger conditions:**
- Conversion failures only on compressed responses that take full-buffer
  (for example Brotli or full cache validation)
- Memory corruption symptoms (unexpected crashes after decompression)
- Fail-open triggering more frequently for gzip responses

**Rollback procedure:**

This optimization is an internal implementation detail with no configuration
surface. It is default-on because it maintains identical fail-open semantics
and observable behavior. Rollback requires a code revert and binary rebuild:

1. Revert the copy-reduction changes in the body filter:
   ```bash
   git revert <copy-reduction-commit-sha>
   ```

2. Rebuild the module:
   ```bash
   make build
   ```

3. Replace the module binary and reload:
   ```bash
   cp components/nginx-module/src/ngx_http_markdown_filter_module.so \
     /usr/lib/nginx/modules/
   nginx -t && nginx -s reload
   ```

**Why no config toggle:**

The copy reduction removes the redundant apply-back copy in the internal
decompression pipeline. Rust FFI output is still copied once into an
`ngx_alloc` buffer; after budget checks, that buffer is swapped into
`ctx->buffer.data`. The optimization preserves:
- Identical fail-open semantics (original compressed buffer intact on failure)
- Identical decompression budget enforcement
- Identical output for all inputs

Since there is no behavioral difference observable to operators or clients,
a configuration toggle would add complexity without safety benefit. The
optimization either works correctly (bit-for-bit identical output) or fails
in a way that triggers existing fail-open guards.

**Scope:** Global — affects all full-buffer decompression processing.

---

## Verification After Rollback

After rolling back any optimization, verify the following:

### 1. Conversion Still Works

```bash
curl -sD - \
  -H "Accept: text/markdown" \
  http://localhost/docs/ | head -5
# Expected: Content-Type: text/markdown; charset=utf-8
# Expected: Valid Markdown content in body
```

### 2. Metrics Are Healthy

```bash
curl -s http://localhost/markdown-metrics | \
  grep -E "conversions_(attempted|succeeded|failed)"
# conversions_failed should not be growing relative to attempted
```

### 3. No Error Log Spikes

```bash
grep -c "markdown.*error\|markdown.*fail" /var/log/nginx/error.log
# Compare with pre-rollback count; should not be increasing
```

### 4. Latency Is Acceptable

```bash
curl -s http://localhost/markdown-metrics | \
  grep "conversion_latency"
# Latency distribution should be within normal range
```

---

## Quick Reference Matrix

| Scenario | Action | Command |
|----------|--------|---------|
| Zero-copy causing issues | Set `markdown_streaming_zero_copy off` | `nginx -t && nginx -s reload` |
| Streaming decompress errors | Switch to `markdown_profile balanced` | `nginx -t && nginx -s reload` |
| Streaming decompress errors (keep profile) | Set `markdown_auto_decompress off` | `nginx -t && nginx -s reload` |
| All streaming issues | Set `markdown_streaming off` | `nginx -t && nginx -s reload` |
| Full-buffer copy issues | Code revert + rebuild | `git revert && make build` |
| All optimizations off | Combine above config changes | `nginx -t && nginx -s reload` |

### Emergency Rollback (all optimizations disabled)

```nginx
location /docs {
    markdown_filter on;
    markdown_streaming_zero_copy off;     # zero-copy disabled
    markdown_auto_decompress off;         # streaming decompress disabled
    markdown_streaming off;        # streaming engine disabled
    proxy_pass http://backend;
}
```

```bash
nginx -t && nginx -s reload
```

This configuration produces behavior equivalent to pre-0.9.1 (full-buffer path
only, pool-copy output, no streaming decompression). The full-buffer copy
reduction remains active (internal optimization) but has no observable effect
when decompression routes through the standard full-buffer path.

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.1 | 2026-07-05 | Kiro | Initial 0.9.1 performance rollback documentation |
