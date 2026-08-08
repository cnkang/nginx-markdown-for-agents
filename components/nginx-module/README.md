# NGINX Markdown Filter Module

This directory contains the NGINX-facing part of the project. The C
module plugs into the NGINX filter chain and delegates HTML-to-Markdown
conversion to the Rust converter.

The repository `README.md` explains the product. This component holds the
NGINX-specific request, response, and configuration behavior.

## Responsibilities

The module is responsible for:

- deciding whether a response is eligible for Markdown conversion
- buffering and preparing upstream response bodies
- handling content negotiation and response headers
- applying request and configuration policy inside NGINX
- calling the Rust converter through a stable FFI boundary

In practice, this layer decides whether a request stays as HTML, becomes
Markdown, or fails according to configured policy.

## Source Layout

```text
src/
  ngx_http_markdown_filter_module.c   main module entrypoint, shared globals, and internal wiring
  ngx_http_markdown_filter_module.h   module types and function declarations
  markdown_converter.h                generated Rust FFI header

  config/
    ngx_http_markdown_config_impl.h            aggregate include for the focused config helper units
    ngx_http_markdown_config_core_impl.h       configuration lifecycle, markdown_filter resolution, metrics-zone setup
    ngx_http_markdown_config_handlers_impl.h   custom directive parsing and validation helpers
    ngx_http_markdown_config_directives_impl.h directive registry table and inline usage notes

  request flow/
    ngx_http_markdown_request_impl.h    header/body flow and request-phase state machine helpers
    ngx_http_markdown_payload_impl.h    request-body buffering, decompression, and fail-open replay helpers
    ngx_http_markdown_conversion_impl.h base_url construction, FFI conversion, and output shaping helpers
    ngx_http_markdown_lifecycle_impl.h  worker lifecycle and filter registration helpers
    ngx_http_markdown_filter_chain_impl.h filter chain wiring and output buffer coordination
    ngx_http_markdown_module_state_impl.h per-worker/request state structures
    ngx_http_markdown_output_decision_impl.h output path decision helpers
    ngx_http_markdown_zerocopy_buf.h    zero-copy output buffer handling

  streaming/
    ngx_http_markdown_streaming_impl.h          streaming engine integration helpers
    ngx_http_markdown_streaming_decomp_impl.h   streaming gzip/deflate/Brotli decompression
    ngx_http_markdown_stream_commit.c/h         stream commit and terminal-sent coordination
    ngx_http_markdown_stream_error.c/h          streaming error and fail-open handling
    ngx_http_markdown_stream_postcommit.c/h     post-commit streaming accounting
    ngx_http_markdown_stream_replay.c/h         streaming replay buffer
    ngx_http_markdown_stream_state.c/h          streaming state machine
    ngx_http_markdown_inflight_impl.h           in-flight request accounting

  dynconf/
    ngx_http_markdown_dynconf_impl.h      dynconf wiring helpers
    ngx_http_markdown_dynconf_precedence.h dynamic vs static precedence rules
    ngx_http_markdown_dynconf_snapshot.c/h dynconf snapshot management

  diagnostics & metrics/
    ngx_http_markdown_diagnostics.c/h                    diagnostics endpoint
    ngx_http_markdown_diagnostics_accessors_impl.h       diagnostics accessors
    ngx_http_markdown_diagnostics_reason.c               diagnostics reason-code mapping
    ngx_http_markdown_metrics_impl.h                     metrics endpoint implementation helpers
    ngx_http_markdown_metrics_v1_renderer.h              v1 metrics renderer
    ngx_http_markdown_metrics_json_perf_impl.h           JSON perf metrics helpers
    ngx_http_markdown_postcommit_metrics_impl.h          post-commit delivery metrics
    ngx_http_markdown_prometheus_impl.h                  Prometheus exposition helpers
    ngx_http_markdown_decision_log_impl.h                decision logging helpers

  reason codes/
    ngx_http_markdown_reason.c        reason-code constants and classification
    ngx_http_markdown_reason_ffi.c    reason-code FFI translation

  headers & negotiation/
    ngx_http_markdown_headers.c       response header updates
    ngx_http_markdown_headers_impl.h  shared header manipulation helpers
    ngx_http_markdown_header_plan.c/h planned header mutations
    ngx_http_markdown_accept.c        Accept header parsing
    ngx_http_markdown_auth.c          authentication and cache policy handling
    ngx_http_markdown_conditional.c   conditional request support

  buffering & conversion/
    ngx_http_markdown_buffer.c        response buffering
    ngx_http_markdown_decompression.c compressed upstream handling
    ngx_http_markdown_eligibility.c   conversion eligibility checks
    ngx_http_markdown_error.c         failure handling

  FFI boundary/
    ngx_http_markdown_exports.h           exported module entry points
    ngx_http_markdown_ffi_layout_check.h  FFI struct layout validation
```

## Build and Test

```bash
# Run full module unit suite
make -C tests unit

# Run standalone integration harness
make -C tests integration-c

# Run runtime integration suite (or set NGINX_BIN=/absolute/path/to/nginx)
make -C tests integration-nginx

# Run canonical end-to-end validation
make -C tests e2e
```

`make -C tests e2e` delegates to the maintained suite under `tools/e2e/`, which owns the real proxy-chain, chunked, and large-response runtime checks.

For full build and installation steps, use [../../docs/guides/BUILD_INSTRUCTIONS.md](../../docs/guides/BUILD_INSTRUCTIONS.md) and [../../docs/guides/INSTALLATION.md](../../docs/guides/INSTALLATION.md).

For directive semantics and operator-facing behavior, prefer [../../docs/guides/CONFIGURATION.md](../../docs/guides/CONFIGURATION.md) over repeating those details here.
For canonical architecture and repository-layout notes, prefer [../../docs/architecture/SYSTEM_ARCHITECTURE.md](../../docs/architecture/SYSTEM_ARCHITECTURE.md) and [../../docs/architecture/REPOSITORY_STRUCTURE.md](../../docs/architecture/REPOSITORY_STRUCTURE.md).

## Removed: Streaming Threshold Directive

The 0.9.2 release removed the `markdown_stream_threshold` directive and
threshold-based routing. Since 0.9.2 the `markdown_streaming off|auto|force`
directive selects the processing path, and `markdown_limits
streaming_buffer=` controls buffering. `markdown_limits` uses the current
keys, for example `conversion_memory=` for the full-buffer memory ceiling.

Configurations that still set `markdown_stream_threshold` fail `nginx -t`
with `unknown directive` until migrated. See
[../../docs/guides/0.9.2-breaking-changes.md](../../docs/guides/0.9.2-breaking-changes.md)
for the complete removal reference and migration guidance.

For the full directive reference, see [../../docs/guides/CONFIGURATION.md](../../docs/guides/CONFIGURATION.md). For the design rationale and architecture, see [../../docs/architecture/LARGE_RESPONSE_DESIGN.md](../../docs/architecture/LARGE_RESPONSE_DESIGN.md).

## Development Notes

- Follow NGINX coding conventions and memory-management rules.
- Prefer NGINX types and helpers over generic C abstractions in module code.
- Keep behavior-oriented explanations in the docs tree and source-specific notes here.


## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-07 | Kang | 25-directive convergence, unified limits keys, metrics freeze, removed streaming threshold directive, refreshed source layout and document history |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, verified directive accuracy against code, added update tracking section |
