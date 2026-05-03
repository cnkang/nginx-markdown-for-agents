# Recent Git Harness Steering Analysis

Analysis window: 2026-04-19 through 2026-05-03, covering the two-week branch
scan requested on 2026-05-03.

Remote state was refreshed with `git fetch --all --prune`. Local and remote
refs were enumerated from `refs/heads` and `refs/remotes`, excluding
`origin/HEAD`, and commits were deduplicated by SHA.
`tools/harness/resolve_spec.py --hint "review last two weeks all branch commits
for harness rule updates"` returned `SKIP_NOT_PRESENT`, so this work is treated
as cross-cutting harness maintenance.

## Phase 1 Analysis

The scan covered 23 local/remote refs and 162 unique non-merge commits.

| Category | Commit hits |
|----------|-------------|
| ci/release-matrix | 59 |
| docs/packaging | 52 |
| observability/metrics/otel | 47 |
| streaming/runtime | 45 |
| ffi/rust-boundary | 40 |
| nginx-protocol/auth | 34 |
| harness/rules | 21 |
| dynamic-config | 8 |
| other | 12 |

Representative high-risk commits inspected:

| Commit | Date | Surface | Detail checked |
|--------|------|---------|----------------|
| `603eb9c` | 2026-05-03 | dynamic config | Added dynconf parser and runtime apply code under request handling. |
| `23ab404` | 2026-05-02 | dynamic config | Wired watcher lifecycle and `reload_pending` state. |
| `0195191` | 2026-05-02 | dynamic config | Fixed timer lifecycle and NUL-terminated reload path handling. |
| `df468d7` / `29bad5c` | 2026-05-03 | dynamic config | Repaired failed-reload retry latch ordering and added blocking I/O warning. |
| `80f3e9e` / `0eac978` | 2026-05-03 | dynamic config | Corrected size parsing and final-line-without-newline handling. |
| `ff4ff5f` / `23fcac5` | 2026-04-19 | release matrix CI | Centralized install-verify workflow and split upstream-vs-release matrix sources. |
| `d5626cb` / `a53e10e` | 2026-04-19/20 | release matrix tooling | Removed regex hotspot and tracked latest upstream NGINX versions. |
| `d35d6d1` / `4407448` / `54494a1` | 2026-05-03 | auth/cache-control | Fixed multi-header Cache-Control aggregation and precedence. |
| `5e263c9` / `c37cc89` / `7799d6d` | 2026-05-03 | auth cache bypass | Reworked cookie-name matching and session wildcard behavior. |
| `7d177e1` | 2026-05-03 | OTel tracing | Fixed traceparent traversal across all `ngx_list_part_t` header parts. |
| `315f0cb` / `4cb2941` / `57031d9` | 2026-05-02/03 | per-path metrics | Added SHM fields, path labels, cardinality directive, and `__other__` overflow output. |
| `086713e` / `37fe56b` / `c320b1f` | 2026-05-03 | Homebrew packaging | Added tap workflows and repeatedly corrected archive checksum/tag coupling. |

Recurring problem patterns:

- Dynamic config hot reload crossed parser, worker lifecycle, timer, retry
  latch, request-time apply, and operator docs, but no first-class risk pack
  owned that surface.
- Release governance already covered release gates, but install-verify and
  update-matrix workflow paths were not routed directly through the release
  pack, despite repeated CI fixes in that area.
- Auth/cache-control fixes repeatedly involved list traversal and aggregate
  precedence across multiple header values, while cache-bypass examples needed
  cookie-name boundary matching.
- OTel and metrics fixes showed that header-list traversal, label escaping,
  and cardinality overflow semantics are easy sync points to miss.
- The Homebrew/packaging and OTel routing gaps found in the current
  `dev/wip-v0.6.0` branch were real two-week scan findings, not isolated to
  the branch-vs-main review.

## Findings

| ID | Priority | Finding | Evidence | Required fix |
|----|----------|---------|----------|--------------|
| P1-001 | P1 | Dynamic config hot reload lacked a dedicated routing pack. | Eight commits touched dynconf parsing, timer lifecycle, retry latch state, NUL termination, and runtime apply behavior. | Add a `dynamic-config-hot-reload` pack and route dynconf, lifecycle, request apply, config docs, and examples through it. |
| P1-002 | P1 | Release-governance routing missed install-verify/update-matrix workflow churn. | Multiple 2026-04-19 commits repaired install-verify, upstream matrix discovery, workflow regression tests, and regex hotspots. | Add install-verify/update-matrix/release-matrix paths and sync points to the release-governance route. |
| P1-003 | P1 | Protocol safety sync points did not explicitly cover repeated header-list traversal and cookie-boundary cache-bypass risks. | 2026-05-03 auth/cache commits fixed multi-header Cache-Control aggregation and session-cookie false matches. | Strengthen protocol pack paths and sync points for repeated headers, aggregate precedence, and cookie-name boundaries. |
| P2-001 | P2 | OTel/observability packs under-specified header-list traversal, per-path label escaping, and cardinality overflow. | Traceparent traversal and per-path metrics fixes showed these are recurring observability contract details. | Add sync points to OTel and observability packs. |
| P2-002 | P2 | Packaging/Homebrew and OTel path routing initially missed real v0.6.0 files. | Branch-vs-main review showed `ngx_http_markdown_otel_impl.h`, Homebrew workflows, formula files, and package metadata were not direct path hits. | Keep the route expansion made during the current branch review and record it as part of this scan. |

## Remediation Results

| ID | Status | Changed files | Evidence |
|----|--------|---------------|----------|
| P1-001 | fixed | `docs/harness/risk-packs/dynamic-config-hot-reload.md`, `docs/harness/routing-manifest.json`, `docs/harness/routing-manifest.md`, `docs/harness/risk-packs/README.md` | Added dedicated risk pack, manifest routing, readable overlay, and pack index row. |
| P1-002 | fixed | `docs/harness/routing-manifest.json`, `docs/harness/risk-packs/release-governance.md` | Added install-verify/update-matrix/official-nginx-docker/release-matrix paths and regression sync points. |
| P1-003 | fixed | `docs/harness/routing-manifest.json`, `docs/harness/risk-packs/nginx-protocol-safety.md` | Added example config path routing and multi-header/cookie-boundary sync points. |
| P2-001 | fixed | `docs/harness/risk-packs/otel-integration.md`, `docs/harness/risk-packs/observability-metrics.md` | Added trace-context header-list traversal, per-path escaping, and cardinality overflow sync points. |
| P2-002 | fixed | `docs/harness/routing-manifest.json`, `docs/harness/risk-packs/otel-integration.md`, `docs/harness/risk-packs/packaging-distribution.md`, `docs/harness/routing-manifest.md` | OTel implementation-header/config paths and Homebrew/package paths now route to their dedicated packs. |

## Verification

Commands run during this session:

| Command | Result |
|---------|--------|
| `git fetch --all --prune` | PASS |
| `python3 tools/harness/resolve_spec.py --hint "review last two weeks all branch commits for harness rule updates"` | SKIP_NOT_PRESENT; documented as cross-cutting harness maintenance |
| `git log --all --since='2026-04-19 00:00:00' --no-merges ...` | PASS; 162 unique commits classified |
| `git show --stat ...` for representative dynconf, release-matrix, protocol, OTel, metrics, and packaging commits | PASS; high-risk diffs inspected before edits |
| `python3 /Users/liukang/.codex/skills/nginx-markdown-harness-maintenance/scripts/harness_route.py --from-git --hint "two-week branch scan dynconf install-verify protocol otel packaging harness remediation"` | PASS; matched harness-remediation, docs-tooling-drift, release-governance, dynamic-config-hot-reload, observability-metrics, and otel-integration |
| `python3 -m json.tool docs/harness/routing-manifest.json >/dev/null` | PASS |
| `make harness-check` | PASS; recent-analysis-report closeout check passed |
| `make docs-check` | PASS |
| `git diff --check` | PASS |
| `make harness-check-full` | PASS; docs, harness, release-gate, and naming checks passed |
| `python3 /Users/liukang/.codex/skills/nginx-markdown-harness-maintenance/scripts/harness_route.py --from-git --base main --hint "v0.6.0 dynamic config hot reload install-verify release matrix auth cache control otel homebrew packaging"` | PASS; branch-vs-main diff now routes dynamic-config-hot-reload, packaging-distribution, nginx-protocol-safety, release-governance, otel-integration, and existing supporting packs |
