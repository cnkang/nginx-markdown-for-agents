# Security Architecture and Threat Model

## Overview

This document describes the security architecture, threat model, and security measures implemented in the NGINX Markdown for Agents Rust converter. The primary security concern is **untrusted HTML input** from upstream servers that may contain malicious content.

This page focuses on implementation-level defenses and threat boundaries. For operator-facing rollout, configuration, and monitoring guidance, use `docs/guides/CONFIGURATION.md` and `docs/guides/OPERATIONS.md`.

## Threat Model

### Threats Addressed

1. **XSS (Cross-Site Scripting)**: Malicious JavaScript in HTML that could execute in downstream contexts
2. **XXE (XML External Entity)**: External entity references that could read local files or make network requests
3. **Dangerous URL schemes**: `javascript:`, `data:`, `vbscript:`, `file:`,
   `about:` links removed from output (this module never initiates
   server-side network requests)
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

**Form Elements — Tags Stripped, Content Policy Applied** (`SanitizeAction::StripElement`):
- `<form>`, `<button>`, `<fieldset>`, `<legend>`, and `<label>` - The module removes tags. It preserves descriptive child text, such as labels and button captions.
- `<select>`, `<option>`, and `<datalist>` - The module preserves visible option labels. It never emits an `option[value]` or other control `value` attribute. This keeps stored or suggested user data out of Markdown while retaining page-provided choices.
- `<textarea>` - The module may emit a non-blank `aria-label`, followed by a non-blank `placeholder`. It suppresses default child text because users can enter or prefill that text.
- `<output>` - The module removes the tag. It preserves visible child text because that text represents page content, such as a calculation result.
- `<input>` (void form control) - The module lowercases `type` using ASCII rules. It does not trim whitespace.
- `type="password"` - The module suppresses the entire control, including its `aria-label`, `placeholder`, and `value`.
- `type="hidden"` and `type="image"` - The module keeps their existing full suppression behavior.
- `type="submit"` and `type="reset"` - The module uses `value` as the visible button caption.
- All other input types - The descriptive fallback is non-blank `aria-label` > non-blank `placeholder`. Only the exact `type="button"` may then use `value` as a fallback. Data controls such as `text`, `email`, `number`, `search`, `tel`, `url`, and date/time types never expose their values. Missing, unknown, and whitespace-padded types also receive no value fallback.

This policy favors page descriptions over form state. The module must not expose user-entered, restored, or prefilled values to AI-facing Markdown. It may expose a visible caption from a button or a submit/reset control.

**Embedded Content Elements — Tags Stripped, URL Extracted, Fallback Preserved** (`SanitizeAction::StripElement`):
- `<iframe>`, `<object>`, `<embed>` - The module removes tags. It extracts the `src` (iframe/embed) or `data` (object) URL as a Markdown link, using the `title` attribute as the link label when available. Fallback child text between the tags stays in the output. It suppresses dangerous URL schemes (`javascript:`, `data:`, and so on) — only safe URLs appear in the output. The module blocks unsafe schemes entirely.

**Media Elements — URL Extracted, Fallback Preserved** (handled in traversal):
- `<video>`, `<audio>` - The module extracts the `src` URL as a Markdown link (with `title` as label). Video `poster` thumbnails become Markdown images. Fallback child text stays via normal child traversal.
- `<source>` - The module extracts the `src` URL as a Markdown link with `type` as label (for example `[video/mp4](url)`).
- `<track>` - The module extracts the `src` URL as a Markdown link with `label` as link text (for example `[English](subs.vtt)`).
- `<area>` - The module extracts the `href` as a Markdown link with `alt` or `title` as link text.

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
The module removes all attributes starting with `on` (with length > 2) via prefix matching, following the OWASP/DOMPurify convention. This covers all current and future event handlers, including:
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
- `file:` - Local file access (unsafe scheme for link output)
- `about:` - Browser internal URLs

**URL Forms Retained by the Denylist**:
- `https:` - Secure HTTP
- `http:` - Standard HTTP
- Relative URLs (`/path`, `../parent`)
- Fragment identifiers (`#anchor`)

This is an output-sanitization denylist, not an exhaustive URL allowlist. The
converter never dereferences link destinations or makes server-side network
requests, so other non-dangerous application schemes such as `mailto:`,
`tel:`, and `ftp:` may remain in Markdown. Consumers that render or fetch the
converted Markdown must apply their own scheme allowlist if their boundary
requires one.

**Implementation**:
```rust
pub fn is_dangerous_url(&self, url: &str) -> bool {
    let trimmed = url.trim();
    if trimmed.chars().any(|ch| ch == '\0' || ch.is_control()) {
        return true;
    }
    if Self::contains_percent_encoded_control(trimmed) {
        return true;
    }

    let url_lower = trimmed.to_ascii_lowercase();
    DANGEROUS_URL_SCHEMES
        .iter()
        .any(|scheme| url_lower.starts_with(scheme))
}
```

**Applied to**:
- `<a href="...">` - Links
- `<img src="...">` - Images. A safe URL renders as Markdown image syntax and may keep the `title` attribute: `![alt](src "title")`. A blocked or missing URL never reaches the output: only escaped `alt` text stays as plain text — no URL and no title
- Embedded/media URL attributes including `<iframe src>`, `<object data>`, `<embed src>`, `<video src/poster>`, `<audio src>`, `<source src>`, `<track src>`, and `<area href>`

### Layer 5: Markdown Output Escaping

**Locations**: `src/security.rs` - `escape_markdown_text()` and
`escape_link_label()`

The module escapes untrusted ordinary text before emitting it as Markdown.
The module always escapes inline delimiters, link brackets, and raw-HTML
brackets. It escapes block markers at the beginning of a line. This
prevents HTML text such as `[click](javascript:...)`, raw tags, emphasis, or
headings from becoming active Markdown in the converted document. Code spans
and code blocks use their own context, so ordinary-text escaping does not
process them.

User-controlled text that appears inside Markdown link/image label brackets is
subject to separate escaping before emission. This prevents HTML text or
attributes from breaking out of the label and stops injection of a new
Markdown destination such as `](javascript:...)`.

**Applied to**:
- `<a>` child text before `[text](url)` emission
- `<img alt>` before `![alt](url)` emission
- Embedded/media labels emitted by `<iframe>`, `<object>`, `<embed>`, `<video>`, `<audio>`, `<source>`, `<track>`, and `<area>`
- Plain-text fallback for blocked image URLs

### Layer 6: XXE Prevention

**Location**: `src/parser.rs` - html5ever parser

**Mechanism**: html5ever is an HTML5 parser, not an XML parser. HTML5 does not support external entity references, so the design prevents XXE attacks.

**Key Points**:
- html5ever does NOT resolve external entities
- html5ever does NOT process DOCTYPE declarations for entity definitions
- html5ever does NOT load external DTDs
- Entity references become text content, not executable directives

**Documentation**:
```rust
pub fn xxe_prevention_documentation() -> &'static str {
    "html5ever is an HTML5 parser that does not support XML external entities. \
     HTML5 does not have a concept of external entities, so XXE attacks are \
     prevented by design."
}
```

### Layer 7: Memory Safety (Rust)

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

The listed suite contains 10 XSS/Markdown-injection, 6 unsafe-URL filtering, 7 XXE, 3
URL-sanitization, and 5 defense-in-depth tests (31 tests total).

**Location**: `tests/security_tests.rs`

**Test Categories**:

1. **XSS / Markdown Injection Prevention (10 tests)**:
   - Script tag removal
   - Inline script removal
   - Event handler removal
   - JavaScript URL blocking in links and images
   - Case-insensitive JavaScript URL blocking
   - Data URL blocking in links and images
   - Markdown link-label and image-alt injection blocking

2. **Unsafe URL Filtering (6 tests)**:
   - iframe tag stripping with URL extraction and dangerous scheme suppression
   - object tag stripping with URL extraction and dangerous scheme suppression
   - embed tag stripping with URL extraction
   - file: URL blocking
   - Dangerous URL scheme suppression in embedded content
   - External stylesheet link removal

3. **XXE Prevention (7 tests)**:
   - DOCTYPE entity handling
   - External, parameter, internal, and nested entity handling
   - Standard and PUBLIC DOCTYPE handling without external fetches

4. **URL Sanitization (3 tests)**:
   - VBScript and `about:` URL blocking
   - Safe URL preservation

5. **Defense-in-Depth Tests (5 tests)**:
   - Multiple XSS vectors
   - Deep nesting
   - GFM mode security
   - Style/link/base tag removal

**Total**: 31 security-specific tests, all passing

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

### 2. Fail-Open Availability Trade-off
- Pre-commit conversion paths default to fail-open (`markdown_error_policy pass`)
  and return the original eligible HTML response. This preserves availability
  when conversion fails before headers are sent. After the streaming headers
  commit (post-commit), failures finish safely when possible or abort otherwise.
  An abort can leave truncated converted output because the original HTML cannot
  rewind. Conversion failures never expose internal details.
- Error messages are generic to clients, detailed in logs

### 3. Defense in Depth
- Multiple layers of validation and sanitization
- Each layer provides independent protection
- Bypass of one layer does not compromise security

### 4. Secure Defaults
- Conservative resource limits: `decompressed_size` 10MB (cumulative
  decompressed-output cap), `conversion_memory` 64MB (input admission +
  generated-output budget), `conversion_timeout` 30s, `parser_timeout`
  10s, `parser_memory` 32MB, `streaming_buffer` 2MB — with cooperative
  parser checkpoints. An in-progress parse may overshoot its configured
  `parser_timeout`
- Fail-open returns the original HTML after a conversion failure.
  resource limits and bounded buffers provide resource-exhaustion
  protection
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

This is a maintainer-run project. Response times are best effort and may be slower during holidays or periods of limited availability.

1. **Acknowledgment**: Target within 14 calendar days
2. **Assessment**: Target within 45 calendar days when enough detail is available
3. **Fix Development**: Priority based on severity and maintainer availability
4. **Disclosure**: Coordinated with reporter

### Security Updates

The project releases security updates as:
- Patch releases for critical vulnerabilities
- Minor releases for moderate vulnerabilities
- Documented in CHANGELOG.md with CVE references

## Operator Follow-Through

The implementation details in this document feed into a few operator-facing concerns. The project maintains those concerns elsewhere:

- resource limits and failure policy: `docs/guides/CONFIGURATION.md`
- metrics, logs, and troubleshooting: `docs/guides/OPERATIONS.md`
- request-path security boundaries and failure branches: `docs/architecture/REQUEST_LIFECYCLE.md`

## Known Limitations

### 1. Content Extraction Heuristics
- Conversion is heuristic, not perfect
- Some malicious content may render as text
- This is acceptable as text cannot execute

### 2. CSS Injection
- The module removes `<style>` tags entirely
- Inline `style` attributes stay (safe in Markdown)
- CSS expressions cannot execute in Markdown output

### 3. HTML Entity Handling
- html5ever decodes HTML entities
- Malicious entity sequences become text
- No entity expansion attacks possible

### 4. Unicode Normalization
- No Unicode normalization performed
- Homograph attacks not mitigated (out of scope)
- Markdown consumers should handle Unicode safely

## Compliance

### Standards Compliance

- **OWASP Top 10**: Addresses A03:2021 (Injection)
- **CWE-79**: XSS prevention, including dangerous URL-scheme filtering
  (`javascript:`, `data:`, `vbscript:`, `file:`, `about:` removed from
  link output)
- **CWE-611**: XXE prevention

### Security Properties

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
- Unsafe URL scheme filtering (iframe, object, embed removal)
- Comprehensive security test suite
- Security documentation

---

**Last Updated**: 2026-08-24
**Version**: 1.0
**Status**: Production Ready

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-24 | Kang | Image URL behavior now distinguishes safe URLs (Markdown image syntax may keep title) from blocked or missing URLs (escaped alt text only, no URL, no title) |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
