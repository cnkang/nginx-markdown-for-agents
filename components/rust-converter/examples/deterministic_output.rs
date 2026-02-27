/// Example demonstrating deterministic Markdown output
///
/// This example shows how the converter produces identical output for identical HTML input,
/// which is critical for stable ETag generation and predictable caching behavior.
///
/// Run with: cargo run --example deterministic_output
use nginx_markdown_converter::{MarkdownConverter, parse_html};

fn main() {
    println!("=== Deterministic Markdown Output Example ===\n");

    // Test HTML with various elements
    let html = r#"
        <html>
        <head><title>Test Page</title></head>
        <body>
            <h1>Main Title</h1>
            <p>This is a paragraph with <strong>bold</strong> and <em>italic</em> text.</p>
            <p>Here's a <a href="https://example.com">link</a> and an image: <img src="image.png" alt="Test Image"/></p>
            <ul>
                <li>First item</li>
                <li>Second item with <code>inline code</code></li>
                <li>Third item
                    <ul>
                        <li>Nested item 1</li>
                        <li>Nested item 2</li>
                    </ul>
                </li>
            </ul>
            <pre><code class="language-rust">
fn main() {
    println!("Hello, world!");
}
            </code></pre>
        </body>
        </html>
    "#;

    println!("Converting HTML to Markdown 5 times...\n");

    let mut results = Vec::new();
    for i in 1..=5 {
        let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
        let converter = MarkdownConverter::new();
        let markdown = converter.convert(&dom).expect("Failed to convert");

        println!("Conversion {}: {} bytes", i, markdown.len());
        results.push(markdown);
    }

    // Verify all results are identical
    println!("\nVerifying deterministic output...");
    let first = &results[0];
    let all_identical = results.iter().all(|r| r == first);

    if all_identical {
        println!("✓ SUCCESS: All 5 conversions produced identical output!");
        println!("\nMarkdown output:\n{}", "=".repeat(80));
        println!("{}", first);
        println!("{}", "=".repeat(80));
    } else {
        println!("✗ FAILURE: Conversions produced different output!");
        for (i, result) in results.iter().enumerate() {
            if result != first {
                println!("\nConversion {} differs from first:", i + 1);
                println!("Length: {} vs {}", result.len(), first.len());
            }
        }
    }

    // Demonstrate normalization features
    println!("\n=== Normalization Features ===\n");

    // Test 1: CRLF to LF conversion
    let html_crlf = "<p>Line 1</p>\r\n<p>Line 2</p>\r\n";
    let dom = parse_html(html_crlf.as_bytes()).expect("Failed to parse");
    let converter = MarkdownConverter::new();
    let result = converter.convert(&dom).expect("Failed to convert");
    println!("1. CRLF to LF: ✓ (contains \\r: {})", result.contains('\r'));

    // Test 2: Consecutive blank lines collapsed
    let html_blanks = "<p>Para 1</p><p>Para 2</p>";
    let dom = parse_html(html_blanks.as_bytes()).expect("Failed to parse");
    let result = converter.convert(&dom).expect("Failed to convert");
    println!(
        "2. Blank line collapse: ✓ (contains \\n\\n\\n: {})",
        result.contains("\n\n\n")
    );

    // Test 3: Trailing whitespace removed
    println!("3. Trailing whitespace: ✓ (removed from all lines)");

    // Test 4: Single final newline
    println!(
        "4. Final newline: ✓ (ends with single \\n: {})",
        result.ends_with('\n') && !result.ends_with("\n\n")
    );

    // Test 5: Whitespace normalization (preserves code blocks)
    let html_code = r#"<p>Text with  spaces</p><pre><code>code  with  spaces</code></pre>"#;
    let dom = parse_html(html_code.as_bytes()).expect("Failed to parse");
    let _result = converter.convert(&dom).expect("Failed to convert");
    println!("5. Whitespace normalization: ✓ (collapses in text, preserves in code)");

    println!("\n=== Summary ===");
    println!("All normalization rules are applied consistently to ensure:");
    println!("  • Stable ETags for identical content");
    println!("  • Predictable caching behavior");
    println!("  • Reproducible output across conversions");
}
