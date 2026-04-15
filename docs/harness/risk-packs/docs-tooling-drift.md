# Docs Tooling Drift Pack

Use this as the primary pack when docs, validators, CI filters, or contract
summaries change together.

## Triggers

- touched files under `docs/**`, `tools/docs/**`, `tools/release/**`
- touched `AGENTS.md`, `Makefile`, or `.github/workflows/**`
- keywords like `validator`, `path filter`, `installation`, `release gate`

## Common Supporting Packs

- `observability-metrics` when docs or validators mention operator-facing metrics

## Sync Points

- `AGENTS.md` vs `docs/harness/` map and contract references
- docs vs validator scope and naming
- CI path filters vs new truth surfaces
- operator commands vs actual output format requirements

## Minimum Verification

```bash
make harness-check
make docs-check
make harness-check-full
```

## Canonical References

- [../../DOCUMENTATION_DUPLICATION_POLICY.md](../../DOCUMENTATION_DUPLICATION_POLICY.md)
- [../../README.md](../../README.md)
- [../../../AGENTS.md](../../../AGENTS.md)
