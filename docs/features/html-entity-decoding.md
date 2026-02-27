# HTML Entity Decoding

## Overview

The NGINX Markdown for Agents converter handles HTML entity decoding automatically through the html5ever parser library. No additional implementation is required in the converter code itself.

## How It Works

### Automatic Decoding by html5ever

The html5ever parser implements the HTML5 specification's entity decoding algorithm. When HTML is parsed, all HTML entities are automatically decoded before the DOM tree is constructed. This means:

1. **Named Entities**: Common entities like `&amp;`, `&lt;`, `&gt;`, `&quot;`, `&#39;`, `&nbsp;` are decoded to their corresponding characters
2. **Decimal Numeric Entities**: Entities like `&#65;` (A), `&#48;` (0) are decoded to their Unicode characters
3. **Hexadecimal Numeric Entities**: Entities like `&#x41;` (A), `&#x20AC;` (€) are decoded to their Unicode characters
4. **Unicode Entities**: All valid Unicode entities are decoded, including special characters like smart quotes, currency symbols, etc.

### Text Extraction

When the converter extracts text from DOM nodes using the `extract_text()` function, it receives text that has already been decoded by html5ever. The text content in `NodeData::Text` nodes contains the actual characters, not the entity representations.

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

All valid Unicode characters can be represented and decoded:
- Currency symbols: `€`, `£`, `¥`, `₹`
- Smart quotes: `'`, `'`, `"`, `"`
- Mathematical symbols: `×`, `÷`, `±`, `≠`
- Arrows: `←`, `→`, `↑`, `↓`
- And many more...

## Edge Cases

### Double-Encoded Entities

If HTML contains double-encoded entities (e.g., `&amp;lt;`), html5ever decodes them only once:
- `&amp;lt;` → `&lt;` (not `<`)
- `&amp;amp;` → `&amp;` (not `&`)

This is correct behavior according to the HTML5 specification.

### Entities in Different Contexts

Entities are decoded consistently across all HTML contexts:
- **In text content**: `<p>&lt;tag&gt;</p>` → `<tag>`
- **In attributes**: `<a href="?a=1&amp;b=2">` → `?a=1&b=2`
- **In headings**: `<h1>&amp; Title</h1>` → `& Title`
- **In code blocks**: `<code>&lt;html&gt;</code>` → `<html>`
- **In lists**: `<li>&amp; item</li>` → `& item`

Note: While entities in code blocks are decoded by html5ever, the Markdown output preserves the decoded characters. If you need to display literal `<` or `>` in code, they should be escaped in the Markdown output (handled by the code block formatter).

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

Entity decoding is performed once during HTML parsing, not during Markdown conversion. This is efficient because:
- Decoding happens as part of the parsing process
- No additional passes over the text are needed
- The converter works with decoded text directly

### Correctness

Using html5ever's built-in entity decoding ensures:
- **Specification Compliance**: Follows HTML5 specification exactly
- **Comprehensive Support**: All valid HTML entities are supported
- **Edge Case Handling**: Malformed entities, invalid code points, etc. are handled correctly
- **Security**: No risk of entity-related vulnerabilities (XSS, etc.)

## Requirements Satisfied

This implementation satisfies requirement **FR-03.4**:
> THE Conversion_Engine SHALL handle common HTML entities correctly

The implementation:
- ✅ Decodes common named entities (`&amp;`, `&lt;`, `&gt;`, `&quot;`, `&#39;`)
- ✅ Handles numeric entities (decimal and hexadecimal)
- ✅ Supports all Unicode characters
- ✅ Works correctly in all HTML contexts (text, attributes, code, etc.)
- ✅ Is tested comprehensively

## References

- [HTML5 Specification - Character References](https://html.spec.whatwg.org/multipage/syntax.html#character-references)
- [html5ever Documentation](https://docs.rs/html5ever/)
- [List of HTML Entities](https://html.spec.whatwg.org/multipage/named-characters.html)
