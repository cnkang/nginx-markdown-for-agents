---
domain: harness-routing
rules: [36]
paths:
  - "docs/harness/**"
  - "tools/harness/**"
---

## Harness Routing Coverage

### 36. Harness routing coverage for tooling security surfaces
Historical issues: repeated branch fixes around CWE-22/S2083 in tooling paths
that were not routed to focused security verification families.

Required:
- When `main...HEAD` shows repeated fixes in tooling path-safety classes
  (path traversal, write-path validation, command-injection guardrails),
  update `docs/harness/routing-manifest.json` in the same changeset so touched
  path patterns map to at least one focused security verification family.
- Security detection commands must remain callable as focused routing families
  (for example `harness-security`), not only as transitive umbrella checks.
- If a new tooling area accepts CLI path inputs, add both path patterns and
  keywords to routing-manifest so `harness_route.py` can match by either
  changed files or task hints.
- Every routing-manifest update must be mirrored in
  `docs/harness/routing-manifest.md` and the corresponding risk-pack docs in
  the same changeset.

Verification:
- `python3 skills/nginx-markdown-harness-maintenance/scripts/harness_route.py --from-git --base main`
- `make harness-security-checks`
- `PYTHONPATH=. pytest -q tools/perf/tests`
