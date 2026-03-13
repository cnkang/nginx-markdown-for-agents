# NGINX Markdown for Agents - Project Status

## Status Snapshot

This project is a production-oriented NGINX filter module backed by a Rust HTML-to-Markdown converter (via FFI). It performs HTTP content negotiation and returns Markdown when clients request `Accept: text/markdown`.

## Current Assessment

As of **March 13, 2026**, the project is at **version 0.2.1** with additional maintainability and validation work reflected in the current codebase. Core features are implemented and tested. The codebase includes unit, integration, E2E, and fuzz-oriented validation entrypoints, along with documentation covering installation, configuration, operations, and architecture.

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

## Release 0.2.1 Highlights

The latest release (0.2.1, March 11, 2026) includes:

### Added
- CI jobs for clang compiler and AddressSanitizer/UndefinedBehaviorSanitizer smoke tests
- SonarCloud Quality Gate Status badge in both English and Chinese READMEs

### Changed
- Updated documentation to reflect correct Rust minimum version (1.85.0+ for edition 2024)
- Corrected NGINX minimum version references across all documentation (1.24.0+)
- Added missing feature documentation references (CONTENT_NEGOTIATION.md, CACHE_AWARE_RESPONSES.md) to feature index
- Updated component README source layouts to match current file structure
- Removed placeholder migration notes for non-existent future versions from Operations guide
- Corrected make target references throughout documentation
- Added clang and sanitizer smoke test targets to testing documentation

### Fixed
- Rust minimum version requirement in all documentation (was 1.70.0+, now 1.85.0+)
- NGINX minimum version in CONTRIBUTING.md (was 1.18.0, now 1.24.0)

## Previous Release: 0.2.0 (March 6, 2026)

### New Features
- Variable-driven `markdown_filter` support using NGINX variables and complex values
- Phase-consistent decision caching for reliable header and body processing
- Enhanced installation script with Docker support (`SKIP_ROOT_CHECK=1`)
- Simplified Chinese documentation (`README_zh-CN.md`)
- Comprehensive deployment examples guide
- Complete architecture documentation suite

### Improvements
- Refactored NGINX module internals for better maintainability
- Split Rust converter internals into focused renderer submodules
- Split NGINX module config wiring/core/handlers, request-state, payload buffering/replay, conversion/output, lifecycle, and metrics endpoint logic into focused helper units
- Tightened authenticated-request cache-control handling
- Hardened canonical E2E validation with TLS backend support
- Enhanced CI/CD workflows and release automation
- Refreshed documentation across all guides and references

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
- Security scanning with CodeQL and Snyk
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

### Current Release (0.2.1)
- Documentation accuracy improvements
- Corrected version requirements (Rust 1.85.0+, NGINX 1.24.0+)
- Enhanced CI with clang and sanitizer smoke tests
- Updated feature documentation index
- Improved testing documentation

### Near-Term
- Performance regression tracking with CI artifact capture
- Deployment validation across diverse environments
- Community feedback integration
- Parser-path optimization opportunities

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
- **Security Scanning**: CodeQL and Snyk vulnerability scanning
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

**NGINX Markdown for Agents** is at version 0.2.0. The project provides HTML-to-Markdown conversion through NGINX content negotiation.

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
