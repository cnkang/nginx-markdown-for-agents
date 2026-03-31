# Large Response Optimization Design

This document describes the architecture for handling large HTTP responses in `nginx-markdown-for-agents`. It covers the incremental processing path, the threshold-based routing logic, the relationship to the existing full-buffer path, and the non-degradation guarantees that protect small-response performance.

## Context

The current architecture buffers the entire upstream response body before invoking the Rust converter through FFI. This works well for small and medium responses but creates memory pressure and latency spikes for large documents (100KB+). The current optimization introduces an optional incremental processing path as a routing and API-separation step: it lets large responses follow a distinct conversion path and establishes the Rust/C interfaces needed for future streaming work, but it does not yet eliminate the full-buffer memory profile because buffering still happens before conversion.

For background on the existing request lifecycle and buffering model, see:

- [REQUEST_LIFECYCLE.md](REQUEST_LIFECYCLE.md) — full request flow from header filter through body filter, including buffering, decompression, conditional requests, and fail-open behavior
- [PERFORMANCE_BASELINES.md](../testing/PERFORMANCE_BASELINES.md) — current FFI and E2E latency baselines, stage breakdown, and memory usage data

## Design Principles

- **Default off**: the incremental path is disabled by default at both the NGINX configuration level and the Rust compilation level
- **Non-degradation**: introducing the new path must not regress small-response performance or break existing functionality
- **Semantic equivalence**: for any valid input, the incremental path must produce output identical to the full-buffer path
- **Observable**: path selection is tracked through metrics so operators can monitor routing behavior in production

## Processing Path Architecture

```text
Response arrives at body filter
        |
        v
+------------------+
| Threshold Router |  (NGINX C module)
+------------------+
        |
        +--- threshold = off ---------> Full-Buffer Path (existing logic, unchanged)
        |
        +--- HEAD / 304 / fail-open --> Full-Buffer Path (always, regardless of threshold)
        |
        +--- Content-Length known
        |       |
        |       +--- CL < threshold --> Full-Buffer Path
        |       |
        |       +--- CL >= threshold -> Incremental Path
        |
        +--- No Content-Length -------> Buffer first, then decide:
                |
                +--- buffered < threshold --> Full-Buffer Path
                |
                +--- buffered >= threshold -> Incremental Path
```

### Full-Buffer Path (Existing)

No changes. The module buffers the complete response body, optionally decompresses it, resolves conditional requests, then calls `markdown_convert()` through FFI. This is the only active path when the feature is disabled.

### Incremental Path (New, Feature-Gated)

When the threshold router selects the incremental path, the NGINX module still buffers the complete response body (and decompresses it if needed), then hands the full buffer to the Rust `IncrementalConverter` via FFI in a single call sequence:

1. `markdown_incremental_new()` — create a converter instance with the current `ConversionOptions`
2. `markdown_incremental_feed()` — called once with the complete buffered body (`ctx->buffer.data`)
3. `markdown_incremental_finalize()` — produce the final `MarkdownResult`
4. `markdown_incremental_free()` — release the converter (only on error paths; `finalize` consumes the handle on success)

True per-upstream-chunk feeding from NGINX (calling `feed` as each body chunk arrives from upstream) is not implemented yet and remains a future change. The current implementation buffers first, then delegates to the incremental Rust API.

In practical terms, the current incremental path should be understood as:

- a threshold-routed conversion path with independent metrics and rollout control
- an API/ABI scaffold for future chunk-oriented processing
- semantic groundwork for proving equivalence between full-buffer and future streaming variants

It should not yet be understood as a true streaming or peak-memory-reduction path. The request body still exists as a full NGINX-side buffer, and the Rust incremental API currently accumulates fed bytes internally before parsing.

> [!NOTE]
> **64 MiB Hard Limit**: To prevent uncontrolled memory growth before true streaming is implemented, the `IncrementalConverter` in Rust enforces a strict 64 MiB buffer limit. If the sum of fed chunks exceeds this size, `feed_chunk` returns a `MemoryLimit` error, triggering the C module's `markdown_on_error` policy.

> [!WARNING]
> **Architecture Warning: Do not increase the 64 MiB limit**
> 
> A 64 MiB HTML document currently translates to roughly 2.5GB-3GB of peak RAM consumption during Rust DOM tree construction (an empirical ~40x memory bloat factor). 
> 
> Because NGINX uses a multi-worker concurrency model, arbitrarily increasing this limit (e.g., to 1 GB) exposes the server to extreme OOM (Out Of Memory) risks. Just 4 concurrent 1 GB requests would demand over 160 GB of RAM, triggering the OS OOM Killer, crashing NGINX workers, and taking down all other in-flight requests. It also creates a massive DoS attack vector.
> 
> **To safely support GB-scale responses in the future**, the architecture must be fundamentally shifted from DOM-tree building to a **Streaming SAX Parser**. True stream processing (maintaining $O(1)$ memory by discarding parsed chunks instantly) is the only safe way to surpass the 64 MiB limit without unbounded memory amplification.

The incremental API is compiled only when the `incremental` Rust feature flag is enabled. When the feature is not compiled but a threshold is configured, the module logs a warning per request and falls back to the full-buffer path.

## Threshold Router

The Threshold Router is the decision point in the NGINX C module that selects which processing path a request follows.

### Configuration Directive

```nginx
# Default: incremental path disabled
markdown_large_body_threshold off;

# Enable: route responses >= 512KB to incremental path
markdown_large_body_threshold 512k;
```

| Directive | Syntax | Default | Context |
|-----------|--------|---------|---------|
| `markdown_large_body_threshold` | `off \| <size>` | `off` | http, server, location |

### Path Selection Logic

The router evaluates in this order:

1. If `large_body_threshold == 0` (off): all requests use the full-buffer path. Behavior is identical to a build without this feature.
2. If the request is HEAD, 304, or fail-open replay: always use the full-buffer path (see Special Path Semantics below).
3. If `Content-Length` is present and `Content-Length >= large_body_threshold`: use the incremental path.
4. If `Content-Length` is absent: buffer the response; once buffered size exceeds the threshold, switch to the incremental path.

### Data Model Extensions

Request context (`ngx_http_markdown_ctx_t`):

```c
ngx_uint_t  processing_path;  /* 0 = full-buffer, 1 = incremental */
```

Module configuration (`ngx_http_markdown_conf_t`):

```c
size_t      large_body_threshold;  /* 0 = off (default) */
```

Metrics (`ngx_http_markdown_metrics_t`):

```c
struct {
    ngx_atomic_t  fullbuffer;      /* Requests routed to full-buffer path */
    ngx_atomic_t  incremental;     /* Requests routed to incremental path */
} path_hits;
```

Path hit counters are exposed through the existing `markdown_metrics` endpoint.

## Incremental Converter API (Rust)

### Feature Gate

```toml
[features]
default = []
incremental = []
```

The `incremental` feature is off by default. When disabled, no incremental-only symbols are exported and the legacy `markdown_convert()` ABI remains unchanged.

### Rust Interface

```rust
#[cfg(feature = "incremental")]
pub struct IncrementalConverter { /* internal state */ }

#[cfg(feature = "incremental")]
impl IncrementalConverter {
    pub fn new(options: ConversionOptions) -> Self;
    pub fn feed_chunk(&mut self, data: &[u8]) -> Result<(), ConversionError>;
    pub fn finalize(self) -> Result<MarkdownResult, ConversionError>;
}
```

### State Machine

```text
[*] --> Created        (new)
Created --> Feeding     (feed_chunk)
Feeding --> Feeding     (feed_chunk)
Created --> Finalized   (finalize, empty input)
Feeding --> Finalized   (finalize)
Feeding --> Error       (feed_chunk failed)
Finalized --> [*]       (result returned)
Error --> [*]
```

### FFI Functions

Exported only when `incremental` feature is enabled:

| FFI Function | Purpose |
|-------------|---------|
| `markdown_incremental_new()` | Create converter instance |
| `markdown_incremental_feed()` | Feed a chunk of input data |
| `markdown_incremental_finalize()` | Finalize and return result |
| `markdown_incremental_free()` | Free converter resources |

Existing FFI function signatures are not modified.

## Special Path Semantics

HEAD requests, 304 responses, and fail-open replays always use the full-buffer path, regardless of the configured threshold. This preserves the existing behavior documented in [REQUEST_LIFECYCLE.md](REQUEST_LIFECYCLE.md):

| Special Path | Behavior | Rationale |
|-------------|----------|-----------|
| HEAD request | Full-buffer path | No body is sent; conversion may still run for ETag/conditional support but the incremental path adds no value |
| 304 Not Modified | Full-buffer path | No body conversion needed; conditional logic operates on cached state |
| Fail-open replay | Full-buffer path | The module is replaying already-buffered original HTML; incremental processing does not apply |

When `markdown_large_body_threshold` is set to `off`, all requests follow the full-buffer path. The runtime behavior is identical to a build that does not include this feature.

## Non-Degradation Guarantees

### Small Response P50 Latency

The introduction of the threshold router and incremental path must not degrade small-response P50 latency by more than 5% compared to the baseline recorded in [PERFORMANCE_BASELINES.md](../testing/PERFORMANCE_BASELINES.md).

This is validated by:

1. Running `tools/perf/run_perf_baseline.sh --tier small` after changes
2. Comparing the measured `p50_ms` against the platform baseline in `perf/baselines/<platform>.json`
3. Failing the check if degradation exceeds 5%

This 5% threshold is specific to the large-response optimization validation and is independent of the general CI performance gate thresholds (which use different warning/blocking levels).

### Functional Consistency

For all inputs that produce correct results through the full-buffer path, the incremental path (single `feed_chunk` of the complete input + `finalize`) must produce byte-identical output. This is verified through property-based testing (proptest) that generates random HTML inputs and compares both paths.

## Error Handling

| Error Scenario | Handling | User-Visible Behavior |
|---------------|----------|----------------------|
| `feed_chunk()` failure | Returns `ConversionError`; C module applies `markdown_on_error` policy | fail-open: original HTML; fail-closed: 502 |
| `finalize()` failure | Same as above | Same as above |
| Missing `Content-Length` | Buffer first, decide path after threshold comparison | Transparent to client |
| `incremental` feature not compiled but threshold configured | Ignore threshold, use full-buffer path, log warning | `error.log` warning; conversion still works |

## Rollback

Disabling the incremental path requires no code changes:

```nginx
markdown_large_body_threshold off;
```

```bash
nginx -s reload
```

This immediately routes all requests back to the full-buffer path. See [LARGE_RESPONSE_ROLLOUT.md](../guides/LARGE_RESPONSE_ROLLOUT.md) for the full rollout and rollback playbook.

## Related Documents

- [REQUEST_LIFECYCLE.md](REQUEST_LIFECYCLE.md) — existing request flow and buffering model
- [PERFORMANCE_BASELINES.md](../testing/PERFORMANCE_BASELINES.md) — latency and memory baselines
- [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) — overall C + Rust architecture
- [ADR/0002-full-buffering-approach.md](ADR/0002-full-buffering-approach.md) — why the current path uses full buffering
