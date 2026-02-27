# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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

## [1.0.0] - 2026-02-26

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
- NGINX 1.18.0+
- Rust 1.70.0+

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

#### Upgrading to 1.0.0

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
