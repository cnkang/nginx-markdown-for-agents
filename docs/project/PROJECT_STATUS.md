# NGINX Markdown for Agents - Project Status

## Status Snapshot

This project is a production-oriented NGINX filter module backed by a Rust HTML-to-Markdown converter (via FFI). It performs HTTP content negotiation and returns Markdown when clients request `Accept: text/markdown`.

The repository also includes a repo-owned harness for spec resolution, agent
routing, risk overlays, and harness-specific validation. Public docs and tools
track that harness rather than private local steering files.

## Current Assessment

As of the **current release line (0.9.2)**, the project includes a
streaming-default conversion model with full-buffer fallback,
Rust-first
architecture modules for Accept negotiation, conditional requests, decision
logic, and header plan application, unified decompression budget via
markdown_limits (conversion_memory, parser_memory, decompressed_size, conversion_timeout, parser_timeout), read-only runtime diagnostics endpoint,
dynconf dry-run and last-known-good failed-reload protection with atomic file
restore, DEB/RPM packaging pipeline, Kubernetes
deployment examples, FFI ABI layout verification, CI supply-chain hardening,
supplemental static security checks, report-oriented supply-chain visibility, and a
repo-owned harness for agent workflow governance. The project has
implemented and tested the core feature set. The codebase includes unit, integration, E2E,
fuzz-oriented validation entrypoints, and harness-specific validation
entrypoints, along with documentation covering installation, configuration,
operations, architecture, and contributor-facing harness maintenance.

### Current Release Line 0.9.2

**Status:** Development release line. 0.9.1 is the latest released patch.
0.9.2 is the current development line. 0.9.2 is the final pre-1.0 breaking
release, with the public configuration surface reduced from 63 directives to
25, retired profile/conflict FFI snapshots removed, and the bundled FFI ABI at
version 2. Development version metadata is
0.9.2. The release tag, GitHub Release, package assets, and checksums remain
pending until the blocking gates pass.

#### 0.9.2 (current development)

- **OTel removal**: The experimental OTel directives and implementation are
  absent from the 0.9.2 production surface. ADR-0027 records conditions for a
  possible future redesign.
- **Metrics freeze**: The eleven-family v1 contract replaces the production
  metrics endpoint (`requests_total`, `conversion_attempts_total`,
  `conversion_deliveries_total`, `conversion_duration_seconds`,
  `input_bytes_total`, `output_bytes_total`,
  `streaming_events_total`, `streaming_peak_memory_bytes`,
  `decompression_events_total`, `dynconf_reloads_total`, `build_info`).
  This replaces the legacy multi-format, per-path, shadow, and debug families.
- **Directive removal (38 total) and ABI 2**: The release removes 19 reject-only
  migration stubs plus 14 active directives and 5 standalone limit directives.
  The public surface drops from 63 directives to 25, and the bundled Rust/C
  FFI ABI moves to version 2.
- **Release-gates-check-092**: Additive on 091, adds public-surface drift
  check, version consistency gate (0.9.2), and reason-code registry
  completeness gate.
- **Reason-code registry normalization**: one declarative lowercase registry
  now drives Rust, C, logs, metrics, diagnostics, and generated projections.
  The project removed the former C-only uppercase mirror.
- **README consistency verification**: English and Chinese READMEs verified
  for version, directive, and default-value consistency.

### Historical release records

Release-specific implementation recaps before the current 0.9.x line are
kept in the [release notes](../releases/) and [CHANGELOG](../../CHANGELOG.md).
This status document intentionally records the current release state and
verification posture rather than duplicating the historical project ledger.

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

Earlier release-specific changes remain available in the linked release notes
and changelog. This current-status page deliberately omits those details.

This assessment rests on:

- Implementation of core features and runtime configurability
- Test coverage across unit, integration, E2E, and property-based tests
- Documentation suite covering installation, configuration, operations, and architecture
- CI/CD pipeline with automated builds and security scanning
- Shared-memory metrics aggregation across workers in the module implementation
- Further decomposition of the NGINX module into focused config wiring/core/handlers, request-state, payload buffering/replay, conversion/output, lifecycle, and metrics helper units
- Shared native-build helper logic for Rust/NGINX verification scripts, including aligned macOS deployment-target handling
- Delegated runtime validations now reuse an exported module-enabled `NGINX_BIN` only when it has a reusable runtime layout. Otherwise they fall back to self-building their own native NGINX runtime
- The GitHub Actions `runtime-regressions` job now retains the validated IMS runtime and reuses its `NGINX_BIN` for chunked and large-response checks. This avoids rebuilding native NGINX three times
- Canonical E2E coverage splits ownership across two directories. Compatibility wrapper scripts and suite entrypoints stay under `tools/e2e/`. Migrated fixtures, requests, and assertions live under `tools/e2e-harness/`. The `make test-e2e` target delegates to a focused proxy/TLS, chunked, and large-response suite instead of maintaining a second full inline runner. Native and not-yet-migrated scenarios stay in the inline runner until they move to `tools/e2e/`
- The Rust converter now keeps the public `ffi.rs` and `metadata.rs` entrypoints. It pushes ABI decoding, memory handling, export wiring, metadata traversal, and URL resolution into focused submodules
- `cargo-fuzz` targets and nightly fuzz workflow for parser, FFI, and security-validator paths
- A separate non-blocking Darwin/macOS smoke workflow validates native Rust build plus real-nginx runtime checks on GitHub-hosted macOS
- Release artifacts and installation tooling

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
- Bounded large-response streaming selected by the internal response-shape heuristic
- Forwarded header trust control with `markdown_trusted_proxies` directive

## Test Coverage

The project includes tests at multiple levels:

### Rust Tests

- Unit tests for all core modules (converter, parser, security, and so on)
- Integration tests for FFI boundary and lifecycle management
- Property-based tests for invariants and edge cases
- Timeout and error handling tests
- YAML front matter and ETag generation tests
- Security tests for XSS, XXE, and SSRF prevention

Run with: `cargo test --all` or `make test-rust`

### NGINX Module Tests

- Unit tests for major components (30+ test targets)
- Standalone tests that do not require system NGINX
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
3. **Set appropriate limits**: Configure `markdown_limits` (for example `conversion_memory=<size> conversion_timeout=<time>`)
4. **Choose failure mode**: Select `markdown_error_policy` based on requirements
5. **Test caching**: Verify cache behavior with your CDN or caching layer
6. **Review security**: Ensure authentication policies match your security model

See [DEPLOYMENT_EXAMPLES.md](../guides/DEPLOYMENT_EXAMPLES.md) for configuration patterns.

## Current Focus and Roadmap

### Current Release Line (0.9.x)

The 0.9.x release line is the current maintained line. The current
development version is 0.9.2. 0.9.1 remains the latest released patch until
the 0.9.2 release tag and release gates are complete. It is a
breaking surface-freeze release that consolidates the configuration surface
to the 25-directive contract (removing profile presets and per-path metrics),
freezes the observability surface (eleven v1 metric families, reason registry,
diagnostics JSON v1), advances the bundled FFI boundary to ABI 2, and moves
the musl dynamic-module build before publication, on top of the 0.9.1
streaming-decompression and zero-copy foundation.

#### 0.9.1 (previous release)

Details for 0.9.1 (hybrid zero-copy streaming, streaming decompression,
performance evidence gate, harness rules 56-62, FFI ABI v1) are in the
[0.9.1 release notes](../releases/0.9.1-release-notes.md) and the
[CHANGELOG](../../CHANGELOG.md).

#### 0.9.0 (previous breaking release)

Details for 0.9.0 (Config V2 directives, profile system, and the
0.8.x→0.9.0 migration mapping) are in the
[0.9.0 release notes](../releases/0.9.0-release-notes.md),
[MIGRATION-0.9.0.md](../guides/MIGRATION-0.9.0.md), and the
[CHANGELOG](../../CHANGELOG.md).

The [0.8.x release notes](../releases/) and the
[CHANGELOG](../../CHANGELOG.md) record historical architecture and patch
details.

### Near-Term
- Expand streaming rollout samples across mixed traffic profiles
- Increase automated evidence collection for release-gate checks
- Continue operator-facing diagnostics hardening for drift/degradation cases

### Future Exploration
- Additional Markdown flavors and output formats
- Expanded observability integrations beyond built-in shared metrics and the
  removed OTel tracing proposal

## Known Limitations

The following limitations appear in the documentation:

1. **Streaming Is Default**: Streaming is the default policy, with
   `markdown_streaming off|auto|force`. `off` explicitly selects full-buffer
   conversion and ineligible or failed streaming requests follow fallback
   policy.
2. **HTML Input**: Requires HTML input (uncompressed or automatically decompressed)
3. **Conversion Fidelity**: Some complex HTML structures may not convert perfectly to Markdown
4. **Performance Overhead**: Large documents incur conversion overhead (mitigated by caching)

These limitations are acceptable for current use cases. Future releases may
address them.

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
- [DECOMPRESSION.md](../features/DECOMPRESSION.md)
- [CACHE_AWARE_RESPONSES.md](../features/CACHE_AWARE_RESPONSES.md)
- [CONTENT_NEGOTIATION.md](../features/CONTENT_NEGOTIATION.md)
- [COOPERATIVE_TIMEOUT.md](../features/COOPERATIVE_TIMEOUT.md)
- [TOKEN_ESTIMATOR.md](../features/TOKEN_ESTIMATOR.md)
- [YAML_FRONT_MATTER.md](../features/YAML_FRONT_MATTER.md)
- [SECURITY_MODEL.md](../features/SECURITY_MODEL.md)
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
- Rust 1.97.1 is the repository's pinned build toolchain. Rust 1.97 is the
  public source-build MSRV

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
| supported | 48 |
| experimental | 1 |
| best-effort | 1 |

### Release-Blocking Entries

| Entry | Workflow |
|-------|----------|
| 1.24.0 debian12 glibc amd64 deb-package | `.github/workflows/release-packages.yml` |
| 1.24.0 debian12 glibc arm64 deb-package | `.github/workflows/release-packages.yml` |
| 1.24.0 linux glibc amd64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.24.0 linux musl amd64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.24.0 linux glibc arm64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.24.0 linux musl arm64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.24.0 almalinux9 glibc amd64 rpm-package | `.github/workflows/release-packages.yml` |
| 1.24.0 almalinux9 glibc arm64 rpm-package | `.github/workflows/release-packages.yml` |
| 1.26.3 debian12 glibc amd64 deb-package | `.github/workflows/release-packages.yml` |
| 1.26.3 debian12 glibc arm64 deb-package | `.github/workflows/release-packages.yml` |
| 1.26.3 debian12 glibc amd64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.26.3 alpine3.20 musl amd64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.26.3 debian12 glibc arm64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.26.3 alpine3.20 musl arm64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.26.3 linux glibc amd64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.26.3 linux musl amd64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.26.3 linux glibc arm64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.26.3 linux musl arm64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.26.3 almalinux9 glibc amd64 rpm-package | `.github/workflows/release-packages.yml` |
| 1.26.3 almalinux9 glibc arm64 rpm-package | `.github/workflows/release-packages.yml` |
| 1.28.3 debian12 glibc amd64 deb-package | `.github/workflows/release-packages.yml` |
| 1.28.3 debian12 glibc arm64 deb-package | `.github/workflows/release-packages.yml` |
| 1.28.3 linux glibc amd64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.28.3 linux musl amd64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.28.3 linux glibc arm64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.28.3 linux musl arm64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.28.3 almalinux9 glibc amd64 rpm-package | `.github/workflows/release-packages.yml` |
| 1.28.3 almalinux9 glibc arm64 rpm-package | `.github/workflows/release-packages.yml` |
| 1.30.4 debian12 glibc amd64 deb-package | `.github/workflows/release-packages.yml` |
| 1.30.4 debian12 glibc arm64 deb-package | `.github/workflows/release-packages.yml` |
| 1.30.4 linux glibc amd64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.30.4 linux musl amd64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.30.4 linux glibc arm64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.30.4 linux musl arm64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.30.4 almalinux9 glibc amd64 rpm-package | `.github/workflows/release-packages.yml` |
| 1.30.4 almalinux9 glibc arm64 rpm-package | `.github/workflows/release-packages.yml` |
| 1.31.4 debian12 glibc amd64 deb-package | `.github/workflows/release-packages.yml` |
| 1.31.4 debian12 glibc arm64 deb-package | `.github/workflows/release-packages.yml` |
| 1.31.4 debian12 glibc amd64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.31.4 alpine3.24 musl amd64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.31.4 debian12 glibc arm64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.31.4 alpine3.24 musl arm64 docker-image | `.github/workflows/official-nginx-docker.yml` |
| 1.31.4 linux glibc amd64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.31.4 linux musl amd64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.31.4 linux glibc arm64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.31.4 linux musl arm64 dynamic-module | `.github/workflows/release-packages.yml` |
| 1.31.4 almalinux9 glibc amd64 rpm-package | `.github/workflows/release-packages.yml` |
| 1.31.4 almalinux9 glibc arm64 rpm-package | `.github/workflows/release-packages.yml` |
<!-- END:release-matrix:status-matrix -->

## Summary

**NGINX Markdown for Agents** is on the 0.9.x line: 0.9.2 is the current
development line and 0.9.1 is the latest released patch. The project provides
HTML-to-Markdown conversion through NGINX content negotiation with a
streaming-default conversion model, bounded-memory processing, a full-buffer
fallback, and a repo-owned validation harness. Release readiness remains tied
to the current release matrix and the evidence-producing gates described in
the active release documentation.

### Key Components
- Core feature implementation
- Test coverage (unit, integration, E2E, property-based)
- Documentation for users, operators, and developers
- Deployment tooling and examples
- CI/CD pipeline with security scanning
- Multi-platform support (macOS, Linux, Docker)

### Current State
The team has implemented and tested the core feature set. Production and release
readiness remain pending: release assets and the required verification
evidence (performance baselines, SonarCloud scan on the final SHA, and the
full release-gate suite) stay blocked until all release gates pass. The focus
is on operational validation, performance optimization, and community feedback
integration.
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
| 0.9.2 | 2026-08-24 | Kang | Trimmed historical release recaps and kept the active status page linked to release notes and the changelog |
| 0.9.2 | 2026-08-15 | Kang | Split E2E coverage ownership between tools/e2e/ entrypoints and tools/e2e-harness/ migrated scenarios |
| 0.9.2 | 2026-08-08 | Kang | Fixed summary release line to 0.9.2; removed OTel and per-path metrics from current capabilities |
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation; retire markdown_large_body_threshold) |
| 0.8.3 | 2026-06-26 | Kang | 0.8.3 closeout: streaming state machine fixes, ExitMany batch unwind, decompression buffer memory safety, snapshot capacity, FFI Box::into_raw fix, full release gate validation |
| 0.8.2 | 2026-06-23 | Kang | 0.8.2 release: streaming decompression hardening, implied-closure correctness, FFI panic safety, decompression budget enforcement, security scan scoping, release-line documentation closeout |
| 0.8.0 | 2026-06-16 | Kang | Version bump to 0.8.0; true streaming contract, fallback state machine, streaming observability, streaming security enforcement, release matrix source of truth, streaming config directives |
| 0.7.0 | 2026-06-03 | Kang | Version bump to 0.7.0; add Rust-first architecture, decompression budget, diagnostics, dynconf dry-run, DEB/RPM, K8s, FFI ABI verification, CI supply-chain hardening |
| 0.6.3 | 2026-05-13 | Kang | Version bump to 0.6.3 for release |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
