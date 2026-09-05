# 0.5.0 Unified Release Checklist

## Overview

This checklist aggregates all release gate verification steps. It organizes
them by the five gate categories. Each item has a specific artifact, command,
or review action.

## Documentation Gates

- [ ] All 7 sub-specs have complete requirements documents. Each document covers scope, behavior, and acceptance criteria. Verify: manual review plus `make release-gates-check-strict` (the project retired the former standalone document validator).
- [ ] All 7 sub-specs have complete design documents. Each document covers implementation decisions and validation plans. Verify: manual review plus `make release-gates-check-strict` (the project retired the former standalone document validator).
- [ ] All new operator-facing surfaces appear in docs — Verify: manual review + `make docs-check`
  - A signed review record is mandatory for this item. The record must name the reviewer, the review date, the reviewed scope, and the evidence artifact.
- [ ] Documentation is accurate and complete, including defaults, behavior, failure modes, and migration guidance — Verify: manual review against current docs plus `make docs-check`
  - A signed review record is mandatory for this item. The record must name the reviewer, the review date, the reviewed scope, and the evidence artifact.
- [ ] Streaming configuration guide is complete — Verify: `make docs-check` plus a signed accuracy review that checks defaults, enablement, shadow mode, rollback, and failure-mode guidance
- [ ] Rollout cookbook is complete (streaming enable, shadow mode, gradual expansion) — Verify: `make docs-check` plus a signed accuracy review. The review checks
  enablement, staged rollout, rollback, and failure-mode guidance.
- [ ] Compatibility matrix documentation is complete — Verify: `docs/project/history/compatibility-matrix-0-5-0.md` exists with all capabilities classified
- [ ] 0.5.0 non-goals are explicitly listed — Verify: `docs/project/release-gates-0-5-0.md` contains the non-goals section
- [ ] CHANGELOG.md updated with 0.5.0 entry — Verify: `grep -En '^## \\[?0\.5\.0\\]?([[:space:]]|$)' CHANGELOG.md` returns at least one release heading
- [ ] `make docs-check` passes — Verify: `make docs-check` exit code 0

## Testing Gates

- [ ] CI passes on Ubuntu — Verify: GitHub Actions CI artifacts
- [ ] CI passes on macOS — Verify: GitHub Actions CI artifacts
- [ ] CI passes on NGINX 1.24.x, 1.26.x, 1.27.x — Verify: CI matrix artifacts
- [ ] Streaming path vs full-buffer path differential tests pass — Verify: diff test report artifact
- [ ] Chunk-boundary fuzzing passes — Verify: fuzzing test report (random split points do not change semantic output)
- [ ] Failure-path test coverage — Verify: test report covers decompressor failure, budget overflow, parser invalid state, downstream backpressure
- [ ] Streaming path bounded-memory evidence generated — Verify: memory analysis report in Evidence Pack
- [ ] Evidence Pack generated and archived — Verify: Evidence Pack artifact exists
- [ ] Rust property-based tests pass — Verify: `cargo test` with proptest exit code 0
- [ ] Python property-based tests pass — Verify: `pytest tools/release/gates/tests/` exit code 0

## Compatibility Gates

- [ ] Full-buffer path default behavior unchanged from 0.4.0 — Verify: e2e test comparison against 0.4.0 behavior
- [ ] Streaming path disabled by default — Verify: e2e test under default config confirms full-buffer path active
- [ ] Each capability's classification in compatibility matrix verified — Verify: implementation-phase verification record
- [ ] New configuration directives have documented default values — Verify: configuration documentation review
- [ ] Existing `markdown_*` directive behavior unchanged — Verify: e2e regression tests

## Operations Gates

- [ ] Operator can enable streaming path via documentation — Verify: config guide + operational verification
- [ ] Operator can roll back from streaming to full-buffer via documentation — Verify: rollback docs + operational verification
- [ ] Operator can observe streaming path behavior via metrics and logs — Verify: metrics endpoint + log review
- [ ] Operator can shadow-verify streaming output — Verify: shadow mode docs + operational verification

## Streaming Evidence Gates

- [ ] Streaming path peak memory does not grow linearly with document size — Verify: bounded-memory benchmark report
- [ ] Streaming path has measurable TTFB improvement for large responses — Verify: performance benchmark report
- [ ] Streaming path vs full-buffer path output diff on test corpus within acceptable range — Verify: diff test report
- [ ] Streaming path rollback verified in test environment — Verify: rollback verification record

## Exception Handling

When a checklist item cannot pass:

1. The team must escalate the item to the Go/No-Go review
2. The team must record an explicit exception, including: rationale, risk assessment, mitigation plan
3. Recording an exception does **not** by itself authorize release: any failed
   P0 sub-spec DoD cannot be overridden by an exception or release-owner
   authorization. It remains a No-Go until the team fixes the DoD. A non-P0
   exception may require written release-owner authorization identifying the
   gate, release candidate, and exact exception scope
4. Unresolved failures without exceptions block the release

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-24 | Kang | Operator-facing surfaces and documentation accuracy items now require a signed review record with reviewer, date, scope, and evidence artifact |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
