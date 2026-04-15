---
name: nginx-markdown-harness-maintenance
description: Route and validate harness maintenance work for nginx-markdown-for-agents. Use when changing AGENTS.md, docs/harness, tools/harness, Makefile, or CI harness wiring, and when you need spec resolution, risk-pack routing, and phased verification commands.
---

# NGINX Markdown Harness Maintenance

Keep harness changes aligned with repo-owned truth surfaces and run the right
verification matrix with minimal guesswork.

## Quick Start

1. Resolve current spec intent:
   `python3 tools/harness/resolve_spec.py --hint "<task summary>"`
2. Route changed files to risk packs:
   `python3 skills/nginx-markdown-harness-maintenance/scripts/harness_route.py --from-git`
3. Run cheap blockers first:
   `make harness-check`
4. Run focused or umbrella checks from the route output.
5. If docs/release wiring changed, run:
   `make harness-check-full`

## Example Output

```text
$ python3 skills/nginx-markdown-harness-maintenance/scripts/harness_route.py --from-git
files: 2
  - docs/harness/README.md
  - docs/guides/HARNESS_MAINTENANCE.md
matched risk packs: 1
  - docs-tooling-drift (docs/harness/risk-packs/docs-tooling-drift.md)
    path hits:
      - docs/guides/HARNESS_MAINTENANCE.md
      - docs/harness/README.md
    keyword hits:
      - docs
verification families: 3
  - [cheap-blocker] docs-tooling
  - [cheap-blocker] harness-sync
  - [umbrella] release-quality
```

## Workflow

1. Treat `AGENTS.md` and `docs/harness/` as canonical contract surfaces.
2. Use `tools/harness/resolve_spec.py` before broad edits. If status is
   `WARN_NEEDS_AUTHOR_REVIEW`, stop and explain ambiguity before continuing.
3. Use `python3 skills/nginx-markdown-harness-maintenance/scripts/harness_route.py --from-git`
   to map changed files/hints to risk packs and verification families from
   `docs/harness/routing-manifest.json`.
4. Build a phased matrix:
   - `cheap-blocker` first
   - then `focused-semantic`
   - then `umbrella` only when needed
5. On first drift trigger:
   - identify minimal changed files related to the failing family
   - rerun only affected verification family commands
   - run `make harness-check` before broad retries
   - if drift repeats, escalate with full error output and affected surfaces
6. Keep optional local adapters optional. Missing local files must degrade as
   `SKIP_NOT_PRESENT`, not repository failure.
7. If harness behavior changes, update repo truth in the same change set:
   `AGENTS.md`, `docs/harness/`, `tools/harness/`, `Makefile`, CI workflow.

## Definition of Done

- `resolve_spec.py` returns `PASS` or explicitly documented `WARN_*`
- matched verification families have been run and outcomes recorded
- repeated drift has either converged or been escalated with concrete evidence
- repo truth surfaces are updated when harness behavior changed

## References

- Verification map:
  [references/verification-map.md](references/verification-map.md)
- Truth surfaces and escalation rules:
  [references/truth-surfaces.md](references/truth-surfaces.md)
