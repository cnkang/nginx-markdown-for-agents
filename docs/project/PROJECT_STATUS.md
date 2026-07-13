# NGINX Markdown for Agents - Project Status

## Status Snapshot

This project is a production-oriented NGINX filter module backed by a Rust HTML-to-Markdown converter (via FFI). It performs HTTP content negotiation and returns Markdown when clients request `Accept: text/markdown`.

The repository also includes a repo-owned harness for spec resolution, agent
routing, risk overlays, and harness-specific validation. That harness is
tracked in public docs and tools rather than living only in private local
steering files.

## Current Assessment

As of the **current release line (0.9.x)**, the project includes a dual-engine
conversion model (streaming default with full-buffer fallback), Rust-first
architecture modules for Accept negotiation, conditional requests, decision
logic, and header plan application, independent decompression budget with parse
timeout and parser budget directives, runtime diagnostics endpoint, dynconf
dry-run and last-known-good rollback, DEB/RPM packaging pipeline, Kubernetes
deployment examples, FFI ABI layout verification, CI supply-chain hardening,
supplemental static security checks, report-oriented supply-chain visibility, and a
repo-owned harness for agent workflow governance. Core features are
implemented and tested. The codebase includes unit, integration, E2E,
fuzz-oriented validation entrypoints, and harness-specific validation
entrypoints, along with documentation covering installation, configuration,
operations, architecture, and contributor-facing harness maintenance.

### Release 0.8.x Line (Current)

**Status:** Previous release line. The 0.8.x line began with 0.8.0 (true
streaming contract) and continued with patch releases (0.8.1, 0.8.2, 0.8.3) that
hardened stability, security, and release-gate consistency without introducing
new user-visible features or breaking configuration changes.

Version 0.8.0 formalized true streaming semantics as a first-class, verifiable
contract. The current release line is 0.9.x — see [Current Release Line 0.9.x](#current-release-line-09x).

### Release 0.7.0 Updates

- P0 runtime correctness:
  - Full-buffer pending chain on NGX_AGAIN with resume (Rule 1).
  - `failopen_completed` flag prevents duplicate finalize (Rule 38).
  - Safe output ordering: alloc→copy→chain→headers→body filter.
  - `markdown_decompress_max_size` directive: independent decompression
    budget decoupled from `max_size`.
  - `markdown_parse_timeout` directive (default: 30s).
  - `markdown_parser_budget` directive (default: 64m).
  - Rust error codes: `DecompressionBudgetExceeded` (9), `ParseTimeout` (10),
    `ParseBudgetExceeded` (11).
- Rust-first architecture modules:
  - `negotiator`: RFC 7231 Accept header q-value negotiation with
    `FFIAcceptResult` struct and `markdown_negotiate_accept` FFI export.
  - `conditional`: If-None-Match / If-Modified-Since handling in Rust.
  - `decision`: pure decision engine with reason codes.
  - `header_plan`: declarative header mutation plan for atomic application.
  - `security` extensions: URL control-char rejection, link escaping,
    forwarded header parsing.
- FFI ABI verification:
  - Rust layout tests for `MarkdownResult`, `FFIAcceptResult`, error/reason
    code uniqueness.
  - C `static_assert` for critical struct sizeof and offsetof.
- DEB/RPM packaging pipeline with SHA256SUMS, mandatory tag-release GPG signing,
  release-manifest.json traceability, and repository metadata.
- Kubernetes deployment examples (Ingress Controller, Helm chart, manifests).
- Runtime diagnostics endpoint (`/nginx-markdown/diagnostics`).
- Dynconf dry-run and last-known-good rollback.
- Rules 39–40: NGX_DONE terminal semantics, invalidated header hash==0
  filtering.
- CI supply-chain hardening: SHA-pinned Actions, checksum verification,
  ClusterFuzzLite integration.
- Streaming memory budget enforcement during code block accumulation.

### Release 0.6.3 Updates

- Rust-first E2E migration closure:
  - `tools/e2e-harness/` is now a first-class migrated-scenario runner for:
    `accept-negotiation`, `metrics-endpoint`, `conditional-requests`,
    `auth-cache`, and `status-codes`.
  - `make test-e2e` delegates migrated scenarios to the Rust harness while
    retaining non-migrated canonical shell scenarios.
  - `make test-e2e-rust` provides a direct migrated-scenario entrypoint.
- Python E2E cleanup:
  - removed stale spec-only files:
    `components/nginx-module/tests/e2e/test_streaming_e2e.py` and
    `components/nginx-module/tests/e2e/test_streaming_failure_cache_e2e.py`.
- Harness rule/governance alignment:
  - routing manifest includes Rust harness ownership surfaces;
  - harness checks enforce Rust harness contract and migration-policy guards for
    migrated shell wrappers and removed Python E2E surfaces.
- Final mainline hardening before the v0.6.3 tag:
  - release/performance tooling path validation uses `REPO_ROOT` as the
    repository-boundary source of truth;
  - release binary matrix targets current NGINX versions `1.30.1` and
    `1.31.0`;
  - local runner path and round-trip temp-file tests keep artifacts under the
    repository root;
  - development test dependencies are aligned with the current CI baseline.

### Repository Harness Updates

- `docs/harness/` is the public entrypoint for spec routing, risk packs, and
  harness checks.
- `tools/harness/check_harness_sync.py` and Make targets
  `make harness-check` / `make harness-check-full` provide executable
  validation instead of prose-only workflow rules.
- Optional local adapter inputs remain supported, but public repository
  validation no longer depends on private local assets being present.
- The harness now records short-lived execution memory in a user-local state
  carrier instead of tracked repository docs.

### Release 0.6.2 Updates

- Dynamic configuration hot-reload:
  - `markdown_dynamic_config` directive for runtime configuration reload without
    NGINX restart, with snapshot isolation and reload retry contract.
  - Dynconf snapshot isolation: `dynconf_enabled=0` locations are never
    influenced by the global snapshot; `header_filter` reads the snapshot
    exactly once to eliminate race windows.
  - Unknown dynconf keys cause atomic reload rejection (entire file rejected).
- Snapshot race elimination (v0.6.2):
  - `active_snapshot` read once at `header_filter` entry into function-lifetime
    `snap_copy`; effective conf built once from that copy.
  - Deferred-state latches cleared on both success and failure resume paths.
- dynconf_path_configured lifecycle:
  - Flag lives in `main_conf_t` (per-reload isolation), not file-scope static.

### Release 0.6.1 Updates

- Hardening release:
  - Rules 27–31: Markdown output escaping and injection prevention, full
    `ngx_list_part_t` chain iteration, flag clearing ordering, NUL-termination
    of `ngx_str_t` before C API calls, merge residual code integrity checks.
  - Output-safety risk pack added under `docs/harness/risk-packs/`.

### Release 0.6.0 Updates

- Production readiness release:
  - Streaming engine as default (`markdown_streaming_engine auto`).
  - Noise pruning default enabled (`markdown_prune_noise on`).
  - Unified memory budget (`markdown_memory_budget`, 0.6.0; retired in 0.9.0 as `markdown_limits`) superseding dual
    `markdown_max_size` + `markdown_streaming_budget`.
  - OpenTelemetry tracing integration (self-implemented OTLP HTTP/protobuf).
  - Per-path metrics with cardinality control.
  - OS package manager distribution (APT, YUM/DNF, Homebrew).
  - Helm chart with Ingress annotation support.
  - Coverage gate as CI merge requirement.
  - MDX and Org-mode flavor support (experimental, subject to change).
  - Dynamic configuration hot-reload (`markdown_dynamic_config`).
  - ADR-0006 (OTel), ADR-0007 (Streaming Default), ADR-0008 (Noise Pruning Default).

### Release 0.5.0 Updates

- Streaming architecture and runtime:
  - True streaming path integrated into the dual-engine model (opt-in), with
    request-scoped engine selection.
  - Streaming lifecycle and failure semantics hardened across fallback,
    backpressure, and post-commit handling.
- Streaming quality gates:
  - Expanded parity, diff, and chunk-boundary validation for streaming outputs.
  - Streaming-focused evidence and release-gate tooling aligned with 0.5.0
    scope.
- Harness and governance:
  - Repo-owned harness docs and checks promoted as canonical truth surfaces.
  - `make harness-check` and `make harness-check-full` are wired as executable
    validation entrypoints.

### Release 0.4.0 Updates

- 0.4.0 release-gate validation and tests refined based on automated code-review feedback:
  - Metric naming validation now supports histogram `_seconds_bucket/_sum/_count` series while keeping stricter rejection of ambiguous suffixes.
  - Checklist verifiability checks now include checked items and ignore fenced code examples.
  - Release-gate constants are centralized in a shared module to reduce drift between tooling, tests, and governance docs.
  - File read and directory listing paths in release-gate checks are hardened for graceful failure reporting.
- Release checklist wording now explicitly requires both Rust proptest and Python Hypothesis property-based test suites.

This assessment is based on:

- Implementation of core features and runtime configurability
- Test coverage across unit, integration, E2E, and property-based tests
- Documentation suite covering installation, configuration, operations, and architecture
- CI/CD pipeline with automated builds and security scanning
- Shared-memory metrics aggregation across workers in the module implementation
- Further decomposition of the NGINX module into focused config wiring/core/handlers, request-state, payload buffering/replay, conversion/output, lifecycle, and metrics helper units
- Shared native-build helper logic for Rust/NGINX verification scripts, including aligned macOS deployment-target handling
- Delegated runtime validations now reuse an exported module-enabled `NGINX_BIN` only when it has a reusable runtime layout; otherwise they fall back to self-building their own native NGINX runtime
- The GitHub Actions `runtime-regressions` job now retains the validated IMS runtime and reuses its `NGINX_BIN` for chunked and large-response checks instead of rebuilding native NGINX three times
- Canonical E2E coverage now lives under `tools/e2e/`, with `make test-e2e` delegating to a focused proxy/TLS, chunked, and large-response suite instead of maintaining a second full inline runner
- The Rust converter now keeps the public `ffi.rs` and `metadata.rs` entrypoints while pushing ABI decoding, memory handling, export wiring, metadata traversal, and URL resolution into focused submodules
- `cargo-fuzz` targets and nightly fuzz workflow for parser, FFI, and security-validator paths
- A separate non-blocking Darwin/macOS smoke workflow validates native Rust build plus real-nginx runtime checks on GitHub-hosted macOS
- Release artifacts and installation tooling

## Release 0.3.0 Highlights

The 0.3.0 release includes:

### Added
- Incremental processing for large responses with `markdown_large_body_threshold` directive
- `IncrementalConverter` Rust API behind the `incremental` feature gate
- Matrix-driven release automation pipeline (`tools/release/`)
- Third-party notices coverage checks and generated `THIRD-PARTY-NOTICES`
- Performance baseline gating system with `nightly-perf.yml` workflow
- Path hit metrics (`fullbuffer_path_hits`, `incremental_path_hits`) via metrics endpoint
- Large response design document and rollout guide
- `Cargo.toml` version now strictly tracks the release tag (synchronized from 0.1.0 to 0.3.0)

### Changed
- Event handler attribute sanitization now uses `on*` prefix matching instead of a static allowlist, following the OWASP/DOMPurify convention
- Form-related elements now use strip-tag-keep-content instead of full removal, preserving child text for AI agents
- Embedded content elements (`iframe`, `object`, `embed`) now use strip-tag-keep-content instead of full removal
- Image conversion now preserves the `title` attribute in Markdown syntax; missing/blocked image URLs emit `alt` text as plain text
- Media elements (`video`, `audio`) now have their `src` URL extracted as a Markdown link; video `poster` thumbnails extracted as Markdown images
- Image map `<area>` elements now have their `href` extracted as Markdown links
- X-Forwarded-Host/Proto headers are no longer trusted by default; added `markdown_trusted_proxies on|off` directive (default: off)
- Decompression buffer estimation now logs a warning when the estimated output exceeds 50 MB
- CI jobs for clang compiler and AddressSanitizer/UndefinedBehaviorSanitizer smoke tests
- SonarCloud Quality Gate Status badge in both English and Chinese READMEs
- Updated documentation to reflect correct Rust minimum version (1.85.0+ for edition 2024)
- Corrected NGINX minimum version references across all documentation (1.24.0+)

### Fixed
- Rust minimum version requirement in all documentation (was 1.70.0+, now 1.85.0+)
- NGINX minimum version in CONTRIBUTING.md (was 1.18.0, now 1.24.0)

## Previous Release: 0.2.2 (March 15, 2026)

### Added
- Canonical native E2E entrypoints under `tools/e2e/`
- Shared native-build helper logic for runtime verification scripts
- `cargo-fuzz` targets for parser, FFI, and security-validator paths, plus a nightly GitHub Actions workflow
- Non-blocking Darwin/macOS smoke workflow
- Additional performance artifact sampling for the medium front-matter path

### Changed
- Split the NGINX module implementation into focused config, request-state, payload, conversion, lifecycle, and metrics helper units
- Moved shared metrics collection to a cross-worker shared-memory model
- Split the Rust converter internals into dedicated submodules
- Reworked `make test-e2e` to delegate to the canonical native E2E suite
- Expanded C and Rust inline documentation
- Completed broader doc pass across decompression/buffering and Rust FFI/parser hot paths

### Fixed
- Hardened native E2E/runtime scripts for reusable `NGINX_BIN` paths
- Aligned Darwin native builds around a consistent `MACOSX_DEPLOYMENT_TARGET`
- Restored Darwin Rust target detection in the shared native-build helper
- Fixed `markdown_stream_types` content-type validation for stricter `-Werror` builds
- Synchronized documentation with the refactored implementation

## Previous Release: 0.2.1 (March 11, 2026)

### New Features
- Variable-driven `markdown_filter` support using NGINX variables and complex values
- Phase-consistent decision caching for reliable header and body processing
- Enhanced installation script with Docker support (`SKIP_ROOT_CHECK=1`)
- Simplified Chinese documentation (`README_zh-CN.md`)
- Comprehensive deployment examples guide
- Complete architecture documentation suite

### Bug Fixes
- Normalized `markdown_filter` runtime parsing for consistent variable resolution
- Restored const compatibility for stricter compiler builds
- Hardened path and URL-scheme parsing utilities
- Corrected installation paths and documentation details

## Implemented Features

### Rust Converter (`components/rust-converter/`)

- HTML parsing and HTML-to-Markdown conversion
- Output normalization and deterministic output behavior
- Charset detection and entity decoding
- YAML front matter generation
- Token estimation for LLM context management
- ETag generation for cache-aware responses
- FFI boundary with panic safety and memory management
- Security-oriented input sanitization (XSS, XXE, SSRF prevention)
- Enhanced element handling: form elements, embedded content, media elements, and image maps preserve meaningful content for AI agents
- Property-based tests for correctness and resilience
- Cooperative timeout mechanism
- `cargo-fuzz` targets for parser, FFI, and security-validator paths
- Internal FFI and metadata helper modules for a smaller public surface per file
- Incremental processing API (`IncrementalConverter`) behind the `incremental` feature gate

### NGINX Module (`components/nginx-module/`)

- Directive parsing and configuration structure
- Content negotiation based on `Accept` header
- Response buffering and conversion decision flow
- Response header updates (`Content-Type`, `Vary`, `ETag`)
- HEAD request handling
- Conditional request support (If-None-Match)
- Range request bypass logic
- Fail-open / fail-closed strategy handling
- Error classification and logging
- Metrics collection and endpoint
- Shared-memory metrics aggregation across workers
- Automatic upstream decompression (gzip, brotli, deflate)
- Authentication-aware caching (Cache-Control: private)
- Variable-driven configuration support
- Large response routing (retired `markdown_large_body_threshold` directive; no Config V2 replacement)
- Forwarded header trust control with `markdown_trusted_proxies` directive

## Test Coverage

The project includes tests at multiple levels:

### Rust Tests

- Unit tests for all core modules (converter, parser, security, etc.)
- Integration tests for FFI boundary and lifecycle management
- Property-based tests for invariants and edge cases
- Timeout and error handling tests
- YAML front matter and ETag generation tests
- Security tests for XSS, XXE, and SSRF prevention

Run with: `cargo test --all` or `make test-rust`

### NGINX Module Tests

- Unit tests for major components (30+ test targets)
- Standalone tests that don't require system NGINX
- Mock-based tests for filter chain behavior
- Configuration parsing and merge tests
- Header manipulation and cache-control tests
- Metrics collection and endpoint tests
- Shared metrics aggregation and latency-bucket formatting coverage

Run with: `make test-nginx-unit` or `make -C components/nginx-module/tests unit`

### Integration Tests

- NGINX runtime integration with real module loading
- End-to-end proxy chain validation with TLS backend
- Content negotiation and variant handling
- Compression and decompression flows
- Authentication and caching behavior
- Range-request bypass and shared metrics aggregation in the runtime integration script
- Delegated `If-Modified-Since`, chunked native smoke, and large-response native regression checks

Run with: `make test-nginx-integration` and `make test-e2e`

### CI/CD Pipeline

- Automated builds for multiple platforms (macOS, Linux)
- Security scanning with CodeQL plus fuzz-oriented validation coverage
- Release artifact generation and validation
- Docker image builds and testing

## Production Readiness

### Current State

The project includes:

- HTML-to-Markdown conversion with deterministic output
- Resource limits, timeouts, and configurable failure strategies
- ETag generation, conditional requests, and Vary header support
- Input sanitization and XSS/XXE/SSRF prevention
- Metrics endpoint, structured logging, and error classification
- Cross-worker shared metrics aggregation with averages and latency buckets
- Installation script, Docker examples, and configuration templates
- Documentation for installation, configuration, and operations

### Deployment Considerations

When deploying:

1. **Start incrementally**: Enable on one location or path first
2. **Monitor behavior**: Use the metrics endpoint to track conversions
3. **Set appropriate limits**: Configure `markdown_limits` (e.g., `memory=<size> timeout=<time>`)
4. **Choose failure mode**: Select `markdown_error_policy` based on requirements
5. **Test caching**: Verify cache behavior with your CDN or caching layer
6. **Review security**: Ensure authentication policies match your security model

See [DEPLOYMENT_EXAMPLES.md](../guides/DEPLOYMENT_EXAMPLES.md) for configuration patterns.

## Current Focus and Roadmap

### Current Release Line (0.9.x)

The 0.9.x release line is the current maintained line. The initial release
is 0.9.0 — a breaking release that consolidates the configuration surface
and adds profile-based deployments, while preserving upgrade paths from 0.8.x.

#### 0.9.0 (current)

- **Config V2 directives**: `markdown_error_policy`, `markdown_accept`,
  `markdown_trusted_proxies`, `markdown_limits`, `markdown_cache_validation`
  replace 0.8.x legacy directives.
- **Profile system**: `markdown_profile` (strict_cache, balanced, streaming_first)
  for one-line preset deployments.
- **0.8.x migration**: All 0.8.x configs work unchanged; legacy aliases
  silently remap to new directives.
- Breaking: removed `markdown_max_size`, `markdown_timeout`,
  `markdown_streaming_budget`, `markdown_conditional_requests` as standalone directives.

#### 0.8.3 (last 0.8.x patch)

- Streaming state machine correctness: corrected `pop_contexts_up_to`
  return order (innermost-first) and added `CodeBlock` handling in
  `ol`/`ul` derived state branches (Rule 6).
- Streaming emitter `ExitMany` action for batch context unwinding from
  mid-stack (Rule 6).
- Decompression buffer memory safety: switched workspace from `ngx_alloc`/`ngx_free`
  to `ngx_pnalloc`/`ngx_pfree` (Rule 43).
- Snapshot capacity raised from 4 to 8 entries in stream commit path
  (Rule 39).
- FFI `Box::into_raw` correctness: fixed use-after-free in converter
  handle allocation (Rule 15).
- Added `ngx_pfree` mock to `decompression_production_test.c` for
  unit test compilation.
- Full release gate validation: all 0.8.x gates pass (harness-check
  15/15, test-harness, release-gates-check-08x, test-nginx-unit,
  test-rust-fuzz-smoke, FFI panic safety --strict, all detector tests).
- Release integrity: `release-manifest.json` added as a release asset for
  DEB/RPM packages, generated before `SHA256SUMS` and covered by the
  `SHA256SUMS.asc` GPG signature chain for tag releases.

#### 0.8.2

- Streaming decompression hardening: eliminated heap leaks in `finish_zlib()`
  across all exit paths, hardened buffer expansion error paths, and enforced
  `ngx_alloc`/`ngx_free` exclusively for resizable buffer backing stores
  (Rule 43).
- Implied closure correctness (Rule 6): structural closures now unwind
  inner-to-outer; the Rust converter consumes implied closures before the
  sanitizer Skip decision and mirrors them to the state machine.
- FFI safety: centralized header plan reset for panic safety; parser working-set
  estimation with overflow-safe budget enforcement.
- Streaming decompression budget and memory accounting (Rule 3, Rule 44).
- Code fence language handling: `lang-` prefix support and sanitization in the
  streaming converter.
- `parse_size()` hardening for empty, whitespace-only, and malformed input.
- Security scan scoping: gitleaks scoped to tracked worktree content, skipping
  deleted paths and guarding against empty scan roots (Rule 48).
- Synced "current release line" wording across project, README, installation,
  and compatibility docs to 0.8.x (latest patch 0.8.3).
- Finalized RFC-0008 status from `Draft` to `Accepted / Implemented in 0.8.0`.
- Narrowed `markdown_stream_flush_interval` commitment from "future 0.8.x" to
  "future release".
- Added stream commit multipart header-list rollback regression tests covering
  cross-part `orig_nelts` semantics (Rule 39 / Rule 40).
- Added `make release-gates-check-08x` as the canonical 0.8.x patch-line entry
  point.
- Updated `THIRD-PARTY-NOTICES` with current dependency versions (Rule 49).

#### 0.8.1

- Streaming header commit atomicity / Rule 39 rollback semantics.
- FFI streaming finish invalid input handle ownership / cleanup contract.
- Content-Type OWS / HTAB compliance.
- Full-buffer backpressure `NGX_AGAIN` resume tail duplication.
- Release/perf/coverage tooling path traversal and taint sink hardening.
- Static security / supply-chain gate additions and documentation.

#### 0.8.0 (line anchor)

- True streaming contract: formalize incremental input processing, incremental
  output emission, and bounded memory as a single verifiable contract
  (RFC 0008, ADR-0011)
- Fallback state machine: implement pre-commit/post-commit two-phase error
  handling with deterministic recovery semantics (ADR-0012)
- Default-auto engine: align the auto-mode streaming policy with the true
  streaming contract definition (ADR-0013)
- Support matrix source of truth: consolidate platform, version, and package
  support declarations into a single machine-readable matrix consumed by CI,
  docs, and packaging (ADR-0014)

**Non-goals for 0.8.x line:**
- SSE/NDJSON conversion (out of scope for this release line)
- Full parser rewrite (incremental improvements only)
- Edge-CDN deployment model (origin-near architecture retained)
- Implementing `markdown_stream_flush_interval` (reserved for a future release;
  current use causes `nginx -t` to fail)

### Implemented Features (0.8.x line)

- Dual-engine conversion architecture: streaming default with full-buffer
  fallback
- Rust-first architecture: Accept negotiation, conditional requests, decision
  engine, and header plan modules implemented in Rust with narrow FFI boundary
- Independent decompression budget, parse timeout, and parser budget directives
- Runtime diagnostics endpoint with config snapshot and recent decisions
- Dynamic configuration hot-reload with dry-run validation and LKG rollback
- DEB/RPM packaging pipeline with GPG signing
- Kubernetes deployment examples and Helm chart
- FFI ABI layout verification (Rust layout tests + C static_assert)
- CI supply-chain hardening (SHA-pinned Actions, checksum verification)
- ClusterFuzzLite integration for continuous fuzz testing
- Streaming failure semantics and fallback controls aligned to commit
  boundaries
- Streaming parity and differential validation across chunk boundaries and
  failure paths
- Benchmark corpus and evidence-based release-gate validation
- Repo-owned harness governance (`AGENTS.md`, `docs/harness/`, `tools/harness/`)
- Prometheus-compatible metrics endpoint for operational monitoring
- OpenTelemetry tracing integration (self-implemented OTLP HTTP/protobuf)
- Per-path metrics with cardinality control
- OS package manager distribution (APT, YUM/DNF, Homebrew)
- Rollout and rollback guides with executable operator procedures
- Performance baseline gating system and hardened CI/CD validation

### Near-Term
- Expand streaming rollout samples across mixed traffic profiles
- Increase automated evidence collection for release-gate checks
- Continue operator-facing diagnostics hardening for drift/degradation cases

### Future Exploration
- Additional Markdown flavors and output formats
- Expanded observability integrations beyond built-in shared metrics and OTel tracing

## Known Limitations

The following limitations are documented:

1. **Streaming Is Default**: Streaming is the default engine; full-buffer is
   the fallback for explicit opt-out or engine-selection override
2. **HTML Input**: Requires HTML input (uncompressed or automatically decompressed)
3. **Conversion Fidelity**: Some complex HTML structures may not convert perfectly to Markdown
4. **Performance Overhead**: Large documents incur conversion overhead (mitigated by caching)

These limitations are acceptable for current use cases and may be addressed in future releases.

## Documentation Status

The project includes documentation covering:

### User Guides
- [BUILD_INSTRUCTIONS.md](../guides/BUILD_INSTRUCTIONS.md) - Building from source
- [INSTALLATION.md](../guides/INSTALLATION.md) - Installation and setup
- [CONFIGURATION.md](../guides/CONFIGURATION.md) - Configuration reference
- [DEPLOYMENT_EXAMPLES.md](../guides/DEPLOYMENT_EXAMPLES.md) - Production deployment patterns
- [OPERATIONS.md](../guides/OPERATIONS.md) - Operations and troubleshooting

### Architecture Documentation
- [SYSTEM_ARCHITECTURE.md](../architecture/SYSTEM_ARCHITECTURE.md) - System design overview
- [CONFIG_BEHAVIOR_MAP.md](../architecture/CONFIG_BEHAVIOR_MAP.md) - Directive behavior mapping
- [ADR/](../architecture/ADR/) - Architecture decision records

### Feature Documentation
- [AUTOMATIC_DECOMPRESSION.md](../features/AUTOMATIC_DECOMPRESSION.md)
- [CACHE_AWARE_RESPONSES.md](../features/CACHE_AWARE_RESPONSES.md)
- [CONTENT_NEGOTIATION.md](../features/CONTENT_NEGOTIATION.md)
- [COOPERATIVE_TIMEOUT.md](../features/COOPERATIVE_TIMEOUT.md)
- [TOKEN_ESTIMATOR.md](../features/TOKEN_ESTIMATOR.md)
- [YAML_FRONT_MATTER.md](../features/YAML_FRONT_MATTER.md)
- [security.md](../features/security.md)
- Additional features under [docs/features/](../features/)

### Testing Documentation
- [Testing README](../testing/README.md) - Test suite overview
- Test execution guides for unit, integration, and E2E tests
- Performance testing references

### Project Documentation
- [README.md](../../README.md) - Project overview (English)
- [README_zh-CN.md](../../README_zh-CN.md) - Project overview (Simplified Chinese)
- [CHANGELOG.md](../../CHANGELOG.md) - Version history
- [CONTRIBUTING.md](../../CONTRIBUTING.md) - Contribution guidelines
- This status document

## Verification and Testing

### Quick Verification

To verify the current state of the project:

```bash
# Clone and build
git clone https://github.com/cnkang/nginx-markdown-for-agents.git
cd nginx-markdown-for-agents
make test

# Run comprehensive tests
make test-rust              # Rust converter tests
make test-nginx-unit        # NGINX module unit tests
make test-nginx-integration # Integration tests (requires nginx)
make test-e2e               # End-to-end tests (requires nginx)
make test-rust-fuzz-smoke   # Short fuzz smoke checks (requires nightly + cargo-fuzz)
```

### Continuous Integration

The project uses GitHub Actions for automated testing:

- **CI Workflow**: Builds and tests on multiple platforms
- **Security Scanning**: CodeQL and nightly fuzz validation coverage
- **Release Automation**: Automated artifact generation and publishing

View the latest CI status: [GitHub Actions](https://github.com/cnkang/nginx-markdown-for-agents/actions)

## Platform Support

### Supported Platforms
- macOS (Apple Silicon and Intel)
- Linux (x86_64 and aarch64)
- NGINX 1.24.0 and later
- Rust 1.91.0 and later

### Docker Support
- Official NGINX base images
- Source build examples
- Installation script integration

See `examples/docker/` for Docker build examples.

<!-- BEGIN:release-matrix:status-matrix -->

## Release Matrix Summary

### Tier Distribution

| Tier | Count |
|------|-------|
| supported | 32 |
| experimental | 1 |
| best-effort | 1 |

### Release-Blocking Entries

| Entry | Workflow |
|-------|----------|
| 1.24.0 linux glibc amd64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.24.0 linux glibc arm64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.26.3 linux glibc amd64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.26.3 linux glibc arm64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.28.3 linux glibc amd64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.28.3 linux glibc arm64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.30.3 linux glibc amd64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.30.3 linux glibc arm64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.31.2 linux glibc amd64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.31.2 linux glibc arm64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.26.3 debian12 glibc amd64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.26.3 debian12 glibc arm64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.26.3 alpine3.20 musl amd64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.26.3 alpine3.20 musl arm64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.31.2 debian12 glibc amd64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.31.2 debian12 glibc arm64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.31.2 alpine3.20 musl amd64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.31.2 alpine3.20 musl arm64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.26.3 debian12 glibc amd64 deb-package | `.github/workflows/release-packages.yml` |
| 1.26.3 debian12 glibc arm64 deb-package | `.github/workflows/release-packages.yml` |
| 1.26.3 almalinux9 glibc amd64 rpm-package | `.github/workflows/release-packages.yml` |
| 1.26.3 almalinux9 glibc arm64 rpm-package | `.github/workflows/release-packages.yml` |
<!-- END:release-matrix:status-matrix -->

## Summary

**NGINX Markdown for Agents** is on the 0.9.x release line (latest patch:
0.9.0). The project provides
HTML-to-Markdown conversion through NGINX content negotiation with a
dual-engine model, with bounded-memory streaming as the default path and
full-buffer conversion as the fallback. Version 0.8.0 formalizes the true
streaming contract (RFC 0008, ADR-0011), introduces the streaming fallback
state machine (ADR-0012), aligns the auto-mode streaming policy with the true
streaming contract definition (ADR-0013), and consolidates platform and
version support declarations into a release matrix source of truth (ADR-0014).
The 0.8.1 through 0.8.3 patch releases harden streaming atomicity, FFI cleanup,
OWS compliance, backpressure resume, streaming decompression, implied-closure
correctness, release-gate naming, and documentation
consistency without changing the 0.8.x configuration contract. They also
includes streaming observability (metrics and tracing), streaming security
enforcement (policy validation and alerts), streaming configuration directives, Prometheus-compatible
metrics, decision reason codes, rollout and rollback guides, parity and
evidence workflows for streaming rollout safety, dynamic configuration
hot-reload, OpenTelemetry tracing, per-path metrics, OS package distribution,
release automation, performance baseline gating, runtime validation reuse,
fuzzing workflows, and shared metrics aggregation for observability.

### Key Components
- Core feature implementation
- Test coverage (unit, integration, E2E, property-based)
- Documentation for users, operators, and developers
- Deployment tooling and examples
- CI/CD pipeline with security scanning
- Multi-platform support (macOS, Linux, Docker)

### Current State
Core features are implemented and tested. The focus is on operational validation, performance optimization, and community feedback integration.

### Getting Started
- **Evaluate**: Read the [README](../../README.md) and [DEPLOYMENT_EXAMPLES](../guides/DEPLOYMENT_EXAMPLES.md)
- **Install**: Follow the [INSTALLATION](../guides/INSTALLATION.md) guide
- **Configure**: Use the [CONFIGURATION](../guides/CONFIGURATION.md) reference
- **Operate**: Consult the [OPERATIONS](../guides/OPERATIONS.md) guide
- **Contribute**: See [CONTRIBUTING](../../CONTRIBUTING.md) for guidelines

For questions, issues, or feature requests, use the [GitHub issue tracker](https://github.com/cnkang/nginx-markdown-for-agents/issues).

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.6.3 | 2026-05-13 | Kang | Version bump to 0.6.3 for release |
| 0.7.0 | 2026-06-03 | Kang | Version bump to 0.7.0; add Rust-first architecture, decompression budget, diagnostics, dynconf dry-run, DEB/RPM, K8s, FFI ABI verification, CI supply-chain hardening |
| 0.8.0 | 2026-06-16 | Kang | Version bump to 0.8.0; true streaming contract, fallback state machine, streaming observability, streaming security enforcement, release matrix source of truth, streaming config directives |
| 0.8.2 | 2026-06-23 | Kang | 0.8.2 release: streaming decompression hardening, implied-closure correctness, FFI panic safety, decompression budget enforcement, security scan scoping, release-line documentation closeout |
| 0.8.3 | 2026-06-26 | Kang | 0.8.3 closeout: streaming state machine fixes, ExitMany batch unwind, decompression buffer memory safety, snapshot capacity, FFI Box::into_raw fix, full release gate validation |
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation; retire markdown_large_body_threshold) |
