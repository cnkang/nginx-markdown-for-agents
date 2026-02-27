# Repository Structure

This repository uses a component-oriented layout.

## Top-level

- `components/nginx-module/` - NGINX C module, module config, and module-specific tests
- `components/rust-converter/` - Rust converter library and FFI crate
- `tests/corpus/` - shared HTML corpus used by converter and E2E tests
- `tools/` - project scripts grouped by domain (`docs`, `corpus`, `e2e`, `ci`, `c-extract`)
- `docs/` - canonical documentation

## NGINX module conventions

- Production code lives in `components/nginx-module/src/`.
- Test assets live in `components/nginx-module/tests/`.
- Test binaries and generated files must go to `components/nginx-module/tests/build/`.

## Rust converter conventions

- Source: `components/rust-converter/src/`
- Tests: `components/rust-converter/tests/`
- Generated C header: `components/rust-converter/include/markdown_converter.h`

## Script conventions

- Do not add new project scripts at repo root.
- Place scripts under `tools/<domain>/` and ensure paths are workspace-root aware.
