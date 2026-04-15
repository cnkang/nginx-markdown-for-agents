# 0.5.0 Compatibility Matrix

## Overview

This matrix explicitly classifies each existing operator-facing capability's support
status under the streaming path. Every capability must be assigned exactly one of the
three allowed states; a fourth state ("theoretically supported but silently degrades on
failure") is forbidden.

## State Definitions

| State | Meaning |
|-------|---------|
| `streaming-supported` | Streaming path fully supports this capability. Some capabilities carry a *conditional* qualifier — see the Notes column for the prerequisite that must hold. |
| `full-buffer-only` | This capability is only available under the full-buffer path. When the streaming engine is active and this capability is required, the engine selector forces the request to the full-buffer path before any streaming work begins. |
| `pre-commit-fallback-only` | The streaming path may begin processing, but if the capability's requirements exceed the lookahead budget the engine falls back to full-buffer processing during the Pre-Commit Phase. The fallback is transparent to the client. |

## Capability Classification Matrix

| # | Capability | Classification | Notes |
|---|-----------|---------------|-------|
| 1 | automatic decompression | `streaming-supported` | Streaming decompressor performs incremental decompression; each upstream chunk is decompressed and fed to the Rust engine without buffering the full response. |
| 2 | charset detection / transcoding | `streaming-supported` | Three-level cascade (BOM → HTTP header → content sniff). The first 1024 bytes are sniffed during the Pre-Commit Phase; once the charset is locked the transcoder runs incrementally on every subsequent chunk. |
| 3 | security sanitization | `streaming-supported` | `StreamingSanitizer` provides the same XSS/XXE/SSRF protections as the full-buffer `SecurityValidator`. Tag-level state machine processes each token incrementally. |
| 4 | deterministic output | `streaming-supported` | Chunk split invariance guarantee: the concatenation of all emitted Markdown chunks is byte-identical to the output that the full-buffer path would produce for the same input, regardless of how the input is chunked. |
| 5 | `markdown_timeout` | `streaming-supported` | Cooperative timeout checked inside the Rust engine at every `feed()` / `finalize()` call. Exceeding the timeout in the Pre-Commit Phase triggers the `markdown_streaming_on_error` policy; in the Post-Commit Phase it triggers fail-closed. |
| 6 | `markdown_max_size` | `streaming-supported` | Cumulative input byte tracking. The byte counter is incremented on every `feed()` call. Exceeding the limit follows the same phase-dependent error handling as `markdown_timeout`. |
| 7 | `markdown_token_estimate` | `streaming-supported` | Token count is accumulated incrementally during streaming. The final estimate is returned by `finalize()` and exposed in the `X-Markdown-Tokens` response trailer or logged to metrics. |
| 8 | `markdown_front_matter` (common head metadata within lookahead) | `streaming-supported` | The `<head>` region of typical HTML pages is under 10 KB, well within the streaming lookahead budget. Metadata extraction completes during the Pre-Commit Phase and the YAML front matter block is emitted as the first Markdown output at the Commit Boundary. |
| 9 | `markdown_front_matter` (metadata beyond lookahead budget) | `pre-commit-fallback-only` | When the metadata required for front matter generation exceeds the lookahead budget (e.g., deeply nested or very large `<head>` sections), the Rust engine returns `ERROR_STREAMING_FALLBACK`. The NGINX module transparently falls back to the full-buffer path during the Pre-Commit Phase. This fallback is always executed regardless of the `markdown_streaming_on_error` setting. |
| 10 | `markdown_etag` (response-header ETag) | `full-buffer-only` | In streaming mode, response headers are sent at the Commit Boundary — before the full Markdown output is known. Because the ETag is computed from the complete Markdown output (BLAKE3 hash), it cannot be included in the response headers during streaming. The engine selector forces requests that require a response-header ETag to the full-buffer path. |
| 11 | `markdown_etag` (internal hash) | `streaming-supported` | BLAKE3 incremental hash: each emitted Markdown chunk is fed to the hasher at every flush point. The final hash is available after `finalize()` and is recorded to debug logs and metrics for observability, debug correlation, and future cache-layer design. In 0.5.0 the internal hash does not appear in client-visible response headers and does not participate in response revalidation. |
| 12 | `markdown_conditional_requests` (`if_modified_since_only`) | `streaming-supported` (conditional) | `If-Modified-Since` evaluation is completed entirely in the header filter phase, before the streaming engine starts. Condition: the check must complete before the body filter chain runs. |
| 13 | `markdown_conditional_requests` (`full_support`) | `full-buffer-only` | Markdown-variant `If-None-Match` revalidation requires the complete Markdown ETag to be available before response headers are sent. This is incompatible with streaming's "send headers first, then body" model. The engine selector forces these requests to the full-buffer path. |
| 14 | authenticated request policy / cache-control | `streaming-supported` (conditional) | Authentication detection and `Cache-Control` header adjustments are completed in the header filter phase. Condition: the header filter must run before the body filter chain. |
| 15 | decision logs / reason codes / metrics | `streaming-supported` | Streaming-specific reason codes (`STREAMING_PRECOMMIT_FAILOPEN`, `STREAMING_PRECOMMIT_REJECT`, `STREAMING_FAIL_POSTCOMMIT`, `STREAMING_FALLBACK_PREBUFFER`, `STREAMING_CONVERT`) and corresponding metrics counters are recorded at each decision point. |
| 16 | table conversion | `pre-commit-fallback-only` | Table rendering requires full lookahead to determine column count and alignment before emitting the first row. When the Rust engine encounters a `<table>` element that cannot be resolved within the lookahead budget, it returns `ERROR_STREAMING_FALLBACK` and the module falls back to the full-buffer path during the Pre-Commit Phase. |
| 17 | `prune_noise_regions` | `pre-commit-fallback-only` | Noise-region pruning requires structural analysis that may exceed the streaming lookahead budget. Triggers pre-commit fallback under the streaming path. |
| 18 | `markdown_on_wildcard` | `streaming-supported` | Wildcard `Accept` header handling is completed in the header filter phase, before the streaming engine starts. |

## Classification Notes

### `streaming-supported` — Conditions and Guarantees

Capabilities classified as `streaming-supported` work correctly under the streaming
path with no fallback required. Some carry a *(conditional)* qualifier, meaning they
depend on a prerequisite being satisfied:

- **Header-filter prerequisites** (rows 12, 14): These capabilities complete their
  work in the NGINX header filter phase, which runs before the body filter chain.
  The streaming engine only operates in the body filter chain, so there is no
  conflict. The condition is architectural and always holds in normal operation.

- **Incremental processing** (rows 1–8, 11, 15, 18): These capabilities use
  incremental algorithms that process data chunk-by-chunk without requiring the
  full response body.

### `full-buffer-only` — Why These Capabilities Cannot Stream

- **Response-header ETag** (row 10): The ETag is a hash of the complete Markdown
  output. In streaming mode, response headers (including `Content-Type` and `Vary`)
  are sent at the Commit Boundary — the moment the first Markdown chunk is ready.
  At that point the ETag has not been computed yet. Including a partial or
  placeholder ETag would violate HTTP semantics.

- **`full_support` conditional requests** (row 13): Markdown-variant `If-None-Match`
  revalidation compares the client-supplied ETag against the Markdown output's ETag.
  This comparison must happen before sending response headers (to potentially return
  304 Not Modified). Since the Markdown ETag is only available after full conversion,
  this is incompatible with streaming.

### `pre-commit-fallback-only` — Trigger Conditions

Capabilities in this category may begin streaming, but fall back to full-buffer
processing if their requirements exceed the streaming engine's lookahead budget.
The fallback always occurs during the Pre-Commit Phase (before any data is sent to
the client), so it is transparent to the client.

Trigger conditions:

- **Front matter beyond lookahead** (row 9): The `<head>` section is unusually large
  or deeply nested, exceeding the lookahead budget. The Rust engine signals
  `ERROR_STREAMING_FALLBACK`.

- **Table conversion** (row 16): A `<table>` element is encountered that requires
  full structural analysis (column count, alignment, cell spans) beyond the
  lookahead budget. The Rust engine signals `ERROR_STREAMING_FALLBACK`.

- **Noise-region pruning** (row 17): Structural analysis for noise-region
  identification exceeds the lookahead budget. The Rust engine signals
  `ERROR_STREAMING_FALLBACK`.

In all cases, `ERROR_STREAMING_FALLBACK` triggers an unconditional fallback to the
full-buffer path. This fallback is independent of the `markdown_streaming_on_error`
directive — it always executes regardless of the operator's failure policy setting.

## Lifecycle

1. **Design phase**: Determine initial classification (this document)
2. **Implementation phase**: Verify classification; record any changes
3. **Pre-release**: Final confirmation; publish as operator documentation

## Change Tracking

Any classification change must be recorded in this section and updated in all
affected documents.

| Date | Capability | Previous State | New State | Reason | Affected Documents |
|------|-----------|----------------|-----------|--------|--------------------|
| — | — | — | — | — | — |

## Sub-Capability Split Rules

- The canonical row set is owned by spec #12
- Downstream sub-specs (#13–#18) may split a canonical row into documented
  sub-capability rows
- Downstream sub-specs must not introduce new top-level operator-facing capability
  rows without first updating spec #12
