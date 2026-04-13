# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Repo-owned harness documentation under `docs/harness/`, including a central overview, core execution loop, canonical routing manifest, and initial risk-pack overlays for runtime streaming, FFI boundary work, observability, and docs/tooling drift
- Durable open-source documentation for the harness design and maintenance model:
  - `docs/architecture/HARNESS_ARCHITECTURE.md`
  - `docs/architecture/ADR/0005-repo-owned-harness.md`
  - `docs/guides/HARNESS_MAINTENANCE.md`
- Executable harness tooling:
  - `tools/harness/check_harness_sync.py`
  - `tools/harness/resolve_spec.py`
  - `tools/harness/state_store.py`
- Harness regression coverage for sync checks, spec resolution, and local state-carrier behavior
- Local maintenance skills for ongoing harness upkeep:
  - `sync-harness-rules`
  - `evolve-harness-rules`

### Changed
- `AGENTS.md` now points explicitly at the repo-owned harness entrypoints instead of leaving agent workflow guidance scattered across local-only steering docs
- `.kiro/steering/product.md`, `structure.md`, and `tech.md` were reduced to thin local adapter summaries that point back to `docs/harness/`
- `Makefile` now exposes `make harness-check` and `make harness-check-full`
- CI path filters now include `AGENTS.md` so harness contract changes trigger validation
- `tools/docs/check_docs.py` now scans maintained canonical markdown truth surfaces instead of also treating root-level scratch notes as release-facing docs
- Release-gates compatibility-matrix validation now supports both the legacy three-state-column table format and the current canonical single `Classification` column format
- `README.md`, `docs/README.md`, `docs/architecture/README.md`, and `docs/testing/README.md` now point contributors at the harness workflow and commands

### Fixed
- Restored green `make harness-check-full` validation by aligning release-gate compatibility-matrix parsing with the current 0.5.0 canonical document structure
- Prevented local scratch markdown files from causing false failures in canonical docs validation

## [0.4.1] - 2026-04-12

This patch release focuses on dependency security hygiene and release metadata alignment.

### Fixed
- Resolved RustSec advisory `RUSTSEC-2026-0097` by updating `rand` from `0.9.2` to `0.9.3` in `components/rust-converter/Cargo.lock` (dependency path: `proptest` -> `rand`)
- Restored green status for the GitHub Actions `Rust Security Audit` job (`cargo audit --deny warnings`)

### Changed
- Bumped `nginx-markdown-converter` crate version to `0.4.1` for release-tag alignment
- Updated release-facing project docs (`README.md`, `README_zh-CN.md`, `docs/project/PROJECT_STATUS.md`) to reflect the current release

## [0.4.0] - 2026-03-31

This release focuses on operational visibility, rollout safety, and conversion performance. Key themes: Prometheus metrics endpoint, unified decision reason codes, rollout and rollback operational guides, benchmark corpus for regression detection, and parser path optimizations.

### Added
- Prometheus-compatible metrics endpoint via `markdown_metrics_format prometheus` directive with text exposition format output
- `markdown_metrics_format` configuration directive (`auto|prometheus`) for opt-in Prometheus text exposition format on the existing `markdown_metrics` endpoint
- Prometheus text exposition format (`text/plain; version=0.0.4; charset=utf-8`) output on the `markdown_metrics` endpoint, selected via Accept header content negotiation when enabled
- New shared-memory counters: `requests_entered`, skip-reason breakdown (`skips.config`, `skips.method`, `skips.status`, `skips.content_type`, `skips.size`, `skips.streaming`, `skips.auth`, `skips.range`, `skips.accept`), `failopen_count`, `estimated_token_savings`
- Content negotiation for metrics endpoint: JSON, plain text, or Prometheus format based on Accept header and `markdown_metrics_format` configuration
- Prometheus metrics guide (`docs/guides/prometheus-metrics.md`) covering metric catalog, scrape configuration, rollout judgment advice, and metric stability policy
- Unified decision reason codes for logs and metrics (ELIGIBLE_CONVERTED, SKIP_METHOD, SKIP_STATUS, SKIP_CONTENT_TYPE, SKIP_SIZE, SKIP_STREAMING, SKIP_AUTH, SKIP_RANGE, SKIP_ACCEPT, SKIP_CONFIG, FAIL_OPEN, FAIL_CLOSED)
- Structured decision logging with reason codes at configurable verbosity
- Rollout cookbook (`docs/guides/ROLLOUT_COOKBOOK.md`) with selective enablement patterns (path, host, header, UA, canary)
- Rollback guide (`docs/guides/ROLLBACK_GUIDE.md`) with trigger conditions and executable procedures
- Benchmark corpus with page-type classification and unified report format
- Evidence-based comparison tooling for regression detection
- Before/after conversion examples generated from benchmark corpus
- Installation guide restructure with shortest success path and troubleshooting SOPs
- Minimal demo configuration (`examples/nginx-configs/00-minimal-demo.conf`)
- Parser path optimizations: noise region early pruning, simple structure fast path, large-response path optimization
- Property-based tests for Prometheus output format correctness (7 properties, Python Hypothesis)
- C unit tests for Prometheus renderer, content negotiation, skip-reason mapping, and snapshot collection
- Shared 0.4.0 release-gate constants module (`tools/release/release_constants.py`) to centralize sub-spec discovery keywords, P0 release-decision sub-specs, and scope-evaluation non-goals used by validation/tests
- Focused release-gate edge-case tests for checklist parsing and file read failures (`tools/release/tests/test_release_gate_checks.py`)
- Scope-evaluation regression coverage to ensure short proposal tokens are not rejected solely because they appear inside a longer non-goal phrase

### Changed
- Metrics endpoint now supports Prometheus text exposition format via content negotiation
- Decision logging uses structured format with reason codes
- Installation guide reorganized with tiered install path classification
- Release-gate naming convention validation now explicitly accepts Prometheus histogram series ending in `_seconds_bucket`, `_seconds_sum`, and `_seconds_count` while rejecting ambiguous non-histogram `_bucket|_sum|_count` suffixes
- `check_checklist_verifiability` now validates both unchecked and checked checklist entries and ignores checklist-like lines inside fenced code blocks to avoid false failures from examples
- Release checklist documentation now explicitly requires both Rust proptest and Python Hypothesis property-based test suites
- Scope-evaluation test logic now rejects proposals only when the proposal contains a non-goal phrase (unidirectional substring matching)
- Validation helpers now use regex `fullmatch` for explicit whole-string conformance checks

### Fixed
- Hardened release-gate file scanning and read paths to handle unreadable directories/files gracefully instead of aborting validation
- `_run_checks` in `validate_release_gates.py` now isolates per-check exceptions so one failing check does not suppress results from subsequent checks
- Updated release-gate docs wording and regex references to align with current validation behavior (including Markdown capitalization consistency and histogram suffix guidance)

### Deferred to 0.5.x or Later
- True streaming HTML-to-Markdown conversion
- OpenTelemetry tracing
- High-cardinality metrics
- Package manager distribution (apt, yum, brew)
- Kubernetes Ingress / Helm chart packaging
- Parser replacement or alternative DOM libraries
- Content-aware heuristic pruning

## [0.3.0] - 2026-03-19

This release introduces incremental processing for large responses, a matrix-driven release automation pipeline, third-party notice coverage checks, a performance baseline gating system, and improved HTML element handling for AI agent content preservation.

### Added

#### Incremental Processing & Large Response Handling
- `markdown_large_body_threshold` configuration directive (`off|<size>`) for routing large responses to the incremental processing path
- Threshold router in header and body filters with deferred path selection for chunked/unknown-length responses
- `IncrementalConverter` Rust API behind the `incremental` feature gate, with FFI functions: `markdown_incremental_new`, `feed`, `finalize`, `free`
- Equivalence guarantee: single `feed` + `finalize` produces identical output to the full-buffer path
- `max_buffer_size` field in `MarkdownOptions` C ABI struct, wired through from the NGINX `markdown_max_size` directive to control the incremental converter's memory ceiling
- Path hit metrics (`fullbuffer_path_hits`, `incremental_path_hits`) exposed via the metrics endpoint
- Large response design document (`LARGE_RESPONSE_DESIGN.md`) and rollout guide (`LARGE_RESPONSE_ROLLOUT.md`)
- `large-100k` and `large-5m` performance tiers in `metrics-schema.json`
- `generate_large_samples.sh` for creating large response test corpus with `--tier` filter and option validation
- `memory_observer.sh` for cross-platform memory peak sampling with process fingerprinting to prevent PID reuse corruption

#### Release Automation
- Matrix-driven release automation pipeline (`tools/release/`)
- `update_matrix.py` for managing the release matrix JSON with doc synchronization and rollback support
- `validate_doc_matrix_sync.py` for verifying documentation tables match the release matrix
- `validate_matrix_install_consistency.py` for checking install consistency across matrix entries
- `completeness_check.py` for verifying release artifact completeness
- Release binaries workflow (`.github/workflows/release-binaries.yml`) with matrix freshness and completeness checks
- Matrix update workflow (`.github/workflows/update-matrix.yml`)
- Install verification workflow (`.github/workflows/install-verify.yml`)
- `release-matrix.json` as the single source of truth for supported platform/version combinations

#### License & Compliance
- `THIRD-PARTY-NOTICES` file covering the distributed third-party dependencies used by the module and Rust converter
- `check_third_party_notices.py` plus tightened C/Rust license validation scripts to verify notice coverage during CI

#### Performance Baseline Gating
- Performance baseline gating system (`perf/`, `tools/perf/`) with dual-threshold regression detection (warning / blocking) for PR and nightly CI
- Threshold engine (`tools/perf/threshold_engine.py`) comparing current measurements against stored baselines with per-platform, per-tier, per-metric configurable thresholds
- Measurement Report and Verdict Report JSON formats conforming to `perf/metrics-schema.json`
- Local runner script (`tools/perf/run_perf_baseline.sh`) for one-command benchmark, comparison, and baseline update
- Nightly performance workflow (`.github/workflows/nightly-perf.yml`) running all tiers × 3 repeats with median aggregation
- PR smoke performance gate (`perf-smoke` job in `ci.yml`) for small + medium tiers on every Rust/perf change
- Platform-layered threshold configuration (`perf/thresholds.json`) with explicit `linux-x86_64` entry and `default` fallback
- `--platform` CLI flag on `perf_baseline` binary for deterministic platform identification in Rosetta and cross-compilation environments
- `--update-baseline` / `--tier` mutual exclusion guard in the local runner
- `PERF_GATE_SKIP=1` environment variable bypass for all performance checks
- Property-based tests for threshold classification, deviation formula, median aggregation, report roundtrip, and schema conformance
- Integration test exercising the real `perf_baseline` binary's full-run and per-tier JSON output for report completeness
- Shell integration test (`test_local_runner_output_paths.sh`) for local runner output path matrix
- Performance documentation: `PERFORMANCE_METRICS.md`, `PERFORMANCE_THRESHOLDS.md`, `PERFORMANCE_GATE.md`
- Baselines directory (`perf/baselines/`) with bootstrap flow documentation

### Changed
- Event handler attribute sanitization in `security.rs` now uses `on*` prefix matching instead of a static allowlist, following the OWASP/DOMPurify convention. This is future-proof against new event handler attributes added to the HTML spec and closes the gap where newer handlers (`onpointerdown`, `ontouchstart`, etc.) could bypass the previous static list.
- Form-related elements (`form`, `button`, `select`, `textarea`, `fieldset`, `label`, `option`, etc.) now use a strip-tag-keep-content approach instead of full removal. HTML tags are stripped but child text is preserved in Markdown output so AI agents retain meaningful content (labels, button captions, option lists). Void form controls (`input`) have descriptive text extracted from `aria-label`, `placeholder`, or `value` attributes; hidden inputs are suppressed.
- Embedded content elements (`iframe`, `object`, `embed`) now use strip-tag-keep-content instead of full removal. The `src`/`data` URL is extracted as a Markdown link (with `title` as label when available), and fallback child text is preserved. Dangerous URL schemes (`javascript:`, `data:`, etc.) are still suppressed.
- Image conversion now preserves the `title` attribute in Markdown syntax (`![alt](src "title")`). When the image URL is missing or blocked by URL sanitization, the `alt` text is emitted as plain text so AI agents do not lose the description.
- Media elements (`video`, `audio`) now have their `src` URL extracted as a Markdown link before traversing fallback children. Video `poster` thumbnails are extracted as Markdown images. Child `<source>` elements have their `src` extracted with `type` as label; `<track>` elements have their `src` extracted with `label` as link text.
- Image map `<area>` elements now have their `href` extracted as Markdown links with `alt` (or `title`) as link text.
- X-Forwarded-Host/Proto headers are no longer trusted by default for base URL construction. Added `markdown_trust_forwarded_headers on|off` directive (default: off) to prevent client-supplied header injection that could poison relative URLs in Markdown output. Enable only behind a trusted reverse proxy.
- Decompression buffer estimation now logs a warning when the estimated output exceeds 50 MB, improving operator visibility into large allocation events.
- Metrics endpoint access-control comment now documents the container/proxy limitation and provides an `allow`/`deny` configuration example for non-localhost environments.
- Synchronized `Cargo.toml` version from `0.1.0` to `0.3.0` to align with project release tags; from this release onward, `Cargo.toml` version strictly tracks the release tag
- `MarkdownOptions` C ABI now unconditionally includes `max_buffer_size` in all builds (ABI-breaking change requiring header/binding regeneration); external callers must set the field to 0 when the incremental feature is not in use
- `IncrementalConverter` carries `content_type` and `timeout` fields, using `parse_html_with_charset` and `ConversionContext` in `finalize()` to match full-buffer path behavior
- Incremental routing guarded with `#ifdef MARKDOWN_INCREMENTAL_ENABLED` in both header and body filters so metrics stay accurate when the feature is not linked
- `fullbuffer_path_hits` and `incremental_path_hits` moved to end of `ngx_http_markdown_metrics_t` to preserve shared-memory layout compatibility across hot reloads
- Path metrics guarded behind eligibility check so only eligible requests are counted
- Incremental `new`/`feed` failures routed through `ngx_http_markdown_handle_conversion_failure` for proper `conversions_failed` and failure category counter updates
- `markdown_incremental_new()` NULL return now uses `ERROR_INVALID_INPUT` instead of `ERROR_INTERNAL` with explicit `ngx_log_error` at the call site
- Extended `perf_baseline.rs` with JSON Measurement Report generation, `--single` / `--json-output` / `--platform` CLI arguments, `large-1m` canonical tier naming, and per-sample stage breakdown
- Added `serde` and `serde_json` dependencies to `components/rust-converter`
- Added `.hypothesis/` to `.gitignore`
- Hardened installer verification and JSON output (`tools/install.sh`): proper JSON escaping, jq-based construction with fallback, correct status based on nginx test result
- Hardened release tooling: path injection prevention, ReDoS-safe regex, empty matrix detection, support tier normalization, input validation, and error handling

### Fixed
- Double-free risk in incremental API: `finalize` always consumes handle regardless of success/failure (CWE-415)
- `IncrementalConverter::finalize()` now rejects empty buffer with `InvalidInput`, matching the full-buffer path's `parse_html_with_charset()` behavior
- Conditional-request conversions (`If-None-Match` handler) now respect `ctx->processing_path` instead of always using full-buffer conversion
- Memory observer PID reuse corruption: process fingerprinting via `/proc/<pid>/stat` starttime (Linux) or `ps -o lstart=` (macOS) with TOCTOU window closure

### Dependencies
- Bump `actions/download-artifact` from 8.0.0 to 8.0.1
- Bump `dorny/paths-filter` from 4.0.0 to 4.0.1
- Bump `softprops/action-gh-release` from 2.5.0 to 2.6.1

## [0.2.2] - 2026-03-15

### Added
- Canonical native E2E entrypoints under `tools/e2e/`, including a focused proxy/TLS backend validation script and a thin orchestration wrapper for `make test-e2e`
- Shared native-build helper logic for runtime verification scripts under `tools/lib/nginx_markdown_native_build.sh`
- `cargo-fuzz` targets for parser, FFI, and security-validator paths, plus a nightly GitHub Actions workflow
- Non-blocking Darwin/macOS smoke workflow covering native Rust build, real-nginx IMS validation, and chunked runtime smoke
- Additional performance artifact sampling for the medium front-matter path in the Rust benchmark harness

### Changed
- Split the NGINX module implementation into focused config, request-state, payload, conversion, lifecycle, and metrics helper units while keeping directive behavior stable
- Moved shared metrics collection to a cross-worker shared-memory model and expanded metrics validation coverage
- Split the Rust converter internals into dedicated `converter/`, `ffi/`, and `metadata/` submodules without changing the exported ABI
- Reworked `make test-e2e` to delegate to the canonical native E2E suite instead of maintaining a second inline runner
- Updated Linux CI runtime regressions to retain and reuse a validated IMS runtime across delegated native checks
- Expanded C and Rust inline documentation around Accept-header parsing and DOM traversal paths, including complexity and borrow-context rationale for high-branching logic
- Continued documentation pass across decompression/buffering and Rust FFI/parser hot paths, clarifying allocation bounds, amortized complexity, timeout checkpoints, and zero-copy charset decoding behavior
- Completed a broader doc pass: filled missing Rust function docstrings across converter/metadata/FFI modules and expanded C header comments for config/lifecycle entrypoints and filter-chain responsibilities
- Performed a consistency audit to align comments with implementation: filled remaining Rust function docs and tightened C helper/header comments so interface intent, complexity notes, and behavior descriptions stay accurate

### Fixed
- Hardened native E2E/runtime scripts so reusable `NGINX_BIN` paths are only reused when the runtime layout and module support are actually compatible
- Aligned Darwin native builds around a consistent `MACOSX_DEPLOYMENT_TARGET` to avoid mismatched static-library link warnings in verification flows
- Restored Darwin Rust target detection in the shared native-build helper after Linux libc-variant matching expanded the host key used by macOS smoke validation
- Fixed `markdown_stream_types` content-type validation so stricter `-Werror` official NGINX builds do not fail on `ngx_strchr()` pointer signedness/type checks
- Restored the Rust perf baseline artifact path resolution used by GitHub Actions so the benchmark job can locate repository corpus fixtures again
- Cleaned up SonarCloud maintainability findings in native E2E/integration scripts and top-level NGINX implementation include ordering without regressing native builds
- Synchronized architecture, testing, operations, security, status, and top-level README documentation with the refactored implementation and validation surface

## [0.2.1] - 2026-03-11

### Added
- CI jobs for clang compiler and AddressSanitizer/UndefinedBehaviorSanitizer smoke tests (`unit-clang-smoke`, `unit-sanitize-smoke`)
- SonarCloud Quality Gate Status badge in both English and Chinese READMEs

### Changed
- Updated documentation to reflect correct Rust minimum version (1.85.0+ for edition 2024)
- Corrected NGINX minimum version references across all documentation (1.24.0+)
- Added missing feature documentation references (CONTENT_NEGOTIATION.md, CACHE_AWARE_RESPONSES.md) to feature index
- Updated component README source layouts to match current file structure
- Removed placeholder migration notes for non-existent future versions from Operations guide
- Corrected `make test-nginx-e2e` references to actual `make test-e2e` target
- Added clang and sanitizer smoke test targets to testing documentation

### Fixed
- Rust minimum version requirement in all documentation (was 1.70.0+, now 1.85.0+ to match `edition = "2024"`)
- NGINX minimum version in CONTRIBUTING.md (was 1.18.0, now 1.24.0)

## [0.2.0] - 2026-03-06

This release expands runtime configurability, tightens module internals and validation infrastructure, and refreshes the documentation and release workflow around the current implementation.

### Added
- Variable-driven `markdown_filter` support using NGINX variables and complex values
- Phase-consistent `markdown_filter` decision caching so header and body processing do not re-evaluate divergent values mid-request
- Standalone runtime coverage for `markdown_filter` parsing, merge behavior, and cached decision handling
- `SKIP_ROOT_CHECK=1` support in `tools/install.sh` for Docker-oriented install flows
- `tools/build_release/Dockerfile.install-example` as a reference image for install-script-based packaging
- Simplified Chinese top-level README (`README_zh-CN.md`)
- Deployment examples guide under `docs/guides/DEPLOYMENT_EXAMPLES.md`
- Architecture documentation index and runtime references under `docs/architecture/`

### Changed
- Refactored the NGINX module's filter, conditional, auth, and header-handling paths into smaller helpers with a shared header-manipulation implementation used by production code and standalone tests
- Tightened authenticated-request cache-control rewriting so public cache directives are upgraded to private while preserving stronger directives such as `no-store`
- Reworked E2E proxy-chain validation to run the backend over TLS, and hardened integration/E2E runner scripts around that flow
- Hardened CI workflows, release build images, QA scripts, and Docker build context handling
- Refreshed the top-level README, component READMEs, guides, feature notes, testing docs, and FAQ to match the current runtime behavior and supported workflows
- Added clearer architecture, repository-structure, and request-path references so operational docs, implementation docs, and release notes describe the same system boundaries

### Fixed
- Normalized `markdown_filter` runtime parsing and helper behavior so variable-driven enablement resolves consistently across phases
- Restored const compatibility for stricter `-Werror` builds and addressed remaining Sonar/security-hotspot cleanup in core code and unit tests
- Hardened path and URL-scheme parsing utilities, bounded string handling in tests, and related helper code used by validation tooling
- Corrected installation and documentation details such as repository URLs, generated header paths, metrics fields, compression rollout guidance, and outdated placeholders
- Refreshed corpus fixtures and validation assets for better semantic coverage in tables, images, malformed HTML, and mixed-entity cases

## [0.1.0] - 2026-03-02

### Added
- Initial release of NGINX Markdown for Agents
- HTML-to-Markdown conversion via HTTP content negotiation
- Rust-based converter with memory safety guarantees
- NGINX filter module with C integration
- FFI bridge between Rust and C components
- Content negotiation based on `Accept: text/markdown` header
- Deterministic output for consistent ETag generation
- Security protections (XSS, XXE, SSRF prevention)
- Token estimation for LLM context management
- YAML front matter generation
- Charset detection and entity decoding
- Automatic upstream decompression (gzip, brotli, deflate)
- Cooperative timeout mechanism
- Configurable resource limits (size, timeout)
- Fail-open and fail-closed strategies
- Metrics collection and endpoint
- Conditional request support (ETag, If-None-Match)
- Cache-friendly Vary header handling
- Authentication-aware caching (Cache-Control: private)
- Comprehensive test suite (unit, integration, E2E)
- Property-based tests for core invariants
- Complete documentation set:
  - Build instructions
  - Installation guide
  - Configuration reference
  - Operations guide
  - Feature documentation
  - Testing documentation
- Comprehensive documentation improvements
- Configuration example templates in `examples/nginx-configs/`
- Quick verification section in README
- Common issues quick reference guide
- Enhanced Rust converter README with usage examples
- CONTRIBUTING.md with development guidelines
- This CHANGELOG.md file

### Changed
- Improved README structure for better user experience
- Enhanced documentation navigation and cross-references
- Standardized project license declarations to BSD-2-Clause across repository docs and component metadata
- Aligned GitHub repository metadata baseline (description/topics/labels) for issue triage and discoverability
- Installer now auto-wires runtime configuration by generating module loader and markdown-enable snippets, with `nginx -t` validation and manual fallback guidance only when needed
- Installer now supports both absolute and relative module load styles (for example `load_module /usr/lib/nginx/modules/...` and `load_module modules/...`) based on detected NGINX build metadata

### Configuration Directives
- `markdown_filter` - Enable/disable conversion
- `markdown_max_size` - Maximum response size limit
- `markdown_timeout` - Conversion timeout
- `markdown_on_error` - Failure strategy (pass/reject)
- `markdown_flavor` - Markdown flavor (commonmark/gfm)
- `markdown_token_estimate` - Token count header
- `markdown_front_matter` - YAML front matter generation
- `markdown_on_wildcard` - Wildcard Accept header handling
- `markdown_auth_policy` - Authentication policy
- `markdown_auth_cookies` - Authentication cookie patterns
- `markdown_etag` - ETag generation
- `markdown_conditional_requests` - Conditional request support
- `markdown_log_verbosity` - Module log verbosity
- `markdown_buffer_chunked` - Chunked response buffering
- `markdown_stream_types` - Streaming content type exclusions
- `markdown_metrics` - Metrics endpoint

### Supported Platforms
- macOS (Apple Silicon and Intel)
- Linux (x86_64 and aarch64)
- NGINX 1.24.0+
- Rust 1.85.0+

### Known Limitations
- Full buffering required (no streaming conversion)
- Requires uncompressed or automatically decompressed HTML input
- Some complex HTML structures may not convert perfectly
- Performance overhead for large documents

## Version History

### Version Numbering

This project uses Semantic Versioning:
- MAJOR version for incompatible API changes
- MINOR version for backwards-compatible functionality additions
- PATCH version for backwards-compatible bug fixes

### Upgrade Notes

#### Upgrading to 0.4.1

- This is a patch release with no directive or runtime behavior changes.
- If you vendor `Cargo.lock`, refresh it to pick up the `rand` `0.9.3` security fix.

#### Upgrading to 0.4.0

- New `markdown_metrics_format` directive defaults to `auto` (JSON), preserving 0.3.0 behavior. Set to `prometheus` to enable Prometheus text exposition format.
- All new configuration directives have safe defaults that preserve 0.3.0 behavior. No configuration changes are required for upgrading.
- Parser path optimizations are transparent — output is equivalent to 0.3.0 for all inputs.
- Decision reason codes are now included in logs at `info` verbosity and above. Adjust `markdown_log_verbosity` if needed.

#### Upgrading to 0.3.0

- `MarkdownOptions` C ABI has changed: `max_buffer_size` is now unconditionally present. External callers must regenerate bindings and set the field to 0 when the incremental feature is not in use.
- `fullbuffer_path_hits` and `incremental_path_hits` have been moved to the end of `ngx_http_markdown_metrics_t`. If you use shared-memory metrics, a graceful reload is sufficient; no data migration is needed.
- The `incremental` feature is off by default. Enable it with `--features incremental` when building the Rust converter to use the new `markdown_large_body_threshold` directive.
- `X-Forwarded-Host` and `X-Forwarded-Proto` headers are no longer trusted by default for base URL construction. If NGINX sits behind a trusted reverse proxy that sets these headers, add `markdown_trust_forwarded_headers on;` to restore the previous behavior. Without this directive, only the NGINX request schema and server header are used.

#### Upgrading to 0.2.0

No public directive renames are introduced in this release. If you relied on older documentation, review the updated guides for clarified installation paths, compression rollout guidance, metrics fields, and architecture references.

#### Upgrading to 0.1.0

This is the initial release. No upgrade path from previous versions.

### Deprecation Notices

None at this time.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on contributing to this project.

## Support

For issues, questions, or feature requests, please use the repository's issue tracker.

## License

This project is licensed under the BSD 2-Clause "Simplified" License (`BSD-2-Clause`).
