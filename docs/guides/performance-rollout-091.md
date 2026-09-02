# Performance Rollout and Rollback Guide — 0.9.1 Optimizations

> **Superseded in 0.9.2.** This document applies only to the 0.9.1 release
> line. The directives removed in 0.9.2 — `markdown_streaming_zero_copy`,
> `markdown_profile`, and the other removed directives referenced below —
> fail `nginx -t` with an `unknown directive` error. In 0.9.2, zero-copy
> delivery is an internal optimization selected automatically from buffer
> ownership and backpressure state, and profile-based switching is no longer
> available.
> For 0.9.2 behavior, see [CONFIGURATION.md](CONFIGURATION.md) and
> [0.9.2-breaking-changes.md](0.9.2-breaking-changes.md).

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
Markdown filter module. Two of them (zero-copy output and streaming
decompression) roll back with a configuration change and a graceful reload —
no binary rebuild, no NGINX restart, and no downtime. The third
(full-buffer copy reduction) is an internal implementation detail with no
operator toggle. Rolling it back requires a code revert, a binary rebuild,
and an NGINX restart.

### Key Principle

Operators can disable the optimizations that expose a runtime toggle without
a binary rebuild. Zero-copy output and streaming decompression have runtime
controls. Full-buffer copy reduction is an internal implementation detail and
does not have an operator toggle:

- **Zero-copy output**: disabled via `markdown_streaming_zero_copy off` + HUP
- **Streaming decompression**: disabled via profile switch or
  `markdown_auto_decompress off`
- **Full-buffer copy reduction**: internal implementation detail with no
  operator toggle — rolling it back requires a code revert and a binary
  rebuild (plus NGINX restart), not a configuration change

### Related Documents

- [OPERATIONAL_ROLLBACK.md](OPERATIONAL_ROLLBACK.md) — general module rollback procedures
- [Rollout Cookbook — Streaming-Focused Rollout](ROLLOUT_COOKBOOK.md#streaming-focused-rollout)
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
# (0.9.1 only; 0.9.2 removed the counter — see the note below)
curl -s http://localhost/markdown-metrics | \
  grep -E "zero_copy_output_total|copied_output_total"

# Wait 30 seconds, check again
sleep 30
curl -s http://localhost/markdown-metrics | \
  grep -E "zero_copy_output_total|copied_output_total"
# zero_copy_output_total should be unchanged
# copied_output_total should be incrementing
```

> **0.9.2 note:** the 0.9.2 release removed both `zero_copy_output_total`
> and the `markdown_streaming_zero_copy` directive. The frozen v1 metrics
> registry has no per-path output counter at all (see
> [prometheus-metrics.md](prometheus-metrics.md) for the frozen 12-family
> list). Monitor `nginx_markdown_requests_total{outcome="converted"}`
> and `nginx_markdown_conversion_deliveries_total{engine="streaming"}`
> instead. The 0.9.1 steps above stay for rollback verification on the
> 0.9.1 release line only.

**How it works:**

The `markdown_streaming_zero_copy` directive is a location-level `NGX_CONF_FLAG`
that defaults to `off` (0). On HUP reload, NGINX re-reads the configuration
and spawns new worker processes. New requests in the new workers evaluate the
directive at the body-filter entry point. When set to `off`, the hybrid
decision logic unconditionally selects the pool-copy path for all output
chunks, bypassing the buffer factory entirely. In-flight requests on old
workers complete with their existing configuration. Graceful reloads normally
preserve active requests, but NGINX may terminate old workers when
`worker_shutdown_timeout` expires.

**Memory Lifecycle and Safety Invariants:**

NGINX request pool cleanup handlers manage the Rust-owned memory buffers allocated for zero-copy streaming chunks. This prevents use-after-free and ensures memory safety during asynchronous downstream transmissions. Consequently, *Rust-allocated buffers are not freed immediately after a single chunk is successfully delivered downstream*. Rather, they persist in memory throughout the request duration. The pool releases them in batch when it destroys the NGINX request pool upon request termination.

For long-lived streaming responses with many chunks, this tail retention can cause memory usage to accumulate in the request pool. It can result in a higher worker RSS peak. Due to this characteristic, `markdown_streaming_zero_copy` stays **disabled by default**. It serves as an opt-in optimization under explicit profile selection (such as `streaming_first`). Latency reduction outweighs strict RSS floors there.

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
   RFC 1950, or raw deflate RFC 1951). Brotli uses bounded full-buffer
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

3. Replace the module binary and perform a complete restart:
   ```bash
   # Derive the modules directory from the installed NGINX build; RPM
   # packages use /usr/lib64/nginx/modules, Debian packages /usr/lib/nginx/modules.
   MODULES_DIR="$(nginx -V 2>&1 | sed -nE 's/.*--modules-path=([^ ]+).*/\1/p')"
   if [ -z "$MODULES_DIR" ]; then
     echo "cannot determine the NGINX modules path from nginx -V" >&2
     exit 1
   fi
   sudo cp components/nginx-module/src/ngx_http_markdown_filter_module.so \
     "$MODULES_DIR/"
   # Abort the rollout if the replaced module fails configuration validation;
   # never restart with a broken module in place.
   sudo nginx -t || {
     echo "ERROR: nginx -t failed after module replacement; restore the previous module and re-validate" >&2
     exit 1
   }
   # Restart through systemd only when it actually owns the running NGINX
   # process.  A unit file existing on disk is not proof of ownership —
   # the process may be started by another supervisor or directly.
   if command -v systemctl >/dev/null 2>&1 \
       && systemctl is-active --quiet nginx.service; then
     main_pid="$(systemctl show -p MainPID --value nginx.service)"
     if [[ "$main_pid" =~ ^[0-9]+$ ]] \
         && pgrep -x nginx | grep -qx "$main_pid"; then
       sudo systemctl restart nginx
     else
       echo "ERROR: nginx.service is active but does not own the running NGINX master; refusing to stop/start" >&2
       exit 1
     fi
   else
     sudo nginx -s quit
     # Wait for the master to exit before starting fresh: an immediate
     # `nginx` start can race the old master's shutdown and fail the
     # module swap semantics this rollout depends on.
     waited=0
     while pgrep -x nginx >/dev/null 2>&1; do
       if [[ "$waited" -ge 30 ]]; then
         echo "ERROR: NGINX master did not exit within 30s of 'nginx -s quit'; aborting" >&2
         exit 1
       fi
       sleep 1
       waited=$((waited + 1))
     done
     sudo nginx
   fi
   ```

**Why no config toggle:**

The copy reduction removes the redundant apply-back copy in the internal
decompression pipeline. Rust FFI output is still copied once into an
`ngx_alloc` buffer. After budget checks, that buffer swaps into
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

This configuration disables the streaming controls (streaming engine,
zero-copy and streaming decompression are off), so behavior is equivalent
to pre-0.9.1 for those controls. Full-buffer copy reduction remains active
(internal optimization) and the full-buffer path is not byte-identical to
pre-0.9.1, so equivalence applies only to the disabled streaming controls.

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-24 | Kang | The 0.9.2 note no longer claims a copied_output_total metric; operators are directed to requests_total and conversion_deliveries_total |
| 0.9.1 | 2026-07-05 | Kiro | Initial 0.9.1 performance rollback documentation |
