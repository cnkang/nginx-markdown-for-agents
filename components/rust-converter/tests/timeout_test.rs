//! Integration tests for cooperative timeout mechanism
//!
//! These tests verify that the timeout mechanism correctly aborts conversion
//! operations that exceed the configured timeout limit.

use nginx_markdown_converter::converter::{ConversionContext, MarkdownConverter};
use nginx_markdown_converter::error::ConversionError;
use nginx_markdown_converter::parser::parse_html;
use proptest::prelude::*;
use std::time::Duration;

/// Test that conversion succeeds with no timeout (Duration::ZERO)
#[test]
fn test_no_timeout() {
    let html = b"<h1>Title</h1><p>Content</p>";
    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();

    // No timeout
    let mut ctx = ConversionContext::new(Duration::ZERO);
    let result = converter.convert_with_context(&dom, &mut ctx);

    assert!(result.is_ok());
    let markdown = result.unwrap();
    assert!(markdown.contains("# Title"));
}

/// Test that conversion succeeds with generous timeout
#[test]
fn test_generous_timeout() {
    let html = b"<h1>Title</h1><p>Content</p>";
    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();

    // 10 second timeout (very generous for simple HTML)
    let mut ctx = ConversionContext::new(Duration::from_secs(10));
    let result = converter.convert_with_context(&dom, &mut ctx);

    assert!(result.is_ok());
    let markdown = result.unwrap();
    assert!(markdown.contains("# Title"));
}

/// Test that timeout is detected with very short timeout
///
/// Note: This test may be flaky on slow systems. We use a very short timeout
/// and a large document to increase the likelihood of timeout.
#[test]
fn test_timeout_detection() {
    // Create a large HTML document with many nested elements
    let mut html = String::from("<html><body>");
    for i in 0..10000 {
        html.push_str(&format!("<div><p>Paragraph {}</p></div>", i));
    }
    html.push_str("</body></html>");

    let dom = parse_html(html.as_bytes()).expect("Parse failed");
    let converter = MarkdownConverter::new();

    // Very short timeout (1 microsecond) - should timeout
    let mut ctx = ConversionContext::new(Duration::from_micros(1));

    // Sleep briefly to ensure some time has passed
    std::thread::sleep(Duration::from_millis(1));

    let result = converter.convert_with_context(&dom, &mut ctx);

    // Should timeout
    assert!(result.is_err());
    match result {
        Err(ConversionError::Timeout) => {
            // Expected
        }
        Err(e) => panic!("Expected Timeout error, got: {:?}", e),
        Ok(_) => panic!("Expected timeout, but conversion succeeded"),
    }
}

/// Test that ConversionContext tracks node count
#[test]
fn test_node_count_tracking() {
    let html = b"<h1>Title</h1><p>Content</p><p>More content</p>";
    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();

    let mut ctx = ConversionContext::new(Duration::from_secs(10));
    let _ = converter.convert_with_context(&dom, &mut ctx);

    // Should have processed multiple nodes
    assert!(ctx.node_count() > 0);
}

/// Test that elapsed time is tracked
#[test]
fn test_elapsed_time_tracking() {
    let html = b"<h1>Title</h1><p>Content</p>";
    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();

    let mut ctx = ConversionContext::new(Duration::from_secs(10));

    // Add a small delay before conversion
    std::thread::sleep(Duration::from_millis(10));

    let _ = converter.convert_with_context(&dom, &mut ctx);

    // Should have some elapsed time
    assert!(ctx.elapsed() >= Duration::from_millis(10));
}

/// Test backward compatibility - convert() method still works
#[test]
fn test_backward_compatibility() {
    let html = b"<h1>Title</h1><p>Content</p>";
    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();

    // Old method should still work (no timeout)
    let result = converter.convert(&dom);

    assert!(result.is_ok());
    let markdown = result.unwrap();
    assert!(markdown.contains("# Title"));
}

/// Test that timeout checking happens at checkpoints (every 100 nodes)
#[test]
fn test_checkpoint_frequency() {
    // Create HTML with exactly 250 elements to test checkpoint behavior
    let mut html = String::from("<html><body>");
    for i in 0..250 {
        html.push_str(&format!("<p>Paragraph {}</p>", i));
    }
    html.push_str("</body></html>");

    let dom = parse_html(html.as_bytes()).expect("Parse failed");
    let converter = MarkdownConverter::new();

    // Generous timeout - should succeed
    let mut ctx = ConversionContext::new(Duration::from_secs(5));
    let result = converter.convert_with_context(&dom, &mut ctx);

    assert!(result.is_ok());

    // Should have processed multiple checkpoints (every 100 nodes)
    // With 250 paragraphs + structure, we should have > 100 nodes
    assert!(ctx.node_count() > 100);
}

proptest! {
    /// Property 22: Timeout Enforcement (cooperative checkpoints)
    /// Validates: FR-10.2, FR-10.7
    #[test]
    fn prop_cooperative_timeout_enforced_at_checkpoints(node_increments in 0u32..220) {
        let mut ctx = ConversionContext::new(Duration::from_nanos(1));

        // Ensure the timeout is already exceeded before we start incrementing.
        std::thread::sleep(Duration::from_millis(1));

        let mut first_err_at: Option<u32> = None;
        for step in 1..=node_increments {
            if ctx.increment_and_check().is_err() {
                first_err_at = Some(step);
                break;
            }
        }

        if node_increments < 100 {
            prop_assert_eq!(
                first_err_at, None,
                "Cooperative timeout should not trigger before the first 100-node checkpoint"
            );
        } else {
            prop_assert_eq!(
                first_err_at, Some(100),
                "Timeout should trigger at the first checkpoint once already exceeded"
            );
        }
    }
}
