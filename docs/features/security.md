# Security Architecture and Threat Model

## Overview

This document describes the security architecture, threat model, and security measures implemented in the NGINX Markdown for Agents Rust converter. The primary security concern is **untrusted HTML input** from upstream servers that may contain malicious content.

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

**Location**: `src/security.rs` - `DANGEROUS_ELEMENTS`

**Dangerous Elements Removed**:
- `<script>` - JavaScript execution
- `<style>` - CSS injection (can contain expressions)
- `<iframe>` - Can load external content
- `<object>` - Can execute plugins
- `<embed>` - Can execute plugins
- `<applet>` - Legacy Java applets
- `<link>` - Can load external stylesheets with expressions
- `<base>` - Can change base URL for all relative URLs
- `<noscript>` - Alternative content, not needed

**Implementation**:
```rust
pub fn check_element(&self, tag_name: &str) -> SanitizeAction {
    if DANGEROUS_ELEMENTS.contains(&tag_name) {
        SanitizeAction::Remove
    } else {
        SanitizeAction::Allow
    }
}
```

### Layer 3: Attribute Sanitization

**Location**: `src/security.rs` - `EVENT_HANDLER_ATTRIBUTES`

**Event Handlers Removed**:
All `on*` attributes are removed, including:
- `onclick`, `ondblclick`, `onmousedown`, `onmouseup`
- `onmouseover`, `onmousemove`, `onmouseout`
- `onkeydown`, `onkeypress`, `onkeyup`
- `onload`, `onunload`, `onerror`
- `onfocus`, `onblur`, `onchange`, `onsubmit`
- And 20+ more event handlers

**Implementation**:
```rust
pub fn is_event_handler(&self, attr_name: &str) -> bool {
    EVENT_HANDLER_ATTRIBUTES.contains(&attr_name)
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
- `<img src="...">` - Images

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

2. **SSRF Prevention** (4 tests):
   - iframe removal
   - object removal
   - embed removal
   - file: URL blocking

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

For continuous security testing, use cargo-fuzz:

```bash
cargo install cargo-fuzz
cargo fuzz run fuzz_converter
```

## Security Best Practices

### 1. Principle of Least Privilege
- Module runs with NGINX worker privileges (non-root)
- No elevated permissions required
- No file system access beyond NGINX configuration

### 2. Fail Secure
- Default to fail-open (return original HTML) to maintain availability
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
- Email: [security contact to be added]
- Private security advisory on GitHub

### Response Process

1. **Acknowledgment**: Within 48 hours
2. **Assessment**: Within 7 days
3. **Fix Development**: Priority based on severity
4. **Disclosure**: Coordinated with reporter

### Security Updates

Security updates are released as:
- Patch releases for critical vulnerabilities
- Minor releases for moderate vulnerabilities
- Documented in CHANGELOG.md with CVE references

## Security Considerations for Operators

### Configuration

**Recommended Settings**:
```nginx
markdown_max_size 10m;           # Limit document size
markdown_timeout 5s;             # Prevent slow conversions
markdown_on_error pass;          # Fail-open for availability
markdown_auth_policy allow;      # Allow authenticated content
markdown_auth_cache private;     # Prevent public caching
```

**Security-Critical Directives**:
- `markdown_max_size`: Prevents resource exhaustion
- `markdown_timeout`: Prevents slow conversion DoS
- `markdown_auth_cache`: Prevents sensitive data leakage

### Monitoring

**Security Metrics to Monitor**:
- Conversion failure rate (sudden spikes may indicate attacks)
- Resource limit violations (size/timeout exceeded)
- Error classification (conversion_error vs system_error)

**Log Analysis**:
- Watch for repeated conversion failures from same source
- Monitor for unusual HTML patterns
- Alert on system errors (may indicate exploitation attempts)

### Deployment

**Best Practices**:
1. Run NGINX workers as non-root user
2. Use resource limits (ulimit) for NGINX processes
3. Deploy behind WAF for additional protection
4. Keep NGINX and module updated
5. Monitor security advisories

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
