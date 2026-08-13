# Review Report: 0.9.2 Pre-Code-Freeze — Documentation Accuracy

**Date**: 2026-08-07
**Branch**: `dev/wip-0.9.2-harness` (base: `dev/wip-0.9.2`)
**Scope**: Git-tracked `.md` files under `docs/` (204 files. 45 deep-read, remainder targeted grep cross-validation) + root-level/component READMEs, CHANGELOG, THIRD-PARTY-NOTICES, packaging/chart/matrix carriers (32 files reviewed).
**Method**: Cross-validated against code truth (25 directives, `markdown_limits` 8 keys, 12 metric families, 27 reason codes, ABI v2, MSRV 1.97, Cargo.lock). No files modified during review.

## Summary

- docs/ findings: 34 (High 13 / Medium 15 / Low 6)
- root-level findings: 24 (High 4 / Medium 12 / Low 8)
- Verified-clean: 25-directive table, limits keys, 12-family metrics table, reason codes, ABI=2, MSRV, release matrix entries, relative links.

## Findings — docs/

### D-H1 (High) — `docs/releases/0.9.2-release-notes.md:166`
- **Issue**: Compatibility table "Metric names/labels | Unchanged" contradicts 0.9.2's replacement of legacy families with the 12-family v1 freeze (v1 renderer introduced 2026-08-04, absent in v0.9.1 tag). Contradicts `docs/guides/prometheus-metrics.md:134-136`.
- **Fix**: Change to "Metric families | legacy multi-format families | twelve-family v1 freeze (removes per-path/shadow/legacy debug families)".

### D-H2 — `docs/features/DECISION_CHAIN.md:142-144`
- **Issue**: References deleted families `nginx_markdown_conversions_total` / `failopen_total` / `failures_total`.
- **Fix**: Use `nginx_markdown_requests_total{outcome="converted"|"failed_open"|"failed_closed"}` and `conversion_deliveries_total` (match OPERATIONS.md:1019-1023).

### D-H3 — `docs/features/DECISION_CHAIN.md:113`
- **Issue**: References deleted `nginx_markdown_failures_total`.
- **Fix**: `nginx_markdown_requests_total` (match OPERATIONS.md:936).

### D-H4 — `docs/features/DECISION_CHAIN.md:176`
- **Issue**: `decompression_budget_exceeded` cites deleted `markdown_decompress_max_size`.
- **Fix**: `markdown_limits decompressed_size=`.

### D-H5 — `docs/features/DECISION_CHAIN.md:180-181`
- **Issue**: `markdown_parse_timeout` (default 30s) and `markdown_parser_budget` (default 64m) deleted, defaults wrong.
- **Fix**: `markdown_limits parser_timeout=` (default 10s) and `parser_memory=` (default 32m).

### D-H6 — `docs/architecture/SYSTEM_ARCHITECTURE.md:231-233`
- **Issue**: `markdown_stream_threshold` directive deleted in 0.9.2 (threshold is internal fixed 1 MiB).
- **Fix**: "threshold is a fixed internal 1 MiB, not a directive".

### D-H7 — `docs/architecture/SYSTEM_ARCHITECTURE.md:239`
- **Issue**: `markdown_stream_precommit_buffer` deleted.
- **Fix**: `markdown_limits streaming_buffer=`.

### D-H8 — `docs/architecture/SYSTEM_ARCHITECTURE.md:291-294`
- **Issue**: `markdown_decompress_max_size` deleted + nonexistent `markdown_limits memory=<size>`.
- **Fix**: `markdown_limits decompressed_size=`, independent of `conversion_memory=`.

### D-H9 — `docs/architecture/SYSTEM_ARCHITECTURE.md:297-300`
- **Issue**: `markdown_parse_timeout`/`markdown_parser_budget` deleted, wrong FFI codes (Timeout=9, BudgetExceeded=10, ReplayError=11, not 10/11).
- **Fix**: limits keys + corrected codes.

### D-H10 — `docs/architecture/observability-schema-v1.md:58-70`
- **Issue**: Claims "exactly these twelve families" but lists only 11 (missing `nginx_markdown_streaming_peak_memory_bytes`).
- **Fix**: Add the missing family.

### D-H11 — `docs/project/PROJECT_STATUS.md:385`
- **Issue**: `markdown_limits memory=<size> timeout=<time>` keys do not exist.
- **Fix**: `conversion_memory=` / `conversion_timeout=`.

### D-H12 — `docs/testing/DIRECTIVE_VALIDATION_TESTS.md:33`
- **Issue**: `markdown_limits memory=<size>` wrong key.
- **Fix**: `conversion_memory=`.

### D-H13 — `docs/testing/DIRECTIVE_VALIDATION_TESTS.md:59`
- **Issue**: `markdown_limits timeout=<time>` wrong key.
- **Fix**: `conversion_timeout=`.

### D-M1 — `SYSTEM_ARCHITECTURE.md:338-359` — "v0.9.1 Feature Set" section describes removed `markdown_streaming_zero_copy` as current; no v0.9.2 section. Add a v0.9.2 current-state section; mark 0.9.1 section historical.
### D-M2 — `SYSTEM_ARCHITECTURE.md:309` — cites nonexistent `ngx_http_markdown_dynconf.c`; actual `ngx_http_markdown_dynconf_snapshot.c` / `ngx_http_markdown_dynconf_impl.h`.
### D-M3 — `SYSTEM_ARCHITECTURE.md:331` — cites nonexistent `ngx_http_markdown_ffi_helpers.c`; actual `ngx_http_markdown_header_plan.c`.
### D-M4 — `PROJECT_STATUS.md:402-406` — "#### 0.9.1 (current)" stale; zero_copy listed without removal note. Change to "(previous release)"; note zero_copy removed in 0.9.2.
### D-M5 — `PROJECT_STATUS.md:38-50` — 0.9.2 section missing metrics freeze (12-family v1 contract) item. Add it.
### D-M6 — `docs/harness/rules/docs-tooling.md:41-45` — rule text describes v0.9.1 reject-only stub contract; detector updated to "stub removed in 0.9.2". Align rule text.
### D-M7 — `docs/harness/rules/docs-tooling.md:30-32` — rate example uses `shadow_diff_total`/`shadow_total` (shadow removed in 0.9.2). Use in-family example (e.g., decompression_events_total).
### D-M8 — `docs/glossary.md:11` — `markdown_large_body_threshold` "reject-only stub" → "removed in 0.9.2; unknown directive".
### D-M9 — `docs/guides/performance-rollout-091.md` — add 0.9.2 superseded banner (zero_copy directive removed).
### D-M10 — ADR-0023 number collision: `docs/architecture/decisions/ADR-0023-otel-removal-reintroduction-conditions.md` vs `docs/architecture/ADR/0023-single-streaming-policy.md`. PROJECT_STATUS.md:41, PUBLIC_SURFACE_INVENTORY.md:77/89/191 reference "ADR-0023" meaning the OTel record. Renumber the decisions/ file (e.g., ADR-0027) and update references in PROJECT_STATUS.md + PUBLIC_SURFACE_INVENTORY.md.
### D-M11 — `DECISION_CHAIN.md:135` — SKIPPED row missing `skipped_conditional` (referenced in table at 157). Align.
### D-M12 — `DECISION_CHAIN.md:10` — Prometheus reason labels cited to `ngx_http_markdown_prometheus_impl.h` (now test fixture); production renderer is `ngx_http_markdown_metrics_v1_renderer.h`.
### D-M13 — `docs/guides/streaming-default-migration.md:6-17` — banner targets "v0.9.1+ operators" but table instructs `markdown_stream_threshold` (deleted). Add 0.9.2 note.
### D-M14 — `docs/features/CACHE_AWARE_RESPONSES.md:135` — `full_support` is not a valid `markdown_cache_validation` value; use `full`.
### D-M15 — `CHANGELOG.md:7-9` — empty `## [Unreleased]` section coexists with `## [0.9.2] - Unreleased candidate`. Remove the empty section.
### D-L1 — `CONFIG_BEHAVIOR_MAP.md:199` — "sole public streaming selector in v0.9.1" → current line.
### D-L2 — `CONFIG_BEHAVIOR_MAP.md:101` — "mdx and org-mode selectors are rejected in v0.9.1" → v0.9.2.
### D-L3 — `streaming-compression-strategy.md:5` — title "v0.9.2" vs body "0.9.1 streaming conversion engine"; unify.
### D-L4 — `0.9.2-release-notes.md` "Default behavior | Unchanged" — soften to "Unchanged except removed legacy surfaces".
### D-L5 — `PROJECT_STATUS.md:52-111` — historical 0.7.0/0.6.x sections unmarked; add "historical" labels.
### D-L6 — code comment cross-check: `filter_module.h:616-619` stale defaults (covered by C-H1).

## Findings — root-level docs / version carriers

### R-H1 (High) — `THIRD-PARTY-NOTICES:199`
- **Issue**: serde_json declared 1.0.150, `components/rust-converter/Cargo.lock` resolves 1.0.151. Violates Rule 49. Checker `tools/ci/check_third_party_notices.py` only validates `[dependencies]`, serde_json is a dev-dependency so the drift escapes CI.
- **Fix**: Update entry to 1.0.151, consider checker coverage for dev-deps (note as follow-up).
- **Verify**: `python3 tools/ci/check_third_party_notices.py`, grep Cargo.lock.

### R-H2 (High) — `components/nginx-module/README.md:69-95`
- **Issue**: Entire "Streaming Threshold Directive" section documents deleted `markdown_stream_threshold` and threshold routing mechanism.
- **Fix**: Replace section with a short "removed in 0.9.2" historical note pointing to `markdown_streaming` selector and `markdown_limits streaming_buffer=`.

### R-H3 (High) — `components/nginx-module/README.md:90`
- **Issue**: `markdown_limits memory=<size>` — wrong key (`conversion_memory=` is current).
- **Fix**: Use `conversion_memory=` (covered by R-H2 rewrite).

### R-H4 (High) — `tools/release-matrix.json:413-440`
- **Issue**: File carries legacy top-level `matrix` array (20 stale entries with `nginx`/`os_type`/`support_tier:"full"` aliases) + `updated_at`. Violates own schema (`additionalProperties:false`) and Rule 62 (alias coexistence).
- **Evidence**: `normalize_matrix.py:88-90` fail-closes when both `entries` and `matrix` present. Consumers `validate_matrix_install_consistency.py:65` and `check_packaging_consistency.py:313` read legacy `matrix` — they must migrate to the canonical `entries` array.
- **Fix**: Remove legacy `matrix` array + `updated_at`, update the two consumers to read `entries` (with key normalization via `normalize_matrix.load_and_normalize` where feasible).
- **Verify**: `python3 -m pytest tools/release/matrix/tests tools/docs/tests -q`, run both consumers.

### R-M1 — `README.md:373` / `README_zh-CN.md:370` — doc nav lists only MIGRATION-0.9/0.8; add MIGRATION-0.9.1.md and MIGRATION-0.9.2.md links.
### R-M2 — `README.md:440-453` / `README_zh-CN.md:431-444` — Document Updates table missing 0.9.2 row. Add.
### R-M3 — `CHANGELOG.md:10` — `## [0.9.2] - Unreleased candidate (2026-08-07)` carries a date while unreleased; keep `Unreleased` without date (date at release time).
### R-M4 — `components/nginx-module/README.md:22-44` — Source Layout lists 23 of ~65 files. Regenerate the list (or link to REPOSITORY_STRUCTURE.md).
### R-M5 — `components/nginx-module/README.md:104-109` — Document Updates table stuck at 0.6.2; add rows or drop table.
### R-M6 — `components/rust-converter/README.md:93` — "default streaming path in 0.9.1" → "0.9.x"/current.
### R-M7 — `packaging/rpm/nginx-markdown-module.spec:2` — hardcoded `Version: 0.8.0`, stale legacy copy (CI uses `SPECS/nginx-module-markdown.spec` with `%{version}`). Sync to 0.9.2 or mark legacy.
### R-M8 — `packaging/rpm/SPECS/nginx-module-markdown.spec:29` — "GFM/MDX flavor support"; MDX removed in 0.9.1 (spec's own changelog says so). Change to "CommonMark/GFM".
### R-M9 — `packaging/README.md:75-76` — 0.8.3 smoke-test artifact names → 0.9.2.
### R-M10 — `packaging/repo/apt/README.md:9` + `repo/yum/README.md:9` — "public v0.7.0 release channel" → "latest GitHub Release" / 0.9.x.
### R-M11 — `perf/baselines/README.md:146-153` — Baseline Files list missing tracked `module-baseline-092*.json`/raw-probes series (24 files). Add 092 entries as the 0.9.2 evidence baseline.
### R-M12 — `Makefile:535-540` — `PKG_VERSION=${PKG_VERSION:-0.7.0}` default stale → 0.9.2 or derive from Cargo.toml.
### R-L1 — `tools/install.sh:9` — `VERSION=v0.1.0` example → current line.
### R-L2 — `packaging/repo/apt/README.md:41,47` — package name `nginx-module-markdown` → `nginx-module-markdown-for-agents`.
### R-L3 — `packaging/nginx-checksums.yaml:13-17` — self-deprecated stale versions; update or delete.
### R-L4 — `packaging/rpm/SOURCES/README.md:14` — `git archive --prefix=...0.7.0/` example stale.
### R-L5 — `packaging/debian/changelog:1-9` — 0.9.2 date 2026-07-30 vs CHANGELOG 2026-08-07; align on release date.
### R-L6 — `components/rust-converter/README.md:141-145` — Document Updates stuck at 0.6.3; update or drop.
### R-L7 — `packaging/README.md:59` — "0.7.0 release package naming" → current line.
### R-L8 — `tools/release-matrix.json` schema_version "1.0" vs docs/release "1" — add README note distinguishing the two matrix files' roles.

## Verification method (post-fix)

- `make docs-check`
- `python3 tools/harness/detect_doc_sync.py` (Rule 9 sync)
- `python3 tools/ci/check_third_party_notices.py`
- `make release-matrix-check`
- `python3 -m pytest tools/release/matrix/tests tools/docs/tests -q`
- `make harness-check`

---

## Closeout (2026-08-07)

The team remediated all non-deferred findings on `dev/wip-0.9.2-harness`.
R-L5 remains explicitly deferred until the release-time Debian changelog update:

| ID | Status |
|----|--------|
| D-H1..D-H13, D-M1..D-M15, D-L1..D-L6 | **fixed** — 16 docs/ files updated; ADR-0023 (OTel) renamed to ADR-0027 with references updated in PROJECT_STATUS.md + PUBLIC_SURFACE_INVENTORY.md |
| R-H1 | **fixed** — THIRD-PARTY-NOTICES serde_json → 1.0.151 (matches Cargo.lock) |
| R-H2/R-H3 | **fixed** — nginx-module README threshold section rewritten for 0.9.2 |
| R-H4 | **fixed** — all matrix consumers now normalize the canonical `entries` view through one alias boundary; compatibility metadata (`updated_at`, `support_tiers`, `tier_mapping`) is preserved, and legacy aliases remain input-only. |
| R-M1..R-M12, R-L1..R-L4, R-L6..R-L8 | **fixed** |
| R-L5 | **deferred** — Debian changelog date is set at release time |

Verification (fresh runs, all green):
- `make docs-check` — PASS
- `python3 tools/ci/check_third_party_notices.py` — PASS
- `make release-matrix-check` — PASS
- `python3 tools/release/matrix/validate_matrix_install_consistency.py` — PASS
- `python3 tools/docs/check_packaging_consistency.py` — PASS
- `python3 -m pytest tools/release/matrix/tests tools/docs/tests -q` — PASS
