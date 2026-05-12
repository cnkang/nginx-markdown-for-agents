# Release Notes — 0.6.3

## Migration Summary

Version 0.6.3 introduces the Rust-first E2E test harness (`tools/e2e-harness/`)
and migrates five product-level HTTP E2E scenarios from shell scripts to native
Rust scenario modules. This improves assertion diagnostics, enables structured
JSON reporting, and provides a foundation for future E2E scenario development.

## New Rust E2E Harness Entry Point

The `e2e-harness` binary is built from `tools/e2e-harness/` and supports:

- `e2e-harness scenario <name>` — run a single migrated scenario
- `e2e-harness suite --profile smoke` — run all migrated scenarios
- `e2e-harness --list` — list registered scenarios
- `--nginx-bin <path>` — reuse a prebuilt module-enabled NGINX binary
- `--keep-artifacts` — retain artifacts on success
- `--json-report <path>` — write structured JSON report

Migrated scenarios:

| Scenario | Module | Shell source |
|----------|--------|-------------|
| `accept-negotiation` | `src/scenarios/accept_negotiation.rs` | `verify_accept_negotiation_e2e.sh` |
| `metrics-endpoint` | `src/scenarios/metrics_endpoint.rs` | `verify_metrics_endpoint_e2e.sh` |
| `conditional-requests` | `src/scenarios/conditional_requests.rs` | `verify_conditional_requests_e2e.sh` |
| `auth-cache` | `src/scenarios/auth_cache.rs` | `verify_auth_cache_e2e.sh` |
| `status-codes` | `src/scenarios/status_codes.rs` | `verify_status_codes_e2e.sh` |

## New `test-e2e-rust` Makefile Target

```bash
make test-e2e-rust
```

Builds the `e2e-harness` binary and runs all migrated scenarios with the smoke
profile. This is separate from `make test-rust` (which runs only the Rust
converter test suite).

## Python E2E Files

The Python E2E spec files under `components/nginx-module/tests/e2e/` are
**retained** in 0.6.3. They are classified as Remove in the test surface audit
but removal is deferred until the Rust harness covers streaming scenarios that
currently rely on shell paths. No new Python pytest files may be added under
that directory (AGENTS.md Rule 37).

## C Test Boundary Clarification

C unit tests under `components/nginx-module/tests/unit/` are unchanged and
remain the canonical path for NGINX module internals, C/NGINX glue, ABI
boundaries, and metrics structures. The Rust harness does not replace C tests.

## Harness Rules Update

AGENTS.md Rule 37 (Rust-first E2E test runner) has been added, mandating that
new product-level HTTP E2E scenarios must be implemented in `tools/e2e-harness/`.
The routing manifest includes a new `e2e-harness-rust` verification family and
an `e2e-migration` risk pack.

## 0.6.3 Non-Goals

1. Does not remove C unit tests or integration tests
2. Does not rewrite the NGINX module in Rust
3. Does not eliminate Python tooling (property tests, release gates, harness checkers)
4. Does not break any public command (`make test-e2e`, `make test-rust`, `make test-nginx-unit`)
