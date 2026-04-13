# Routing Manifest Summary

The canonical routing source is [routing-manifest.json](routing-manifest.json).
This page is the readable overlay, not the machine-owned truth.

## Verification Families

| Family | Phase | Main commands |
|--------|-------|---------------|
| `harness-sync` | cheap blocker | `make harness-check` |
| `docs-tooling` | cheap blocker | `make docs-check` |
| `rust-streaming` | focused semantic | `make test-rust-streaming` |
| `nginx-streaming` | focused semantic | `make test-nginx-unit-streaming` |
| `ffi-boundary` | focused semantic | `make build`, `make test-rust` |
| `observability-metrics` | focused semantic | `make docs-check`, `make release-gates-check` |
| `runtime-e2e` | umbrella | `make verify-chunked-native-e2e-smoke`, `make verify-streaming-failure-cache-e2e-plan` |
| `release-quality` | umbrella | `make harness-check-full` |

## Risk Packs

| Pack | Primary surfaces | Supporting surfaces | Doc |
|------|------------------|---------------------|-----|
| `runtime-streaming` | request/response streaming, pending chains, fail-open behavior | observability | [risk-packs/runtime-streaming.md](risk-packs/runtime-streaming.md) |
| `ffi-boundary` | Rust/C ABI, header sync, option and error-code drift | observability | [risk-packs/ffi-boundary.md](risk-packs/ffi-boundary.md) |
| `observability-metrics` | metrics schema, output semantics, release visibility | docs-tooling | [risk-packs/observability-metrics.md](risk-packs/observability-metrics.md) |
| `docs-tooling-drift` | docs, validators, CI path filters, operator commands | observability | [risk-packs/docs-tooling-drift.md](risk-packs/docs-tooling-drift.md) |

## Task Entry Points

| Entry point | Default behavior |
|-------------|------------------|
| `implement-spec` | bind spec, emit short card, route by touched area |
| `fix-regression` | bias toward historical traps and targeted regression tests |
| `fix-static-quality` | keep correctness/safety bar above green checks |
| `sync-docs-or-contract` | run cheap blockers first, then release-quality if risk rises |

## Adaptive Checks

- Public truth surfaces always check.
- Optional local `.kiro/` adapters check only when present.
- Missing `.kiro/` should produce `SKIP_NOT_PRESENT`, not failure.
- Optional local active-spec pointers may refine spec resolution:
  - `.kiro/active-spec.json`
  - `.kiro/active-spec.txt`
