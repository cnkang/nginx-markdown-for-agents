# Large Response Optimization Design

This document describes the architecture for handling large HTTP responses in `nginx-markdown-for-agents`. It covers the streaming processing path, the policy-based routing logic, and the relationship to the full-buffer path. It also covers the non-degradation guarantees that protect small-response performance.

## Context

Since v0.8.0, the module supports **two conversion engines**:

- **Full-buffer engine** (default for small responses): buffers the complete eligible response body before conversion through FFI. This remains the simplest and most tested path.
- **Streaming engine** (enabled via `markdown_streaming`): processes HTML incrementally through a bounded-memory pipeline. The pipeline runs charset detection, tokenization, sanitization, a state machine, and emission, with per-request memory limits and backpressure.

The legacy incremental path was a stepping stone toward true streaming. It sits behind the `incremental` Rust feature flag and routes through the retired `markdown_large_body_threshold` directive. It is **no longer the recommended path** for new deployments. See [RFC-0008](RFC-0008-streaming-conversion-support-contract.md) and [ADR-0023](ADR/0023-single-streaming-policy.md) for the streaming design.

For background on the existing request lifecycle and buffering model, see:

- [REQUEST_LIFECYCLE.md](REQUEST_LIFECYCLE.md) — full request flow from header filter through body filter, including buffering, decompression, conditional requests, and fail-open behavior
- [PERFORMANCE_BASELINES.md](../testing/PERFORMANCE_BASELINES.md) — current FFI and E2E latency baselines, stage breakdown, and memory usage data
- [ADR-0002](ADR/0002-full-buffering-approach.md) — why the original path uses full buffering
- [ADR-0004](ADR/0004-streaming-bounded-memory-conversion.md) — bounded-memory streaming design

## Design Principles

- **Policy-selected**: `markdown_streaming off` selects bounded full-buffer
  conversion. `auto` applies the bounded response-shape heuristic. `force`
  requests streaming after hard eligibility gates
- **Non-degradation**: introducing the new path must not regress small-response performance or break existing functionality
- **Semantic equivalence**: for any valid input, the active streaming path must
  produce output equivalent to the full-buffer path
- **Observable**: metrics track path selection, so operators can monitor routing behavior in production

## Active 0.9.2 Processing Path Architecture

```text
Response enters the header/body filter chain
        |
        v
+-------------------------+
| markdown_streaming      |
+-------------------------+
        |
        +--- off ----------> Bounded Full-Buffer Path
        |
        +--- auto ---------> Shape heuristic + eligibility gates
        |                         |
        |                         +--> Streaming Path when eligible
        |                         +--> Bounded Full-Buffer otherwise
        |
        +--- force --------> Streaming Path when all hard gates pass
```

### Full-Buffer Path

No changes. The module buffers the complete response body, optionally
decompresses it, resolves conditional requests, then calls `markdown_convert()`
through FFI. This full-buffer Rust path remains available when the module
selects full-buffer processing (`markdown_streaming off`), when `auto` decides
that streaming is not suitable, or when a codec/cache-validation gate requires
it.

### Active Streaming Path

The active streaming path feeds bounded chunks through charset detection,
tokenization, sanitization, state management, and emission. The
`markdown_limits streaming_buffer=` setting bounds its working-set and replay
memory. Its backpressure state preserves ownership across `NGX_AGAIN`. It is
the supported large-response path in 0.9.2.

### Historical Incremental Path (retired in 0.9.0)

> ⚠️ **RETIRED IN 0.9.0** — The incremental conversion path described below is
> a historical record, not a current feature claim. The 0.9.2 binary does not
> expose `markdown_large_body_threshold`. The `markdown_incremental_*` FFI
> functions remain in the production FFI registry (see
> [FFI_MIGRATION_CONTRACT.md](FFI_MIGRATION_CONTRACT.md)) but no operator
> threshold selects this path. Use `markdown_streaming` and
> `markdown_limits streaming_buffer=` for the active path.

When the feature-gated incremental conversion path handled a request, the NGINX
module still buffered the complete response body. It decompressed the body if
needed and handed the full buffer to the Rust `IncrementalConverter` via FFI.
The historical call sequence was:

1. `markdown_incremental_new_with_code()` — create a converter instance with the current `ConversionOptions` and retain the status code
2. `markdown_incremental_feed()` — called once with the complete buffered body (`ctx->buffer.data`)
3. `markdown_incremental_finalize()` — produce the final `MarkdownResult`
4. `markdown_incremental_free()` — release the converter only when `feed`
   fails or `finalize` rejects invalid arguments. After the call accepts valid
   non-NULL arguments, `finalize` consumes the handle regardless of its
   return code

True per-upstream-chunk feeding from NGINX was never implemented by this retired
path. The current implementation buffers first, then delegates to the incremental
Rust API. No operator threshold selects this path.

In historical terms, the retired incremental path represented:

- a threshold-routed conversion path with independent metrics and rollout control
- an API/ABI scaffold for future chunk-oriented processing
- semantic groundwork for proving equivalence between full-buffer and future streaming variants

It was not a true streaming or peak-memory-reduction path. The request body still existed as a full NGINX-side buffer, and the Rust incremental API accumulated fed bytes internally before parsing. The active streaming engine supersedes this path in 0.9.2.

> [!NOTE]
> **Historical pre-0.9.0 limit**: Before the bounded streaming path was
> implemented, the Rust `IncrementalConverter` used a strict 64 MiB buffer
> limit. This note describes that retired path. The active configuration sets
> limits through `markdown_limits` and the active streaming engine.

> [!WARNING]
> **Historical architecture warning: do not increase the old 64 MiB limit**
> 
> A 64 MiB HTML document currently translates to roughly 2.5GB-3GB of peak RAM consumption during Rust DOM tree construction. That is an empirical ~40x memory bloat factor.
> 
> Because NGINX uses a multi-worker concurrency model, increasing this limit
> (for example to 1 GB) exposes the server to extreme OOM (Out Of Memory) risks.
> Just 4 concurrent 1 GB requests would demand over 160 GB of RAM. This triggers the OS OOM Killer, crashes NGINX workers, and takes down all other in-flight requests. It also creates a massive DoS attack vector.
> 
> **To safely support GB-scale responses in the future**, the architecture must be fundamentally shifted from DOM-tree building to a **Streaming SAX Parser**. True stream processing maintains $O(1)$ memory by discarding parsed chunks instantly. It is the only safe way to surpass the 64 MiB limit without unbounded memory amplification.

The incremental API and its threshold router are historical pre-0.9.0
material. The 0.9.2 binary does not expose `markdown_large_body_threshold`.
Use `markdown_streaming` and `markdown_limits streaming_buffer=` for the active
path.

## Historical pre-0.9.0 threshold router

> ⚠️ **RETIRED IN 0.9.0, REMOVED IN 0.9.2** — The `markdown_large_body_threshold`
> directive was a **reject-only stub** in 0.9.0 and 0.9.1. The 0.9.2 release
> deleted the stub, so setting it in `nginx.conf` now fails `nginx -t`
> with the standard `unknown directive` error. There is no Config V2
> replacement. The
> internal `routing.large_body_threshold` struct field persists for the
> feature-gated incremental path, but the threshold is no longer
> user-configurable. The following sections remain as historical design
> reference for pre-0.9.0 deployments.

The Threshold Router is the decision point in the NGINX C module that selects which processing path a request follows.

> The configuration directive, path selection logic, and data model below
> describe **pre-0.9.0 behavior**. They do not apply to 0.9.2 or later. The
> 0.9.2 binary removed `markdown_large_body_threshold` and the threshold
> router. Use `markdown_streaming` for path selection.

### Configuration Directive (pre-0.9.0)

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
4. If `Content-Length` is absent: buffer the response. Once the buffered size
   reaches or exceeds the threshold, switch to the incremental path.

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

The module exposes path hit counters through the existing `markdown_metrics` endpoint.

## Incremental Converter API (Rust)

### Feature Gate

```toml
[features]
default = []
incremental = []
```

The `incremental` feature is off by default. When disabled, the module exports no incremental-only symbols and the legacy `markdown_convert()` ABI remains unchanged.

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

The build exports these functions only when you enable the `incremental` feature:

| FFI Function | Purpose |
|-------------|---------|
| `markdown_incremental_new_with_code()` | Create converter instance with explicit status |
| `markdown_incremental_feed()` | Feed a chunk of input data |
| `markdown_incremental_finalize()` | Finalize and return result |
| `markdown_incremental_free()` | Free converter resources |

The pre-v1 ABI reset removed the redundant NULL-only constructor.

## Special Path Semantics

HEAD requests, 304 responses, and fail-open replays always use the full-buffer path, regardless of the configured threshold. This preserves the existing behavior documented in [REQUEST_LIFECYCLE.md](REQUEST_LIFECYCLE.md):

| Special Path | Behavior | Rationale |
|-------------|----------|-----------|
| HEAD request | Full-buffer path | No body is sent; representation headers describe the Markdown representation a GET would select (no fabricated body-derived fields) but the incremental path adds no value |
| 304 Not Modified | Full-buffer path | No body conversion needed; conditional logic operates on cached state |
| Fail-open replay | Full-buffer path | The module is replaying already-buffered original HTML; incremental processing does not apply |

When `markdown_large_body_threshold` is set to `off`, all requests follow the full-buffer path. The runtime behavior is identical to a build that does not include this feature.

## Non-Degradation Guarantees

### Small Response P50 Latency

The introduction of the active streaming engine must not degrade small-response P50 latency by more than 5%. [PERFORMANCE_BASELINES.md](../testing/PERFORMANCE_BASELINES.md) records the comparison baseline.

Validation steps:

1. Running `tools/perf/run_perf_baseline.sh --tier small` after changes
2. Comparing the measured `p50_ms` against the platform baseline in `perf/baselines/<platform>.json`
3. Failing the check if degradation exceeds 5%

This 5% threshold is specific to the large-response optimization validation. It is independent of the general CI performance gate thresholds (which use different warning/blocking levels).

### Functional Consistency

For all inputs that produce correct results through the full-buffer path, the active streaming engine must produce byte-identical output. Property-based testing (proptest) verifies this by feeding random HTML inputs through the streaming engine's chunked feed path and comparing the output against the full-buffer path.

## Error Handling (Historical — pre-0.9.0 threshold router)

> ⚠️ **HISTORICAL** — The table below describes the retired `markdown_large_body_threshold` routing removed in 0.9.2. It does not apply to the active `markdown_streaming` path.

|| Error Scenario | Handling | User-Visible Behavior ||
|---------------|----------|----------------------|
| `feed_chunk()` failure | Returns `ConversionError`; C module applies `markdown_error_policy` | fail-open: original HTML; fail-closed: 502 |
| `finalize()` failure | Same as above | Same as above |
| Missing `Content-Length` | Buffer first, decide path after threshold comparison | Transparent to client |
| `incremental` feature not compiled but threshold configured (pre-0.9.0) | Ignore threshold, use full-buffer path, log warning | `error.log` warning; conversion still works |

For the active `markdown_streaming` path error handling, see [STREAMING_COMPATIBILITY.md](../features/STREAMING_COMPATIBILITY.md) and [AUTOMATIC_DECOMPRESSION.md](../features/AUTOMATIC_DECOMPRESSION.md).

## Rollback

Disabling the streaming path requires no code changes:

```nginx
markdown_streaming off;
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
