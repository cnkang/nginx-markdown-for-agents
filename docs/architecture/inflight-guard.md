# Per-Worker Inflight Guard

## Overview

The inflight guard is a per-worker concurrency limiter that prevents any
single NGINX worker from accumulating unbounded in-flight markdown
conversions.  When the number of active conversions reaches the configured
limit, new eligible requests are handled according to the unified error
policy (pass-through, return a status code, or fail closed).

## Design Decisions

### Per-Worker Counter (Not Shared Memory)

Each NGINX worker process maintains its own counter.  This avoids:

- Cross-worker lock contention on the hot path
- Shared-memory synchronization overhead
- Cache-line bouncing between cores

Since NGINX workers are single-threaded event loops, a per-worker counter
with `ngx_atomic_t` fields is sufficient.  The atomic type is used only to
allow safe reads from the metrics snapshot path (which runs in the same
worker but may read during a different event-loop phase).

### Cleanup Handler Pattern

The decrement is guaranteed on **every** exit path through a pool cleanup
handler registered on `r->pool`.  When the request pool is destroyed—whether
due to normal completion, client abort, timeout, or any error—the cleanup
handler fires and decrements the counter.

This eliminates the need to manually track every code path that can
terminate a request.

## Increment/Decrement Timing Matrix

| Event | Increment | Decrement | Notes |
|-------|-----------|-----------|-------|
| Eligibility check passes, before conversion | ✓ | — | Registered via `ngx_http_markdown_inflight_try_increment()` |
| Normal conversion completion | — | ✓ | Pool cleanup on `r->pool` destroy |
| Client abort | — | ✓ | Pool cleanup on `r->pool` destroy |
| Timeout | — | ✓ | Pool cleanup on `r->pool` destroy |
| Memory budget exceeded | — | ✓ | Pool cleanup on `r->pool` destroy |
| Conversion error (Rust FFI) | — | ✓ | Pool cleanup on `r->pool` destroy |
| FFI panic | — | ✓ | Pool cleanup on `r->pool` destroy |
| Decompression error | — | ✓ | Pool cleanup on `r->pool` destroy |
| HeaderPlan apply error | — | ✓ | Pool cleanup on `r->pool` destroy |
| Streaming mid-flight error | — | ✓ | Pool cleanup on `r->pool` destroy |
| NGX_AGAIN (backpressure) | — | — | Request still in progress |
| NGX_DONE (subrequest) | — | — | Request still in progress |

**Key invariant**: The counter is incremented exactly once per eligible
request, and decremented exactly once when that request completes (by any
means).

## Overflow Protection

The cleanup handler includes an **idempotency flag** (`decremented`).  Even
if the handler is somehow invoked multiple times, only the first call
performs the decrement.  This prevents counter underflow.

The counter itself is guarded against going negative: the decrement path
checks `current > 0` before subtracting.

## Configuration

```nginx
markdown_limits max_inflight=64;
```

- **Directive**: `markdown_limits` (Config V2, multi-key)
- **Parameter**: `max_inflight=N`
- **Default**: 64
- **Scope**: `http`, `server`, `location`
- **Value 0**: Unlimited (no inflight guard)

The default value of 64 is chosen to protect typical deployments where:
- Each conversion may hold a response buffer up to `markdown_limits memory=<size>` (10 MB built-in default)
- 64 × 10 MB = 640 MB peak memory per worker, which fits comfortably in most server configurations
- Legitimate crawlers rarely exceed 10-20 concurrent requests to a single origin

## Overload Behavior

When `current >= max_inflight`:

1. The `overload_total` counter is incremented
2. The request is **not** counted in the inflight counter
3. The configured error policy determines behavior:
   - **pass** (default): Original response passed through unmodified
   - **status N**: Configured HTTP status code returned (e.g., 503)
   - **fail_closed**: 502 Bad Gateway returned

## Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `nginx_markdown_inflight_current` | gauge | Currently in-flight conversions in this worker |
| `nginx_markdown_inflight_high_watermark` | gauge | Peak concurrent conversions observed since worker start |
| `nginx_markdown_overload_total` | counter | Total requests rejected due to inflight limit |

These metrics have no labels (per-worker global gauges/counters).  Each
worker reports its own values independently at the `/metrics` endpoint.

## Implementation Files

| File | Role |
|------|------|
| `src/ngx_http_markdown_inflight_impl.h` | Counter module (increment, decrement, cleanup) |
| `src/ngx_http_markdown_request_impl.h` | Integration point (header filter, after eligibility) |
| `src/ngx_http_markdown_metrics_impl.h` | Snapshot collection for inflight fields |
| `src/ngx_http_markdown_prometheus_impl.h` | Prometheus text exposition |
| `src/ngx_http_markdown_filter_module.h` | `max_inflight` config field and default |
