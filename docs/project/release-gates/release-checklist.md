# 0.4.0 Unified Release Checklist

This is the unified release checklist for the 0.4.0 Go/No-Go review. Every item below is derived from the four release gate categories defined in the 0.4.0 Overall Scope and Release Gates spec. Each item is verifiable by a specific artifact, command, or review action — not subjective judgment.

This checklist must be completed and archived as part of the 0.4.0 release record. Any item that cannot be satisfied must be escalated to the Go/No-Go review with a documented exception, including rationale, risk assessment, and mitigation plan.

Requirements references: 10.1, 10.2, 10.3, 10.4, 10.5.

---

## Documentation Gates

- [ ] All 6 sub-specs have requirements documents — verified by `make release-gates-check`
- [ ] All 6 sub-specs have design documents — verified by `make release-gates-check`
- [ ] All new configuration directives are documented in `docs/guides/`
- [ ] Installation guide updated for 0.4.0 changes
- [ ] Rollout cookbook complete in `docs/guides/`
- [ ] Metrics documentation complete (metric names, labels, meanings, scrape config)
- [ ] 0.4.0 non-goals explicitly listed in release notes
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
- [ ] Rollout strategies tested for primary enablement paths (path-based, host-based, header-based)
- [ ] Parser optimization (if included): diff coverage and regression tests pass
- [ ] Property-based tests pass (`cargo test` with proptest)

## Compatibility Gates

- [ ] Default behavior (no new config) identical to 0.3.0 — verified by e2e test
- [ ] All configuration changes have compatibility documentation in `docs/guides/`
- [ ] Metric names and reason codes match the naming conventions — verified against `docs/project/release-gates/naming-conventions.md`
- [ ] New configuration directives have documented default values
- [ ] Existing `markdown_*` directives unchanged in behavior

## Operations Gates

- [ ] Operator can complete minimal first-run following documentation
- [ ] Operator can execute small-scope rollout following rollout cookbook
- [ ] Operator can observe module behavior through metrics endpoint
- [ ] Operator can observe decision reasons through log inspection
- [ ] Operator can perform rollback using documented procedure
