# Recent Git Harness Steering Analysis

Analysis window: initially 2026-03-25 through 2026-04-24, then extended to
2026-02-24 through 2026-04-24 for the 60-day reassessment.  A full-history
scan confirmed that the earliest reachable local/remote commit is 2026-02-27,
so the 60-day reassessment covers the complete repository history available in
this clone.

Remote state was refreshed with `git fetch --all --prune --tags` before
analysis. Local and remote refs were enumerated from `refs/heads` and
`refs/remotes`, excluding `origin/HEAD`, and commits were deduplicated by SHA.
`tools/harness/resolve_spec.py --hint "recent git harness steering analysis and
remediation"` returned `WARN_NEEDS_AUTHOR_REVIEW`; this work is therefore
treated as cross-cutting harness maintenance rather than a single bound spec.

## Phase 1 Analysis

The scan covered 40 local/remote refs and 953 unique commits.

| Category | Commit hits |
|----------|-------------|
| docs/packaging | 400 |
| streaming/runtime | 365 |
| ci/tooling/sonar | 345 |
| metrics/observability | 218 |
| benchmark/evidence | 197 |
| harness/steering/spec | 167 |
| security/path | 156 |
| ffi/boundary | 112 |
| other | 58 |

Hot files by recent commit frequency:

| File | Hits | Signal |
|------|------|--------|
| `AGENTS.md` | 91 | Repeated rule hardening and review-driven guardrails |
| `components/nginx-module/src/ngx_http_markdown_streaming_impl.h` | 90 | Streaming runtime remains the highest-risk implementation surface |
| `docs/guides/INSTALLATION.md` | 71 | Packaging/docs drift stayed active |
| `tools/docs/check_packaging_consistency.py` | 57 | Tool-backed documentation checks changed frequently |
| `components/nginx-module/src/ngx_http_markdown_metrics_impl.h` | 49 | Metrics schema and runtime write paths changed often |
| `.github/workflows/ci.yml` | 30 | CI wiring and path filters remained volatile |

Representative high-risk commits inspected:

| Commit | Date | Surface | Detail checked |
|--------|------|---------|----------------|
| `c040dc282a` | 2026-04-21 | docs/runtime/auth | Large docs standardization also touched C auth/conditional paths and Rust converter behavior; rule updates must not rely on docs-only assumptions. |
| `9b510ea7fc` | 2026-04-21 | docs/harness | Broad markdown standardization created high churn in public truth surfaces. |
| `a68875462c` | 2026-04-15 | release tooling/CI | Release gate refactor and CI workflow wiring showed route/checker drift risk. |
| `1dd598c349` | 2026-03-31 | benchmark/evidence | Corpus pipeline added many fixtures, metadata, baselines, and quality gates; evidence schema drift is a recurring risk. |
| `a4ef50d185` | 2026-03-31 | metrics/reason codes | Unified reason codes and rollout docs showed need for complete runtime emission plus operator-facing docs. |
| `801a93d6df` | 2026-04-15 | corpus/streaming | Fixture expansion reinforced sidecar metadata and multi-boundary regression coverage needs. |
| `a295fe301e` | 2026-04-12 | streaming parity | Streaming parity harness hardening reinforced Result parity and known-difference scoping rules. |
| `0beec7ca54` | 2026-04-15 | release tooling | Tool package reorganization reinforced cross-script interface and Make/CI alignment checks. |

Recurring problem patterns:

- Review and CI fixes repeatedly added rules after regressions; the missing
  contract was not the individual rule, but traceable closeout from finding to
  remediation and verification.
- Harness and steering docs already pointed at repo-owned truth surfaces, but
  there was no dedicated route for broad history-analysis remediation work.
- Existing risk packs covered runtime, FFI, metrics, and docs-tooling surfaces,
  but no pack owned meta-level "analyze recent Git, update harness, then prove
  every finding closed" work.
- Optional local adapters mostly behaved as thin pointers, but SonarQube MCP
  steering used absolute MUST language without an explicit unavailable-tool
  degradation rule.

## 60-Day Extension Analysis

The extended scan covered 40 local/remote refs and 1084 unique commits.

| Category | 60-day commit hits | Hits older than 30 days |
|----------|--------------------|-------------------------|
| docs/packaging | 467 | 67 |
| nginx/protocol | 430 | 74 |
| ci/tooling/sonar | 413 | 67 |
| streaming/runtime | 400 | 35 |
| security/path | 278 | 46 |
| metrics/observability | 253 | 34 |
| benchmark/evidence | 219 | 22 |
| release/spec-governance | 207 | 44 |
| harness/steering/spec | 170 | 3 |
| ffi/boundary | 147 | 35 |

The 30-day window already covered most harness/steering churn.  The extra
30-day history changed the risk picture in two places:

- NGINX protocol/auth/cache/conditional behavior was prominent before the
  streaming-heavy period: auth-deny tests, decision logging, reason code
  lifecycle, large-response modular internals, and real NGINX IMS/source-build
  scripts.
- Release gate governance had several early fixes: backtracking checklist regex
  removal, validation/governance refinements, release automation, source-build
  target fixes, traversable temp build roots, and matrix/checklist tooling.

Those surfaces were covered indirectly by AGENTS rules and docs-tooling checks,
but they were not first-class harness risk packs.

## Full-History Reassessment

The full-history scan covered the same 40 refs and 1084 unique commits as the
60-day scan.  The earliest reachable commit is `db24ff8f06` on 2026-02-27
(`feat(repo): initial import as first commit`), and there are no commits before
2026-02-24 in the local or remote-tracking refs.

Because the 60-day scan already covered the complete reachable history, the
full-history reassessment did not add new findings beyond `P1-003`, `P1-004`,
and `P2-003`.  The existing additions remain the complete remediation set:

- `nginx-protocol-safety` covers the older auth/cache/conditional/status/header
  risk pattern.
- `release-governance` covers the older release gate, matrix, source-build, and
  governance pattern.
- Legacy release-gate validation remains conditional on legacy spec inputs
  being present.

## Findings

| ID | Priority | Finding | Evidence | Required fix |
|----|----------|---------|----------|--------------|
| P0-001 | P0 | Recent-change analysis reports had no machine-enforced closeout contract. | 953 commits scanned; many fixes became AGENTS rules after review, but no checker required finding IDs, final status, or verification evidence. | Add harness checker support for recent analysis reports and require traceable remediation states. |
| P1-001 | P1 | Broad harness/steering remediation had no dedicated risk pack or routing manifest entry. | Existing packs covered runtime, FFI, metrics, and docs-tooling only. | Add `harness-remediation` pack and route `AGENTS.md`, `docs/harness/**`, local Kiro steering adapters, `tools/harness/**`, and recent analysis reports to it. |
| P1-002 | P1 | The core harness loop did not define the requested two-phase analysis-then-remediation workflow. | `docs/harness/core.md` described spec routing and drift rescue, but not priority-ordered remediation closeout. | Add two-phase history analysis/remediation protocol to core harness docs and AGENTS meta-rule surface. |
| P1-003 | P1 | 60-day history exposed NGINX protocol/auth/cache/conditional risk without a dedicated routing pack. | Older-than-30-day hits: `nginx/protocol=74`, `security/path=46`; commits included auth-deny integration, decision logging, large-response internals, and IMS verification. | Add `nginx-protocol-safety` risk pack and focused verification family. |
| P1-004 | P1 | 60-day history exposed release-gate governance/source-build CI risk without dedicated routing. | Older-than-30-day hits: `release/spec-governance=44`; commits included release gate regex fixes, validation refinements, release automation, and source-build target fixes. | Add `release-governance` risk pack and verification family. |
| P2-001 | P2 | Kiro SonarQube steering could be read as stronger than repo-owned harness truth. | The local SonarQube MCP steering adapter used local MCP MUST language without an unavailable-tool fallback. | Clarify adapter-only status and SKIP-style degradation. |
| P2-002 | P2 | Harness entrypoints did not expose recent Git remediation as a first-class workflow. | `docs/harness/README.md` and risk-pack index lacked a direct entry for this work. | Add readable entrypoints and document update rows. |
| P2-003 | P2 | Legacy release-gate validation should not be a default focused command when legacy spec inputs are absent. | `make release-gates-check-legacy` failed because no legacy sub-spec directories were present under the optional local spec area. | Keep legacy validation conditional and remove it from default release-governance commands. |

## Optimization Plan

| Finding | Modification point | Implementation step | Expected effect | Verification |
|---------|--------------------|---------------------|-----------------|--------------|
| P0-001 | `tools/harness/check_harness_sync.py` | Add `recent-analysis-report` validation for report sections, finding IDs, remediation rows, and final status values. | Prevents prose-only reports with unclosed findings. | `python3 -m pytest tools/harness/tests/test_check_harness_sync.py`; `make harness-check` |
| P1-001 | `docs/harness/routing-manifest.json`, risk-pack docs | Add `harness-remediation` pack and summary/index entries. | Routes future broad harness/steering work to the right checks. | `make harness-check` |
| P1-002 | `AGENTS.md`, `docs/harness/core.md` | Define two-phase analysis/remediation and P0/P1 then P2/P3 closeout policy. | Makes execution order and closeout states explicit before editing. | `make harness-check` |
| P1-003 | `docs/harness/routing-manifest.json`, protocol risk-pack docs | Add `nginx-protocol-safety` with auth/cache/conditional/status/header sync points. | Routes protocol and security-sensitive NGINX changes to unit/integration checks. | `make harness-check`; `make test-nginx-unit` / `make test-nginx-integration` when touched |
| P1-004 | `docs/harness/routing-manifest.json`, release governance risk-pack docs | Add `release-governance` with release-gate, matrix, source-build, and parser sync points. | Routes release governance changes beyond generic docs drift. | `make release-gates-check`; strict/legacy checks when touched |
| P2-001 | local SonarQube MCP steering adapter | Add repo-owned truth precedence and unavailable-tool degradation note. | Prevents local adapter guidance from overriding harness truth. | `make harness-check-full` or optional adapter check |
| P2-002 | `docs/harness/README.md`, `routing-manifest.md`, risk-pack index | Add direct links and update history rows. | Improves discoverability for future agents. | `make harness-check` |
| P2-003 | `docs/harness/routing-manifest.json`, `docs/harness/risk-packs/release-governance.md` | Remove legacy release-gate validation from default commands and document when to run it. | Prevents optional legacy spec absence from blocking current release-governance checks. | `make release-gates-check-strict`; record legacy absence |

## Remediation Results

| ID | Status | Changed files | Evidence |
|----|--------|---------------|----------|
| P0-001 | fixed | `tools/harness/check_harness_sync.py`, `tools/harness/tests/test_check_harness_sync.py`, this report | Added `recent-analysis-report` check and tests for pass/fail closeout behavior. |
| P1-001 | fixed | `docs/harness/routing-manifest.json`, `docs/harness/risk-packs/harness-remediation.md`, `docs/harness/routing-manifest.md`, `docs/harness/risk-packs/README.md` | Added primary route and readable overlays for harness remediation work. |
| P1-002 | fixed | `AGENTS.md`, `docs/harness/core.md` | Added two-phase recent Git analysis and remediation closeout rules. |
| P1-003 | fixed | `docs/harness/routing-manifest.json`, `docs/harness/risk-packs/nginx-protocol-safety.md`, `docs/harness/routing-manifest.md`, `docs/harness/risk-packs/README.md` | Added protocol safety route, sync points, and focused verification family. |
| P1-004 | fixed | `docs/harness/routing-manifest.json`, `docs/harness/risk-packs/release-governance.md`, `docs/harness/routing-manifest.md`, `docs/harness/risk-packs/README.md` | Added release governance route, sync points, and focused verification family. |
| P2-001 | fixed | local SonarQube MCP steering adapter | Clarified adapter-only status and unavailable-tool degradation. |
| P2-002 | fixed | `docs/harness/README.md`, local structure and tech steering adapters | Added entrypoint and steering pointers for recent analysis reports and remediation routing. |
| P2-003 | fixed | `docs/harness/routing-manifest.json`, `docs/harness/risk-packs/release-governance.md`, `docs/harness/routing-manifest.md` | Scoped legacy release-gate validation to clones where legacy spec inputs are present. |

## Verification

Commands run during this session:

| Command | Result |
|---------|--------|
| `git fetch --all --prune --tags` | PASS |
| `python3 tools/harness/resolve_spec.py --hint "recent git harness steering analysis and remediation"` | WARN_NEEDS_AUTHOR_REVIEW; documented as cross-cutting maintenance |
| `python3 -m pytest tools/harness/tests/test_check_harness_sync.py` | PASS; 9 tests passed |
| `python3 skills/nginx-markdown-harness-maintenance/scripts/harness_route.py --from-git` | PASS; matched `harness-remediation` and `docs-tooling-drift` |
| `make harness-check` | PASS; `recent-analysis-report` check passed |
| `make docs-check` | PASS after replacing untracked local adapter path references with adapter descriptions |
| `make harness-check-full` | PASS; docs, harness, release-gate, and naming checks passed |
| 60-day Git history scan (`2026-02-24..2026-04-24`) | PASS; 1084 unique commits analyzed, two additional routing gaps fixed |
| Full Git history scan | PASS; earliest commit is 2026-02-27, so full history equals the 60-day scan |
| `make release-gates-check-strict` | PASS; 39 passed, 0 failed, 17 skipped |
| `make release-gates-check-legacy` | Not default evidence; failed because optional legacy sub-spec directories were absent, so routing was adjusted to keep this conditional |

Final reassessment:

- All P0 and P1 findings are implemented.
- All worthwhile P2 findings are implemented.
- No finding is intentionally deferred.
- Final routing and verification covered `harness-remediation`,
  `docs-tooling-drift`, `nginx-protocol-safety`, `release-governance`,
  `harness-sync`, `docs-tooling`, and `release-quality`.
