# Docs Tooling Drift Pack

Use this as the primary pack when docs, validators, CI filters, or contract
summaries change together.

## Triggers

- touched files under `docs/**`, `tools/docs/**`, `tools/release/**`
- touched `tools/sonar/**` or `.sonarcloud.properties`
- touched `AGENTS.md`, `Makefile`, or `.github/workflows/**`
- keywords like `validator`, `path filter`, `installation`, `release gate`,
  `sonar`, `compile_commands`

## Common Supporting Packs

- `observability-metrics` when docs or validators mention operator-facing metrics

## Sync Points

- `AGENTS.md` vs `docs/harness/` map and contract references
- docs vs validator scope and naming
- CI path filters vs new truth surfaces
- operator commands vs actual output format requirements
- cross-script CLI contract consistency (flag/env/positional semantics)
- cross-script invocation portability (no executable-bit assumptions in CI)
- shell helper functions use explicit success returns when intentionally
  producing no output, so static analysis and callers see deterministic status

## Minimum Verification

```bash
make harness-check
make docs-check
make harness-check-full
make coverage-c
make coverage-sonar-xml
```

## Canonical References

- [../../DOCUMENTATION_DUPLICATION_POLICY.md](../../DOCUMENTATION_DUPLICATION_POLICY.md)
- [../../README.md](../../README.md)
- [../../../AGENTS.md](../../../AGENTS.md)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
| 0.5.5 | 2026-04-25 | Codex | Added shell helper explicit-return drift guardrail |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
