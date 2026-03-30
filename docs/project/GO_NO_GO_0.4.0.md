# Go/No-Go Decision Record — 0.4.0

Requirements references: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6

## Decision Record

- Date: 2026-03-30
- Decision: Conditional Go
- Rationale: All P0 and P1 sub-specs have completed their implementation tasks. Local verification passes (`make test-all` on macOS, `make docs-check`, `make release-gates-check`). CI verification on Ubuntu and multi-NGINX-version matrix is pending — the Go decision is conditional on those CI runs passing against the release candidate commit. The tag must not be created until CI evidence is recorded.

## P0 Sub-Spec Status

| Sub-Spec | DoD Status | Notes |
|----------|-----------|-------|
| overall-scope-release-gates (5) | ✅ | All governance artifacts created: naming conventions, DoD template, release checklist, risk register template, test matrix, boundary description template, Go/No-Go template, scope evaluation process. Validation scripts implemented and passing. |
| rollout-safety-controlled-enablement (6) | ✅ | Reason code lookup and decision log emission implemented. Rollout cookbook and rollback guide created. Decision chain reference documentation complete. All property tests pass. |
| prometheus-module-metrics (7) | ✅ | Shared-memory metrics extended with new counters. `markdown_metrics_format` directive implemented. Prometheus text renderer with content negotiation. 7 property-based tests (Python Hypothesis) pass. C unit tests pass. |
| benchmark-corpus-and-evidence (8) | ✅ | Corpus metadata infrastructure with fixture sidecars. Benchmark script with per-fixture conversion, token reduction estimation, and unified report generation. Property tests for fixture count, aggregate computation, and token reduction pass. |
| packaging-and-first-run (9) | ✅ | Minimal demo configuration created. Installation guide restructured with 11 sections and 9 troubleshooting SOPs. README Quick Start updated. Documentation validation scripts integrated into `make docs-check`. |

## P1 Sub-Spec Status

| Sub-Spec | DoD Status | Decision |
|----------|-----------|----------|
| parser-path-optimization (10) | ✅ | Include — All 6 tasks complete: noise region early pruning, simple structure fast path, large-response path optimization. Property tests for pruning invariance, output equivalence, fast path correctness, security baseline, and timeout behavior all pass. |

## Release Gate Summary

| Gate Category | Status | Exceptions |
|--------------|--------|------------|
| Documentation | ✅ | None — All 6 sub-specs have requirements and design documents. All new directives documented. `make docs-check` passes. CHANGELOG updated with 0.4.0 entries including deferred items. |
| Testing | ⏳ | CI verification pending — `make test-all` passes on macOS; Ubuntu and multi-NGINX-version matrix require GitHub Actions runs on the release branch. See Blocking Items below. |
| Compatibility | ✅ | None — `markdown_metrics_format` defaults to `auto`, preserving 0.3.0 behavior. No existing directive behavior changed. `make release-gates-check` passes. |
| Operations | ✅ | None — Installation guide restructured. Rollout cookbook and rollback guide exist. Metrics endpoint supports JSON, plain-text, and Prometheus formats. Decision logging with reason codes documented. |

## Blocking Items (CI Verification)

The following CI workflows must pass against the release candidate commit before the Conditional Go becomes a final Go:

| Workflow | Status | Run ID | Commit SHA |
|----------|--------|--------|------------|
| `ci.yml` (`make test-all` on Ubuntu) | Pending | — | — |
| `nightly-perf.yml` | Pending | — | — |
| `release-binaries.yml` | Pending | — | — |
| `install-verify.yml` | Pending | — | — |

Once all four workflows pass, update this table with run IDs and commit SHAs, change the Decision to "Go", and proceed with tag creation.

## Exceptions

| # | Gate Item | Exception Rationale | Risk Assessment | Mitigation |
|---|----------|-------------------|-----------------|------------|
| — | No unresolved exceptions | — | — | — |

## DoD Evaluation Summary

### Checkpoint Coverage

| Checkpoint | Spec 5 | Spec 6 | Spec 7 | Spec 8 | Spec 9 | Spec 10 |
|-----------|--------|--------|--------|--------|--------|---------|
| Functionally correct | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Observable | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Rollback-safe | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Documented | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Auditable | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Compatible | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

Note: Formal DoD evaluation tables have not been recorded as separate artifacts in each sub-spec's completion file. The checkpoint coverage above is assessed based on task completion status, test results, and artifact review. The `make release-gates-check` DoD validation reports "no DoD evaluation tables found — passing vacuously" because the sub-specs did not produce standalone DoD table artifacts.

### Evidence References

- Spec 5: Governance artifacts in `docs/project/release-gates/`; validation script `tools/release/validate_release_gates.py`
- Spec 6: Reason codes in `ngx_http_markdown_reason.c`; rollout cookbook in `docs/guides/ROLLOUT_COOKBOOK.md`; rollback guide in `docs/guides/ROLLBACK_GUIDE.md`; decision chain docs in `docs/features/DECISION_CHAIN.md`
- Spec 7: Prometheus renderer in `ngx_http_markdown_prometheus_impl.h`; metrics guide in `docs/guides/prometheus-metrics.md`; 7 Hypothesis property tests
- Spec 8: Corpus metadata in `tests/corpus/`; benchmark script in `tools/perf/run_corpus_benchmark.py`; evidence in `docs/evidence/`
- Spec 9: Demo config in `examples/nginx-configs/00-minimal-demo.conf`; installation guide in `docs/guides/INSTALLATION.md`; validation scripts in `tools/docs/`
- Spec 10: Pruning module in `converter/pruning.rs`; fast path in `converter/fast_path.rs`; large response in `converter/large_response.rs`; feature docs in `docs/features/parser-path-optimization.md`

## Risk Register Review

The following High-likelihood or High-impact risks were identified across sub-spec risk registers. All have documented, actionable mitigations.

### Spec 8 (benchmark-corpus-and-evidence)

| Risk | Likelihood | Impact | Mitigation | Status |
|------|-----------|--------|------------|--------|
| R2: Token reduction estimates misinterpreted as precise billing data | Medium | High | Label all estimates clearly in reports and documentation, include explicit disclaimers | Mitigated — reports and docs include disclaimers |
| R3: Benchmark latency measurements are noisy across CI environments | High | Medium | Use percentile-based metrics (P50/P95/P99), integrate with existing threshold engine, document expected variance | Mitigated — threshold engine supports configurable tolerance |
| R4: Report schema changes break comparison across versions | Low | High | Version the schema, require major version bump for breaking changes, validate reports against schema | Mitigated — schema is versioned |

### Spec 10 (parser-path-optimization)

| Risk | Likelihood | Impact | Mitigation | Status |
|------|-----------|--------|------------|--------|
| R1: Early pruning of nav/footer/aside causes false kills on pages with primary content in these elements | Medium | High | Tag-name-only matching (no heuristics), validate against benchmark corpus, tricky-case fixtures, fallback to Normal_Path | Mitigated — property tests and corpus validation pass |
| R3: Fast path qualification criteria too broad, causing incorrect output | Low | High | Property-based tests verify output equivalence, fast path falls back to Normal_Path, corpus diff catches regressions | Mitigated — property tests pass |
| R4: Optimization work consumes too much time and threatens 0.4.0 release | Medium | High | P1 spec — defer entirely if it risks the release; P0 specs take priority | Resolved — all P1 tasks complete without impacting P0 timeline |
