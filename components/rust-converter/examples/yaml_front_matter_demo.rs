//! Demonstration of YAML front matter generation
//!
//! This example shows how to enable YAML front matter with metadata extraction.
//!
//! Run with: cargo run --example yaml_front_matter_demo

use nginx_markdown_converter::converter::{ConversionOptions, MarkdownConverter};
use nginx_markdown_converter::parser::parse_html;

fn main() {
    // Sample HTML with rich metadata
    let html = br#"
    <html>
    <head>
        <title>Understanding YAML Front Matter</title>
        <meta name="description" content="A comprehensive guide to YAML front matter in Markdown documents">
        <meta property="og:image" content="/images/yaml-guide.png">
        <meta name="author" content="Jane Developer">
        <meta property="article:published_time" content="2024-02-25">
        <link rel="canonical" href="https://example.com/yaml-guide">
    </head>
    <body>
        <h1>Understanding YAML Front Matter</h1>
        <p>YAML front matter is a powerful feature for adding metadata to Markdown documents.</p>
        
        <h2>What is YAML Front Matter?</h2>
        <p>It's a block of YAML at the beginning of a file, enclosed in triple dashes.</p>
        
        <h2>Why Use It?</h2>
        <ul>
            <li>Provides structured metadata</li>
            <li>Enables better content organization</li>
            <li>Improves AI agent understanding</li>
        </ul>
    </body>
    </html>
    "#;

    // Parse HTML
    let dom = parse_html(html).expect("Failed to parse HTML");

    // Configure converter with YAML front matter enabled
    let options = ConversionOptions {
        include_front_matter: true,
        extract_metadata: true,
        base_url: Some("https://example.com/yaml-guide".to_string()),
        resolve_relative_urls: true,
        ..Default::default()
    };

    let converter = MarkdownConverter::with_options(options);

    // Convert to Markdown
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // Display result
    println!("=== Markdown with YAML Front Matter ===\n");
    println!("{}", markdown);
    println!("\n=== End of Output ===");

    // Demonstrate without front matter for comparison
    println!("\n\n=== Same Content WITHOUT Front Matter ===\n");
    let converter_no_fm = MarkdownConverter::new();
    let markdown_no_fm = converter_no_fm.convert(&dom).expect("Failed to convert");
    println!("{}", markdown_no_fm);
    println!("\n=== End of Output ===");
}
