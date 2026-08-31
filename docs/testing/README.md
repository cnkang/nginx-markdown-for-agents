# Testing Documentation

This directory maps the project's validation strategy and test-reference documents.

Use it to answer three practical questions. The answers cover the test surface, runtime needs, and starting commands for your change.

## Start Here

For most contributors, these are the most useful entrypoints:

```bash
make test
make test-rust
make test-nginx-unit
make test-nginx-integration
make harness-check
```

Use the documents in this directory to understand what the tests cover and what requires a real `nginx` runtime. They also show where performance expectations appear.

## Test Reference Index

| Document | Purpose |
|----------|---------|
| [DIRECTIVE_VALIDATION_TESTS.md](DIRECTIVE_VALIDATION_TESTS.md) | Directive parsing and configuration validation coverage |
| [DECOMPRESSION_TESTS.md](DECOMPRESSION_TESTS.md) | Decompression-related unit, integration, and E2E coverage |
| [INTEGRATION_TESTS.md](INTEGRATION_TESTS.md) | Integration scenarios and expected behavior |
| [E2E_TESTS.md](E2E_TESTS.md) | End-to-end workflows with real NGINX and backend services |
| [C_TEST_BOUNDARY.md](C_TEST_BOUNDARY.md) | C test scope and boundary (what the C unit suite covers) |
| [benchmark-corpus.md](benchmark-corpus.md) | Benchmark corpus used by the performance harness |
| [PERFORMANCE_GATE.md](PERFORMANCE_GATE.md) | Performance gate workflow, CI wiring, and reproduction |
| [PERFORMANCE_METRICS.md](PERFORMANCE_METRICS.md) | Measurement schema companion for `perf/metrics-schema.json` |
| [PERFORMANCE_THRESHOLDS.md](PERFORMANCE_THRESHOLDS.md) | Threshold policy (values live in `perf/thresholds.json`) |
| [PERFORMANCE_BASELINES.md](PERFORMANCE_BASELINES.md) | Baseline/evidence/provenance and comparison guidance |

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

# C module coverage (E2E + lcov)
make coverage-c

# Rust converter coverage
make coverage-rust

# Full coverage pipeline (C + Rust + SonarQube XML)
make coverage-all

# Use a specific nginx binary when it is not on PATH
NGINX_BIN=/path/to/nginx make test-nginx-integration
NGINX_BIN=/path/to/nginx make test-e2e

# Short fuzz smoke checks (requires nightly + cargo-fuzz)
make test-rust-fuzz-smoke
```

## Coverage Standards

- **Minimum**: 80% aggregate line coverage for both the C module and the Rust converter
- **Target**: 90% aggregate line coverage
- **Critical paths** (auth, error handling, FFI boundary, conditional requests): 90% line coverage for new code
- The project collects coverage via `make coverage-c` (C module E2E + gcov/lcov) and `make coverage-rust` (Rust `cargo llvm-cov`)
- `tools/ci/coverage_gate.py` blocks below 80% aggregate line coverage and below 90% critical-path line coverage
- The lcov report is always produced regardless of coverage level, ensuring SonarCloud trends remain visible

## Terminology

- Standalone or mock tests do not require a system `nginx` binary.
- Integration and E2E tests usually require a real `nginx` runtime and more environment setup.
- The inline integration runner and the canonical `tools/e2e/` suite accept `NGINX_BIN=/absolute/path/to/nginx` when the desired test binary is not on `PATH`.
- The delegated native scripts in `tools/ci/` and `tools/e2e/` also accept `NGINX_BIN=/absolute/path/to/nginx`. When that path points to a reusable runtime install (for example `.../sbin/nginx` beside `.../conf/mime.types`), they reuse it. They skip downloading and rebuilding NGINX.
- Performance references are guidance for regression detection, not hard SLAs.
- The CI workflow records non-blocking performance artifacts for `perf_baseline`, including a front-matter-enabled medium sample. It also runs a runtime-regression job that self-builds a module-enabled NGINX runtime for delegated `If-Modified-Since`. The job reuses that retained binary for the chunked native smoke and large-response checks.
- Nightly fuzzing runs parser, FFI, and security-validator targets from `components/rust-converter/fuzz/`.
- The native NGINX verification scripts share `tools/lib/nginx_markdown_native_build.sh`. It centralizes Rust target detection, header sync, and macOS deployment-target alignment. It also centralizes HTTP readiness polling, CLI flag validation, and HTTP assertion helpers for E2E scripts.
- A separate non-blocking Darwin/macOS smoke workflow exercises native Rust build, real-nginx IMS validation, and chunked native smoke on GitHub-hosted macOS runners.

As a working rule:

- small parser or converter changes usually start with `make test` or `make test-rust`
- module behavior changes usually need `make test-nginx-unit`
- compiler compatibility checks use `make test-nginx-unit-clang-smoke` and `make test-nginx-unit-sanitize-smoke`
- proxy-chain, header-propagation, and runtime-path changes usually need `make test-nginx-integration` or `make test-e2e`

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.6.0 | 2026-05-02 | v060-prod | Updated shared helper description to include new E2E helpers |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
