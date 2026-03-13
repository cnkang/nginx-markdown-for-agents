# Integration Tests for the NGINX Markdown Filter Module

## Overview

This document describes the integration tests executed by:

```bash
components/nginx-module/tests/integration/run_integration_tests.sh
```

These tests run against a real NGINX process and validate end-to-end request/response behavior for the currently implemented runtime suite.

This document is about the inline/runtime integration path. The canonical E2E suite lives under `tools/e2e/` and is described in [E2E_TESTS.md](E2E_TESTS.md).

## Current Runtime Test Set

The unified runtime entrypoint currently runs 10 scenarios:

### Test 1: Basic Conversion with `Accept: text/markdown`

Validates that HTML responses are converted to Markdown when conversion is enabled and the client requests `text/markdown`.

### Test 2: Passthrough with `Accept: text/html`

Validates that conversion is skipped when the client does not request Markdown.

### Test 3: Configuration Inheritance

Validates that `markdown_filter` inheritance and location-level overrides behave correctly.

### Test 4: Authenticated Content Handling

Validates authenticated-request handling with `markdown_auth_policy allow` and `Cache-Control: private`.

### Test 5: Variable-Driven `markdown_filter` Resolution

Validates dynamic enablement via `markdown_filter $variable`, including:

- truthy/falsy runtime values (`on`, `off`, `yes`, `no`, etc.)
- surrounding whitespace normalization (for example `" on "`)
- safe fallback for invalid runtime values (treated as disabled)

### Test 6: Range Request Bypass with Real NGINX File Serving

Validates that `Range` requests remain passthrough HTML and are not converted to Markdown.

### Test 7: Shared Metrics Aggregation Across Workers

Validates that the `markdown_metrics` endpoint reports cross-worker totals from shared memory, not worker-local counters.

### Test 8: Delegated `If-Modified-Since` Runtime Validation

Runs `tools/ci/verify_real_nginx_ims.sh` from the integration entrypoint to verify Markdown-negotiated `If-Modified-Since` behavior. When `run_integration_tests.sh` is launched with a reusable `NGINX_BIN=/absolute/path/to/nginx` install layout (binary under `sbin/` with adjacent `conf/mime.types`), the delegated script reuses that binary instead of compiling another NGINX runtime.

### Test 9: Chunked Streaming Native Smoke Validation

Runs `tools/e2e/verify_chunked_streaming_native_e2e.sh --profile smoke` from the integration entrypoint to cover chunked buffering and oversized fail-open behavior. The delegated script reuses the exported `NGINX_BIN` only when that binary exposes reusable runtime assets; otherwise it falls back to self-building its own runtime.

### Test 10: Large Response Native Validation

Runs `tools/e2e/verify_large_markdown_response_e2e.sh` from the integration entrypoint to keep the large-response buffering path under regression coverage. The delegated script reuses the exported `NGINX_BIN` only when that binary exposes reusable runtime assets; otherwise it falls back to self-building its own runtime.

## Requirements Coverage (Current Runtime Suite)

The current runtime script provides direct end-to-end coverage for:

- core content negotiation and conversion path
- passthrough behavior
- configuration inheritance and override behavior
- authenticated-request policy handling
- variable-driven `markdown_filter` resolution behavior
- range-request bypass behavior
- shared metrics aggregation across workers
- delegated `If-Modified-Since` runtime behavior
- chunked/streaming edge paths
- large-response buffering path

## How to Run

```bash
cd components/nginx-module/tests/integration
./run_integration_tests.sh

# Or point the inline scenarios at a specific nginx binary
NGINX_BIN=/absolute/path/to/nginx ./run_integration_tests.sh
```

When `NGINX_BIN` is supplied, the delegated native runtime checks reuse it only if it looks like a module-enabled runtime install (for example `.../sbin/nginx` beside `.../conf/mime.types`). If not, the inline integration tests still use that binary, but the delegated runtime checks self-build their own NGINX runtime.

## Prerequisites

1. `nginx` is available in `PATH` for the inline runtime scenarios, or you provide `NGINX_BIN=/absolute/path/to/nginx`.
2. That NGINX build includes this module.
3. Rust converter library is built and linked in that NGINX build.
4. `curl` is installed.
5. External runtime checks invoked by the script always need Python 3 and curl-related tooling; `cargo`, `make`, `tar`, and native build tools are only required when the delegated scripts cannot reuse a runtime-style `NGINX_BIN` and must build their own NGINX binary.

## Expected Output (Shape)

You should see 10 test sections and a summary similar to:

```text
Test 1: Basic Conversion with Accept: text/markdown
...
Test 10: Large response native validation
...
Tests run:    10
Tests failed: 0
```

## Troubleshooting

### `nginx not found in PATH`

Install NGINX, add it to your shell `PATH`, or run the suite with `NGINX_BIN=/absolute/path/to/nginx`.

### Conversion not happening in variable-driven mode

Check:

1. `markdown_filter $your_var;` is configured in the active location.
2. The variable resolves to supported values (`1/0`, `on/off`, `yes/no`, `true/false`).
3. Client sends `Accept: text/markdown`.

### NGINX startup/config errors

Inspect temporary logs generated by the script:

- `/tmp/nginx-markdown-test-error.log`
- `/tmp/nginx-start.log`
