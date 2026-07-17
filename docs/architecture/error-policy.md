# Error Policy Architecture

## Overview

The unified error policy determines how conversion failures are handled at
runtime. A single directive `markdown_error_policy` covers all error paths
(full-buffer, streaming, overload) with consistent semantics.

## Directive

```nginx
# Context: http, server, location
markdown_error_policy pass;            # Default: deliver original response
markdown_error_policy status 429;      # Return explicit overload status
markdown_error_policy status 503;      # Recommended for overload (with spec 52)
markdown_error_policy fail_closed;     # Return 502, never leak original content
```

Allowed status codes: `429`, `503` (`502` is the `fail_closed` default; use `fail_closed` instead of `status 502`).

## Error Classes

| Class | Commit Stage | Configurable | Description |
|-------|-------------|-------------|-------------|
| `conversion_error` | pre-commit | Yes | HTML-to-Markdown conversion failed |
| `timeout` | pre-commit | Yes | Conversion exceeded timeout |
| `memory_budget_exceeded` | pre-commit | Yes | Memory budget exceeded |
| `ffi_panic` | pre-commit | Yes | Rust FFI panic caught |
| `decompression_error` | pre-commit | Yes | Decompression failed |
| `overload` | pre-commit | Yes | Inflight limit exceeded |
| `invalid_dynconf` | pre-commit | Yes | Dynamic config invalid |
| `degraded_snapshot` | pre-commit | Yes | Using last-known-good snapshot |
| `header_plan_apply_error` | post-commit | **No** | HeaderPlan commit failed |
| `streaming_mid_flight_error` | post-commit | **No** | Streaming failed mid-body |

## Pre-commit vs Post-commit

### Pre-commit Errors

Errors that occur before response headers are sent to the client. The module
can safely:

- **Pass**: Deliver the original upstream response unmodified (fail-open).
- **Status**: Return the configured HTTP status code.
- **FailClosed**: Return 502 Bad Gateway (never leak original content).

### Post-commit Errors

Errors that occur after headers have been sent (e.g., `Content-Type: text/markdown`
is already on the wire). The module **cannot** rewrite the status line.

The configured policy selects one of two protocol-safe actions:

- **Pass**: Ask the Rust streaming converter to close any open Markdown
  structures and send only those closure bytes. If safe finish fails, abort the
  response.
- **FailClosed or Status**: Abort the response immediately. The configured
  pre-commit status cannot replace a status line that is already committed.

Both actions preserve these safety invariants:
- Cannot return original HTML content (client expects Markdown).
- Cannot send a new status code (headers already sent).
- Cannot synthesize Markdown closure in C; only Rust-owned state may produce
  safe-finish bytes.

## Decision Function

```
decide_error_behavior(class, policy) → behavior
```

1. If the failure is post-commit:
   - `Pass` -> `SafeFinish`, falling back to `Abort`
   - `FailClosed` or `Status(n)` -> `Abort`
2. Otherwise, apply the configured pre-commit policy:
   - `Pass` → `PassThrough`
   - `Status(n)` → `ReturnStatus(n)`
   - `FailClosed` → `ReturnStatus(502)`

## Runtime boundary

The NGINX C module applies the configured pass/status/fail-closed behavior at
the request lifecycle boundary. Rust exposes `markdown_classify_error_code`
for canonical error classification; the former zero-consumer FFI behavior
decision structs and export were removed before the v1 ABI freeze.

## Stability Contract

- Error class enum: frozen at 0.9.0, additive-only after 1.0.
- Post-commit never replays HTML or rewrites status; only Rust safe-finish or
  abort is allowed.
- Error class → reason code mapping: stable, additive-only after 1.0.
- `markdown_error_policy` directive semantics: frozen at 1.0.

## Related Documents

- **Config V2**: Directive syntax (see [MIGRATION-0.9.md](../guides/MIGRATION-0.9.md)).
- **HeaderPlan**: Pre-commit/post-commit boundary (see [header mutation inventory](header-mutation-inventory.md)).
- **Worker Inflight Guard**: Overload detection.
- **Reason Code Registry**: See [Observability Schema v1](observability-schema-v1.md).
