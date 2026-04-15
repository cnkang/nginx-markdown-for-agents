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
