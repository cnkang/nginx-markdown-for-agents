# Risk Packs

Risk packs are thin overlays for high-risk change surfaces. They do not restate
runtime semantics from canonical docs. They answer four practical questions:

- when should this pack become primary
- what supporting packs usually co-occur
- which sync points are easy to miss
- which focused verification commands should run before broader checks

## Initial Packs

| Pack | Use when | Canonical docs |
|------|----------|----------------|
| [runtime-streaming.md](runtime-streaming.md) | streaming, fail-open, deferred output, pending chains | `AGENTS.md`, `docs/architecture/REQUEST_LIFECYCLE.md` |
| [ffi-boundary.md](ffi-boundary.md) | Rust/C headers, ABI, FFI error codes, option structs | `AGENTS.md`, `docs/architecture/REPOSITORY_STRUCTURE.md` |
| [observability-metrics.md](observability-metrics.md) | metrics, reason codes, release visibility, dashboards | `AGENTS.md`, `docs/guides/prometheus-metrics.md` |
| [docs-tooling-drift.md](docs-tooling-drift.md) | docs, validators, release-gate commands, CI path filters | `AGENTS.md`, `docs/DOCUMENTATION_DUPLICATION_POLICY.md` |
| [nginx-protocol-safety.md](nginx-protocol-safety.md) | auth/cache-control, conditional requests, statuses, headers | `AGENTS.md`, `docs/architecture/REQUEST_LIFECYCLE.md` |
| [release-governance.md](release-governance.md) | release gates, source-build CI, scope governance, matrix tooling | `AGENTS.md`, `docs/project/release-gates/README.md` |
| [harness-remediation.md](harness-remediation.md) | recent Git analysis, harness rules, steering adapters, remediation closeout | `AGENTS.md`, `docs/harness/core.md` |
| [otel-integration.md](otel-integration.md) | OTel tracing, OTel metrics, OTLP export, span attributes | `AGENTS.md`, `docs/features/otel-tracing.md` |
| [packaging-distribution.md](packaging-distribution.md) | APT/YUM repos, Homebrew tap, Helm chart, K8s Ingress | `AGENTS.md`, `docs/guides/INSTALLATION.md` |
| [dynamic-config-hot-reload.md](dynamic-config-hot-reload.md) | dynamic config parsing, reload retry, runtime apply | `AGENTS.md`, `docs/guides/CONFIGURATION.md` |

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
| 0.5.5 | 2026-04-24 | Codex | Added harness-remediation pack |
| 0.5.5 | 2026-04-24 | Codex | Added protocol safety and release governance packs |
| 0.6.0 | 2026-04-28 | v0.6.0-planning | Added otel-integration and packaging-distribution packs |
| 0.6.0 | 2026-05-03 | Codex | Added dynamic-config-hot-reload pack from two-week branch scan |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
