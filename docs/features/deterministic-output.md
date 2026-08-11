# Deterministic Markdown Output

## Output Determinism Policy

**Contract:** within the same module version and build feature set, identical
effective inputs produce byte-identical response bodies.

**Determinism identity:** the effective input tuple consists of the
upstream HTML bytes plus every explicit conversion option — base URL,
content type, markdown flavor, pruning configuration, front matter mode,
token-estimate mode, and any other explicit conversion option. Two requests
that agree on every element of this tuple must produce byte-identical
response bodies.

**Only headers that participate in the effective input tuple are part of the
determinism identity.** `Accept` is an explicit Markdown-negotiation input and
must therefore remain the same when comparing converted responses. Headers
such as `User-Agent` and `Accept-Language` do not participate in the tuple and
may vary freely unless the implementation explicitly adds them as inputs.

**Version compatibility policy:** the 1.x compatibility policy does NOT
promise byte-for-byte output stability across patch versions. Correctness,
security, and standards-conformance fixes MAY alter non-critical byte layout
after golden/semantic diff review. Consumers that rely on exact bytes across
module upgrades must pin the module version.

**Verification:** a determinism corpus in CI (`make test-corpus-determinism`)
converts every fixture in `tests/corpus/` twice in independent converter
processes and requires byte-identical output (`perf/reports/corpus-determinism-report.json`).

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

**Rule**: The module normalizes CRLF line endings to LF (`\n`). Lone carriage
returns are not part of the documented line-ending guarantee.

**Rationale**: Different systems use different line endings (Windows: CRLF, Unix/Linux/macOS: LF). Normalizing to LF ensures consistent output across platforms.

**Example**:
```
Input:  "Line 1\r\nLine 2\r\n"
Output: "Line 1\nLine 2\n"
```

### 2. Consecutive Blank Lines

**Rule**: The module collapses multiple consecutive blank lines to a single blank line.

**Rationale**: Markdown uses blank lines to separate block elements. Multiple blank lines do not add semantic meaning and create inconsistent output.

**Example**:
```
Input:  "Para 1\n\n\n\nPara 2"
Output: "Para 1\n\nPara 2"
```

### 3. Trailing Whitespace

**Rule**: The module removes trailing whitespace (spaces and tabs) from all lines.

**Rationale**: Trailing whitespace is invisible and does not affect Markdown rendering. Removing it ensures consistent output and prevents spurious diffs.

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

**Rule**: The module collapses consecutive spaces within text to a single space, **except**:
- Inside fenced code blocks (` ``` `)
- Inside inline code (` ` `)
- At the start of lines (for list indentation)

**Rationale**: Multiple spaces in regular text do not affect Markdown rendering but create inconsistent output. Code blocks and inline code must preserve exact spacing for correctness.

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

**Rule**: The module escapes special Markdown characters consistently according
to context.

**Rationale**: Ensures that special characters in HTML content are correctly represented in Markdown without breaking formatting.

**Ordinary text**: `\`, `` ` ``, `*`, `_`, `[`, `]`, `<`, `>`, and `~` are
escaped before untrusted text is emitted. Line-start block markers (`#`, `>`,
`+`, `-`, `!`, and ordered-list periods) are escaped when they could change
the Markdown structure. This keeps ordinary prose such as `a-b` readable.

**Generated syntax**: Code spans/blocks retain their code content, and link or
image labels use the dedicated label escaper so generated Markdown remains
valid while untrusted label text cannot close the label or introduce a new
destination.

### 8. DOM Attribute Order

**Rule**: The module processes HTML attributes in the order they appear in the DOM (insertion order).

**Rationale**: The html5ever parser maintains consistent attribute ordering. By processing attributes in DOM order, we ensure deterministic output even when HTML has attributes in different orders.

**Example**:
```html
<!-- Both produce identical Markdown -->
<img src="image.png" alt="Description">
<img alt="Description" src="image.png">

Output: ![Description](image.png)
```

## Implementation

The `normalize_output()` function implements the normalization in
`components/rust-converter/src/converter/normalize.rs`:

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

Currently, normalization is always enabled. The module offers no disable switch. This ensures consistent behavior across all deployments.

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


## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, added mermaid diagrams where applicable, verified directive accuracy against code, added update tracking section |
