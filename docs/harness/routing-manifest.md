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
| `nginx-protocol` | focused semantic | `make test-nginx-unit`, `make test-nginx-integration` |
| `ffi-boundary` | focused semantic | `make build`, `make test-rust` |
| `observability-metrics` | focused semantic | `make docs-check`, `make release-gates-check` |
| `release-governance` | focused semantic | `make release-gates-check`, `make release-gates-check-strict` |
| `runtime-e2e` | umbrella | `make verify-chunked-native-e2e-smoke`, `make verify-streaming-failure-cache-e2e` |
| `release-quality` | umbrella | `make harness-check-full` |

`runtime-e2e` requires at least one executing runtime target each session.
Plan-only targets (for example `*-plan`) are documentation aids, not evidence.

## Risk Packs

| Pack | Primary surfaces | Supporting surfaces | Doc |
|------|------------------|---------------------|-----|
| `runtime-streaming` | request/response streaming, pending chains, fail-open behavior | observability | [risk-packs/runtime-streaming.md](risk-packs/runtime-streaming.md) |
| `ffi-boundary` | Rust/C ABI, header sync, option and error-code drift | observability | [risk-packs/ffi-boundary.md](risk-packs/ffi-boundary.md) |
| `observability-metrics` | metrics schema, output semantics, release visibility | docs-tooling | [risk-packs/observability-metrics.md](risk-packs/observability-metrics.md) |
| `docs-tooling-drift` | docs, validators, CI path filters, operator commands | observability | [risk-packs/docs-tooling-drift.md](risk-packs/docs-tooling-drift.md) |
| `nginx-protocol-safety` | auth/cache-control, conditional requests, status and header semantics | observability, docs-tooling | [risk-packs/nginx-protocol-safety.md](risk-packs/nginx-protocol-safety.md) |
| `release-governance` | release gates, scope governance, source-build CI | docs-tooling, harness-remediation | [risk-packs/release-governance.md](risk-packs/release-governance.md) |
| `harness-remediation` | harness rules, steering adapters, post-analysis closeout | docs-tooling, observability | [risk-packs/harness-remediation.md](risk-packs/harness-remediation.md) |

## Task Entry Points

| Entry point | Default behavior |
|-------------|------------------|
| `implement-spec` | bind spec, emit short card, route by touched area |
| `fix-regression` | bias toward historical traps and targeted regression tests |
| `fix-static-quality` | keep correctness/safety bar above green checks |
| `sync-docs-or-contract` | run cheap blockers first, then release-quality if risk rises |

## Adaptive Checks

- Public truth surfaces always check.
- Optional local adapters check only when present.
- Missing optional local adapters should produce `SKIP_NOT_PRESENT`, not
  failure.
- Optional local active-spec pointers may refine spec resolution.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
| 0.5.5 | 2026-04-24 | Codex | Added harness-remediation routing pack |
| 0.5.5 | 2026-04-24 | Codex | Added 60-day protocol and release governance packs |
| 0.5.5 | 2026-04-24 | Codex | Scoped legacy release-gate validation to clones with legacy specs |
