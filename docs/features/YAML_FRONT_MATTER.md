# YAML Front Matter Implementation

## Overview

This document describes the YAML front matter generation feature in the Rust converter.

## Requirements Validated

- **FR-15.3**: Extract metadata from HTML (title, description, URL, image, author, published date)
- **FR-15.4**: Generate YAML front matter block with extracted metadata
- **FR-15.5**: Include resolved absolute URLs for images in front matter
- **FR-15.6**: Feature toggle independence (can be enabled/disabled independently)
- **FR-15.7**: Configurable via `include_front_matter` option
- **FR-15.8**: When disabled, no front matter is included

## Implementation Details

### Core Functions

1. **`write_front_matter()`** - Generates YAML front matter from PageMetadata
   - Formats metadata as YAML block enclosed in `---` delimiters
   - Only includes non-empty fields
   - Properly escapes YAML special characters

2. **`write_yaml_string()`** - Escapes YAML special characters
   - Escapes: `"`, `\`, `\n`, `\r`, `\t`
   - Wraps values in double quotes for safety

### YAML Format

```yaml
---
title: "Page Title"
url: "https://example.com/page"
description: "Page description"
image: "https://example.com/image.png"
author: "Author Name"
published: "2024-01-15"
---

```

### Field Priority

1. **title** (required per FR-15.4): From `<title>` tag or Open Graph
2. **url** (required per FR-15.4): From canonical link or base_url
3. **description** (optional per FR-15.5): From meta tags
4. **image** (optional per FR-15.5): From Open Graph, resolved to absolute URL
5. **author** (optional): From meta author tag
6. **published** (optional): From article:published_time meta tag

### Configuration

Enable YAML front matter by setting both flags:

```rust
let options = ConversionOptions {
    include_front_matter: true,  // Enable front matter output
    extract_metadata: true,       // Enable metadata extraction
    base_url: Some("https://example.com/page".to_string()),
    resolve_relative_urls: true,  // Resolve relative URLs to absolute
    ..Default::default()
};
```

### YAML Escaping Rules

The implementation properly escapes YAML special characters:

| Character | Escaped As | Example |
|-----------|------------|---------|
| `"` | `\"` | `"Quote \"test\""` |
| `\` | `\\` | `"Path\\to\\file"` |
| `\n` | `\\n` | `"Line\\nbreak"` |
| `\r` | `\\r` | `"Carriage\\rreturn"` |
| `\t` | `\\t` | `"Tab\\there"` |

Colons and other YAML special characters are safe within double-quoted strings.

## Testing

### Unit Tests (11 tests)

1. `test_yaml_front_matter_basic` - Basic title and URL
2. `test_yaml_front_matter_complete` - All metadata fields
3. `test_yaml_front_matter_escaping` - Special character escaping
4. `test_yaml_front_matter_whitespace_escaping` - Newlines and tabs
5. `test_yaml_front_matter_image_url_resolution` - Absolute URL resolution
6. `test_yaml_front_matter_disabled_by_default` - Default behavior
7. `test_yaml_front_matter_requires_both_flags` - Flag independence
8. `test_yaml_front_matter_minimal` - Minimal metadata
9. `test_yaml_front_matter_empty_metadata` - Empty metadata handling
10. `test_yaml_front_matter_format` - YAML structure validation
11. `test_yaml_front_matter_unicode` - Unicode character preservation

All tests pass successfully.

### Example

Run the demonstration:

```bash
cargo run --example yaml_front_matter_demo
```

## Integration with NGINX Module

The NGINX module will control this feature via the FFI layer:

```c
markdown_options_t options = {
    .flavor = MARKDOWN_FLAVOR_COMMONMARK,
    .timeout_ms = 5000,
    .generate_etag = 1,
    .estimate_tokens = 1,
    .front_matter = 1,  // Enable YAML front matter
    .base_url = (const uint8_t*)base_url,
    .base_url_len = strlen(base_url)
};
```

The NGINX configuration directive will be:

```nginx
markdown_front_matter on;  # Enable YAML front matter
```

## Security Considerations

- All metadata values are properly escaped to prevent YAML injection
- Unicode characters are preserved correctly
- No sensitive information is included in front matter by default
- URL resolution respects the configured base_url

## Performance Impact

- Minimal overhead: metadata extraction is fast
- YAML generation is simple string formatting
- Only enabled when explicitly configured
- No impact when disabled (default)

## Future Enhancements

Potential improvements for future versions:

1. Configurable field selection (choose which fields to include)
2. Custom field mapping (rename fields in output)
3. Additional metadata sources (JSON-LD, microdata)
4. YAML validation using external library
5. Support for nested YAML structures
