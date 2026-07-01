# Error Policy Architecture (spec 51)

## Overview

The unified error policy determines how conversion failures are handled at
runtime. A single directive `markdown_error_policy` covers all error paths
(full-buffer, streaming, overload) with consistent semantics.

## Directive

```nginx
# Context: http, server, location
markdown_error_policy pass;            # Default: deliver original response
markdown_error_policy status 502;      # Return specified HTTP status code
markdown_error_policy status 503;      # Recommended for overload (with spec 52)
markdown_error_policy fail_closed;     # Return 502, never leak original content
```

Allowed status codes: `429`, `502`, `503`.

## Error Classes

| Class | Commit Stage | Configurable | Description |
|-------|-------------|-------------|-------------|
| `conversion_error` | pre-commit | Yes | HTML-to-Markdown conversion failed |
| `timeout` | pre-commit | Yes | Conversion exceeded timeout |
| `memory_budget_exceeded` | pre-commit | Yes | Memory budget exceeded |
| `ffi_panic` | pre-commit | Yes | Rust FFI panic caught |
| `decompression_error` | pre-commit | Yes | Decompression failed |
| `overload` | pre-commit | Yes | Inflight limit exceeded (spec 52) |
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

**Behavior**: Always `TerminateConnection` (close/abort the connection).

This is a safety invariant:
- Cannot return original HTML content (client expects Markdown).
- Cannot send a new status code (headers already sent).
- Must terminate to avoid client seeing partial/mixed content.

## Decision Function

```
decide_error_behavior(class, policy) → behavior
```

1. If `class.is_post_commit()` → `TerminateConnection` (forced, ignores policy)
2. Otherwise, apply policy:
   - `Pass` → `PassThrough`
   - `Status(n)` → `ReturnStatus(n)`
   - `FailClosed` → `ReturnStatus(502)`

## FFI Interface

```c
#include "markdown_converter.h"

/* Error class constants (0-9) */
/* 0=conversion_error, 1=timeout, 2=memory_budget_exceeded, ... */

/* Policy construction */
FFIErrorPolicy policy = { .kind = FFI_ERROR_POLICY_PASS, .status_code = 0 };
/* or */
FFIErrorPolicy policy = { .kind = FFI_ERROR_POLICY_STATUS, .status_code = 503 };

/* Decision call */
FFIErrorBehavior behavior;
uint8_t rc = markdown_decide_error_behavior(error_class, policy, &behavior);

/* Interpret result */
if (behavior.kind == FFI_ERROR_BEHAVIOR_PASS_THROUGH) {
    /* deliver original response */
} else if (behavior.kind == FFI_ERROR_BEHAVIOR_RETURN_STATUS) {
    /* return behavior.status_code */
} else if (behavior.kind == FFI_ERROR_BEHAVIOR_TERMINATE) {
    /* close connection (behavior.forced == 1) */
}
```

## Stability Contract

- Error class enum: frozen at 0.9.0, additive-only after 1.0.
- Post-commit forced TerminateConnection: safety invariant, never relaxed.
- Error class → reason code mapping: stable, additive-only after 1.0.
- `markdown_error_policy` directive semantics: frozen at 1.0.

## Related Specs

- **Spec 45**: Config V2 directive syntax.
- **Spec 48**: HeaderPlan (pre-commit/post-commit boundary).
- **Spec 52**: Worker inflight guard (overload detection).
- **Spec 53**: Reason code registry.
