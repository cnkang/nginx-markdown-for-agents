---
name: nginx-markdown-harness-maintenance
description: Route and validate harness maintenance work for nginx-markdown-for-agents. Use when changing AGENTS.md, docs/harness, tools/harness, Makefile, or CI harness wiring. Also use it when you need spec resolution, risk-pack routing, or phased verification commands.
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
  - docs/harness/HARNESS_MAINTENANCE.md
matched risk packs: 1
  - docs-tooling-drift (docs/harness/risk-packs/docs-tooling-drift.md)
    path hits:
      - docs/harness/HARNESS_MAINTENANCE.md
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
2. Use `tools/harness/resolve_spec.py` before broad edits. Triage
   `WARN_NEEDS_AUTHOR_REVIEW` against the user request and current evidence.
   Record a resolved warning and continue. Ask only when missing information
   materially affects correctness or authorized scope; pause only dependent
   work. Missing optional specs do not block an explicit task.
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
   - if the same approach yields no new evidence or progress, change approach
     or escalate with relevant redacted error output and affected surfaces
6. Keep optional local adapters optional. Missing local files must degrade as
   `SKIP_NOT_PRESENT`, not repository failure.
7. If harness behavior changes, synchronize the affected truth surfaces and
   consumers in the same change set. Inspect `AGENTS.md`, `docs/harness/`,
   `tools/harness/`, `Makefile`, and CI; edit only consumers that need changes.
8. Follow explicit user scope over skill workflow defaults. Reviews and plans
   do not authorize writes. If this skill causes a pause, link this file,
   quote the applicable instruction, and explain the unresolved dependency.
9. Finish after applicable checks pass. Repeat checks only for new edits,
   failures, or unresolved risks; retain required production-code gates.

## Definition of Done

- `resolve_spec.py` returns `PASS` for every checked spec or returns an explicitly documented `WARN_*` code with a recorded reason
- matched verification families have been run. Their outcomes are recorded in the evidence directory: `perf/reports/evidence-<version>.json` for perf evidence gate output (see `make perf-evidence-check`) and `docs/project/recent-git-harness-steering-analysis-*.md` for steering-analysis closeout reports
- repeated drift has either converged on the spec or escalated with concrete evidence attached to the escalation record
- escalation records live in the user-local state carrier per `docs/harness/core.md` step 7 (reflection/promotion evidence)
- closeout artifacts use the `harness-remediation` risk pack with stable finding IDs, final status, changed files, and verification evidence
- the repo truth surfaces (`AGENTS.md`, `docs/harness/`, `tools/harness/`, `Makefile`, CI workflow) are updated in the same change set when harness behavior changes

## References

- Verification map:
  [references/verification-map.md](references/verification-map.md)
- Truth surfaces and escalation rules:
  [references/truth-surfaces.md](references/truth-surfaces.md)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-24 | Hermes | Split long Definition of Done bullets into concise statements while keeping all evidence paths and requirements |
| 0.9.2 | 2026-08-08 | Kang | STE-inspired writing-style cleanup (long instruction, passive voice) |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
