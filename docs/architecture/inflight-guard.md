# Per-Worker Inflight Guard

## Overview

The inflight guard is a per-worker concurrency limiter that prevents any
single NGINX worker from accumulating unbounded in-flight markdown
conversions.  When the number of active conversions reaches the configured
limit, the module handles new eligible requests according to the unified error
policy (pass-through, return a status code, or fail closed).

## Design Decisions

### Per-Worker Counter (Not Shared Memory)

Each NGINX worker process maintains its own counter.  This avoids:

- Cross-worker lock contention on the hot path
- Shared-memory synchronization overhead
- Cache-line bouncing between cores

Since NGINX workers are single-threaded event loops, a per-worker counter
with `ngx_atomic_t` fields is sufficient.  The atomic type serves only to
allow safe reads from the metrics snapshot path (which runs in the same
worker but may read during a different event-loop phase).

### Cleanup Handler Pattern

The module guarantees the decrement on **every** exit path through a pool cleanup
handler registered on `r->pool`.  When the request pool gets destroyed—whether
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
| NGX_DONE (filter terminal) | — | — | No retry or second delivery count; request-pool cleanup still owns the decrement |

**Key invariant**: The module increments the counter exactly once for each
admitted eligible request and decrements it exactly once when that request
completes by any means.

## Overflow Protection

The cleanup handler includes an **idempotency flag** (`decremented`).  Even
if the handler is somehow invoked multiple times, only the first call
performs the decrement.  This prevents counter underflow.

The module guards the counter itself against going negative: the decrement path
checks `current > 0` before subtracting.

## Configuration

```nginx
markdown_limits max_inflight=64;
```

- **Directive**: `markdown_limits` (Config V2, multi-key)
- **Parameter**: `max_inflight=N`
- **Default**: 64
- **Scope**: `http`, `server`, `location`
- **Value 0**: Rejected at config time — `max_inflight` must be an integer
  from 1 to 65535 (there is no "unlimited" value; see Overload Behavior)

The default value of 64 is a conservative guardrail, not a capacity or
concurrency promise. The actual per-worker working set depends on the
effective `conversion_memory`, parser/decompression budgets, streaming
working-set budget, request mix, and NGINX pool overhead. Operators must size
`max_inflight` from measured worker memory and workload limits. Crawler
concurrency is not used as a safety assumption.

## Overload Behavior

The overload condition applies only when a positive bound is set.
Config time validates `max_inflight` as an integer greater than 0
(1–65535), so **`max_inflight=0` is never accepted** — there is no
"unlimited" configuration value. The overload branch triggers when
`current >= max_inflight`. With the enforced positive bound the worker
has always reached the configured limit at that point.

1. The module records a terminal request outcome in the frozen
   `nginx_markdown_requests_total{outcome,stage,reason}` family with
   `reason="overload"`.
2. The request is **not** counted in the inflight counter
3. The configured error policy determines behavior:
   - **pass** (default): Original response passed through unmodified
   - **status N**: Configured HTTP status code returned (for example 503)
   - **fail_closed**: 502 Bad Gateway returned

## Metrics

The frozen Prometheus surface exposes the current gauge and request outcome:

| Metric | Type | Description |
|--------|------|-------------|
| `nginx_markdown_inflight_requests` | gauge | Currently in-flight conversions |
| `nginx_markdown_requests_total{outcome=...,stage=...,reason=...}` | counter | Terminal request outcomes, including inflight-limit decisions |

The module aggregates counters and gauges in its shared metrics zone and
exposes them at the configured `markdown_metrics` location. There is no
separate `overload_total` metric family.

## Implementation Files

| File | Role |
|------|------|
| `src/ngx_http_markdown_inflight_impl.h` | Counter module (increment, decrement, cleanup) |
| `src/ngx_http_markdown_request_impl.h` | Integration point (header filter, after eligibility) |
| `src/ngx_http_markdown_metrics_impl.h` | Snapshot collection for inflight fields |
| `src/ngx_http_markdown_metrics_v1_renderer.h` | Prometheus text exposition |
| `src/ngx_http_markdown_filter_module.h` | `max_inflight` config field and default |
