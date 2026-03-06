# NGINX Markdown Filter Module

This directory contains the NGINX-facing part of the project: the C module that plugs into the NGINX filter chain and delegates HTML-to-Markdown conversion to the Rust converter.

If the repository `README.md` explains the product, this component is where the NGINX-specific request, response, and configuration behavior actually lives.

## Responsibilities

The module is responsible for:

- deciding whether a response is eligible for Markdown conversion
- buffering and preparing upstream response bodies
- handling content negotiation and response headers
- applying request and configuration policy inside NGINX
- calling the Rust converter through a stable FFI boundary

In practice, this is the layer that decides whether a request should stay as HTML, become Markdown, or fail according to configured policy.

## Source Layout

```text
src/
  ngx_http_markdown_filter_module.c   main module entrypoint and configuration
  ngx_http_markdown_accept.c          Accept header parsing
  ngx_http_markdown_auth.c            authentication and cache policy handling
  ngx_http_markdown_buffer.c          response buffering
  ngx_http_markdown_conditional.c     conditional request support
  ngx_http_markdown_decompression.c   compressed upstream handling
  ngx_http_markdown_eligibility.c     conversion eligibility checks
  ngx_http_markdown_error.c           failure handling
  ngx_http_markdown_headers.c         response header updates
  markdown_converter.h                generated Rust FFI header
```

## Build and Test

```bash
# Run full module unit suite
make -C tests unit

# Run integration coverage
make -C tests integration-c
make -C tests integration-nginx

# Run end-to-end validation
make -C tests e2e
```

For full build and installation steps, use [../../docs/guides/BUILD_INSTRUCTIONS.md](../../docs/guides/BUILD_INSTRUCTIONS.md) and [../../docs/guides/INSTALLATION.md](../../docs/guides/INSTALLATION.md).

For directive semantics and operator-facing behavior, prefer [../../docs/guides/CONFIGURATION.md](../../docs/guides/CONFIGURATION.md) over repeating those details here.

## Development Notes

- Follow NGINX coding conventions and memory-management rules.
- Prefer NGINX types and helpers over generic C abstractions in module code.
- Keep behavior-oriented explanations in the docs tree and source-specific notes here.
