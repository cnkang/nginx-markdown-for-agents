# Testing Documentation

This directory maps the project's validation strategy and test-reference documents.

Use it to answer three practical questions: what is covered already, which tests need a real NGINX runtime, and which command is the right starting point for the change you just made.

## Start Here

For most contributors, these are the most useful entrypoints:

```bash
make test
make test-rust
make test-nginx-unit
make test-nginx-integration
make harness-check
```

Use the documents in this directory when you need to understand what is covered, what requires a real `nginx` runtime, and where performance expectations are documented.

## Test Reference Index

| Document | Purpose |
|----------|---------|
| [DIRECTIVE_VALIDATION_TESTS.md](DIRECTIVE_VALIDATION_TESTS.md) | Directive parsing and configuration validation coverage |
| [DECOMPRESSION_TESTS.md](DECOMPRESSION_TESTS.md) | Decompression-related unit, integration, and E2E coverage |
| [INTEGRATION_TESTS.md](INTEGRATION_TESTS.md) | Integration scenarios and expected behavior |
| [E2E_TESTS.md](E2E_TESTS.md) | End-to-end workflows with real NGINX and backend services |
| [PERFORMANCE_BASELINES.md](PERFORMANCE_BASELINES.md) | Performance expectations and comparison guidance |

For repo-owned harness validation and adaptive local `.kiro` checks, use
[../harness/README.md](../harness/README.md) plus `make harness-check`.

## Common Commands

```bash
# Rust converter tests
make test-rust

# Full NGINX module unit suite
make test-nginx-unit

# NGINX module unit smoke tests with clang
make test-nginx-unit-clang-smoke

# NGINX module unit smoke tests with AddressSanitizer/UndefinedBehaviorSanitizer
make test-nginx-unit-sanitize-smoke

# Integration tests
make test-nginx-integration

# Canonical end-to-end tests
make test-e2e

# Use a specific nginx binary when it is not on PATH
NGINX_BIN=/path/to/nginx make test-nginx-integration
NGINX_BIN=/path/to/nginx make test-e2e

# Short fuzz smoke checks (requires nightly + cargo-fuzz)
make test-rust-fuzz-smoke
```

## Terminology

- Standalone or mock tests do not require a system `nginx` binary.
- Integration and E2E tests usually require a real `nginx` runtime and more environment setup.
- The inline integration runner and the canonical `tools/e2e/` suite accept `NGINX_BIN=/absolute/path/to/nginx` when the desired test binary is not on `PATH`.
- The delegated native scripts in `tools/ci/` and `tools/e2e/` also accept `NGINX_BIN=/absolute/path/to/nginx`; when that path points to a reusable runtime install (for example `.../sbin/nginx` beside `.../conf/mime.types`), they reuse it instead of downloading and rebuilding NGINX.
- Performance references are guidance for regression detection, not hard SLAs.
- The CI workflow records non-blocking performance artifacts for `perf_baseline`, including a front-matter-enabled medium sample, and runs a runtime-regression job that self-builds a module-enabled NGINX runtime for delegated `If-Modified-Since`, then reuses that retained binary for the chunked native smoke and large-response checks.
- Nightly fuzzing runs parser, FFI, and security-validator targets from `components/rust-converter/fuzz/`.
- The native NGINX verification scripts share `tools/lib/nginx_markdown_native_build.sh`, which centralizes Rust target detection, header sync, and macOS deployment-target alignment for Rust and NGINX builds.
- A separate non-blocking Darwin/macOS smoke workflow exercises native Rust build, real-nginx IMS validation, and chunked native smoke on GitHub-hosted macOS runners.

As a working rule:

- small parser or converter changes usually start with `make test` or `make test-rust`
- module behavior changes usually need `make test-nginx-unit`
- compiler compatibility checks use `make test-nginx-unit-clang-smoke` and `make test-nginx-unit-sanitize-smoke`
- proxy-chain, header-propagation, and runtime-path changes usually need `make test-nginx-integration` or `make test-e2e`
