# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0] - 2026-05-25

P0 runtime correctness fixes, Rust-first architecture modules, independent
decompression budget, Accept header negotiation, parse timeout/budget,
FFI ABI layout verification, DEB/RPM packaging, Kubernetes deployment,
dynconf dry-run/rollback, and runtime diagnostics.

### Added

- **P0 Runtime Correctness**
  - Full-buffer pending chain on NGX_AGAIN with resume (Rule 1).
  - `failopen_completed` flag prevents duplicate finalize (Rule 38).
  - Safe output ordering: alloc→copy→chain→headers→body filter.
  - `markdown_decompress_max_size` directive: independent decompression
    budget decoupled from `max_size` (default: inherits max_size).
  - Bounded decompression with budget enforcement: output terminated
    when decompressed size exceeds the configured budget.
  - `markdown_parse_timeout` directive (default: 30s).
  - `markdown_parser_budget` directive (default: 64m).
  - Rust `DecompressionBudgetExceeded` error (code 9), `ParseTimeout`
    (code 10), `ParseBudgetExceeded` (code 11) with FFI mapping.
  - C-side error classification updated for new error codes.

- **Rust-first Architecture**
  - `negotiator` module: RFC 7231 Accept header q-value negotiation
    with `FFIAcceptResult` struct and `markdown_negotiate_accept` FFI
    export. Accept negotiation fully in Rust. 22 unit tests.
  - `conditional` module: If-None-Match / If-Modified-Since conditional
    request and ETag handling in Rust. 15 unit tests.
  - `decision` module: pure decision engine with reason codes
    (CONVERT, SKIP_ACCEPT, SKIP_NO_ACCEPT, etc.). 11 unit tests.
  - `header_plan` module: declarative header mutation plan for atomic
    application. 5 unit tests.
  - `security` module extensions: `url_contains_control_chars`,
    `validate_link_url`, `parse_forwarded_headers`,
    `escape_link_label`, `escape_link_destination`. 10 unit tests.
  - `docs/architecture/FFI_MIGRATION_CONTRACT.md`: FFI function/struct
    registry, error code registry, migration priority, compatibility rules.

- **FFI ABI Verification**
  - Rust layout tests: `MarkdownResult` size/offset validation,
    `FFIAcceptResult` layout, error code uniqueness, reason code uniqueness.
  - C `static_assert` for critical struct sizeof and offsetof.
  - `check-headers` integrated into `harness-check-full`.

- **DEB/RPM Packaging Pipeline**
  - `packaging/debian/` directory with control, postinst, postrm, conffiles.
  - `packaging/rpm/` directory with SPEC file and build scripts.
  - NGINX ABI dependency declarations for both package formats.
  - GPG signing pipeline for packages and repository metadata.
  - APT repository structure (`dists/<codename>/...`).
  - YUM repository structure (`repodata/repomd.xml`).

- **Kubernetes Deployment Examples**
  - `examples/kubernetes/Dockerfile.ingress`: parameterized Ingress
    Controller image build.
  - `examples/kubernetes/manifest/`: Deployment, ConfigMap, Service,
    Ingress manifests.
  - `charts/nginx-markdown/` Helm chart updates.
  - K8s smoke and E2E test scripts.

- **Runtime Diagnostics Endpoint**
  - `/nginx-markdown/diagnostics` endpoint with config snapshot,
    recent decisions, and metrics snapshot.
  - `markdown_diagnostics` directive (on/off + allow CIDR).

- **Dynconf Dry-Run and Last-Known-Good Rollback**
  - `markdown_dynconf_dry_run` directive: validates configuration
    changes without applying them.
  - Last-known-good (LKG) snapshot preserved on successful reload.
  - Manual rollback restores LKG as active configuration.
  - `applied_mtime` only updates on successful reload.

- **Documentation**
  - `docs/guides/PACKAGE_DISTRIBUTION.md`: distribution strategy.
  - `docs/guides/PACKAGE_INSTALLATION.md`: DEB/RPM install guide.
  - `docs/guides/KUBERNETES_DEPLOYMENT.md`: K8s reference examples.
  - `docs/guides/F5_INGRESS_FEASIBILITY.md`: F5 Ingress feasibility.
  - `docs/guides/DYNAMIC_CONFIG.md`: dynconf operational guide with
    LKG/rollback semantics and dry-run workflow.
  - `docs/FAQ.md`: decompression architecture recommendations.
  - `packaging/matrix.yaml`: build matrix definition.
  - `examples/kubernetes/manifest/markdown-configmap.yaml`.
  - `docs/guides/CONFIGURATION.md`: new directive documentation
    (`markdown_dynconf_dry_run`, parse timeout/budget directives).
  - `docs/guides/prometheus-metrics.md`: v0.7.0 metrics
    (`delivery_total`, `decision_total`, `decompression_budget_exceeded_total`,
    `parse_timeouts_total`, `parse_budget_exceeded_total`).

### Changed

- Decompression budget now uses `conf->decompress_max_size` instead of
  `conf->max_size` in `calc_output_size`, `decompress_gzip`,
  `decompress_brotli`, and streaming `decomp_create`.
- `harness-check-full` now includes `check-headers`.
- Cargo.toml version bumped to 0.7.0.
- Helm chart version and appVersion bumped to 0.7.0.
- SECURITY.md supported version updated to 0.7.x.
- Resolve actionable SonarQube findings in C unit tests: const-qualify
  read-only locals (S5350), move loop variables into for-statements (S5955),
  split multi-variable declarations (S1659), add NULL guard to
  `ngx_strncasecmp` stub (S3807).
- Production-path unit tests for Accept negotiation, auth/cache-control, and
  conditional request handling (`accept_production_test.c`,
  `auth_production_test.c`, `conditional_production_test.c`).
- Bump `serde_json` from 1.0.149 to 1.0.150.
- Bump `openssl` from 0.10.79 to 0.10.80.
- Bump `taiki-e/install-action` from 2.75.30 to 2.79.7.
- Bump `SonarSource/sonarqube-scan-action` from 8.0.0 to 8.1.0.

## [0.6.3] - 2026-05-14

This release closes the Rust-first E2E migration scope for the first scenario
batch, aligns harness governance with migrated execution surfaces, removes
stale Python E2E spec files, and includes the final mainline security/test
hardening fixes before the v0.6.3 tag.

### Added
- Rust E2E harness migration artifacts and documentation for 0.6.3:
  - `tools/e2e-harness/` scenario modules for `accept-negotiation`,
    `metrics-endpoint`, `conditional-requests`, `auth-cache`, and
    `status-codes`.
  - `docs/project/0.6.3-test-surface-audit.md`,
    `docs/project/0.6.3-e2e-parity.md`,
    `docs/testing/C_TEST_BOUNDARY.md`,
    `docs/project/release-notes-0-6-3.md`,
    `docs/architecture/ADR/0009-rust-first-e2e-test-architecture.md`.
- Harness risk pack for E2E migration:
  `docs/harness/risk-packs/e2e-migration.md`.
- Harness validation contract for Rust E2E migration policy in
  `tools/harness/check_harness_sync.py` (Rust harness binary contract, migrated
  wrapper checks, and removed Python E2E guard).

### Changed
- `make test-e2e` canonical suite now delegates migrated scenarios through
  `e2e-harness` while keeping deferred scenarios on canonical shell paths.
- Migrated scenario shell entrypoints were reduced to thin compatibility
  wrappers that delegate to `e2e-harness scenario <name>`.
- Release/test tooling now keeps performance runner paths and round-trip temp
  files under the repository root, matching the hardened path-validation
  contract used by local and CI checks.
- Release binary matrix refreshed for current NGINX versions `1.30.1` and
  `1.31.0`, replacing stale `1.29.8` and `1.30.0` entries.
- Release binary CI now refreshes the release matrix before resolving build
  targets, and the scheduled/manual matrix updater auto-approves and auto-merges
  validated matrix-update PRs when repository policy allows it.
- Development test dependencies were refreshed for the 0.6.3 release line.
- `PROJECT_STATUS.md` current release line advanced from 0.6.2 to 0.6.3.

### Fixed
- Hardened release/performance tooling path validation so untrusted or
  user-derived path components are not materialized before containment checks.
- Aligned threshold-engine validation with `REPO_ROOT` so security checks and
  local runner behavior share the same repository-boundary source of truth.
- Suppressed the validated-write-path static-analysis false positive without
  weakening the underlying containment guard.

### Removed
- Stale Python E2E spec files from `components/nginx-module/tests/e2e/`:
  - `test_streaming_e2e.py`
  - `test_streaming_failure_cache_e2e.py`

#### Upgrading to 0.6.3

- `make test-e2e` remains the canonical entrypoint and now delegates migrated
  scenarios to `e2e-harness`; no caller-side command change is required.
- For migrated scenarios, thin shell wrappers under `tools/e2e/` are
  compatibility delegates. New product-level HTTP scenario logic should be
  implemented in `tools/e2e-harness/src/scenarios/` rather than as new
  independent shell assertions.
- Python E2E spec-only files under
  `components/nginx-module/tests/e2e/` are removed from this release line.
  Use Rust harness scenarios and retained canonical shell runtime paths.

## [0.6.2] - 2026-05-08

This release hardens dynamic-configuration safety with snapshot isolation,
reload retry contract, and unknown-key atomic rejection, and updates all
version-bearing surfaces to 0.6.2.

### Added
- **Harness Rule 35** in AGENTS.md:
  - Dynconf snapshot isolation: `dynconf_enabled=0` locations must not consume
    the global snapshot; `header_filter` passes NULL snapshot to
    `ngx_http_markdown_build_effective_conf()`.
  - Reload retry contract: `applied_mtime` separated from `last_mtime`; retry
    on next poll cycle when they differ, regardless of mtime change detection.
  - Unknown-key atomic rejection: unrecognized dynconf keys cause `NGX_ERROR`,
    rejecting the entire file atomically (not `NGX_DECLINED` silent skip).
  - Startup apply: `dynconf_start` parses and applies the existing dynconf file
    immediately at startup; failed parse leaves `applied_mtime=0` for retry.
  - `harness-check-full` now includes `harness-security-checks`.
- Snapshot race elimination in `header_filter`: global `active_snapshot` read
  exactly once at function entry into function-lifetime `snap_copy`; ctx binding
  copies from function-level variables via
  `ngx_http_markdown_bind_request_snapshot()`.
- `dynconf_path_configured` flag moved from file-scope static to
  `ngx_http_markdown_main_conf_t` for per-reload isolation.

### Changed
- Updated all version-bearing surfaces (Cargo.toml, Chart.yaml, values.yaml,
  README, INSTALLATION, CI workflows, Homebrew release docs) to 0.6.2.

## [0.6.1] - 2026-05-06

This release hardens the harness rule surface with five new defect-prevention
rules (27–31) extracted from 14-day fix-commit analysis, adds an output-safety
risk pack, and introduces the two-phase dynconf snapshot model.

### Added
- **Harness Rules 27–31** in AGENTS.md:
  - Rule 27: Markdown output escaping and injection prevention
  - Rule 28: Full `ngx_list_part_t` chain iteration and multi-value HTTP header
    semantics
  - Rule 29: Flag/state clearing ordering (clear-after-success, not before)
  - Rule 30: NUL-termination of `ngx_str_t` before C API calls and EOF boundary
    handling
  - Rule 31: Residual code integrity after merge and large commits
- **Output-safety risk pack** (`docs/harness/risk-packs/output-safety.md`) for
  link/URL emission, escaping, and content-injection prevention routing.
- Pre-output checklist items for C module (22–26), Rust (21–22), and
  documentation (8) covering Rules 27–31.
- NUL-termination and directive-matching sync points in `nginx-protocol-safety`
  risk pack.
- Merge-integrity sync points in `release-governance` risk pack.
- `output-safety` pack entry in routing manifest (JSON + MD overlay).

### Changed
- Dynconf: two-phase snapshot model with `active_snapshot` and
  `staging_snapshot`; staged commit semantics (entire file must parse or
  nothing applied).
- Dynconf: request-bound snapshot (`ctx->dynconf_snapshot`) bound at
  header_filter time; no file I/O on request path.
- Dynconf: `log_verbosity` module enum, `enabled_source`/`enabled_complex`
  override, global-only scope guard.
- Conversion: hardened `X-Forwarded-Host` normalization (first-hop extraction,
  control character rejection, IPv6 bracket validation, fallback to server
  name).
- `METRIC_SAFE_DEC` macro for metrics decrement under dynconf.

### Fixed
- Invalid `log_verbosity` enum fallback in decision log impl.
- Dynconf request-path file I/O removed from filter chain.
- Forwarded-header injection vectors from unsanitized `X-Forwarded-Host`.

#### Upgrading to 0.6.1

- **Dynamic config** is off by default (`markdown_dynamic_config off`).
  To enable hot-reload without NGINX restart, set
  `markdown_dynamic_config on` and `markdown_dynamic_config_path <path>`
  at the http or server level.
- **X-Forwarded-Host validation** is now stricter: leading/trailing
  whitespace in the first-hop token is trimmed; non-IPv6 hosts with a
  `:` must be followed by digits only (port); IPv6 bracket literals
  may include an optional `:<port>` suffix (e.g. `[::1]:8080`).
  Previously-accepted malformed values may now be rejected with a
  fallback to `r->headers_in.server`.
- **Request-bound dynconf snapshot**: each request deep-copies the
  active snapshot at header_filter time, eliminating the window where
  a concurrent timer reload could swap the snapshot mid-request.
  No configuration change is required; behavior is more consistent.

## [0.6.0] - 2026-05-05

This release upgrades nginx-markdown-for-agents from a feature-complete opt-in
system to a production-ready default-enabled system. Streaming engine is now the
default conversion path (auto mode), noise pruning is enabled out-of-the-box,
and a unified memory budget simplifies configuration.

### Added
- Streaming engine auto mode: `markdown_streaming_engine` default changed from
  `off` to `auto`. In auto mode, responses with Content-Length >=
  `markdown_streaming_auto_threshold` (default 32K) or chunked transfer use
  streaming; smaller responses use full-buffer.
- `markdown_streaming_auto_threshold` directive (default 32k, context:
  http/server/location) for controlling the auto-mode engine selection
  threshold.
- `markdown_prune_noise` directive (default on, context: http/server/location)
  for runtime enable/disable of noise region pruning.
- `markdown_prune_selectors` directive (context: http/server/location) for
  custom prune selectors (space-separated tag names, replaces built-in
  defaults: `nav footer aside`).
- `markdown_prune_protection_selectors` directive (context: http/server/location)
  for protection selectors that override prune selectors (protection wins).
- `markdown_memory_budget` directive (context: http/server/location) for
  unified memory budget controlling both streaming and full-buffer engines.
  Priority: explicit per-engine > unified > default.
- Reason codes `ELIGIBLE_STREAMING_AUTO` and `ELIGIBLE_FULLBUFFER_AUTO` for
  auto-mode engine selection observability.
- Rust `PruneConfig` struct with runtime-configurable pruning: `from_ffi()`,
  `default_enabled()`, `disabled()`, custom selectors, and protection-selector
  override.
- Rust `should_prune_with_config()` function for runtime pruning decisions.
- FFI fields: `prune_noise`, `prune_selectors`/`len`,
  `prune_protection_selectors`/`len`, `memory_budget` in `MarkdownOptions`.
- ADR-0007: Streaming Engine as Default (auto mode).
- ADR-0008: Noise Pruning Enabled by Default.
- Migration guide: `docs/guides/streaming-default-migration.md` with rollback
  instructions for both default changes.
- Release gate validator: `tools/release/gates/validate_release_gates_060.py`
  (12 gates covering spec docs, ADRs, migration guide, directives, reason
  codes, Cargo features, and harness manifest).
- Makefile target `release-gates-check-060`.
- New E2E validation scripts: `verify_metrics_endpoint_e2e.sh`,
  `verify_conditional_requests_e2e.sh`, `verify_config_merge_e2e.sh`,
  `verify_auth_cache_e2e.sh`, `verify_status_codes_e2e.sh`.
- Shared helper functions in `nginx_markdown_native_build.sh`:
  `markdown_wait_for_http` (HTTP readiness polling),
  `markdown_require_flag_value` (CLI flag validation),
  `markdown_expect_status` (HTTP status assertion),
  `markdown_expect_header` (header pattern assertion),
  `markdown_extract_header` (header value extraction).
- Makefile targets for all new E2E scripts.
- Homebrew tap publication workflow:
  `.github/workflows/homebrew-tap-publish.yml`
  (release/manual trigger, computes SHA-256 from GitHub tag tarball, updates
  formula, pushes to external tap repository).
- Homebrew post-release macOS verification workflow:
  `.github/workflows/homebrew-post-release-verify.yml`
  (`brew tap`, `brew audit --strict`, `brew install --build-from-source`,
  `brew test`).
- Homebrew formula PR/push gate on GitHub macOS runners:
  `.github/workflows/homebrew-formula-gate.yml`.
- Homebrew tap release guide:
  `docs/guides/HOMEBREW_TAP_RELEASE.md`.

### Changed
- **Default behavior change**: `markdown_streaming_engine` default changed from
  `off` to `auto`. Operators who need identical 0.5.x output must set
  `markdown_streaming_engine off` explicitly.
- **Default behavior change**: `prune_noise_regions` Cargo feature is now in
  `default = ["prune_noise_regions"]` (was opt-in). Operators who need
  identical 0.5.x output must set `markdown_prune_noise off` explicitly.
- Auto-mode threshold uses `markdown_streaming_auto_threshold` instead of
  `markdown_large_body_threshold` (which retains its original semantics for
  the full-buffer threshold router).
- Engine selection logic: when `streaming_engine == NULL` (no directive set),
  defaults to auto mode instead of full-buffer (v0.6.0 default).
- `REASON_TO_REQUEST_STATE` mapping updated with `ELIGIBLE_STREAMING_AUTO` and
  `ELIGIBLE_FULLBUFFER_AUTO` mapping to `CONVERTED` state.
- Total defined reason codes count: 17 (was 15).
- `markdown_require_flag_value` now returns 2 instead of calling
  `usage` and exiting, allowing callers to handle the error under
  `set -e` without coupling to a `usage` function.
- `markdown_extract_header` now uses POSIX awk instead of GNU sed
  `/I` for macOS portability.
- `PATTERN_CT_PROMETHEUS` now escapes dots (`0\.0\.4`) for
  correct grep matching.
- Deduplicated local `wait_for_http` and `assert_http_200_header`
  definitions across E2E scripts in favor of shared helpers.
- Hardened auth/cache E2E assertions: Case 5 compares exact
  upstream Cache-Control, Case 6 fails on empty ETag, Case 7
  requires `Vary:.*Cookie` (not just `Vary:`).
- Synced `verify_config_merge_e2e.sh` top docstring to actual
  checks; fixed `verify_proxy_tls_backend_e2e.sh` source ordering.
- Updated installation documentation and READMEs with Homebrew tap install
  path and release-tag checksum guidance.

#### Upgrading to 0.6.0

- **Streaming is now the default engine.** The `markdown_streaming_engine` directive defaults to `auto`, which selects the streaming path for eligible responses. To retain the previous full-buffer-only behavior, set `markdown_streaming_engine off;` in your nginx configuration.
- **Noise pruning is now enabled by default.** The `markdown_prune_noise` directive defaults to `on`. To disable it, set `markdown_prune_noise off;`.
- **Unified memory budget.** The `markdown_memory_budget` directive supersedes the dual `markdown_max_size` + `markdown_streaming_budget` pattern. Existing configurations using the old directives continue to work but `markdown_memory_budget` is recommended for new deployments.
- **OpenTelemetry tracing.** If you enable OTLP tracing, ensure your collector endpoint accepts HTTP/protobuf on the configured port.

### Fixed
- Homebrew source builds now install `cbindgen` as an explicit build
  dependency before running `make build`.
- Homebrew source builds now detect the installed Homebrew `nginx` version and
  build the dynamic module against the matching NGINX source.
- SonarCloud finding: missing `return 0` in
  `check_status_passthrough` (`verify_status_codes_e2e.sh`).
- SonarCloud finding: repeated `'Cookie: session=abc123'` literal
  extracted to `readonly HEADER_COOKIE_AUTH`.
- CodeRabbit findings: SC2015 anti-patterns, readiness URL
  mismatches, inline Python string interpolation, missing
  `.PHONY` targets, `wait_for_http` docstring drift.
- Orphaned doc blocks and stale local helper definitions removed
  from `verify_accept_negotiation_e2e.sh`,
  `verify_chunked_streaming_native_e2e.sh`,
  `verify_error_handling_e2e.sh`.

## [0.5.5] - 2026-04-26

This release consolidates all post-0.5.0 work into one stabilization release,
including converter correctness fixes, build-script hardening, documentation
synchronization, and the 0.5.5 correctness workstreams.

The release focuses on semantic fidelity, HTTP protocol correctness,
auth/cache safety, streaming parity evidence, operator diagnostics, and
release-gate/documentation synchronization.

### Added
- Semantic-fidelity coverage for high-risk HTML structures, including headings,
  nested lists, tables, blockquotes, code blocks, links, metadata/front matter,
  malformed input, UTF-8 chunk boundaries, and media-bearing elements.
- Media extraction coverage for `video`, `audio`, `source`, `track`, and
  `area`, with regression tests for both missing-attribute and
  attribute-present branches.
- A media-rich corpus fixture and sidecar metadata for streaming parity and
  corpus validation.
- Protocol-correctness unit coverage for ETag replacement/removal, weak and
  wildcard `If-None-Match` matching, 304 response metadata, `Vary: Accept`,
  HEAD routing/header parity, 206 handling, and fail-open header preservation.
- Auth/cache-safety coverage for cookie pattern matching and authenticated
  response cache-control behavior across full-buffer and streaming paths.
- Streaming parity evidence artifact at
  `tests/streaming/evidence/summary.json`, plus stricter known-difference
  metadata (`drift_type`, `severity`, and fixture/global-scope discipline).
- Streaming reason-code lifecycle audit tooling
  (`tools/harness/audit_reason_codes.sh`) and decision-log tests covering
  failure/degradation classification and verbosity gating.
- 0.5.5 release governance surfaces:
  `docs/project/0.5.5-release-spec.md`,
  `docs/project/release-checklist-0-5-5.md`,
  `docs/project/test-matrix-0-5-5.md`, go/no-go criteria, waiver format, and
  version-specific release-gate validator
  (`tools/release/gates/validate_release_gates_055.py`).
- New E2E validation scripts for Accept negotiation, error handling, and
  sanitizer/security behavior.
- Harness remediation and release-governance risk-pack documentation, plus
  routing-manifest updates for 0.5.5 verification families.

### Changed
- Consolidated all post-0.5.0 patch work into this 0.5.5 release; no
  intermediate release note or compatibility step is required.
- Expanded full-buffer URL resolution so FFI `base_url` is reflected in emitted
  Markdown links and media URLs, including `http://`, host-only, trailing-slash,
  root-relative, and already-absolute URL cases.
- Aligned streaming URL resolution behavior with full-buffer trailing-slash
  base URL handling.
- Hardened URL sanitization to reject percent-encoded control characters such
  as `%00`, `%01`, and `%7F`, not only literal control bytes.
- Tightened CommonMark output behavior for nested code fences and link
  destination escaping inherited from the post-0.5.0 patch work.
- Updated streaming, FFI, charset, budget, fail-open, fast-path, fuzz-target,
  shell CLI, and performance-tool documentation so implementation contracts
  describe current runtime behavior.
- Updated operator documentation for actual metrics names, JSON key paths,
  Prometheus series names, HELP text semantics, skip metrics, 206 `SKIP_RANGE`
  handling, shadow metrics, and `markdown_metrics_format prometheus`.
- Required operator-facing verification examples to send explicit `Accept`
  headers matching the format being parsed.
- Updated `AGENTS.md`, harness docs, routing manifest, and risk packs so
  release-gate and remediation work route through repo-owned truth surfaces.
- Refined release-gate tooling to validate 0.5.5 document existence,
  evidence-artifact schema, known-difference metadata, changelog version
  heading, reason-code audit execution, and missing interpreter/tool failures.
- Improved shell portability and automation behavior in release/build/corpus
  scripts, including curl timeouts, stderr diagnostics, and safer failure-path
  handling.
- Refreshed repository documentation broadly across README, architecture,
  guides, testing docs, project docs, examples, and component READMEs.
- Updated CI/dependency maintenance surfaces, including GitHub Action bumps and
  sorted Docker package lists.

### Fixed
- Fixed full-buffer relative-link resolution from FFI options; URL-resolution
  tests now assert the emitted Markdown content, not just `error_code == 0`.
- Fixed sanitizer coverage gaps where percent-encoded control characters could
  remain visible as unsafe URL text in converted output.
- Fixed review and SonarCloud findings in Rust tests, shell E2E scripts,
  release-gate Python validators, C unit tests, and corpus validation scripts.
- Fixed shell E2E tests that previously logged warning-only mismatches for
  required assertions, including missing `Vary: Accept`, upstream 5xx status
  preservation, 206 content-type preservation, and oversize fail-open behavior.
- Fixed the oversize E2E fixture so the `markdown_max_size 1k` path actually
  exercises an input larger than 1024 bytes.
- Fixed streaming FFI tests that relied on default `result.error_code` instead
  of asserting direct return codes from `markdown_streaming_feed()` and
  `markdown_streaming_finalize()`.
- Fixed C unit tests so merge-conf and header-update assertions verify
  production writes rather than initial zeroed state.
- Fixed release-gate validator fragility: JSON top-level type errors now fail
  cleanly, changelog detection requires a real release heading, missing
  `tomllib`/`tomli` is a hard failure with installation guidance, and missing
  `bash` is reported before subprocess execution.
- Fixed build and release helper diagnostics from the post-0.5.0 patch work,
  including curl/python failure tolerance, stderr routing, and release-version
  resolver timeout behavior.
- Fixed stale or overclaimed documentation descriptions, generated-header ABI
  comments, metadata drift, and current-version framing across release docs.

## [0.5.0] - 2026-04-20

This release delivers the 0.5.0 streaming line. It moves the project from
full-buffer-only operation to a dual-engine model with bounded-memory
streaming, plus the harness and release-gate surfaces needed to operate it.

### Added
- Expanded E2E coverage scenarios in `collect_nginx_coverage.sh` from ~8 to ~35+
  curl requests, covering auth detection, conditional requests, error paths,
  Accept header diversity, metrics formats, body filter paths, and flavor
  variants
- Added location blocks for `markdown_flavor gfm/commonmark`, `markdown_max_size`
  size-limit, `markdown_log_verbosity error`, and `markdown_metrics_format auto`
  to the coverage nginx.conf
- New `auth_cookie_pattern_test.c` unit test covering prefix/suffix wildcard
  matching, exact matching, empty inputs, and edge cases
- Expanded `error_classification_test.c` with ERROR_SUCCESS mapping, unknown
  category string, and error code classification completeness property test
- Expanded `accept_parser_test.c` with q-value edge cases (q=0, q=1, q=0.000,
  q=1.000), malformed q-values (q=abc, q=, q=2.0), and q-value range invariant
  property test
- Advisory per-file coverage threshold logging in `collect_nginx_coverage.sh`
  (warnings only, not CI-blocking)
- Streaming-focused 0.5.0 scope covering release gates, runtime integration,
  failure semantics, parity testing, rollout observability, and performance
  evidence
- True streaming engine path (opt-in) with chunk-driven conversion and
  dual-engine request routing (`full-buffer` default plus `streaming`)
- Streaming rollout controls and observability primitives, including
  shadow-mode verification flows, streaming reason-code coverage, and
  streaming-oriented metrics/reporting surfaces
- Repo-owned harness documentation under `docs/harness/`, including a central
  overview, core execution loop, canonical routing manifest, and risk-pack
  overlays for runtime streaming, FFI boundary work, observability, and
  docs/tooling drift
- Durable open-source documentation for harness design and maintenance:
  - `docs/architecture/HARNESS_ARCHITECTURE.md`
  - `docs/architecture/ADR/0005-repo-owned-harness.md`
  - `docs/guides/HARNESS_MAINTENANCE.md`
- Executable harness tooling:
  - `tools/harness/check_harness_sync.py`
  - `tools/harness/state_store.py`
- Harness regression coverage for sync checks, spec resolution, and local
  state-carrier behavior
- Local maintenance skills for harness upkeep:
  - `sync-harness-rules`
  - `evolve-harness-rules`

### Changed
- `AGENTS.md` now points explicitly at repo-owned harness entrypoints instead
  of relying on local-only steering docs
- Local adapter summaries were reduced to thin references that point back to
  `docs/harness/`
- `Makefile` now exposes `make harness-check` and `make harness-check-full`
- CI path filters now include `AGENTS.md` so harness contract changes trigger
  validation
- `tools/docs/check_docs.py` now scans canonical markdown truth surfaces rather
  than also treating root-level scratch notes as release-facing docs
- Release-gates compatibility-matrix validation now supports both the legacy
  three-state-column format and the current canonical single
  `Classification` column format
- `README.md`, `docs/README.md`, `docs/architecture/README.md`, and
  `docs/testing/README.md` now point contributors at harness workflow and
  commands

### Fixed
- Resolved SonarCloud leak-period C findings in the NGINX module by tightening
  const-correctness, loop-local variable declarations, narrowing conversions in
  conversion-option wiring, and macro/unused-parameter hygiene without changing
  runtime conversion semantics.
- Refreshed the release-binaries matrix to track nginx.org stable/mainline
  releases directly, so `0.5.0` now publishes binaries for nginx `1.29.8`
  and `1.30.0`, and added a release-event freshness gate so future published
  binaries cannot silently lag behind the official nginx download page.
- Updated harness optional-skill documentation to avoid hard local-link
  dependency in docs checks; the skill remains documented as an optional
  repo-tracked path via `docs/guides/HARNESS_SKILL_SETUP.md`.
- Restored green `make harness-check-full` validation by aligning
  release-gate compatibility-matrix parsing with the 0.5.0 canonical document
  structure
- Prevented local scratch markdown files from causing false failures in
  canonical docs validation
- Hardened streaming correctness and safety paths across backpressure,
  UTF-8 boundary handling, memory-budget enforcement, and fail-open/fallback
  lifecycle behavior
- Bumped Rust MSRV from 1.87 to 1.91 to support
  `str::floor_char_boundary` used in UTF-8 safe link text truncation
- Updated minimum Rust toolchain version in installation docs to 1.91.0+

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
- Rust 1.91.0+

### Known Limitations
- Streaming is the default engine; full-buffer is the fallback
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

#### Upgrading to 0.6.2

- **Dynconf snapshot isolation**: locations with `markdown_dynamic_config off`
  no longer consume the global dynconf snapshot. No configuration change is
  required; behavior is more isolated for non-dynconf locations.
- **Reload retry contract**: if a dynconf file change was detected but the
  reload failed, the watcher now retries on the next poll cycle regardless
  of mtime change detection. No configuration change is required.
- **Unknown-key atomic rejection**: unrecognized keys in the dynconf file
  now cause `NGX_ERROR` (entire file rejected) instead of `NGX_DECLINED`
  (silent ignore). Previously-accepted files with unknown keys will now be
  rejected; remove any non-standard keys from your dynconf files.
- **Startup apply of existing dynconf file**: if a dynconf file exists at
  startup, it is now parsed and applied immediately. Previously the first
  apply was deferred to the first timer poll cycle.

#### Upgrading to 0.5.0

- **Rust MSRV raised from 1.87 to 1.91.** The streaming engine uses
  `str::floor_char_boundary` (stabilized in 1.80) and other APIs that require
  Rust 1.91+. Update your toolchain before building.
- All new streaming directives (`markdown_streaming_engine`,
  `markdown_streaming_on_error`, `markdown_streaming_shadow`,
  `markdown_streaming_budget`) default to **off / safe values**. With no
  configuration changes, runtime behavior is identical to 0.4.x.
- New `make harness-check` and `make harness-check-full` targets are available
  for validating repo contracts and release-gate documents. They are optional
  for runtime-only contributors.
- The `nginx-markdown-converter` crate version is now `0.5.0`.

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
