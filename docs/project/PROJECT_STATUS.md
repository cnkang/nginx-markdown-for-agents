# NGINX Markdown for Agents - Project Status

## Status Snapshot

This project is a production-oriented NGINX filter module backed by a Rust HTML-to-Markdown converter (via FFI). It performs HTTP content negotiation and returns Markdown when clients request `Accept: text/markdown`.

## Current Assessment

As of **version 0.4.0**, the project includes Prometheus-compatible metrics, unified decision reason codes, rollout and rollback operational guides, a benchmark corpus with evidence-based regression detection, restructured installation and first-run documentation, and parser path optimizations. Core features are implemented and tested. The codebase includes unit, integration, E2E, and fuzz-oriented validation entrypoints, along with documentation covering installation, configuration, operations, and architecture.

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
- X-Forwarded-Host/Proto headers are no longer trusted by default; added `markdown_trust_forwarded_headers on|off` directive (default: off)
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
- Large response routing with `markdown_large_body_threshold` directive
- Forwarded header trust control with `markdown_trust_forwarded_headers` directive

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
3. **Set appropriate limits**: Configure `markdown_max_size` and `markdown_timeout`
4. **Choose failure mode**: Select `markdown_on_error` based on requirements
5. **Test caching**: Verify cache behavior with your CDN or caching layer
6. **Review security**: Ensure authentication policies match your security model

See [DEPLOYMENT_EXAMPLES.md](../guides/DEPLOYMENT_EXAMPLES.md) for configuration patterns.

## Current Focus and Roadmap

### Current Release (0.4.0)
- Prometheus-compatible metrics endpoint for operational monitoring
- Unified decision reason codes for conversion transparency
- Rollout cookbook with selective enablement and canary patterns
- Rollback guide with trigger conditions and executable procedures
- Benchmark corpus with reproducible evidence and regression detection
- Parser path optimizations: noise region pruning, simple structure fast path
- Restructured installation guide with shortest success path
- Incremental processing for large responses
- Matrix-driven release automation pipeline
- Performance baseline gating system
- Variable-driven configuration support
- Enhanced installation tooling
- Shared metrics aggregation and runtime-regression coverage
- Hardened CI/CD pipeline

### Near-Term
- Performance regression tracking with CI artifact capture
- Deployment validation across diverse environments
- Community feedback integration

### Future Exploration
- Streaming-oriented conversion approaches for large documents
- Additional Markdown flavors and output formats
- Expanded observability integrations beyond the built-in shared metrics endpoint
- Performance improvements for high-throughput scenarios

## Known Limitations

The following limitations are documented:

1. **Full Buffering Required**: The module buffers the entire response before conversion (no streaming)
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
- Rust 1.85.0 and later

### Docker Support
- Official NGINX base images
- Source build examples
- Installation script integration

See `examples/docker/` for Docker build examples.

## Summary

**NGINX Markdown for Agents** is at version 0.4.0. The project provides HTML-to-Markdown conversion through NGINX content negotiation, with Prometheus-compatible metrics, unified decision reason codes, rollout and rollback operational guides, a benchmark corpus with evidence-based regression detection, parser path optimizations, incremental processing for large responses, release automation, performance baseline gating, runtime validation reuse, fuzzing workflows, and shared metrics aggregation for observability.

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
