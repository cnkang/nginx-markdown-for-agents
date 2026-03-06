# Repository Structure

This repository uses a component-oriented layout. The directory structure is designed to mirror the runtime architecture:

- the NGINX-facing module lives under `components/nginx-module/`
- the conversion engine lives under `components/rust-converter/`
- shared validation assets and project tooling live beside them

Use this document as a code-structure map, not just a directory listing.

## Top-Level Layout

| Path | Purpose |
|------|---------|
| `components/nginx-module/` | NGINX module source, module-specific tests, and NGINX integration artifacts |
| `components/rust-converter/` | Rust HTML-to-Markdown engine, FFI layer, examples, and Rust-side tests |
| `docs/` | Maintained user, operator, testing, architecture, and project documentation |
| `examples/nginx-configs/` | Copy-paste-oriented NGINX configuration examples |
| `tests/corpus/` | Shared HTML corpus used by converter and end-to-end validation |
| `tools/` | Project scripts grouped by purpose such as docs, CI, corpus, packaging, and E2E verification |
| `Makefile` | Top-level build and test entrypoints |

## Runtime Code Map

### `components/nginx-module/`

This is the NGINX-facing side of the system. It owns request-path orchestration, eligibility checks, buffering, header behavior, and metrics exposure.

Key areas:

| Path | Responsibility |
|------|----------------|
| `src/ngx_http_markdown_filter_module.c` | Main module entrypoint, directive registration, filter wiring, and request-path orchestration |
| `src/ngx_http_markdown_filter_module.h` | Shared module types, constants, and interfaces |
| `src/ngx_http_markdown_accept.c` | `Accept` header parsing and media-type negotiation helpers |
| `src/ngx_http_markdown_auth.c` | Auth detection and cache-policy handling for authenticated requests |
| `src/ngx_http_markdown_buffer.c` | Response buffering helpers and size-management logic |
| `src/ngx_http_markdown_conditional.c` | Conditional request support and Markdown-variant cache validation |
| `src/ngx_http_markdown_decompression.c` | Upstream compression detection and decompression flow |
| `src/ngx_http_markdown_eligibility.c` | Request/response eligibility checks and bypass logic |
| `src/ngx_http_markdown_error.c` | Error classification and failure-category mapping |
| `src/ngx_http_markdown_headers.c` | Response-header update logic for Markdown responses |
| `src/ngx_http_markdown_headers_impl.h` | Shared header-manipulation implementation used by production and standalone tests |
| `src/markdown_converter.h` | Generated Rust FFI header copied into the module source tree |

Supporting areas:

| Path | Responsibility |
|------|----------------|
| `config` | NGINX build integration for compiling/linking the module |
| `tests/unit/` | Standalone C unit tests for module behaviors |
| `tests/integration/` | Integration tests that exercise runtime behavior with an NGINX environment |
| `tests/e2e/` | End-to-end tests using a real NGINX instance and backend service |
| `tests/helpers/` | Small helper sources used by standalone test builds |
| `tests/include/` | Shared test harness headers |
| `tests/make/` | Test build target definitions |

Start here if you are changing:

- directive behavior
- response eligibility or passthrough logic
- header handling
- metrics endpoint behavior
- integration with the Rust converter

### `components/rust-converter/`

This is the conversion engine. It owns HTML parsing, sanitization, Markdown generation, metadata extraction, and the C-compatible FFI contract exposed to the NGINX module.

Key areas:

| Path | Responsibility |
|------|----------------|
| `src/lib.rs` | Crate entrypoint and public conversion surface |
| `src/ffi.rs` | C-compatible FFI boundary used by the NGINX module |
| `src/parser.rs` | HTML parsing and DOM construction |
| `src/converter.rs` | Markdown rendering and output normalization |
| `src/security.rs` | Sanitization, input hardening, and URL-safety rules |
| `src/metadata.rs` | Metadata extraction used by front matter and related features |
| `src/token_estimator.rs` | Token estimation support for agent-facing metadata |
| `src/etag_generator.rs` | Markdown-variant ETag generation |
| `src/charset.rs` | Charset detection and decoding support |
| `src/error.rs` | Internal error types and mapping helpers |

Supporting areas:

| Path | Responsibility |
|------|----------------|
| `tests/` | Rust integration and FFI tests |
| `examples/` | Small runnable examples and benchmark-oriented demos |
| `include/markdown_converter.h` | Generated public header for C integration |
| `cbindgen.toml` | Header-generation configuration |
| `proptest-regressions/` | Saved regression cases from property tests |

Start here if you are changing:

- HTML parsing behavior
- Markdown rendering
- sanitization rules
- metadata extraction
- FFI result and error contracts

## Validation Assets

### `tests/corpus/`

The corpus provides shared HTML fixtures across multiple validation layers. It helps keep parser, converter, and end-to-end checks grounded in the same kinds of inputs.

Subgroups include:

- `simple/` for basic semantic elements
- `complex/` for richer document structure
- `edge-cases/` for unusual but valid-or-near-valid inputs
- `malformed/` for broken or irregular HTML
- `encoding/` for charset and text-handling scenarios

## Tooling Layout

### `tools/`

Scripts are grouped by domain rather than scattered at repo root.

| Path | Purpose |
|------|---------|
| `tools/docs/` | Documentation validation and duplicate-doc checks |
| `tools/ci/` | CI-specific helpers such as license and environment checks |
| `tools/e2e/` | Additional native E2E verification scripts |
| `tools/corpus/` | Corpus validation and conversion tooling |
| `tools/build_release/` | Release packaging support and packaging Dockerfiles |
| `tools/c-extract/` | Small developer utility for extracting C functions |
| `tools/install.sh` | Installer for published module artifacts |
| `tools/run-checks.sh` | Repository-level verification helper |

## Generated and Build Artifacts

Some directories visible in the workspace are build outputs rather than maintained source.

Common examples:

- `components/nginx-module/tests/build/`
- `components/rust-converter/target/`

These directories are useful when running tests locally, but they are not the source of truth for behavior or repository structure.

## Practical Reading Order

If you are new to the codebase:

1. Read [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) for the runtime model.
2. Read this file to map that model onto the repository.
3. Read the component README that matches your area:
   [../../components/nginx-module/README.md](../../components/nginx-module/README.md)
   [../../components/rust-converter/README.md](../../components/rust-converter/README.md)
4. Move to the relevant guide, feature note, or ADR from there.

## Conventions

- Do not add new top-level project scripts when an existing `tools/<domain>/` location fits.
- Keep user- and operator-facing guidance in `docs/`, not in source-tree README files alone.
- Keep generated headers and build outputs clearly separated from hand-maintained source.
