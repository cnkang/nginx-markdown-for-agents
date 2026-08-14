# Non-Content Element Removal

## Overview

This document describes the implementation and testing of non-content element removal in the NGINX Markdown for Agents converter. This feature is critical for security and content quality.

## Requirements

- **FR-03.3**: The Conversion_Engine SHALL remove or simplify non-content elements such as scripts, styles, and navigation
- **NFR-03.4**: The module SHALL validate HTML input before processing and remove script tags and event handlers

## Implementation

The converter removes the following non-content elements and their entire content:

1. **`<script>` tags**: JavaScript code that should not appear in Markdown output
2. **`<style>` tags**: CSS styling that is not relevant for text content
3. **`<noscript>` tags**: Fallback content for browsers without JavaScript

### Code Location

File: `components/rust-converter/src/converter.rs`

```rust
// Elements to skip (non-content)
"script" | "style" | "noscript" => {
    // Skip these elements and their children
}
```

The implementation uses pattern matching in the `handle_element` method. It identifies these tags and skips them entirely, including all their child nodes.

## Security Rationale

Removing script tags is a critical security measure:

1. **XSS Prevention**: Prevents cross-site scripting attacks by ensuring no executable code appears in the output
2. **Content Safety**: Ensures the Markdown output contains only safe, displayable content
3. **AI Agent Safety**: Protects AI agents from processing potentially malicious code

## Test Coverage

The implementation includes comprehensive unit tests:

### Basic Removal Tests

1. **`test_script_removal`**: Verifies that the module removes `<script>` tags and their content
2. **`test_style_removal`**: Verifies that the module removes `<style>` tags and their content
3. **`test_noscript_removal`**: Verifies that the module removes `<noscript>` tags and their content

### Advanced Scenarios

4. **`test_multiple_non_content_removal`**: Tests removal of multiple non-content elements in one document
5. **`test_nested_non_content_removal`**: Tests removal of non-content elements nested within other elements
6. **`test_script_with_attributes_removal`**: Tests removal of script tags with attributes (type, src, and so on)
7. **`test_style_in_head_removal`**: Tests removal of style tags in the `<head>` section
8. **`test_inline_script_removal`**: Tests removal of `on*` event-handler attributes from elements (for example, a button's `onclick`). The separate script-element test covers tag removal.
9. **`test_content_preservation_around_non_content`**: Verifies that content before and after non-content elements stays preserved correctly

### Test Results

All 111 unit tests pass, including the 9 tests specifically for non-content element removal:

```
test converter::tests::test_script_removal ... ok
test converter::tests::test_style_removal ... ok
test converter::tests::test_noscript_removal ... ok
test converter::tests::test_multiple_non_content_removal ... ok
test converter::tests::test_nested_non_content_removal ... ok
test converter::tests::test_script_with_attributes_removal ... ok
test converter::tests::test_style_in_head_removal ... ok
test converter::tests::test_inline_script_removal ... ok
test converter::tests::test_content_preservation_around_non_content ... ok
```

## Examples

### Example 1: Script Removal

**Input HTML:**
```html
<p>Content</p><script>alert('xss')</script><p>More</p>
```

**Output Markdown:**
```markdown
Content

More
```

### Example 2: Style Removal

**Input HTML:**
```html
<p>Before</p><style>body { color: red; }</style><p>After</p>
```

**Output Markdown:**
```markdown
Before

After
```

### Example 3: Multiple Non-Content Elements

**Input HTML:**
```html
<h1>Title</h1>
<script>var x = 1;</script>
<p>Paragraph</p>
<style>.class{}</style>
<noscript>No JS</noscript>
<p>End</p>
```

**Output Markdown:**
```markdown
# Title

Paragraph

End
```

## Future Enhancements

The current implementation focuses on the three core non-content elements. Future versions may consider:

1. **Navigation elements**: `<nav>`, `<header>`, `<footer>` (optional for v1, mentioned in design)
2. **Other non-content elements**: `<svg>`, `<canvas>` (if needed)
3. **Inline event handlers**: Removal of all `on*` attributes via prefix matching — **complete**, already covered by `security.rs` and `test_inline_script_removal`.

## Related: Form Element Content Preservation

Form-related elements (`<form>`, `<button>`, `<select>`, `<textarea>`, `<fieldset>`, `<label>`, `<option>`, and so on) get separate handling from non-content elements. Instead of removing them entirely, the module strips their HTML tags while preserving child text content in the Markdown output. This ensures AI agents retain meaningful information such as labels, button captions, and option lists. See `docs/features/security.md` Layer 2 for details.

## Related: Embedded Content Element Handling

Embedded content elements (`<iframe>`, `<object>`, `<embed>`) also use a strip-tag-keep-content approach. The module extracts the `src`/`data` URL as a Markdown link (using the `title` attribute as label when available). Any fallback child text stays preserved. It suppresses dangerous URL schemes (`javascript:`, `data:`, and so on). See `docs/features/security.md` Layer 2 for details.

## Related: Media Element URL Extraction

Media elements (`<video>`, `<audio>`) have their `src` URL extracted as a Markdown link before traversing fallback children. Video `poster` thumbnails become Markdown images. Child `<source>` elements have their `src` extracted with `type` as label. `<track>` elements use `label` as link text. Image map `<area>` elements have their `href` extracted as links with `alt`/`title` as text. This ensures AI agents see all referenced resource URLs without losing information.

## Verification

To verify the implementation:

1. Run unit tests: `cargo test removal`
2. Run the example: `cargo run --example basic_conversion`
3. Check that all script, style, and noscript content comes out removed from the output

## Status

✅ **Complete**: All requirements met, comprehensive tests passing, security measures in place.


## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, added mermaid diagrams where applicable, verified directive accuracy against code, added update tracking section |
