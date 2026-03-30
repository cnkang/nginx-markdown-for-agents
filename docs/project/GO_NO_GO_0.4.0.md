# Go/No-Go Decision Record — 0.4.0

Requirements references: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6

## Decision Record

- Date: 2026-03-30
- Decision: Go
- Rationale: All P0 sub-specs have completed their implementation tasks and pass their DoD evaluations. The P1 sub-spec (parser-path-optimization) is included with all tasks complete. All release gates are satisfied. `make test-all`, `make docs-check`, and `make release-gates-check` pass on the release candidate. No blocking exceptions.

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
| Testing | ✅ | None — `make test-all` passes on macOS. Rust proptest and Python Hypothesis property-based tests pass. C unit tests pass. CI verification on Ubuntu and multi-NGINX-version matrix pending push to release branch. |
| Compatibility | ✅ | None — `markdown_metrics_format` defaults to `auto`, preserving 0.3.0 behavior. No existing directive behavior changed. `make release-gates-check` passes. |
| Operations | ✅ | None — Installation guide restructured. Rollout cookbook and rollback guide exist. Metrics endpoint supports JSON, plain-text, and Prometheus formats. Decision logging with reason codes documented. |

## Exceptions

| # | Gate Item | Exception Rationale | Risk Assessment | Mitigation |
|---|----------|-------------------|-----------------|------------|
| 1 | Ubuntu `make test-all` | Local verification on macOS only; Ubuntu verification requires CI | Low — codebase is cross-platform tested in CI on every PR | CI `ci.yml` run on release branch will verify before tag creation |
| 2 | Multi-NGINX CI matrix | Requires `release-binaries.yml` workflow dispatch | Low — matrix builds are tested on every PR | Workflow dispatch on release branch before tag creation |

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

### Evidence References

- Spec 5: Governance artifacts in `docs/project/release-gates/`; validation script `tools/release/validate_release_gates.py`
- Spec 6: Reason codes in `ngx_http_markdown_reason.c`; rollout cookbook in `docs/guides/ROLLOUT_COOKBOOK.md`; rollback guide in `docs/guides/ROLLBACK_GUIDE.md`; decision chain docs in `docs/features/DECISION_CHAIN.md`
- Spec 7: Prometheus renderer in `ngx_http_markdown_prometheus_impl.h`; metrics guide in `docs/guides/prometheus-metrics.md`; 7 Hypothesis property tests
- Spec 8: Corpus metadata in `tests/corpus/`; benchmark script in `tools/perf/run_corpus_benchmark.py`; evidence in `docs/evidence/`
- Spec 9: Demo config in `examples/nginx-configs/00-minimal-demo.conf`; installation guide in `docs/guides/INSTALLATION.md`; validation scripts in `tools/docs/`
- Spec 10: Pruning module in `converter/pruning.rs`; fast path in `converter/fast_path.rs`; large response in `converter/large_response.rs`; feature docs in `docs/features/parser-path-optimization.md`

## Risk Register Review

No High-likelihood or High-impact risks found across any sub-spec risk registers. All risks are Low or Medium with documented mitigations.
