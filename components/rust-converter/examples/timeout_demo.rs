//! Demonstration of cooperative timeout mechanism
//!
//! This example shows how to use the timeout mechanism to protect against
//! slow or malicious HTML conversions.
//!
//! Run with: cargo run --example timeout_demo

use nginx_markdown_converter::converter::{ConversionContext, MarkdownConverter};
use nginx_markdown_converter::error::ConversionError;
use nginx_markdown_converter::parser::parse_html;
use std::time::Duration;

fn main() {
    println!("=== Cooperative Timeout Mechanism Demo ===\n");

    // Example 1: No timeout
    println!("1. No timeout (Duration::ZERO)");
    demo_no_timeout();
    println!();

    // Example 2: Generous timeout
    println!("2. Generous timeout (10 seconds)");
    demo_generous_timeout();
    println!();

    // Example 3: Short timeout with simple HTML (should succeed)
    println!("3. Short timeout (100ms) with simple HTML");
    demo_short_timeout_simple();
    println!();

    // Example 4: Very short timeout with large HTML (should timeout)
    println!("4. Very short timeout (1ms) with large HTML");
    demo_timeout_detection();
    println!();

    // Example 5: Monitoring elapsed time and node count
    println!("5. Monitoring conversion progress");
    demo_monitoring();
    println!();

    // Example 6: Backward compatibility
    println!("6. Backward compatibility (old convert method)");
    demo_backward_compatibility();
    println!();
}

fn demo_no_timeout() {
    let html = b"<h1>Title</h1><p>Content</p>";
    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();

    let mut ctx = ConversionContext::new(Duration::ZERO);
    match converter.convert_with_context(&dom, &mut ctx) {
        Ok(markdown) => {
            println!("   ✓ Conversion succeeded");
            println!("   Elapsed: {:?}", ctx.elapsed());
            println!("   Nodes processed: {}", ctx.node_count());
            println!("   Output: {}", markdown.trim());
        }
        Err(e) => println!("   ✗ Error: {}", e),
    }
}

fn demo_generous_timeout() {
    let html = b"<h1>Title</h1><p>Content with <strong>bold</strong> text</p>";
    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();

    let mut ctx = ConversionContext::new(Duration::from_secs(10));
    match converter.convert_with_context(&dom, &mut ctx) {
        Ok(markdown) => {
            println!("   ✓ Conversion succeeded");
            println!("   Elapsed: {:?}", ctx.elapsed());
            println!("   Nodes processed: {}", ctx.node_count());
            println!("   Output: {}", markdown.trim());
        }
        Err(e) => println!("   ✗ Error: {}", e),
    }
}

fn demo_short_timeout_simple() {
    let html = b"<h1>Title</h1><p>Simple content</p>";
    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();

    let mut ctx = ConversionContext::new(Duration::from_millis(100));
    match converter.convert_with_context(&dom, &mut ctx) {
        Ok(markdown) => {
            println!("   ✓ Conversion succeeded (within timeout)");
            println!("   Elapsed: {:?}", ctx.elapsed());
            println!("   Nodes processed: {}", ctx.node_count());
            println!("   Output: {}", markdown.trim());
        }
        Err(ConversionError::Timeout) => {
            println!("   ✗ Timeout exceeded");
            println!("   Elapsed: {:?}", ctx.elapsed());
            println!("   Nodes processed: {}", ctx.node_count());
        }
        Err(e) => println!("   ✗ Error: {}", e),
    }
}

fn demo_timeout_detection() {
    // Create a large HTML document
    let mut html = String::from("<html><body>");
    for i in 0..5000 {
        html.push_str(&format!("<div><p>Paragraph {}</p></div>", i));
    }
    html.push_str("</body></html>");

    let dom = parse_html(html.as_bytes()).expect("Parse failed");
    let converter = MarkdownConverter::new();

    // Very short timeout - should timeout
    let mut ctx = ConversionContext::new(Duration::from_millis(1));

    // Add a small delay to ensure timeout
    std::thread::sleep(Duration::from_millis(2));

    match converter.convert_with_context(&dom, &mut ctx) {
        Ok(_) => {
            println!("   ✗ Conversion succeeded (expected timeout)");
            println!("   Elapsed: {:?}", ctx.elapsed());
            println!("   Nodes processed: {}", ctx.node_count());
        }
        Err(ConversionError::Timeout) => {
            println!("   ✓ Timeout detected as expected");
            println!("   Elapsed: {:?}", ctx.elapsed());
            println!("   Nodes processed: {}", ctx.node_count());
            println!("   Note: Timeout detected at checkpoint (every 100 nodes)");
        }
        Err(e) => println!("   ✗ Error: {}", e),
    }
}

fn demo_monitoring() {
    // Create HTML with known structure
    let mut html = String::from("<html><body>");
    for i in 0..500 {
        html.push_str(&format!("<p>Paragraph {}</p>", i));
    }
    html.push_str("</body></html>");

    let dom = parse_html(html.as_bytes()).expect("Parse failed");
    let converter = MarkdownConverter::new();

    let mut ctx = ConversionContext::new(Duration::from_secs(5));
    match converter.convert_with_context(&dom, &mut ctx) {
        Ok(markdown) => {
            println!("   ✓ Conversion succeeded");
            println!("   Elapsed: {:?}", ctx.elapsed());
            println!("   Nodes processed: {}", ctx.node_count());
            println!("   Checkpoints: ~{}", ctx.node_count() / 100);
            println!("   Output size: {} bytes", markdown.len());
            println!(
                "   Compression ratio: {:.1}%",
                (1.0 - markdown.len() as f64 / html.len() as f64) * 100.0
            );
        }
        Err(e) => println!("   ✗ Error: {}", e),
    }
}

fn demo_backward_compatibility() {
    let html = b"<h1>Title</h1><p>Content</p>";
    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();

    // Old method - no timeout
    match converter.convert(&dom) {
        Ok(markdown) => {
            println!("   ✓ Old convert() method still works");
            println!("   Output: {}", markdown.trim());
        }
        Err(e) => println!("   ✗ Error: {}", e),
    }
}
