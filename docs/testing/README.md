# Testing Documentation

This directory maps the project's validation strategy and test-reference documents.

Use it to answer three practical questions: what is covered already, which tests need a real NGINX runtime, and which command is the right starting point for the change you just made.

## Start Here

For most contributors, these are the most useful entrypoints:

```bash
make test
make test-rust
make test-nginx-unit
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

## Common Commands

```bash
# Rust converter tests
make test-rust

# Full NGINX module unit suite
make test-nginx-unit

# Integration tests
make test-nginx-integration

# End-to-end tests
make test-e2e
```

## Terminology

- Standalone or mock tests do not require a system `nginx` binary.
- Integration and E2E tests usually require a real `nginx` runtime and more environment setup.
- Performance references are guidance for regression detection, not hard SLAs.

As a working rule:

- small parser or converter changes usually start with `make test` or `make test-rust`
- module behavior changes usually need `make test-nginx-unit`
- proxy-chain, header-propagation, and runtime-path changes usually need integration or E2E coverage
