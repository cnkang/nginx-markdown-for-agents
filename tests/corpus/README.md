# HTML to Markdown Test Corpus

This directory contains a comprehensive test corpus for validating HTML-to-Markdown conversion across diverse real-world scenarios. The corpus is organized by category to facilitate targeted testing.

## Directory Structure

```
tests/corpus/
├── simple/          # Basic HTML with common elements
├── complex/         # Real-world HTML from various sources
├── malformed/       # Invalid HTML to test error handling
├── edge-cases/      # Boundary conditions and unusual inputs
├── encoding/        # Character encoding test cases
└── README.md        # This file
```

## Test Categories

### 1. Simple HTML (`simple/`)

Basic HTML files with common elements to verify fundamental conversion functionality.

| File | Description | Expected Behavior |
|------|-------------|-------------------|
| `basic.html` | Minimal HTML with title and paragraph | Convert to simple Markdown with heading and paragraph |
| `headings.html` | All heading levels (h1-h6) | Preserve heading hierarchy with # markers |
| `lists.html` | Unordered and ordered lists with nesting | Convert to Markdown list syntax with proper indentation |
| `links.html` | Various link types (internal, external, anchors) | Preserve links using standard Markdown link syntax |
| `images.html` | Image tags with alt text and src | Convert to standard Markdown image syntax |
| `formatting.html` | Bold, italic, code inline | Preserve text formatting with Markdown syntax |
| `code-blocks.html` | Pre and code tags | Convert to fenced code blocks |
| `tables.html` | Simple HTML table | Convert to GFM table format (when GFM enabled) |

### 2. Complex HTML (`complex/`)

Real-world HTML samples from various sources to test practical conversion scenarios.

| File | Description | Expected Behavior |
|------|-------------|-------------------|
| `wikipedia-article.html` | Wikipedia article structure | Extract main content, preserve structure, remove navigation |
| `github-readme.html` | GitHub README with GFM features | Preserve code blocks, tables, task lists |
| `documentation.html` | Technical documentation page | Preserve code examples, headings, links |
| `blog-post.html` | Blog article with metadata | Extract article content, handle metadata |
| `nested-structure.html` | Deeply nested divs and sections | Flatten structure while preserving semantic content |

### 3. Malformed HTML (`malformed/`)

Invalid HTML to test error handling and graceful degradation.

| File | Description | Expected Behavior |
|------|-------------|-------------------|
| `unclosed-tags.html` | Missing closing tags | Parse with html5ever error recovery, produce valid Markdown |
| `invalid-nesting.html` | Incorrectly nested elements | Correct nesting per HTML5 spec, convert to Markdown |
| `broken-attributes.html` | Malformed attribute syntax | Ignore broken attributes, extract content |
| `mixed-case-tags.html` | Inconsistent tag casing | Normalize and convert (HTML is case-insensitive) |
| `unescaped-entities.html` | Raw < > & characters | Handle gracefully, escape in Markdown if needed |

### 4. Edge Cases (`edge-cases/`)

Boundary conditions and unusual inputs to test robustness.

| File | Description | Expected Behavior |
|------|-------------|-------------------|
| `empty.html` | Empty HTML document | Return empty or minimal Markdown |
| `only-scripts.html` | Only script/style tags | Return empty Markdown (non-content removed) |
| `deeply-nested.html` | 100+ levels of nesting | Handle without stack overflow, flatten structure |
| `huge-file.html` | 10MB+ HTML file | Respect size limits, trigger resource limit error if exceeded |
| `single-line.html` | Entire HTML on one line | Parse correctly, format Markdown with proper line breaks |
| `whitespace-heavy.html` | Excessive whitespace and newlines | Normalize whitespace in output |
| `no-body.html` | HTML with only head section | Handle gracefully, return minimal output |
| `multiple-bodies.html` | Invalid multiple body tags | Use first body per HTML5 spec |

### 5. Encoding (`encoding/`)

Character encoding test cases to verify correct handling of various encodings.

| File | Description | Expected Behavior |
|------|-------------|-------------------|
| `utf8.html` | UTF-8 with emoji and CJK characters | Preserve all Unicode characters correctly |
| `utf8-bom.html` | UTF-8 with BOM | Strip BOM, convert content |
| `latin1.html` | ISO-8859-1 encoded content | Detect charset, convert to UTF-8 Markdown |
| `mixed-entities.html` | Mix of entities and Unicode | Decode entities, preserve Unicode |
| `special-chars.html` | Markdown special characters in content | Escape properly in Markdown output |
| `rtl-text.html` | Right-to-left text (Arabic, Hebrew) | Preserve RTL text correctly |

## Usage

### Integration Tests

Use these files in integration tests to verify end-to-end conversion:

```rust
#[test]
fn test_simple_basic_conversion() {
    let html = std::fs::read_to_string("tests/corpus/simple/basic.html").unwrap();
    let result = convert_html_to_markdown(&html);
    assert!(result.is_ok());
    // Verify expected Markdown structure
}
```

### Property-Based Tests

Use corpus files as seed inputs for property-based testing:

```rust
proptest! {
    #[test]
    fn test_conversion_produces_valid_markdown(html in corpus_html_strategy()) {
        let result = convert_html_to_markdown(&html);
        // Verify properties hold
    }
}
```

### Manual Testing

Use with curl to test NGINX module:

```bash
curl -H "Accept: text/markdown" \
     --data-binary @tests/corpus/simple/basic.html \
     http://localhost:8080/convert
```

## Adding New Test Cases

When adding new test cases:

1. Choose the appropriate category directory
2. Create descriptive filename (lowercase, hyphens)
3. Add entry to this README with description and expected behavior
4. Include comments in HTML file explaining test purpose
5. Consider adding corresponding expected Markdown output file

## Expected Behavior Guidelines

### Content Extraction

- **Preserve**: Headings, paragraphs, links, images, lists, tables, code blocks
- **Remove**: Scripts, styles, navigation, ads, tracking pixels
- **Simplify**: Header/footer, sidebars (configurable)

### Markdown Output

- **Format**: CommonMark baseline, GFM extensions when enabled
- **Encoding**: Always UTF-8 output
- **Line Endings**: LF only (\\n), never CRLF
- **Whitespace**: Normalized (single spaces, single blank lines between blocks)
- **Deterministic**: Identical HTML produces identical Markdown

### Error Handling

- **Malformed HTML**: Parse with html5ever, produce valid Markdown
- **Resource Limits**: Respect size/timeout limits, fail gracefully
- **Encoding Issues**: Attempt detection, fall back to UTF-8
- **Empty Content**: Return empty or minimal Markdown, not error

## Maintenance

This corpus should be updated when:

- New HTML patterns are encountered in production
- Conversion bugs are discovered and fixed
- New features are added (e.g., new element handlers)
- Edge cases are identified through testing
