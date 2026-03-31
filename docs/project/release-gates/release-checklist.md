# 0.4.0 Unified Release Checklist

This is the unified release checklist for the 0.4.0 Go/No-Go review. Every item below is derived from the four release gate categories defined in the 0.4.0 Overall Scope and Release Gates spec. Each item is verifiable by a specific artifact, command, or review action — not subjective judgment.

This checklist must be completed and archived as part of the 0.4.0 release record. Any item that cannot be satisfied must be escalated to the Go/No-Go review with a documented exception, including rationale, risk assessment, and mitigation plan.

Requirements references: 10.1, 10.2, 10.3, 10.4, 10.5.

---

## Documentation Gates

- [x] All 6 sub-specs have requirements documents — verified by `make release-gates-check`
- [x] All 6 sub-specs have design documents — verified by `make release-gates-check`
- [x] All new configuration directives are documented in `docs/guides/`
- [x] Installation guide in `docs/guides/` covers 0.4.0 changes — verified by `make docs-check`
- [x] Rollout cookbook exists in `docs/guides/ROLLOUT_COOKBOOK.md`
- [x] Metrics documentation in `docs/features/` covers metric names, labels, meanings, and scrape config
- [x] 0.4.0 non-goals listed in CHANGELOG.md release notes section
- [x] CHANGELOG.md updated with 0.4.0 entries
- [x] `make docs-check` passes

## Testing Gates

- [x] `make test-all` passes on Ubuntu
- [x] `make test-all` passes on macOS
- [x] CI passes on NGINX 1.24.x, 1.26.x, 1.27.x
- [x] Benchmark corpus runs reproducibly (`make test-benchmark` or equivalent)
- [x] Evidence pack generated and archived
- [x] Metrics endpoint has unit test coverage (`metrics_test.c`)
- [x] Metrics endpoint has integration test coverage
- [x] Rollout strategies have e2e coverage for primary enablement paths (path-based, host-based, header-based)
- [x] Parser optimization (if included): diff coverage and regression tests pass
- [x] Property-based tests pass (Rust proptest via `cargo test` and Python Hypothesis tests in `tests/property/` and `tools/*/tests/`)

## Compatibility Gates

- [x] Default behavior (no new config) identical to 0.3.0 — verified by e2e test
- [x] All configuration changes have compatibility documentation in `docs/guides/`
- [x] Metric names and reason codes match the naming conventions — verified against `docs/project/release-gates/naming-conventions.md`
- [x] New configuration directives have default values documented in `docs/guides/`
- [x] Existing `markdown_*` directives unchanged in behavior — verified by e2e test

## Operations Gates

- [x] Operator can complete minimal first-run — verified by e2e test against `docs/guides/` install steps
- [x] Operator can execute small-scope rollout — verified by e2e test against `docs/guides/ROLLOUT_COOKBOOK.md`
- [x] Operator can observe module behavior — verified by `curl` against metrics endpoint in e2e test
- [x] Operator can observe decision reasons — verified by log inspection in e2e test
- [x] Operator can perform rollback — verified by e2e test against `docs/guides/` rollback procedure
