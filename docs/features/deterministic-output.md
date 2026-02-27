# Deterministic Markdown Output

## Overview

The NGINX Markdown Converter implements comprehensive output normalization to ensure **deterministic Markdown generation**. This means that converting identical HTML input multiple times will always produce byte-for-byte identical Markdown output.

Deterministic output is **critical** for:
- **Stable ETags**: Consistent output enables reliable ETag generation for HTTP caching
- **Predictable Caching**: Caches can correctly identify identical content
- **Reproducible Builds**: Testing and debugging produce consistent results
- **Diff-Friendly Output**: Version control systems show meaningful changes

## Normalization Rules

The converter applies the following normalization rules to all Markdown output:

### 1. Line Endings (CRLF → LF)

**Rule**: All line endings are normalized to LF (`\n`), never CRLF (`\r\n`).

**Rationale**: Different systems use different line endings (Windows: CRLF, Unix/Linux/macOS: LF). Normalizing to LF ensures consistent output across platforms.

**Example**:
```
Input:  "Line 1\r\nLine 2\r\n"
Output: "Line 1\nLine 2\n"
```

### 2. Consecutive Blank Lines

**Rule**: Multiple consecutive blank lines are collapsed to a single blank line.

**Rationale**: Markdown uses blank lines to separate block elements. Multiple blank lines don't add semantic meaning and create inconsistent output.

**Example**:
```
Input:  "Para 1\n\n\n\nPara 2"
Output: "Para 1\n\nPara 2"
```

### 3. Trailing Whitespace

**Rule**: Trailing whitespace (spaces and tabs) is removed from all lines.

**Rationale**: Trailing whitespace is invisible and doesn't affect Markdown rendering. Removing it ensures consistent output and prevents spurious diffs.

**Example**:
```
Input:  "Line 1   \nLine 2\t\t\n"
Output: "Line 1\nLine 2\n"
```

### 4. Final Newline

**Rule**: Output always ends with exactly one newline character.

**Rationale**: POSIX standard requires text files to end with a newline. This ensures consistent file handling and prevents issues with tools that expect newline-terminated files.

**Example**:
```
Input:  "Content"           → Output: "Content\n"
Input:  "Content\n\n\n"     → Output: "Content\n"
Input:  "Content\n"         → Output: "Content\n"
```

### 5. Whitespace Normalization

**Rule**: Consecutive spaces within text are collapsed to a single space, **except**:
- Inside fenced code blocks (` ``` `)
- Inside inline code (` ` `)
- At the start of lines (for list indentation)

**Rationale**: Multiple spaces in regular text don't affect Markdown rendering but create inconsistent output. Code blocks and inline code must preserve exact spacing for correctness.

**Example**:
```
Input:  "Word1    Word2  Word3"
Output: "Word1 Word2 Word3"

Input:  "Text with `  code  ` here"
Output: "Text with `  code  ` here"  (spaces in code preserved)

Input:  "- Item\n  - Nested"
Output: "- Item\n  - Nested"  (leading spaces preserved)
```

### 6. List Indentation

**Rule**: Nested lists use exactly 2 spaces per indentation level.

**Rationale**: Consistent indentation ensures predictable rendering and makes the Markdown source readable.

**Example**:
```markdown
- Item 1
  - Nested 1
  - Nested 2
    - Deeply nested
- Item 2
```

### 7. Markdown Escaping

**Rule**: Special Markdown characters are escaped consistently according to context.

**Rationale**: Ensures that special characters in HTML content are correctly represented in Markdown without breaking formatting.

**Characters**: `*`, `_`, `[`, `]`, `(`, `)`, `#`, `\`, `` ` ``

**Note**: Current implementation preserves these characters in plain text context. Future enhancements may add context-aware escaping.

### 8. DOM Attribute Order

**Rule**: HTML attributes are processed in the order they appear in the DOM (insertion order).

**Rationale**: The html5ever parser maintains consistent attribute ordering. By processing attributes in DOM order, we ensure deterministic output even when HTML has attributes in different orders.

**Example**:
```html
<!-- Both produce identical Markdown -->
<img src="image.png" alt="Description">
<img alt="Description" src="image.png">

Output: ![Description](image.png)
```

## Implementation

The normalization is implemented in the `normalize_output()` function in `src/converter.rs`:

```rust
fn normalize_output(&self, output: String) -> String {
    // 1. Normalize line endings (CRLF -> LF)
    let output = output.replace("\r\n", "\n");
    
    // 2-6. Process line by line
    let mut result = String::with_capacity(output.len());
    let mut prev_blank = false;
    let mut in_code_block = false;

    for line in output.lines() {
        // Detect code block boundaries
        if line.trim_start().starts_with("```") {
            in_code_block = !in_code_block;
        }

        // Remove trailing whitespace
        let trimmed = line.trim_end();

        if trimmed.is_empty() {
            // Collapse consecutive blank lines
            if !prev_blank {
                result.push('\n');
                prev_blank = true;
            }
        } else {
            // Normalize whitespace (skip inside code blocks)
            if in_code_block {
                result.push_str(trimmed);
            } else {
                let normalized = self.normalize_line_whitespace(trimmed);
                result.push_str(&normalized);
            }
            result.push('\n');
            prev_blank = false;
        }
    }

    // Ensure single trailing newline
    if !result.ends_with('\n') {
        result.push('\n');
    } else if result.ends_with("\n\n") {
        while result.ends_with("\n\n") {
            result.pop();
        }
    }

    result
}
```

## Testing

The converter includes comprehensive tests for deterministic output:

### Unit Tests

- `test_normalize_crlf_to_lf`: Verifies CRLF → LF conversion
- `test_normalize_consecutive_blank_lines`: Verifies blank line collapsing
- `test_normalize_trailing_whitespace`: Verifies trailing whitespace removal
- `test_normalize_single_final_newline`: Verifies single final newline
- `test_normalize_consecutive_spaces`: Verifies space collapsing
- `test_normalize_preserves_code_blocks`: Verifies code block preservation
- `test_normalize_preserves_inline_code_spaces`: Verifies inline code preservation
- `test_normalize_preserves_list_indentation`: Verifies list indentation
- `test_deterministic_output_identical_html`: Verifies identical output for identical HTML
- `test_deterministic_output_complex_html`: Verifies consistency across multiple conversions

### Property-Based Tests

The converter includes property-based tests (using proptest) that verify deterministic output across thousands of randomly generated HTML inputs.

### Example Program

Run the deterministic output example to see normalization in action:

```bash
cargo run --example deterministic_output
```

This example:
1. Converts the same HTML 5 times
2. Verifies all outputs are byte-for-byte identical
3. Demonstrates each normalization rule
4. Shows the final Markdown output

## Configuration

Currently, normalization is always enabled and cannot be disabled. This ensures consistent behavior across all deployments.

Future versions may add configuration options:

```nginx
markdown_normalize_output on;  # Default: on (ensure deterministic output)
markdown_validate_commonmark off;  # Default: off (skip re-parsing for performance)
```

## Performance Considerations

Normalization adds minimal overhead:
- **Line ending normalization**: Single string replacement, O(n)
- **Whitespace normalization**: Single pass through output, O(n)
- **Blank line collapsing**: Integrated into line processing, no extra pass

Total overhead: ~5-10% of conversion time, negligible compared to HTML parsing and DOM traversal.

## Validation

To verify deterministic output in your own code:

```rust
use nginx_markdown_converter::{MarkdownConverter, parse_html};

let html = b"<h1>Title</h1><p>Content</p>";

// Convert twice
let dom1 = parse_html(html).unwrap();
let result1 = MarkdownConverter::new().convert(&dom1).unwrap();

let dom2 = parse_html(html).unwrap();
let result2 = MarkdownConverter::new().convert(&dom2).unwrap();

// Verify identical output
assert_eq!(result1, result2);
```

## References

- [CommonMark Specification](https://spec.commonmark.org/)
- [POSIX Text File Requirements](https://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap03.html#tag_03_403)
- [HTTP ETag Header](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/ETag)
- [Architecture ADR: Full Buffering Approach](../architecture/ADR/0002-full-buffering-approach.md)
