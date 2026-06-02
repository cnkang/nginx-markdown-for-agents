# Routing Manifest Summary

The canonical routing source is [routing-manifest.json](routing-manifest.json).
This page is the readable overlay, not the machine-owned truth.

## Verification Families

| Family | Phase | Main commands |
|--------|-------|---------------|
| `harness-sync` | cheap blocker | `make harness-check` |
| `docs-tooling` | cheap blocker | `make docs-check` |
| `harness-security` | focused semantic | `make harness-security-checks` (CWE-190, CWE-22, live-conf, shell-hygiene, const-correctness), `PYTHONPATH=. pytest -q tools/perf/tests` |
| `rust-streaming` | focused semantic | `make test-rust-streaming` |
| `nginx-streaming` | focused semantic | `make test-nginx-unit-streaming` |
| `nginx-protocol` | focused semantic | `make test-nginx-unit`, `make test-nginx-integration` |
| `ffi-boundary` | focused semantic | `make build`, `make test-rust` |
| `observability-metrics` | focused semantic | `make docs-check`, `make release-gates-check` |
| `v070-gates` | focused semantic | `make release-gates-check-070`, `make test-rust`, `make check-headers` |
| `release-governance` | focused semantic | `make release-gates-check`, `make release-gates-check-strict`, `make release-gates-check-055` |
| `runtime-e2e` | umbrella | `make verify-chunked-native-e2e-smoke`, `make verify-streaming-failure-cache-e2e` |
| `release-quality` | umbrella | `make harness-check-full` |
| `coverage-gate` | focused semantic | `make coverage-gate` |
| `release-governance-060` | focused semantic | `make release-gates-check-060` |
| `packaging-e2e` | umbrella | `dpkg-deb --info`, `rpm -qip`, `helm lint` |
| `e2e-harness-rust` | focused semantic | `make test-e2e-rust` |

`runtime-e2e` requires at least one executing runtime target each session.
Plan-only targets (for example `*-plan`) are documentation aids, not evidence.

## Risk Packs

| Pack | Primary surfaces | Supporting surfaces | Doc |
|------|------------------|---------------------|-----|
| `runtime-streaming` | request/response streaming, pending chains, fail-open behavior, replay buffer integrity | observability | [risk-packs/runtime-streaming.md](risk-packs/runtime-streaming.md) |
| `ffi-boundary` | Rust/C ABI, header sync, option and error-code drift | observability | [risk-packs/ffi-boundary.md](risk-packs/ffi-boundary.md) |
| `observability-metrics` | metrics schema, output semantics, release visibility | docs-tooling | [risk-packs/observability-metrics.md](risk-packs/observability-metrics.md) |
| `docs-tooling-drift` | docs, validators, CI path filters, operator commands | observability | [risk-packs/docs-tooling-drift.md](risk-packs/docs-tooling-drift.md) |
| `nginx-protocol-safety` | auth/cache-control, conditional requests, status and header semantics | observability, docs-tooling | [risk-packs/nginx-protocol-safety.md](risk-packs/nginx-protocol-safety.md) |
| `tooling-path-security` | tooling path validation, safe path I/O, subprocess argument safety, shell hygiene, const correctness | docs-tooling, release-governance | [risk-packs/tooling-path-security.md](risk-packs/tooling-path-security.md) |
| `release-governance` | release gates, scope governance, source-build CI | docs-tooling, harness-remediation | [risk-packs/release-governance.md](risk-packs/release-governance.md) |
| `harness-remediation` | harness rules, steering adapters, post-analysis closeout | docs-tooling, observability | [risk-packs/harness-remediation.md](risk-packs/harness-remediation.md) |
| `otel-integration` | OTel tracing, OTel metrics, OTLP export, span attributes | observability, nginx-protocol | [risk-packs/otel-integration.md](risk-packs/otel-integration.md) |
| `packaging-distribution` | APT/YUM repos, Homebrew tap, Helm chart, K8s Ingress | docs-tooling, release-governance | [risk-packs/packaging-distribution.md](risk-packs/packaging-distribution.md) |
| `dynamic-config-hot-reload` | dynamic config parser, reload lifecycle, runtime apply | nginx-protocol, observability, docs-tooling | [risk-packs/dynamic-config-hot-reload.md](risk-packs/dynamic-config-hot-reload.md) |
| `output-safety` | Markdown escaping, link/URL emission, injection prevention | nginx-protocol, docs-tooling | [risk-packs/output-safety.md](risk-packs/output-safety.md) |
| `e2e-migration` | e2e-harness, scenario migration, shell-to-rust parity | docs-tooling, nginx-protocol | [risk-packs/e2e-migration.md](risk-packs/e2e-migration.md) |

## Task Entry Points

| Entry point | Default behavior |
|-------------|------------------|
| `implement-spec` | bind spec, emit short card, route by touched area |
| `fix-regression` | bias toward historical traps and targeted regression tests |
| `fix-static-quality` | keep correctness/safety bar above green checks |
| `sync-docs-or-contract` | run cheap blockers first, then release-quality if risk rises |

## Spec Resolver Priority

| Priority | Source | Semantics |
|----------|--------|-----------|
| 1 (highest) | `agents-baseline` | NGINX Baseline + Rules — cannot be overridden by user-task |
| 2 | `user-task` | Task scope and implementation intent — overrides workflow/guidance |
| 3 | `active-spec-pointer` | Active spec file (if present) |
| 4 | `agents-workflow` | Agent workflow guidance — can be overridden by user-task |
| 5 | `harness-core` | Harness execution loop and status semantics |
| 6 (lowest) | `replay-calibration` | Replay calibration data |

Safety/engineering invariants always win; user-task controls scope and approach.

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
| 0.6.0 | 2026-04-28 | v0.6.0-planning | Added coverage-gate, release-governance-060, packaging-e2e families; otel-integration, packaging-distribution packs |
| 0.6.0 | 2026-05-03 | Codex | Aligned coverage-gate overlay command with machine manifest |
| 0.6.0 | 2026-05-03 | Codex | Added dynamic-config-hot-reload pack and tightened protocol/release routes from two-week branch scan |
| 0.6.1 | 2026-05-06 | Kang | Added output-safety pack (Rule 27) and sync points for Rules 28–31 |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.6.3 | 2026-05-11 | Kang | Added harness-security verification family and tooling-path-security risk pack to route repeated tooling path-safety fixes |
| 0.6.3 | 2026-05-12 | Kang | Added e2e-harness-rust verification family, e2e-migration risk pack, and tools/e2e-harness/** paths to runtime-streaming and nginx-protocol-safety packs |
| 0.6.3 | 2026-05-14 | Kang | Added shell-hygiene (S7682/S1066/Rule 18) and const-correctness detection scripts; extended tooling-path-security pack paths and keywords; updated harness-security family description |
| 0.6.6 | 2026-05-16 | Kang | Added replay buffer keywords (replay buffer, failopen_completed, precommit_error) to runtime-streaming risk pack; updated primary surfaces to include replay buffer integrity; introduced Spec Resolver Priority section (priority-ordered resolution: agents-baseline > user-task > active-spec-pointer > agents-workflow > harness-core > replay-calibration) ensuring safety/engineering invariants always win over user-task scope |
| 0.7.0 | 2026-05-31 | Kiro | Added v070-gates verification family (release-gates-check-070, test-rust, check-headers) to sync with JSON manifest |
