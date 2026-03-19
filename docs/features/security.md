# Security Architecture and Threat Model

## Overview

This document describes the security architecture, threat model, and security measures implemented in the NGINX Markdown for Agents Rust converter. The primary security concern is **untrusted HTML input** from upstream servers that may contain malicious content.

This page focuses on implementation-level defenses and threat boundaries. For operator-facing rollout, configuration, and monitoring guidance, use `docs/guides/CONFIGURATION.md` and `docs/guides/OPERATIONS.md`.

## Threat Model

### Threats Addressed

1. **XSS (Cross-Site Scripting)**: Malicious JavaScript in HTML that could execute in downstream contexts
2. **XXE (XML External Entity)**: External entity references that could read local files or make network requests
3. **SSRF (Server-Side Request Forgery)**: External resource loading that could probe internal networks
4. **Code Injection**: Event handlers, inline scripts, and other executable content
5. **Resource Exhaustion**: Deeply nested structures or large documents that could cause DoS

### Trust Boundaries

```
[UNTRUSTED] ← Upstream HTML Content (primary threat)
[TRUSTED]   ← NGINX Module (validated code)
[TRUSTED]   ← Conversion Engine (Rust, memory-safe)
[TRUSTED]   ← Configuration (operator-controlled, validated at startup)
```

**Key Points:**
- **Upstream HTML**: Completely untrusted, may contain malicious content
- **Configuration**: Trusted input from operators, but validated to catch errors
- **Module Code**: Trusted after security review and testing
- **Conversion Engine**: Trusted, with Rust providing memory safety guarantees

## Defense Layers

### Layer 1: Input Validation

**Location**: `src/security.rs` - `SecurityValidator`

**Measures**:
- Validate HTML structure before processing
- Enforce maximum nesting depth (default: 1000 levels)
- Size limits enforced by caller (NGINX module)
- UTF-8 validation in parser

**Implementation**:
```rust
pub fn validate_depth(&self, depth: usize) -> Result<(), String> {
    if depth > self.max_depth {
        Err(format!("HTML nesting depth {} exceeds maximum", depth))
    } else {
        Ok(())
    }
}
```

### Layer 2: Element Sanitization

**Location**: `src/security.rs` - `DANGEROUS_ELEMENTS`, `FORM_ELEMENTS`

The converter classifies elements into three categories using `SanitizeAction`:

**Dangerous Elements — Fully Removed** (`SanitizeAction::Remove`):
- `<script>` - JavaScript execution
- `<style>` - CSS injection (can contain expressions)
- `<applet>` - Legacy Java applets
- `<link>` - Can load external stylesheets with expressions
- `<base>` - Can change base URL for all relative URLs
- `<noscript>` - Alternative content, usually redundant with main content

**Form Elements — Tags Stripped, Content Preserved** (`SanitizeAction::StripElement`):
- `<form>`, `<button>`, `<select>`, `<textarea>`, `<fieldset>`, `<legend>`, `<label>`, `<option>`, `<optgroup>`, `<datalist>`, `<output>` - Tags are removed but child text content is preserved in the Markdown output. This ensures AI agents see meaningful content (labels, button captions, option lists) without raw HTML leaking into the output.
- `<input>` (void form control) - Descriptive text is extracted from attributes in priority order: `aria-label` > `placeholder` > `value`. Hidden inputs (`type="hidden"`) are suppressed entirely.

**Embedded Content Elements — Tags Stripped, URL Extracted, Fallback Preserved** (`SanitizeAction::StripElement`):
- `<iframe>`, `<object>`, `<embed>` - Tags are removed. The `src` (iframe/embed) or `data` (object) URL is extracted as a Markdown link, using the `title` attribute as the link label when available. Fallback child text between the tags is preserved. Dangerous URL schemes (`javascript:`, `data:`, etc.) are suppressed — only safe URLs appear in the output.

**Media Elements — URL Extracted, Fallback Preserved** (handled in traversal):
- `<video>`, `<audio>` - The `src` URL is extracted as a Markdown link (with `title` as label). Video `poster` thumbnails are extracted as Markdown images. Fallback child text is preserved via normal child traversal.
- `<source>` - The `src` URL is extracted as a Markdown link with `type` as label (e.g., `[video/mp4](url)`).
- `<track>` - The `src` URL is extracted as a Markdown link with `label` as link text (e.g., `[English](subs.vtt)`).
- `<area>` - The `href` is extracted as a Markdown link with `alt` or `title` as link text.

**Implementation**:
```rust
pub fn check_element(&self, tag_name: &str) -> SanitizeAction {
    if DANGEROUS_ELEMENTS.contains(&tag_name) {
        SanitizeAction::Remove
    } else if FORM_ELEMENTS.contains(&tag_name)
        || EMBEDDED_CONTENT_ELEMENTS.contains(&tag_name)
    {
        SanitizeAction::StripElement
    } else {
        SanitizeAction::Allow
    }
}
```

### Layer 3: Attribute Sanitization

**Location**: `src/security.rs` - `SecurityValidator::is_event_handler()`

**Event Handlers Removed**:
All attributes starting with `on` (with length > 2) are removed via prefix matching, following the OWASP/DOMPurify convention. This covers all current and future event handlers, including:
- `onclick`, `ondblclick`, `onmousedown`, `onmouseup`
- `onmouseover`, `onmousemove`, `onmouseout`
- `onkeydown`, `onkeypress`, `onkeyup`
- `onload`, `onunload`, `onerror`
- `onfocus`, `onblur`, `onchange`, `onsubmit`
- `onpointerdown`, `ontouchstart`, `onbeforeinput`, and any future `on*` handlers

**Implementation**:
```rust
pub fn is_event_handler(&self, attr_name: &str) -> bool {
    attr_name.starts_with("on") && attr_name.len() > 2
}
```

### Layer 4: URL Sanitization

**Location**: `src/security.rs` - `DANGEROUS_URL_SCHEMES`

**Dangerous URL Schemes Blocked**:
- `javascript:` - JavaScript execution
- `data:` - Can contain executable content
- `vbscript:` - VBScript execution (legacy IE)
- `file:` - Local file access (SSRF)
- `about:` - Browser internal URLs

**Safe URL Schemes Allowed**:
- `https:` - Secure HTTP
- `http:` - Standard HTTP
- Relative URLs (`/path`, `../parent`)
- Fragment identifiers (`#anchor`)

**Implementation**:
```rust
pub fn is_dangerous_url(&self, url: &str) -> bool {
    let url_lower = url.trim().to_lowercase();
    DANGEROUS_URL_SCHEMES
        .iter()
        .any(|scheme| url_lower.starts_with(scheme))
}
```

**Applied to**:
- `<a href="...">` - Links
- `<img src="...">` - Images (when URL is blocked, `alt` text is preserved as plain text; `title` attribute is included in Markdown image syntax)

### Layer 5: XXE Prevention

**Location**: `src/parser.rs` - html5ever parser

**Mechanism**: html5ever is an HTML5 parser, not an XML parser. HTML5 does not support external entity references, so XXE attacks are prevented by design.

**Key Points**:
- html5ever does NOT resolve external entities
- html5ever does NOT process DOCTYPE declarations for entity definitions
- html5ever does NOT load external DTDs
- Entity references are treated as text content, not executable directives

**Documentation**:
```rust
pub fn xxe_prevention_documentation() -> &'static str {
    "html5ever is an HTML5 parser that does not support XML external entities. \
     HTML5 does not have a concept of external entities, so XXE attacks are \
     prevented by design."
}
```

### Layer 6: Memory Safety (Rust)

**Rust Safety Guarantees**:
- No buffer overflows (bounds checking)
- No null pointer dereferences (Option types)
- No use-after-free (ownership system)
- No data races (borrow checker)

**Unsafe Code Audit**:
All `unsafe` blocks in the codebase are:
1. Minimized in scope
2. Documented with safety invariants
3. Audited for correctness
4. Located only in FFI boundary code

## Security Testing

### Test Coverage

**Location**: `tests/security_tests.rs`

**Test Categories**:

1. **XSS Prevention** (10 tests):
   - Script tag removal
   - Inline script removal
   - Event handler removal
   - JavaScript URL blocking (case-insensitive)
   - Data URL blocking
   - VBScript URL blocking

2. **SSRF Prevention** (5 tests):
   - iframe tag stripping with URL extraction and dangerous scheme suppression
   - object tag stripping with URL extraction and dangerous scheme suppression
   - embed tag stripping with URL extraction
   - file: URL blocking
   - Dangerous URL scheme suppression in embedded content

3. **XXE Prevention** (2 tests):
   - DOCTYPE entity handling
   - External entity handling

4. **URL Sanitization** (2 tests):
   - Dangerous URL blocking
   - Safe URL preservation

5. **Comprehensive Tests** (5 tests):
   - Multiple XSS vectors
   - Deep nesting
   - GFM mode security
   - Style/link/base tag removal

**Total**: 23 security-specific tests, all passing

### Running Security Tests

```bash
cd components/rust-converter
cargo test --test security_tests
```

### Fuzzing

For continuous security testing, use the `cargo-fuzz` targets in `components/rust-converter/fuzz/`:

```bash
cargo +nightly install cargo-fuzz --locked

cd components/rust-converter
cargo +nightly fuzz run parser_html
cargo +nightly fuzz run ffi_convert
cargo +nightly fuzz run security_validator
```

The repository also includes `.github/workflows/nightly-fuzz.yml`, which runs these targets on a nightly schedule and uploads fuzz artifacts/corpora for inspection.

## Security Best Practices

### 1. Principle of Least Privilege
- Module runs with NGINX worker privileges (non-root)
- No elevated permissions required
- No file system access beyond NGINX configuration

### 2. Fail Secure
- Default to fail-open (return the original eligible HTML response) to maintain availability
- Conversion failures do not expose internal details
- Error messages are generic to clients, detailed in logs

### 3. Defense in Depth
- Multiple layers of validation and sanitization
- Each layer provides independent protection
- Bypass of one layer does not compromise security

### 4. Secure Defaults
- Conservative resource limits (10MB max size, 5s timeout)
- Fail-open strategy prevents DoS
- All dangerous elements/attributes blocked by default

### 5. Audit Logging
- Security-relevant events logged for monitoring
- No sensitive information in logs (tokens, cookies, personal data)
- Detailed error information only in server logs, not client responses

### 6. Dependency Management
- Regular dependency updates
- Automated vulnerability scanning (cargo-audit)
- Minimal dependency footprint

## Vulnerability Disclosure

### Reporting Security Issues

**DO NOT** open public GitHub issues for security vulnerabilities.

Instead, please report security issues via:
- A private security advisory on GitHub

### Response Process

This is a maintainer-run project, so response times are best effort and may be slower during holidays or periods of limited availability.

1. **Acknowledgment**: Target within 14 calendar days
2. **Assessment**: Target within 45 calendar days when enough detail is available
3. **Fix Development**: Priority based on severity and maintainer availability
4. **Disclosure**: Coordinated with reporter

### Security Updates

Security updates are released as:
- Patch releases for critical vulnerabilities
- Minor releases for moderate vulnerabilities
- Documented in CHANGELOG.md with CVE references

## Operator Follow-Through

The implementation details in this document feed into a few operator-facing concerns, but those concerns are maintained elsewhere:

- resource limits and failure policy: `docs/guides/CONFIGURATION.md`
- metrics, logs, and troubleshooting: `docs/guides/OPERATIONS.md`
- request-path security boundaries and failure branches: `docs/architecture/REQUEST_LIFECYCLE.md`

## Known Limitations

### 1. Content Extraction Heuristics
- Conversion is heuristic, not perfect
- Some malicious content may be rendered as text
- This is acceptable as text cannot execute

### 2. CSS Injection
- `<style>` tags are removed entirely
- Inline `style` attributes are preserved (safe in Markdown)
- CSS expressions cannot execute in Markdown output

### 3. HTML Entity Handling
- HTML entities are decoded by html5ever
- Malicious entity sequences are treated as text
- No entity expansion attacks possible

### 4. Unicode Normalization
- No Unicode normalization performed
- Homograph attacks not mitigated (out of scope)
- Markdown consumers should handle Unicode safely

## Compliance

### Standards Compliance

- **OWASP Top 10**: Addresses A03:2021 (Injection)
- **CWE-79**: XSS prevention
- **CWE-611**: XXE prevention
- **CWE-918**: SSRF prevention

### Security Certifications

- Memory safety guaranteed by Rust
- No unsafe code in conversion logic
- FFI boundary audited for safety

## References

### Security Resources

- [OWASP XSS Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html)
- [OWASP XXE Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/XML_External_Entity_Prevention_Cheat_Sheet.html)
- [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)

### Implementation References

- html5ever: https://github.com/servo/html5ever
- Rust Security Guidelines: https://anssi-fr.github.io/rust-guide/

## Changelog

### Version 1.0 (Current)
- Initial security implementation
- XSS prevention (script tags, event handlers, dangerous URLs)
- XXE prevention (html5ever design)
- SSRF prevention (iframe, object, embed removal)
- Comprehensive security test suite
- Security documentation

---

**Last Updated**: 2024-02-25  
**Version**: 1.0  
**Status**: Production Ready
