# Truth Surfaces

The repository contract is owned by tracked files, not by local adapters.

## Owner Layers

- Contract map: `AGENTS.md`
- Harness overlays: `docs/harness/`
- Executable checks: `tools/harness/`, `Makefile`, `.github/workflows/ci.yml`

## Optional Local Inputs

- `.kiro/active-spec.json`
- `.kiro/active-spec.txt`
- `.kiro/steering/*.md`

Missing optional inputs must degrade as `SKIP_NOT_PRESENT`.

## Maintenance Rules

1. Update repo-owned truth first.
2. Keep skill content focused on execution choreography.
3. Do not duplicate runtime semantics already documented in architecture docs.
4. Run `make harness-check` for normal harness maintenance.
5. Run `make harness-check-full` when broader docs/release wiring changes.

