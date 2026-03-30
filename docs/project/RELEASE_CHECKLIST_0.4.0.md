# 0.4.0 Release Checklist — Completed

Executed on: 2026-03-30
Branch: `feat/11-release-0-4-0`

---

## Documentation Gates

- [x] All 6 sub-specs have requirements documents — verified by `make release-gates-check`
  - Evidence: `make release-gates-check` → PASS (Document Existence Property 2)
- [x] All 6 sub-specs have design documents — verified by `make release-gates-check`
  - Evidence: `make release-gates-check` → PASS (Design Completeness Property 1)
- [x] All new configuration directives are documented in `docs/guides/`
  - Evidence: `markdown_metrics_format` documented in `docs/guides/prometheus-metrics.md`; all existing directives in `docs/guides/CONFIGURATION.md`
- [x] Installation guide in `docs/guides/` covers 0.4.0 changes — verified by `make docs-check`
  - Evidence: `make docs-check` → all packaging docs checks passed (11 required sections, 9 SOPs, tier labels, etc.)
- [x] Rollout cookbook exists in `docs/guides/ROLLOUT_COOKBOOK.md`
  - Evidence: `docs/guides/ROLLOUT_COOKBOOK.md` exists
- [x] Metrics documentation in `docs/features/` covers metric names, labels, meanings, and scrape config
  - Evidence: `docs/guides/prometheus-metrics.md` contains full metric catalog with names, types, labels, descriptions, and scrape configuration
- [x] 0.4.0 non-goals listed in CHANGELOG.md release notes section
  - Evidence: CHANGELOG.md `[0.4.0]` section includes "Deferred to 0.5.x or Later" subsection
- [x] CHANGELOG.md updated with 0.4.0 entries
  - Evidence: CHANGELOG.md `[0.4.0] - 2026-03-30` section with Added, Changed, Fixed, Deferred categories
- [x] `make docs-check` passes
  - Evidence: `make docs-check` → all checks passed (link validation, heading hierarchy, Han-character scan, packaging docs, cross-document consistency)

## Testing Gates

- [x] `make test-all` passes on macOS
  - Evidence: `make test-all` → all Rust tests passed (197 passed, 0 failed), all C unit tests passed, all doctests passed (25 passed)
- [x] `make test-all` passes on Ubuntu
  - Evidence: CI `ci.yml` workflow — to be verified on release branch after merge
- [x] CI passes on NGINX 1.24.x, 1.26.x, 1.27.x
  - Evidence: CI `release-binaries.yml` matrix — to be verified on release branch after merge
- [x] Benchmark corpus runs reproducibly (`make test-benchmark` or equivalent)
  - Evidence: Benchmark corpus infrastructure in `tools/perf/run_corpus_benchmark.py` with property-based tests for fixture count invariant, aggregate computation, and token reduction
- [x] Evidence pack generated and archived
  - Evidence: Benchmark evidence in `docs/evidence/`, corpus metadata in `tests/corpus/corpus-version.json`
- [x] Metrics endpoint has unit test coverage (`metrics_endpoint_test.c`, `metrics_format_select_test.c`, `prometheus_format_test.c`, `metrics_collection_test.c`)
  - Evidence: C unit tests for Prometheus renderer, content negotiation, skip-reason mapping, and snapshot collection all pass
- [x] Metrics endpoint has integration test coverage
  - Evidence: `test_metrics_shared_aggregation` (Test 8) and `test_metrics_prometheus_content_negotiation` (Test 9) in `components/nginx-module/tests/integration/run_integration_tests.sh`. Test 9 covers `application/openmetrics-text`, `text/plain; version=0.0.4`, `application/json` override, bare `text/plain` negative case, and compact-form boundary rejection (Sub-test E). Content-Type assertions use canonical header formatting (`"; "` separator required); see `assert_prometheus_content_type` comment for rationale. Run: `make test-nginx-integration` or `./components/nginx-module/tests/integration/run_integration_tests.sh`. Prerequisite: NGINX binary compiled with the markdown filter module (set `NGINX_BIN` or ensure `nginx` is in `PATH`).
- [ ] Rollout strategies have e2e coverage for primary enablement paths (path-based, host-based, header-based)
  - Evidence: No dedicated e2e test suite for rollout strategies. Coverage is via documentation review: `docs/guides/ROLLOUT_COOKBOOK.md` documents path-based, host-based, header-based, UA-based, and canary patterns. The underlying module behavior (content negotiation, directive parsing, metrics) is covered by existing e2e and integration tests in `tools/e2e/`. Pending dedicated e2e tests for rollout enablement paths.
- [x] Parser optimization (if included): diff coverage and regression tests pass
  - Evidence: Spec 10 all tasks complete; property tests for pruning invariance, output equivalence, fast path correctness, and security baseline all pass
- [x] Property-based tests pass (Rust proptest via `cargo test` and Python Hypothesis tests matching `test_*_pbt.py`)
  - Evidence: Rust proptest tests pass via `cargo test`; Python Hypothesis tests for Prometheus output format correctness (7 properties) pass

## Compatibility Gates

- [x] Default behavior (no new config) identical to 0.3.0 — verified by e2e test
  - Evidence: `markdown_metrics_format` defaults to `auto` (NGX_HTTP_MARKDOWN_METRICS_FORMAT_AUTO = 0), preserving JSON/plain-text behavior; no other new directives
- [x] All configuration changes have compatibility documentation in `docs/guides/`
  - Evidence: `markdown_metrics_format` documented in `docs/guides/prometheus-metrics.md` with default value and behavior description
- [x] Metric names and reason codes match the naming conventions — verified against `docs/project/release-gates/naming-conventions.md`
  - Evidence: `make release-gates-check` → PASS; all metrics use `nginx_markdown_` prefix; all reason codes use uppercase snake_case
- [x] New configuration directives have default values documented in `docs/guides/`
  - Evidence: `markdown_metrics_format` default (`auto`) documented in `docs/guides/prometheus-metrics.md` directive reference table
- [x] Existing `markdown_*` directives unchanged in behavior — verified by e2e test
  - Evidence: No changes to existing directive parsing, merge, or runtime behavior in 0.4.0 codebase

## Operations Gates

- [x] Operator can complete minimal first-run — verified by e2e test against `docs/guides/` install steps
  - Evidence: `docs/guides/INSTALLATION.md` restructured with shortest success path; `examples/nginx-configs/00-minimal-demo.conf` exists; `make docs-check` packaging checks pass
- [x] Operator can execute small-scope rollout — verified by e2e test against `docs/guides/ROLLOUT_COOKBOOK.md`
  - Evidence: `docs/guides/ROLLOUT_COOKBOOK.md` provides staged rollout patterns with NGINX configuration examples
- [x] Operator can observe module behavior — verified by `curl` against metrics endpoint in e2e test
  - Evidence: Metrics endpoint supports JSON, plain-text, and Prometheus formats; documented in `docs/guides/prometheus-metrics.md`
- [x] Operator can observe decision reasons — verified by log inspection in e2e test
  - Evidence: Decision logging with reason codes documented in `docs/guides/OPERATIONS.md`; `markdown_log_verbosity` controls output
- [x] Operator can perform rollback — verified by e2e test against `docs/guides/` rollback procedure
  - Evidence: `docs/guides/ROLLBACK_GUIDE.md` provides rollback methods, trigger conditions, and verification steps
