# Charset Detection Implementation

## Overview

This document describes the charset detection cascade implementation for the NGINX Markdown for Agents module, as specified in Requirements FR-05.1, FR-05.2, and FR-05.3.

## Requirements

- **FR-05.1**: WHEN the Upstream_Response Content-Type includes a charset parameter, THE Module SHALL use that charset for HTML parsing
- **FR-05.2**: WHEN the Upstream_Response Content-Type does not include a charset parameter, THE Module SHALL attempt to detect charset from HTML meta tags
- **FR-05.3**: WHEN charset detection fails, THE Module SHALL use a default charset (UTF-8 recommended)

## Implementation

### Three-Level Cascade

The charset detection follows a strict priority order:

1. **Content-Type Header** (Priority 1)
   - Checks the `Content-Type` header for a `charset` parameter
   - Example: `text/html; charset=ISO-8859-1`
   - Supports both quoted and unquoted values
   - Case-insensitive matching

2. **HTML Meta Tags** (Priority 2)
   - Scans the first 1024 bytes of HTML for charset declarations
   - Supports HTML5 format: `<meta charset="UTF-8">`
   - Supports HTML4 format: `<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">`
   - Case-insensitive matching

3. **Default to UTF-8** (Priority 3)
   - If both methods fail, defaults to UTF-8
   - UTF-8 is the recommended default per W3C standards

### Module Structure

#### `charset.rs`

The main charset detection module with the following functions:

- `detect_charset(content_type: Option<&str>, html: &[u8]) -> String`
  - Main entry point for charset detection
  - Implements the three-level cascade
  - Always returns a valid charset (never fails)

- `extract_charset_from_content_type(content_type: &str) -> Option<String>`
  - Parses Content-Type header for charset parameter
  - Uses regex for robust parsing
  - Handles various formats (quoted, unquoted, with/without spaces)

- `extract_charset_from_html(html: &[u8]) -> Option<String>`
  - Scans HTML for meta charset tags
  - Supports both HTML5 and HTML4 formats
  - Only scans first 1024 bytes for performance

- `normalize_charset(charset: &str) -> String`
  - Normalizes charset names to uppercase
  - Ensures consistent charset representation

#### `parser.rs`

Updated HTML parser with charset detection support:

- `parse_html_with_charset(html: &[u8], content_type: Option<&str>) -> Result<RcDom, ConversionError>`
  - New function that accepts Content-Type header
  - Uses charset detection cascade
  - Currently only supports UTF-8 parsing (logs warning for other charsets)
  - Returns parsed DOM tree or error

- `parse_html(html: &[u8]) -> Result<RcDom, ConversionError>`
  - Convenience function that calls `parse_html_with_charset` with no Content-Type
  - Maintains backward compatibility with existing code

#### `ffi.rs`

FFI interface updated to support charset detection:

- `MarkdownOptions` struct now includes:
  - `content_type: *const u8` - Pointer to Content-Type header string
  - `content_type_len: usize` - Length of Content-Type string

- `markdown_convert` function will use these fields when fully implemented

## Usage Examples

### Rust API

```rust
use nginx_markdown_converter::charset::detect_charset;
use nginx_markdown_converter::parser::parse_html_with_charset;

// Example 1: With Content-Type header
let html = b"<html><body><h1>Hello</h1></body></html>";
let content_type = "text/html; charset=UTF-8";
let dom = parse_html_with_charset(html, Some(content_type))?;

// Example 2: With HTML meta tag
let html = b"<html><head><meta charset=\"UTF-8\"></head><body>Content</body></html>";
let dom = parse_html_with_charset(html, None)?;

// Example 3: Default to UTF-8
let html = b"<html><body>No charset specified</body></html>";
let dom = parse_html_with_charset(html, None)?;

// Example 4: Direct charset detection
let charset = detect_charset(Some("text/html; charset=ISO-8859-1"), html);
assert_eq!(charset, "ISO-8859-1");
```

### C API (Future)

```c
// Set up options with Content-Type
const char *content_type = "text/html; charset=UTF-8";
markdown_options_t options = {
    .flavor = 0,
    .timeout_ms = 5000,
    .generate_etag = 1,
    .estimate_tokens = 1,
    .front_matter = 0,
    .content_type = (const uint8_t*)content_type,
    .content_type_len = strlen(content_type)
};

// Convert HTML with charset detection
markdown_result_t result;
markdown_convert(converter, html, html_len, &options, &result);
```

## Testing

### Unit Tests

The implementation includes comprehensive unit tests:

- **Content-Type Extraction**: 9 tests covering various formats
- **HTML Meta Tag Extraction**: 9 tests covering HTML5 and HTML4 formats
- **Charset Detection Cascade**: 6 tests verifying priority order
- **Charset Normalization**: 5 tests for case handling
- **Parser Integration**: 6 tests for parse_html_with_charset

Total: 35 unit tests for charset detection

### Test Coverage

- ✅ Content-Type with charset parameter
- ✅ Content-Type without charset parameter
- ✅ HTML5 meta charset format
- ✅ HTML4 meta http-equiv format
- ✅ Case-insensitive matching
- ✅ Quoted and unquoted values
- ✅ Multiple parameters in Content-Type
- ✅ Priority order (Content-Type > HTML meta > default)
- ✅ Charset normalization
- ✅ Empty/missing inputs
- ✅ Scan limit enforcement

## Current Limitations

### UTF-8 Only Parsing

The current implementation only supports UTF-8 parsing with html5ever. If a non-UTF-8 charset is detected:

1. A warning is logged to stderr
2. The parser attempts to parse as UTF-8 anyway
3. If the content is not valid UTF-8, an encoding error is returned

**Future Enhancement**: Add charset transcoding support using the `encoding_rs` crate to convert non-UTF-8 content to UTF-8 before parsing.

### Performance Considerations

- Meta tag scanning is limited to the first 1024 bytes for performance
- This is sufficient as meta charset tags should appear in the `<head>` section
- Regex compilation is cached using `OnceLock` for efficiency

## Integration Points

### NGINX Module Integration

When the NGINX C module calls the Rust converter:

1. Extract Content-Type header from upstream response
2. Pass Content-Type to `MarkdownOptions.content_type` field
3. Rust converter uses charset detection cascade
4. HTML is parsed with detected charset

### Error Handling

- Invalid UTF-8 in Content-Type: Falls back to HTML meta tag detection
- Invalid UTF-8 in HTML: Returns `ConversionError::EncodingError`
- Empty input: Returns `ConversionError::InvalidInput`
- Charset detection never fails (always returns UTF-8 as fallback)

## Dependencies

- `regex = "1.10"` - For Content-Type and HTML meta tag parsing
- `html5ever = "0.26"` - For HTML parsing (UTF-8 only)

## Future Enhancements

1. **Charset Transcoding**: Add support for non-UTF-8 charsets using `encoding_rs`
2. **BOM Detection**: Detect UTF-8/UTF-16 BOM (Byte Order Mark)
3. **Configurable Scan Limit**: Make meta tag scan limit configurable
4. **Charset Validation**: Validate detected charset against known encodings
5. **Performance Metrics**: Track charset detection time and cache hit rates

## References

- [W3C Character Encodings](https://www.w3.org/International/questions/qa-html-encoding-declarations)
- [HTML5 Charset Detection](https://html.spec.whatwg.org/multipage/parsing.html#determining-the-character-encoding)
- [RFC 9110 - HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html#name-content-type)
