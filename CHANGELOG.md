# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
