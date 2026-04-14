---
name: nginx-harness-maintenance
description: Route and validate harness maintenance work for nginx-markdown-for-agents. Use when changing AGENTS.md, docs/harness, tools/harness, Makefile, or CI harness wiring, and when you need spec resolution, risk-pack routing, and phased verification commands.
---

# NGINX Harness Maintenance

Keep harness changes aligned with repo-owned truth surfaces and run the right
verification matrix with minimal guesswork.

## Quick Start

1. Resolve current spec intent:
   `python3 tools/harness/resolve_spec.py --hint "<task summary>"`
2. Route changed files to risk packs:
   `python3 skills/nginx-harness-maintenance/scripts/harness_route.py --from-git`
3. Run cheap blockers first:
   `make harness-check`
4. Run focused or umbrella checks from the route output.
5. If docs/release wiring changed, run:
   `make harness-check-full`

## Workflow

1. Treat `AGENTS.md` and `docs/harness/` as canonical contract surfaces.
2. Use `tools/harness/resolve_spec.py` before broad edits. If status is
   `WARN_NEEDS_AUTHOR_REVIEW`, stop and explain ambiguity before continuing.
3. Use `scripts/harness_route.py` to map changed files/hints to risk packs and
   verification families from `docs/harness/routing-manifest.json`.
4. Build a phased matrix:
   - `cheap-blocker` first
   - then `focused-semantic`
   - then `umbrella` only when needed
5. On first drift trigger, shrink scope and retry once. If drift repeats,
   escalate instead of looping.
6. Keep optional local adapters optional. Missing local files must degrade as
   `SKIP_NOT_PRESENT`, not repository failure.
7. If harness behavior changes, update repo truth in the same change set:
   `AGENTS.md`, `docs/harness/`, `tools/harness/`, `Makefile`, CI workflow.

## References

- Verification map:
  [references/verification-map.md](references/verification-map.md)
- Truth surfaces and escalation rules:
  [references/truth-surfaces.md](references/truth-surfaces.md)

