# HTML Entity Decoding

## Overview

The NGINX Markdown for Agents converter handles HTML entity decoding automatically through the html5ever parser library. Entity decoding applies to normal text content and attribute values. The converter code itself needs no additional implementation.

## How It Works

### Automatic Decoding by html5ever

The html5ever parser implements the HTML5 specification's entity decoding algorithm. When the parser processes HTML, it automatically decodes HTML entities in text content and attribute values before constructing the DOM tree. This means:

1. **Named Entities**: Common entities like `&amp;`, `&lt;`, `&gt;`, `&quot;`, `&#39;`, `&nbsp;` decode to their corresponding characters
2. **Decimal Numeric Entities**: Entities like `&#65;` (A), `&#48;` (0) decode to their Unicode characters
3. **Hexadecimal Numeric Entities**: Entities like `&#x41;` (A), `&#x20AC;` (€) decode to their Unicode characters
4. **Unicode Entities**: All valid Unicode entities decode, including special characters like smart quotes, currency symbols, and so on

### Text Extraction

When the converter extracts text from DOM nodes using the `extract_text()` function, it receives text that html5ever has already decoded. The text content in `NodeData::Text` nodes contains the actual characters, not the entity representations.

## Supported Entities

### Common Named Entities

| Entity | Character | Description |
|--------|-----------|-------------|
| `&amp;` | `&` | Ampersand |
| `&lt;` | `<` | Less than |
| `&gt;` | `>` | Greater than |
| `&quot;` | `"` | Double quote |
| `&#39;` or `&apos;` | `'` | Single quote/apostrophe |
| `&nbsp;` | ` ` | Non-breaking space (U+00A0) |

### Numeric Entities

- **Decimal**: `&#NNNN;` where NNNN is a decimal Unicode code point
  - Example: `&#65;` → `A`, `&#8364;` → `€`
- **Hexadecimal**: `&#xHHHH;` where HHHH is a hexadecimal Unicode code point
  - Example: `&#x41;` → `A`, `&#x20AC;` → `€`

### Unicode Characters

The converter can represent and decode all valid Unicode characters. This covers the full Unicode range. The converter handles every valid code point.
- Currency symbols: `€`, `£`, `¥`, `₹`
- Smart quotes: `'`, `'`, `"`, `"`
- Mathematical symbols: `×`, `÷`, `±`, `≠`
- Arrows: `←`, `→`, `↑`, `↓`
- And many more...

## Edge Cases

### Double-Encoded Entities

If HTML contains double-encoded entities (for example `&amp;lt;`), html5ever decodes them only once:
- `&amp;lt;` → `&lt;` (not `<`)
- `&amp;amp;` → `&amp;` (not `&`)

This is correct behavior according to the HTML5 specification.

### Entities in Different Contexts

The parser decodes entities in supported text and attribute contexts:
- **In text content**: `<p>&lt;tag&gt;</p>` → `<tag>`
- **In attributes**: `<a href="?a=1&amp;b=2">` → `?a=1&b=2`
- **In headings**: `<h1>&amp; Title</h1>` → `& Title`
- **In code blocks**: `<code>&lt;html&gt;</code>` → `<html>`
- **In lists**: `<li>&amp; item</li>` → `& item`

Raw-text elements such as `<script>` and `<style>` are not decoded by
html5ever. The converter removes those element subtrees entirely
(including their contents) during sanitization, so they never reach the
Markdown output. They do not pass through unchanged.

Literal angle brackets in source text must be written as `&lt;` (and may be
written as `&gt;` for `>`). The parser treats a literal `<tag>` as HTML markup, not as
text, so callers must not use a raw opening tag when they intend literal text.
After parsing, code spans and fenced code blocks preserve the decoded
characters in their code context.

## Testing

Comprehensive tests verify entity decoding across various contexts:

1. **test_common_named_entities**: Tests `&amp;`, `&lt;`, `&gt;`, `&quot;`, `&#39;`
2. **test_decimal_numeric_entities**: Tests decimal entities like `&#65;`
3. **test_hexadecimal_numeric_entities**: Tests hex entities like `&#x41;`
4. **test_nbsp_entity**: Tests non-breaking space `&nbsp;`
5. **test_entities_in_headings**: Tests entities in heading elements
6. **test_entities_in_links**: Tests entities in link text and href attributes
7. **test_entities_in_code**: Tests entities in inline code and code blocks
8. **test_mixed_entities**: Tests combination of named, decimal, and hex entities
9. **test_entities_in_lists**: Tests entities in list items
10. **test_double_encoded_entities**: Tests double-encoded entities
11. **test_unicode_entities**: Tests Unicode characters like `€`, smart quotes

All tests pass, confirming that html5ever correctly decodes entities automatically.

## Implementation Notes

### No Additional Code Required

The converter does not need any special entity decoding logic because:
1. html5ever handles all entity decoding during parsing
2. The DOM tree contains decoded text
3. The `extract_text()` function simply retrieves the decoded text

### Performance

The parser performs entity decoding once during HTML parsing, not during Markdown conversion. This is efficient because:
- Decoding happens as part of the parsing process
- The text needs no additional passes
- The converter works with decoded text directly

### Correctness

Using html5ever's built-in entity decoding ensures:
- **Specification Compliance**: Follows HTML5 specification exactly
- **Comprehensive Support**: The converter supports all valid HTML entities
- **Edge Case Handling**: The converter handles malformed entities, invalid code points, and so on correctly
- **Security**: Entity decoding itself does not execute markup or scripts.
  Normal URL and Markdown-output sanitization still applies to the decoded
  text.

## Requirements Satisfied

This implementation satisfies requirement **FR-03.4**:
> THE Conversion_Engine SHALL handle common HTML entities correctly

The implementation:
- ✅ Decodes common named entities (`&amp;`, `&lt;`, `&gt;`, `&quot;`, `&#39;`)
- ✅ Handles numeric entities (decimal and hexadecimal)
- ✅ Supports all Unicode characters
- ✅ Works correctly in supported HTML contexts (text, attributes, code, and so on)
- ✅ Has comprehensive test coverage

## References

- [HTML5 Specification - Character References](https://html.spec.whatwg.org/multipage/syntax.html#character-references)
- [html5ever Documentation](https://docs.rs/html5ever/)
- [List of HTML Entities](https://html.spec.whatwg.org/multipage/named-characters.html)


## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, added mermaid diagrams where applicable, verified directive accuracy against code, added update tracking section |
