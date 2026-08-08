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

The source tree changes with the frozen C/Rust boundary and the release
contract. Use the canonical [repository structure](../../docs/architecture/REPOSITORY_STRUCTURE.md)
for the current file inventory instead of maintaining a second, incomplete
list in this component README.

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

The `markdown_stream_threshold` directive and threshold-based routing were
removed in 0.9.2. Since 0.9.2 the processing path is selected solely by the
`markdown_streaming off|auto|force` directive, and buffering is controlled by
`markdown_limits streaming_buffer=`. `markdown_limits` uses the current keys,
for example `conversion_memory=` for the full-buffer memory ceiling.

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
