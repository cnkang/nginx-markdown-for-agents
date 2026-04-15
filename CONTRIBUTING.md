# Contributing to NGINX Markdown for Agents

Thank you for your interest in contributing to this project! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Harness Skill Setup (Optional but Recommended)](#harness-skill-setup-optional-but-recommended)
- [Development Workflow](#development-workflow)
- [Coding Standards](#coding-standards)
- [Testing Requirements](#testing-requirements)
- [Continuous Integration](#continuous-integration)
- [Documentation](#documentation)
- [Submitting Changes](#submitting-changes)

## Code of Conduct

This project follows a professional and respectful code of conduct. Please:

- Be respectful and inclusive
- Focus on constructive feedback
- Assume good intentions
- Help create a welcoming environment

## Getting Started

### Prerequisites

Before contributing, ensure you have:

- Rust 1.91.0 or higher
- NGINX 1.24.0 or higher (source code for module development)
- cbindgen for C header generation
- Basic understanding of NGINX module development (for C contributions)
- Familiarity with Rust (for converter contributions)

### Setting Up Development Environment

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-org/nginx-markdown-for-agents.git
   cd nginx-markdown-for-agents
   ```

2. **Install dependencies:**
   ```bash
   # Install Rust (if not already installed)
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   
   # Install cbindgen
   cargo install cbindgen
   ```

3. **Build the project:**
   ```bash
   make build
   ```

4. **Run tests:**
   ```bash
   make test-all
   ```

## Harness Skill Setup (Optional but Recommended)

If you plan to maintain harness-related surfaces (`AGENTS.md`,
`docs/harness/`, `tools/harness/`, harness CI wiring), install the local helper
skill so your IDE/agent can route and verify changes consistently.

From the repository root:

```bash
npx skills add . --full-depth --skill nginx-markdown-harness-maintenance -y
npx skills ls
```

Why `--full-depth`: this repository keeps skills under `skills/`, so recursive
discovery is required.

For full setup details (project/global/manual symlink options and verification),
see [docs/guides/HARNESS_SKILL_SETUP.md](docs/guides/HARNESS_SKILL_SETUP.md).

## Development Workflow

### Branch Strategy

- `main` - Stable primary branch
- `develop` - Integration branch for features
- `feature/*` - Feature development branches
- `bugfix/*` - Bug fix branches
- `hotfix/*` - Critical production fixes

### Creating a Feature Branch

```bash
git checkout -b feature/your-feature-name
```

### Making Changes

1. Make your changes in the appropriate component:
   - Rust converter: `components/rust-converter/`
   - NGINX module: `components/nginx-module/`
   - Documentation: `docs/`

2. Write tests for your changes

3. Update documentation as needed

4. Run tests locally:
   ```bash
   # Rust tests
   cd components/rust-converter
   cargo test --all
   
   # NGINX module tests
   make -C components/nginx-module/tests unit
   ```

5. Ensure code formatting:
   ```bash
   # Rust formatting
   cd components/rust-converter
   cargo fmt --all
   
   # Check Rust code
   cargo clippy --all-targets --all-features
   ```

## Coding Standards

### Rust Code

- Follow Rust standard style guidelines
- Use `cargo fmt` for formatting
- Address all `cargo clippy` warnings
- Write comprehensive documentation comments
- Include examples in doc comments where appropriate

**Example:**
```rust
/// Converts HTML to Markdown format.
///
/// # Arguments
///
/// * `html` - The HTML string to convert
/// * `options` - Conversion options
///
/// # Returns
///
/// Returns `Ok(String)` with Markdown on success, or `Err` on failure.
///
/// # Examples
///
/// ```
/// use nginx_markdown_converter::convert_html_to_markdown;
///
/// let html = "<h1>Title</h1>";
/// let markdown = convert_html_to_markdown(html, Default::default())?;
/// assert_eq!(markdown, "# Title\n");
/// ```
pub fn convert_html_to_markdown(html: &str, options: ConversionOptions) -> Result<String> {
    // Implementation
}
```

### C Code (NGINX Module)

- Follow NGINX coding style
- Use NGINX memory management functions
- Handle errors appropriately
- Add comprehensive comments
- Ensure thread safety

**Example:**
```c
/*
 * Parse Accept header and determine if Markdown is requested.
 *
 * Returns:
 *   NGX_OK if Markdown is requested
 *   NGX_DECLINED if Markdown is not requested
 *   NGX_ERROR on parsing error
 */
static ngx_int_t
ngx_http_markdown_parse_accept(ngx_http_request_t *r)
{
    // Implementation
}
```

### Commit Messages

Follow the Conventional Commits specification:

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

**Examples:**
```
feat(converter): add support for table conversion

Implement HTML table to Markdown table conversion with proper
column alignment and header detection.

Closes #123
```

```
fix(nginx): handle chunked transfer encoding correctly

Fix buffer management for chunked responses to prevent
memory leaks and ensure complete conversion.

Fixes #456
```

## Testing Requirements

### Rust Tests

All Rust code changes must include:

1. **Unit tests** for new functions
2. **Integration tests** for new features
3. **Property-based tests** for invariants (when applicable)

```bash
# Run all Rust tests
cd components/rust-converter
cargo test --all

# Run specific test
cargo test test_name

# Run with coverage
cargo tarpaulin --out Html
```

### NGINX Module Tests

All NGINX module changes must include:

1. **Unit tests** for isolated functionality
2. **Integration tests** for NGINX runtime behavior

```bash
# Run unit tests
make -C components/nginx-module/tests unit

# Run specific unit test
make -C components/nginx-module/tests unit-eligibility
```

### Test Coverage

- Aim for >80% code coverage for new code
- Critical paths should have >95% coverage
- Include edge cases and error conditions

## Continuous Integration

The project uses several GitHub Actions workflows to maintain quality. Understanding these helps you anticipate what checks your PR will face.

### PR CI (`ci.yml`)

Every pull request triggers path-filtered jobs:

- **Rust Quality Gate** — formatting (`cargo fmt`), linting (`cargo clippy -D warnings`), and `make test-rust`.
- **NGINX C Tests** — unit tests, integration harness, Clang smoke, and sanitizer (ASan/UBSan) smoke.
- **Runtime Regressions** — end-to-end validation of If-Modified-Since, chunked streaming, and large-response handling against a real NGINX build.
- **Docs Check** — duplicate and consistency checks on documentation files.
- **Perf Smoke Gate** — runs the `small` and `medium` performance tiers, then executes the threshold engine against committed baselines. Triggered when Rust source, `perf/`, or `tools/perf/` files change.

### Performance Gating (`nightly-perf.yml` / `perf-smoke` in `ci.yml`)

Performance is guarded at two levels:

1. **PR smoke gate** (`perf-smoke` job in `ci.yml`) — runs the `small` and `medium` benchmark tiers on every PR that touches Rust or perf-related files. The threshold engine compares measurements against platform baselines in `perf/baselines/` using thresholds defined in `perf/thresholds.json`. A regression beyond the configured threshold fails the check.

2. **Nightly full suite** (`nightly-perf.yml`) — runs daily at 03:00 UTC. Executes all tiers (`small`, `medium`, `medium-front-matter`, `large-1m`) with 3 repeats each, computes medians, and runs the threshold engine. Supports a manual `bootstrap_baseline` mode to generate fresh baseline artifacts for a new platform. Measurement and verdict reports are uploaded as workflow artifacts.

Key files:
- `perf/baselines/<platform>.json` — committed baseline measurements per platform.
- `perf/thresholds.json` — per-metric regression thresholds.
- `perf/metrics-schema.json` — metric definitions.
- `tools/perf/threshold_engine.py` — compares current vs. baseline and emits a verdict.
- `tools/perf/run_perf_baseline.sh` — benchmark runner script.

### Install Verification (`install-verify.yml`)

Validates that the built NGINX module installs and works correctly across supported platforms. Runs weekly (Sunday 03:00 UTC) and on manual dispatch.

The workflow:
1. Reads `tools/release-matrix.json` to select representative install targets (lower-bound, latest-stable, latest-mainline, and upper-bound NGINX versions) across glibc and musl on x86_64 and aarch64.
2. For each target, installs the exact NGINX version (from nginx.org repos or official Docker images), runs `tools/install.sh`, and validates:
   - Install script exit code and JSON output match expectations.
   - `nginx -t` passes with the module loaded.
   - The `.so` module file exists in the modules directory.
   - A smoke test serves HTML through the module and verifies Markdown conversion output.
3. Uploads per-target verification summaries as artifacts.

### Release Binaries (`release-binaries.yml`)

Builds pre-compiled module binaries for every full-support entry in `tools/release-matrix.json`. Runs on GitHub Release publish events and manual dispatch.

The workflow:
1. Resolves the build matrix from `tools/release-matrix.json` (full-support entries only).
2. Builds each NGINX version × OS type × architecture combination via `tools/build_release.sh`.
3. Runs a completeness check (`tools/release/completeness_check.py`) to verify that every expected matrix entry produced an artifact.
4. On release events, publishes all `.tar.gz` artifacts and a grouped version manifest to the GitHub Release.

## Documentation

### Documentation Requirements

All contributions must include appropriate documentation:

1. **Code comments** for complex logic
2. **API documentation** for public functions
3. **User documentation** for new features
4. **Update existing docs** if behavior changes

### Documentation Locations

- **User guides**: `docs/guides/`
- **Feature docs**: `docs/features/`
- **Testing docs**: `docs/testing/`
- **API docs**: Generated from code comments

### Documentation Style

- Write in clear, concise English
- Use active voice
- Include examples
- Provide context and rationale
- Link to related documentation

## Submitting Changes

### Pull Request Process

1. **Ensure all tests pass:**
   ```bash
   make test-all
   ```

2. **Update documentation:**
   - Update relevant docs in `docs/`
   - Update CHANGELOG.md (if applicable)
   - Update README.md (if needed)

3. **Create pull request:**
   - Use a descriptive title
   - Reference related issues
   - Provide detailed description
   - Include test results
   - Add screenshots (if UI changes)

4. **Pull request template:**
   ```markdown
   ## Description
   Brief description of changes
   
   ## Related Issues
   Closes #123
   
   ## Changes Made
   - Change 1
   - Change 2
   
   ## Testing
   - [ ] Unit tests pass
   - [ ] Integration tests pass
   - [ ] Manual testing completed
   
   ## Documentation
   - [ ] Code comments added
   - [ ] User documentation updated
   - [ ] API documentation updated
   
   ## Checklist
   - [ ] Code follows project style guidelines
   - [ ] Tests added for new functionality
   - [ ] All tests pass
   - [ ] Documentation updated
   - [ ] Commit messages follow convention
   ```

### Review Process

1. Maintainers will review your pull request
2. Address any feedback or requested changes
3. Once approved, your changes will be merged

### After Merge

- Your contribution will be included in the next release
- You'll be credited in the release notes
- Thank you for contributing!

## Getting Help

If you need help:

- Check existing documentation in `docs/`
- Review closed issues and pull requests
- Open a new issue with your question
- Join community discussions (if available)

## Recognition

Contributors are recognized in:

- Release notes
- CHANGELOG.md
- Project documentation

Thank you for contributing to NGINX Markdown for Agents!
