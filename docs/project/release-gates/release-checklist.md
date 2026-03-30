# 0.4.0 Unified Release Checklist

This is the unified release checklist for the 0.4.0 Go/No-Go review. Every item below is derived from the four release gate categories defined in the 0.4.0 Overall Scope and Release Gates spec. Each item is verifiable by a specific artifact, command, or review action — not subjective judgment.

This checklist must be completed and archived as part of the 0.4.0 release record. Any item that cannot be satisfied must be escalated to the Go/No-Go review with a documented exception, including rationale, risk assessment, and mitigation plan.

Requirements references: 10.1, 10.2, 10.3, 10.4, 10.5.

---

## Documentation Gates

- [ ] All 6 sub-specs have requirements documents — verified by `make release-gates-check`
- [ ] All 6 sub-specs have design documents — verified by `make release-gates-check`
- [ ] All new configuration directives are documented in `docs/guides/`
- [ ] Installation guide in `docs/guides/` covers 0.4.0 changes — verified by `make docs-check`
- [ ] Rollout cookbook exists in `docs/guides/ROLLOUT_COOKBOOK.md`
- [ ] Metrics documentation in `docs/features/` covers metric names, labels, meanings, and scrape config
- [ ] 0.4.0 non-goals listed in CHANGELOG.md release notes section
- [ ] CHANGELOG.md updated with 0.4.0 entries
- [ ] `make docs-check` passes

## Testing Gates

- [ ] `make test-all` passes on Ubuntu
- [ ] `make test-all` passes on macOS
- [ ] CI passes on NGINX 1.24.x, 1.26.x, 1.27.x
- [ ] Benchmark corpus runs reproducibly (`make test-benchmark` or equivalent)
- [ ] Evidence pack generated and archived
- [ ] Metrics endpoint has unit test coverage (`metrics_test.c`)
- [ ] Metrics endpoint has integration test coverage
- [ ] Rollout strategies have e2e coverage for primary enablement paths (path-based, host-based, header-based)
- [ ] Parser optimization (if included): diff coverage and regression tests pass
- [ ] Property-based tests pass (Rust proptest via `cargo test` and Python Hypothesis tests matching `test_*_pbt.py`)

## Compatibility Gates

- [ ] Default behavior (no new config) identical to 0.3.0 — verified by e2e test
- [ ] All configuration changes have compatibility documentation in `docs/guides/`
- [ ] Metric names and reason codes match the naming conventions — verified against `docs/project/release-gates/naming-conventions.md`
- [ ] New configuration directives have default values documented in `docs/guides/`
- [ ] Existing `markdown_*` directives unchanged in behavior — verified by e2e test

## Operations Gates

- [ ] Operator can complete minimal first-run — verified by e2e test against `docs/guides/` install steps
- [ ] Operator can execute small-scope rollout — verified by e2e test against `docs/guides/ROLLOUT_COOKBOOK.md`
- [ ] Operator can observe module behavior — verified by `curl` against metrics endpoint in e2e test
- [ ] Operator can observe decision reasons — verified by log inspection in e2e test
- [ ] Operator can perform rollback — verified by e2e test against `docs/guides/` rollback procedure
