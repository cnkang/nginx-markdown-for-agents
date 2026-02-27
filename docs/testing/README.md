# Testing Documentation

This directory contains test strategy and validation reference documents for the project.

## Contents

- `DIRECTIVE_VALIDATION_TESTS.md` - directive parsing and configuration validation coverage
- `DECOMPRESSION_TESTS.md` - decompression-specific unit/integration/E2E coverage
- `INTEGRATION_TESTS.md` - integration test scenarios and expectations
- `E2E_TESTS.md` - end-to-end testing guidance and workflows
- `PERFORMANCE_BASELINES.md` - performance baseline definitions and comparison guidance

## Running Tests

### Rust Converter Tests

```bash
cd components/rust-converter
cargo test --all
```

### NGINX Module Unit Tests

```bash
make -C components/nginx-module/tests unit
```

Run a specific unit test:

```bash
make -C components/nginx-module/tests unit-eligibility
```

### Integration and E2E

```bash
make -C components/nginx-module/tests integration-c
make -C components/nginx-module/tests integration-nginx
make -C components/nginx-module/tests e2e
```

### Root-Level Smoke Test

```bash
make test
```

## Terminology

- **Module** means the NGINX Markdown filter module (NGINX C component).
- **Rust converter** means the Rust HTML-to-Markdown library and FFI layer.
- **Standalone/mock tests** means local `components/nginx-module/tests` unit tests that do not require a system `nginx` binary.
- **Integration/E2E tests** generally require a real `nginx` runtime and additional environment setup.
