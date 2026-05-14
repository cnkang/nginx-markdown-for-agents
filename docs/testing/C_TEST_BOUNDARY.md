# C Test Boundary

**Status:** Phase 1 deliverable — authoritative boundary definition  
**Spec:** 0.6.3 Rust-first test migration requirements/design/tasks package  
**Requirements:** 5.1, 5.2, 5.3, 5.4, 5.5

---

## Purpose

This document defines which test concerns must remain in C and why. It is the
authoritative explanation for why some tests are not migrated during the 0.6.3
Rust-first test migration, and it provides a decision guide for future
contributors deciding where a new test belongs.

The 0.6.3 migration moves product-level HTTP E2E scenario logic into the Rust
harness. It does **not** remove or migrate C unit tests that validate NGINX
module internals, C/NGINX glue, or ABI concerns. Those tests remain in C
because the concerns they test are inherently C-language concerns — they depend
on NGINX data structures, NGINX memory pools, NGINX filter chain semantics, or
compiler/toolchain behavior that cannot be meaningfully expressed in Rust.

---

## Concern Categories That Must Remain in C

### 1. NGINX Module Glue and Lifecycle Hooks

**Why C:** NGINX module registration, phase handler installation, filter chain
wiring, and request lifecycle callbacks are expressed entirely through NGINX C
APIs (`ngx_http_module_t`, `ngx_command_t`, `ngx_http_top_header_filter`,
`ngx_http_top_body_filter`). These APIs have no Rust equivalent in this
codebase. Testing them requires direct access to NGINX stub structures
(`ngx_http_request_t`, `ngx_http_core_module`, `ngx_cycle_t`) that are only
available in C.

**Concrete examples:**

| File | What it tests |
|---|---|
| `unit/header_filter_test.c` | NGINX header filter chain entry, `ngx_http_top_header_filter` dispatch, header forwarding state |
| `unit/streaming_impl_test.c` | Streaming filter implementation internals, state machine transitions within the NGINX body filter |
| `unit/streaming_test.c` | Streaming path correctness and state machine under NGINX buffer chain semantics |
| `unit/markdown_filter_runtime_test.c` | Filter runtime state machine, per-request context lifecycle |
| `unit/eligibility_impl_test.c` | Eligibility decision chain implementation, phase-handler-level short-circuit logic |
| `unit/fast_path_test.c` | Fast-path eligibility short-circuit within the NGINX filter entry point |
| `unit/head_request_test.c` | HEAD request handling in the filter, body suppression semantics |
| `unit/passthrough_test.c` | Pass-through (non-conversion) path, `NGX_DECLINED` semantics in the filter chain |
| `unit/decision_log_test.c` | Decision log formatting and reason code emission via `ngx_log_error` |
| `unit/otel_impl_test.c` | OpenTelemetry integration implementation within the NGINX module |
| `unit/protocol_correctness_test.c` | HTTP protocol correctness in filter responses (status, headers, body ordering) |
| `unit/reason_code_test.c` | Reason code string definitions and accessor functions used by `ngx_http_markdown_log_decision()` |
| `integration/nginx_runtime_integration_test.c` | Module load, config apply, and request lifecycle against a real NGINX stub runtime |

---

### 2. Configuration Parsing and Merge Logic

**Why C:** NGINX configuration is parsed and merged through NGINX C APIs
(`ngx_conf_t`, `ngx_command_t`, `ngx_conf_merge_value`, `ngx_conf_set_*_slot`).
The merge semantics — how `http {}`, `server {}`, and `location {}` blocks
inherit and override values — are NGINX-internal behavior. Testing them requires
constructing `ngx_conf_t` and `ngx_http_*_conf_t` structures directly in C.
The dynconf (dynamic configuration) subsystem also operates on NGINX pool
memory and must be tested at the C level.

**Concrete examples:**

| File | What it tests |
|---|---|
| `unit/config_core_impl_test.c` | Core configuration parsing, directive defaults, and initialization |
| `unit/config_handlers_impl_test.c` | Configuration directive handler implementations (`ngx_conf_set_*` callbacks) |
| `unit/config_merge_test.c` | Configuration merge across `location`/`server`/`http` blocks |
| `unit/config_parsing_test.c` | Directive value parsing (sizes, flags, strings, enums) |
| `unit/dynconf_impl_test.c` | Dynamic configuration snapshot construction, apply, and reload logic |
| `unit/effective_conf_test.c` | Effective configuration view construction and snapshot isolation |

---

### 3. Header and Body Filter Internals

**Why C:** The NGINX filter chain operates on `ngx_chain_t` buffer chains,
`ngx_buf_t` structures, and `ngx_http_headers_out_t` / `ngx_http_headers_in_t`
header tables. These are C structures with NGINX-specific memory and lifecycle
semantics. Testing filter behavior — buffer accumulation, chain forwarding,
header manipulation, body transformation — requires direct manipulation of these
structures in C.

**Concrete examples:**

| File | What it tests |
|---|---|
| `unit/body_filter_test.c` | NGINX body filter chain and buffer handling, `ngx_chain_t` accumulation |
| `unit/headers_test.c` | Response header manipulation via `ngx_http_headers_out_t` |
| `unit/conditional_requests_test.c` | Conditional request (ETag/IMS) logic in the C filter, `ngx_http_headers_in_t` field access |
| `unit/auth_cache_control_test.c` | Cache-Control header handling in the auth path, header table traversal |
| `unit/auth_cookie_pattern_test.c` | Auth cookie detection pattern matching against `ngx_http_headers_in_t` |
| `unit/decompression_detect_test.c` | Content-Encoding detection logic from response headers |
| `unit/decompression_logging_test.c` | Decompression event logging through NGINX log infrastructure |
| `unit/error_classification_test.c` | Error category classification logic in the filter |
| `unit/error_impl_test.c` | Error handling implementation paths in the filter |
| `unit/failure_strategies_test.c` | Fail-open / fail-closed strategy dispatch in the filter |
| `unit/input_validation_test.c` | Input validation guards at the filter entry point |
| `unit/threshold_router_test.c` | Threshold-based path router (full-buffer vs incremental) |
| `unit/unsupported_format_fallback_test.c` | Unsupported format fallback behavior in the filter |
| `unit/conversion_impl_base_url_test.c` | Base URL construction in the conversion implementation |
| `unit/accept_parser_test.c` | Accept-header parsing logic in C (NGINX module glue) |

---

### 4. NGINX Stub Compatibility

**Why C:** The C unit test suite runs against a minimal NGINX stub that
provides just enough of the NGINX API surface to exercise the module without a
full NGINX binary. The stub itself is a C artifact. Tests that validate the
stub's compatibility with the real NGINX API — or that serve as the minimal
smoke check that the test harness itself is functional — must remain in C.

**Concrete examples:**

| File | What it tests |
|---|---|
| `unit/template_minimal_test.c` | Minimal test template and harness smoke — verifies the stub compiles and the test runner executes correctly |
| `unit/eligibility_test.c` | Eligibility enum and reason code mapping against stub-provided NGINX types |

---

### 5. ABI and FFI Boundary Validation

**Why C:** The Rust converter is linked into the NGINX module as a C-callable
library. The ABI boundary — the `#[repr(C)]` structs, function signatures, and
memory ownership contracts defined in `markdown_converter.h` — must be
validated from the C side. This includes verifying that the header compiles
cleanly, that struct layouts match expectations, and that the C module correctly
handles all FFI error codes and result fields. These checks require C
compilation and cannot be expressed in Rust without circular dependency.

**Concrete examples:**

| File | What it tests |
|---|---|
| `unit/header_compile_test.c` | Header compilation and ABI boundary smoke — verifies `markdown_converter.h` compiles without errors and that struct sizes/offsets are as expected |
| `helpers/headers_standalone.c` | Standalone header helper for tests that cannot link the full module — provides a minimal header-manipulation surface for ABI boundary validation |

---

### 6. Metrics Internals Tied to C Structures

**Why C:** The metrics subsystem is built on NGINX shared memory zones
(`ngx_shm_zone_t`), NGINX atomic types (`ngx_atomic_t`), and a C struct
(`ngx_http_markdown_metrics_t`) that is laid out in shared memory. The
Prometheus, JSON, and plain-text renderers operate directly on this struct.
Testing metrics correctness — counter increments, snapshot isolation, format
rendering, SHM layout compatibility — requires direct access to these C
structures and NGINX SHM APIs.

**Concrete examples:**

| File | What it tests |
|---|---|
| `unit/metrics_collection_test.c` | Metrics counter increment paths via `NGX_HTTP_MARKDOWN_METRIC_INC` |
| `unit/metrics_decompression_test.c` | Decompression metrics accounting in the metrics struct |
| `unit/metrics_endpoint_test.c` | Metrics endpoint handler, response rendering, content-length correctness |
| `unit/metrics_format_select_test.c` | Metrics format content negotiation in C (JSON vs plain-text vs Prometheus) |
| `unit/metrics_output_test.c` | Metrics output rendering for all three formats |
| `unit/metrics_snapshot_new_fields_test.c` | Metrics snapshot field coverage for new fields — guards against snapshot drift |
| `unit/prometheus_format_test.c` | Prometheus text format rendering in C |
| `unit/prometheus_per_path_test.c` | Per-path Prometheus metric labeling |
| `unit/prometheus_renderer_test.c` | Prometheus renderer correctness |
| `unit/skip_counter_test.c` | Skip counter increment logic |
| `unit/streaming_prometheus_metric_family_test.c` | Streaming-specific Prometheus metric families |

---

### 7. Sanitizer Smoke Checks and Compiler/Toolchain-Sensitive C Behavior

**Why C:** Some behaviors are sensitive to the C compiler, optimization level,
or sanitizer instrumentation. AddressSanitizer (ASan) and UndefinedBehaviorSanitizer
(UBSan) operate at the C/C++ level and cannot be applied to Rust code in the
same way. The `make test-nginx-unit-sanitize-smoke` target compiles the C
module with sanitizers enabled and runs the unit suite under them. Similarly,
the clang smoke target (`make test-nginx-unit-clang-smoke`) validates that the
module compiles cleanly under a different compiler toolchain. These are
inherently C-level concerns.

The build infrastructure for these checks lives in:

| File | What it provides |
|---|---|
| `make/targets.mk` | Defines `make test-nginx-unit`, `make test-nginx-unit-clang-smoke`, and `make test-nginx-unit-sanitize-smoke`; sets compiler flags, sanitizer flags, and toolchain selection |
| `unit/brotli_standalone_test.c` | Brotli decompression standalone smoke — compiler/toolchain-sensitive C behavior, validates the decompression library links and runs correctly under the test toolchain |
| `unit/brotli_function_test.c` | Brotli decompression function correctness — validates decompression output under the current toolchain |
| `unit/gzip_deflate_decompression_test.c` | Gzip/deflate decompression correctness — validates zlib integration under the current toolchain |
| `unit/streaming_decomp_test.c` | Streaming decompression path — validates the streaming decompression state machine under the current toolchain |

---

## Decision Guide

Use this guide when deciding whether a new test belongs in C or in the Rust
E2E harness.

### Tests That Belong in C

A test belongs in C when it validates **NGINX C module behavior** — that is,
behavior that is expressed through NGINX C APIs, NGINX data structures, or
C-language semantics that have no equivalent in the Rust harness.

Ask: does the test require any of the following?

- Direct access to `ngx_http_request_t`, `ngx_http_headers_out_t`,
  `ngx_http_headers_in_t`, `ngx_chain_t`, `ngx_buf_t`, or other NGINX C
  structs
- NGINX pool allocation (`ngx_palloc`, `ngx_pcalloc`) or NGINX memory
  lifecycle semantics
- NGINX filter chain dispatch (`ngx_http_top_header_filter`,
  `ngx_http_top_body_filter`, `ngx_http_next_header_filter`,
  `ngx_http_next_body_filter`)
- NGINX configuration parsing or merge APIs (`ngx_conf_t`, `ngx_command_t`,
  `ngx_conf_merge_*`)
- NGINX shared memory zones (`ngx_shm_zone_t`, `ngx_atomic_t`) for metrics
- The `markdown_converter.h` ABI boundary (struct layout, function signatures,
  error codes)
- Compiler/toolchain-specific behavior (sanitizer instrumentation, clang vs
  gcc differences, optimization-level-sensitive code paths)
- The NGINX stub test harness itself (stub compatibility, harness smoke)

If the answer to any of these is yes, the test belongs in C.

**Examples of C-only concerns:**

- Does the header filter correctly set `r->headers_out.content_type` before
  calling `ngx_http_next_header_filter`?
- Does the config merge correctly apply `NGX_CONF_UNSET` inheritance across
  `http {}` → `server {}` → `location {}` blocks?
- Does the metrics struct layout match the expected field offsets after a
  struct change?
- Does the module compile and link cleanly under clang with `-fsanitize=address`?
- Does the FFI header compile without errors when included in a C translation
  unit?

---

### Tests That May Be Expressed in Rust E2E

A test may be expressed in the Rust E2E harness when it validates
**product/runtime behavior** — that is, observable HTTP behavior that can be
verified through real HTTP requests and responses against a running NGINX
instance, without needing to inspect internal C structures.

Ask: can the test be expressed entirely as:

- An HTTP request (method, URL, headers, body) sent to a running NGINX
- An assertion on the HTTP response (status code, response headers, response
  body)
- Without any knowledge of how NGINX internally processes the request

If the answer is yes, the test is a candidate for the Rust E2E harness.

**Examples of Rust E2E candidates:**

- Does a `GET` request with `Accept: text/markdown` return `200 OK` with
  `Content-Type: text/markdown` and a Markdown body?
- Does a `GET` request with `If-None-Match: <etag>` return `304 Not Modified`
  when the ETag matches?
- Does the metrics endpoint return a valid Prometheus exposition format when
  `Accept: text/plain` is sent?
- Does a request with an auth cookie receive a `Cache-Control: no-store`
  response header?
- Does a `HEAD` request return the same headers as the corresponding `GET`
  with an empty body?

These are the concerns that the 0.6.3 first-batch migration moves into
`tools/e2e-harness/src/scenarios/`.

---

### The Key Distinction

| Concern | Where it belongs | Why |
|---|---|---|
| NGINX filter chain wiring and dispatch | C | Requires NGINX C APIs and stub structures |
| Configuration directive parsing and merge | C | Requires `ngx_conf_t` and NGINX merge APIs |
| Header/body buffer chain manipulation | C | Requires `ngx_chain_t`, `ngx_buf_t`, NGINX pool |
| ABI boundary and FFI struct layout | C | Requires C compilation of `markdown_converter.h` |
| Metrics struct field correctness | C | Requires direct access to `ngx_http_markdown_metrics_t` |
| Sanitizer and toolchain smoke | C | Requires C compiler instrumentation |
| Observable HTTP response behavior | Rust E2E | Expressible as HTTP request/response assertions |
| ETag/conditional request HTTP semantics | Rust E2E | Observable via `If-None-Match` / `304` response |
| Metrics endpoint HTTP format | Rust E2E | Observable via HTTP response body |
| Auth/cache header mutations | Rust E2E | Observable via response headers |
| Accept-header content negotiation | Rust E2E | Observable via response `Content-Type` |

Note that some concerns appear in **both** layers. For example:

- `unit/conditional_requests_test.c` tests the C-level ETag computation and
  IMS comparison logic inside the filter — this is a C concern.
- `tools/e2e-harness/src/scenarios/conditional_requests.rs` tests the
  observable HTTP behavior (does a `304` come back? does the ETag round-trip
  correctly?) — this is a Rust E2E concern.

Both tests are correct and complementary. The C test validates the internal
implementation; the Rust E2E test validates the externally observable contract.
They are not duplicates.

---

## Relationship to the Test Surface Audit

The `docs/project/0.6.3-test-surface-audit.md` classifies every in-scope test
file. All files in `components/nginx-module/tests/unit/` and
`components/nginx-module/tests/integration/` are classified **Keep as C** in
that audit. This document provides the authoritative rationale for that
classification.

The 0.6.3 migration does not remove any C test. It adds a Rust E2E harness
layer for product-level HTTP behavior. The C tests remain unchanged and
continue to be the primary validation surface for NGINX module internals.

---

*This document satisfies Requirements 5.1–5.5 of the 0.6.3 Rust-first test
migration spec. It is the authoritative explanation for why some tests remain
in C during and after the 0.6.3 migration.*
