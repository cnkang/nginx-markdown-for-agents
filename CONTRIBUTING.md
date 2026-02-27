# Contributing to NGINX Markdown for Agents

Thank you for your interest in contributing to this project! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Workflow](#development-workflow)
- [Coding Standards](#coding-standards)
- [Testing Requirements](#testing-requirements)
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

- Rust 1.70.0 or higher
- NGINX 1.18.0 or higher (source code for module development)
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

## Development Workflow

### Branch Strategy

- `main` - Stable, production-ready code
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
