# End-to-End Tests for the NGINX Markdown Filter Module

## Overview

The canonical E2E surface now lives under `tools/e2e/`.

These checks validate the full request path with a real NGINX runtime, real TCP connections, and scenario-specific backends where needed. They are the maintained source of truth for native runtime behavior that goes beyond the inline integration suite.

```text
client -> NGINX runtime -> upstream/backend -> NGINX markdown filter -> client
```

Use this page to answer three questions:

- which E2E scenarios are considered canonical
- which script owns each scenario
- how `make test-e2e` and CI reuse native NGINX runtimes

## Canonical Suite

`make test-e2e` delegates to:

```bash
tools/e2e/run_e2e_suite.sh
```

That suite currently runs three focused checks:

| Script | Purpose |
|--------|---------|
| `tools/e2e/verify_proxy_tls_backend_e2e.sh` | Real proxy-chain validation against a local TLS backend |
| `tools/e2e/verify_chunked_streaming_native_e2e.sh --profile smoke` | Chunked buffering and oversized fail-open smoke coverage |
| `tools/e2e/verify_large_markdown_response_e2e.sh` | Large-response buffering and conversion validation |

The suite keeps the public command stable:

```bash
make test-e2e
```

but the maintained implementation now lives entirely under `tools/e2e/`, not under `components/nginx-module/tests/e2e/`.

## What E2E Covers

The canonical E2E suite is intentionally focused on runtime paths that benefit from a native NGINX binary and real network behavior:

- proxy-chain Markdown conversion through HTTPS upstreams
- backend header preservation plus module-side `ETag` regeneration
- backend `500` passthrough behavior
- `HEAD` requests through a proxy path
- chunked buffering behavior
- large-response handling and fail-open size protection

### Coverage E2E Scenarios

The coverage script (`tools/sonar/collect_nginx_coverage.sh`) runs expanded E2E scenarios against an instrumented NGINX instance to collect gcov/lcov data. These scenarios are organized by subsystem:

| Category | Scenarios |
|----------|-----------|
| **Auth detection** | Bearer token, cookie prefix match (`session*`), cookie suffix match (`*_logged_in`), no auth, non-matching cookie |
| **Conditional requests** | `If-None-Match: *`, quoted ETag, multi-ETag, `If-Modified-Since`, IMS-only bypass, disabled conditional bypass |
| **Error paths** | 404 passthrough, reject-error, POST method ineligibility, Range header skip |
| **Accept header diversity** | Exact match, subtype wildcard (`text/*`), all wildcard (`*/*`), q-value sorting, q=0 rejection, no markdown match, multi-entry tie-break, wildcard-disabled rejection |
| **Metrics formats** | Prometheus format, auto format (text/plain), auto format (application/json), default metrics endpoint |
| **Body filter / headers** | Small file (single-buffer), large file (chain accumulation), HEAD request, GFM flavor, CommonMark flavor, size-limit rejection, auth Cache-Control modification |

These checks complement, rather than replace, `make test-nginx-integration`.

Use the integration suite for:

- inline request/response behavior with a real NGINX runtime
- config inheritance and variable-driven directive behavior
- shared-metrics aggregation checks
- delegated `If-Modified-Since` runtime validation

Use the E2E suite for:

- real upstream proxy chains
- native runtime scenarios that are easier to reason about as focused standalone scripts

## Proxy/TLS Backend Check

The proxy/TLS scenario is owned by:

```bash
tools/e2e/verify_proxy_tls_backend_e2e.sh
```

It launches:

- a module-enabled NGINX runtime
- a small local TLS backend fixture at `tools/e2e/fixtures/tls_backend_server.py`

It validates:

1. Markdown conversion through a real HTTPS proxy hop
2. `Cache-Control` preservation plus regenerated Markdown `ETag`
3. backend `500` passthrough without conversion
4. `HEAD` behavior through the proxy path

Run it directly when you are changing proxy or header behavior:

```bash
tools/e2e/verify_proxy_tls_backend_e2e.sh
```

## Runtime Reuse and `NGINX_BIN`

All canonical native E2E scripts accept:

```bash
NGINX_BIN=/absolute/path/to/nginx
```

When that binary belongs to a reusable runtime layout, for example:

```text
.../runtime/sbin/nginx
.../runtime/conf/mime.types
```

the scripts reuse it instead of rebuilding NGINX.

`tools/e2e/run_e2e_suite.sh` bootstraps this once through the proxy/TLS check, then reuses the validated binary for the chunked and large-response checks.

The GitHub Actions `runtime-regressions` job follows the same pattern:

1. `tools/ci/verify_real_nginx_ims.sh` validates the IMS path and emits a reusable runtime
2. chunked smoke reuses that `NGINX_BIN`
3. large-response validation reuses that same `NGINX_BIN`

This keeps the runtime path canonical without rebuilding native NGINX for every delegated check.

## How to Run

Recommended entrypoints:

```bash
# Full canonical E2E suite
make test-e2e

# Run one focused proxy/TLS scenario directly
tools/e2e/verify_proxy_tls_backend_e2e.sh

# Reuse a specific nginx runtime
NGINX_BIN=/absolute/path/to/nginx make test-e2e
```

Artifact retention is opt-in on the focused scripts:

```bash
tools/e2e/verify_proxy_tls_backend_e2e.sh --keep-artifacts
tools/e2e/verify_chunked_streaming_native_e2e.sh --profile smoke --keep-artifacts
tools/e2e/verify_large_markdown_response_e2e.sh --keep-artifacts
```

## Prerequisites

For direct execution of the canonical E2E scripts:

1. `curl`, `python3`, and common shell utilities are installed
2. `openssl` is available for the TLS backend fixture
3. if reusing `NGINX_BIN`, it points to a module-enabled binary
4. if not reusing `NGINX_BIN`, native build tooling is available (`cargo`, `make`, `tar`, compiler toolchain)

The shared native-build logic lives in:

```text
tools/lib/nginx_markdown_native_build.sh
```

That helper owns:

- Rust target detection
- header sync for native verification builds
- macOS deployment-target alignment
- reusable runtime layout checks

## Notes

- `make test-e2e` is the stable user-facing command.
- The maintained native E2E truth lives under `tools/e2e/`.
- Performance measurements captured during native checks are local diagnostics, not product guarantees. Canonical recorded baselines belong in [PERFORMANCE_BASELINES.md](PERFORMANCE_BASELINES.md).
