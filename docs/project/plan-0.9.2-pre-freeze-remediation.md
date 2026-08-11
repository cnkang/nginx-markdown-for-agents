# Action Plan: 0.9.2 Pre-Code-Freeze Remediation

**Date**: 2026-08-07
**Branch**: `dev/wip-0.9.2-harness` (base: `dev/wip-0.9.2`)
**Status**: derived from the three review reports (comment-consistency, documentation, rules) in this directory.

## Scope decisions (pre-freeze)

- Comment/doc/rule-only changes were the original scope. The closeout also
  requires narrowly scoped tooling and consumer migrations where needed to
  make the canonical contracts executable and deterministic. Production
  C/Rust behavior remains outside that historical plan unless a later review
  finding explicitly identifies a correctness defect.
- The review documents dead-code paths rather than removing them (pre-freeze
  risk control). Production dead-code paths remain untouched, except for the
  documented directory moves/removals in the companion report. The companion
  report consolidated `decisions/` into `docs/architecture/ADR/` and
  `docs/release/` into `docs/releases/`.
- The release-matrix migration keeps legacy aliases only at the input boundary.
  all consumers resolve canonical `entries` and preserve compatibility metadata
  in the same change.
- THIRD-PARTY-NOTICES serde_json 1.0.150 → 1.0.151 (Rule 49).
- No CI workflow additions in pre-freeze (F4 relabeled instead of wired).

## Execution order

| # | Task | Items | Verification |
|---|------|-------|--------------|
| 1 | C comment fixes | comment-consistency report: C-H1..C-H2, C-M1..C-M14, C-L1..C-L11 | make test-nginx-unit |
| 2 | Rust comment fixes + FFI header regen | comment-consistency report: R-H1..R-H3 (doc-only), R-M*, R-L* | cargo check, make check-headers |
| 3 | Python/Shell comment fixes | P-H1..P-H3, P-M*, P-L* | pytest tools/harness/tests |
| 4 | docs/ fixes | D-H1..D-H13, D-M1..D-M15, D-L1..D-L6 | make docs-check |
| 5 | Root docs / packaging / matrix | documentation report: R-H1..R-H4, R-M1..R-M12, R-L1..R-L8 | check_third_party_notices.py, release-matrix-check |
| 6 | Rules & harness | F1..F15 | make harness-check, Rule 41 grep |
| 7 | Full verification | all of the above | see below |
| 8 | Commits & push | logical batches | — |
| 9 | PR creation | dev/wip-0.9.2-harness → dev/wip-0.9.2 | gh pr create |

## Commit batching plan

1. `docs(project): add 0.9.2 pre-freeze review reports` — the three review reports
2. `fix(nginx): correct stale/misleading comments in C module` — C comment fixes
3. `docs(rust): correct FFI and API doc comments; regenerate FFI header` — Rust comment fixes + header
4. `fix(tools): correct stale docstrings and comments in Python/Shell scripts` — py/sh fixes
5. `docs: update 0.9.2 stale documentation and metrics references` — docs/ fixes
6. `docs: refresh root-level docs, notices, packaging and matrix carriers` — root docs fixes
7. `fix(harness): align rules, gates and routing manifest with 0.9.2` — F1..F15
8. Final verification results appended to reports (if any), otherwise merged into 7.

## Final verification set

- `make docs-check`
- `make harness-check`
- `make release-matrix-check`
- `make check-headers`
- `make complexity-check`
- `python3 tools/ci/check_third_party_notices.py`
- `python3 tools/harness/detect_doc_sync.py`
- `make test-nginx-unit`
- `cargo check` (components/rust-converter)
- `python3 -m pytest tools/harness/tests tools/release/matrix/tests tools/docs/tests -q`
- Rule 41 grep → zero hits
