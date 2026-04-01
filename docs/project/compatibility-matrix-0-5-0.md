# 0.5.0 Compatibility Matrix

## Overview

This matrix explicitly classifies each existing operator-facing capability's support status under the streaming path.

## State Definitions

| State | Meaning |
|-------|---------|
| `streaming-supported` | Streaming path fully supports this capability |
| `full-buffer-only` | This capability is only available under the full-buffer path |
| `pre-commit-fallback-only` | Streaming path falls back to full-buffer processing during the pre-commit phase |

A fourth state ("theoretically supported but silently degrades on failure") is forbidden.

## Capability Classification Matrix

The following is the initial classification from the design phase. It will be verified during implementation and finalized before release.

| Capability | streaming-supported | full-buffer-only | pre-commit-fallback-only | Notes |
|------------|:---:|:---:|:---:|-------|
| automatic decompression | ✓ | | | Streaming decompressor with incremental decompression |
| charset detection / transcoding | ✓ | | | Three-level cascade, first 1024 bytes sniff |
| security sanitization | ✓ | | | StreamingSanitizer equivalent to SecurityValidator |
| deterministic output | ✓ | | | Chunk split invariance guarantee |
| `markdown_timeout` | ✓ | | | Cooperative timeout checked in Rust engine |
| `markdown_max_size` | ✓ | | | Cumulative input byte tracking |
| `markdown_token_estimate` | ✓ | | | Incremental accumulation, returned at finalize |
| `markdown_front_matter` (common head metadata within lookahead) | ✓ | | | Head region typically <10KB, within lookahead budget |
| `markdown_front_matter` (metadata beyond lookahead budget) | | | ✓ | Pre-commit fallback when exceeding lookahead budget |
| `markdown_etag` (response-header ETag) | | ✓ | | ETag cannot be sent in response headers under streaming (headers sent at Commit_Boundary) |
| `markdown_etag` (internal hash) | ✓ | | | BLAKE3 incremental hash, logged to logs/metrics after finalize |
| `markdown_conditional_requests` (`if_modified_since_only`) | ✓ | | | If-Modified-Since completed in header filter |
| `markdown_conditional_requests` (`full_support`) | | ✓ | | Markdown-variant If-None-Match requires full ETag |
| authenticated request policy / cache-control | ✓ | | | Completed in header filter phase |
| decision logs / reason codes / metrics | ✓ | | | Streaming-specific reason codes and metrics |
| table conversion | | | ✓ | Requires full lookahead to determine column count and alignment |
| `prune_noise_regions` | | | ✓ | Triggers pre-commit fallback under streaming path |
| `markdown_on_wildcard` | ✓ | | | Wildcard Accept handling completed in header filter phase |

## Lifecycle

1. **Design phase**: Determine initial classification (this document)
2. **Implementation phase**: Verify classification; record any changes
3. **Pre-release**: Final confirmation; publish as operator documentation

## Change Tracking

Any classification change must be recorded in this section and updated in all affected documents.

| Date | Capability | Previous State | New State | Reason | Affected Documents |
|------|-----------|----------------|-----------|--------|--------------------|
| — | — | — | — | — | — |

## Sub-Capability Split Rules

- The canonical row set is owned by spec #12
- Downstream sub-specs (#13–#18) may split a canonical row into documented sub-capability rows
- Downstream sub-specs must not introduce new top-level operator-facing capability rows without first updating spec #12
