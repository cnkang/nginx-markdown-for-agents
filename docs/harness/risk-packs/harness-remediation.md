# Harness Remediation Pack

Use this as the primary pack when broad history analysis, harness-rule updates,
or steering adapter changes are meant to prevent repeated agent mistakes.

## Triggers

- touched files under `AGENTS.md`, `docs/harness/**`, `tools/harness/**`, or
  local Kiro steering adapter files
- generated or updated recent-change analysis reports under `docs/project/`
- keywords like `recent git`, `remediation`, `finding`, `risk pack`,
  `steering`, or `harness rule`

## Common Supporting Packs

- `docs-tooling-drift` when reports, docs, validators, or CI wiring change
- `observability-metrics` when findings mention metrics, reason codes, or
  operator-visible diagnostics
- `runtime-streaming` or `ffi-boundary` when findings come from those surfaces

## Sync Points

- every finding in a remediation report has a stable ID and final status
- P0/P1 findings close as `fixed` unless later review proves them false or
  already covered
- P2/P3 findings are either implemented or explicitly marked
  `intentionally deferred` with a reason
- `AGENTS.md`, `docs/harness/`, `routing-manifest.json`, and local Kiro
  steering adapters keep the same truth-surface priority
- tool-backed rules have a validator, checker, or named verification command

## Minimum Verification

```bash
make harness-check
```

If routing, report structure, or docs-tooling behavior changed, also run:

```bash
make docs-check
python3 -m pytest tools/harness/tests/test_check_harness_sync.py
```

## Canonical References

- `AGENTS.md`
- `docs/harness/core.md`
- `docs/harness/routing-manifest.json`

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.5 | 2026-04-24 | Codex | Added history-analysis remediation routing |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
