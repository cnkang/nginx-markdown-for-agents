# 0.5.0 Unified Release Checklist

## Overview

This checklist aggregates all release gate verification steps, organized by the five gate categories. Each item can be verified via a specific artifact, command, or review action.

## Documentation Gates

- [ ] All 7 sub-specs have requirements documents — Verify: `make release-gates-check-strict` or `tools/release/gates/validate_release_gates.py --mode strict --check docs-exist`
- [ ] All 7 sub-specs have design documents — Verify: `make release-gates-check-strict` or `tools/release/gates/validate_release_gates.py --mode strict --check docs-exist`
- [ ] All new operator-facing surfaces are documented — Verify: manual review + `make docs-check`
- [ ] Streaming configuration guide is complete — Verify: document existence check in `docs/guides/`
- [ ] Rollout cookbook is complete (streaming enable, shadow mode, gradual expansion) — Verify: document existence check
- [ ] Compatibility matrix documentation is complete — Verify: `docs/project/compatibility-matrix-0-5-0.md` exists with all capabilities classified
- [ ] 0.5.0 non-goals are explicitly listed — Verify: `docs/project/release-gates-0-5-0.md` contains non-goals section
- [ ] CHANGELOG.md updated with 0.5.0 entry — Verify: `grep '0.5.0' CHANGELOG.md`
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

When a checklist item cannot be satisfied:

1. The item must be escalated to the Go/No-Go review
2. An explicit exception must be recorded, including: rationale, risk assessment, mitigation plan
3. Unresolved failures without exceptions block the release
