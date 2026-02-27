//! Basic conversion example demonstrating the Markdown converter

use nginx_markdown_converter::converter::MarkdownConverter;
use nginx_markdown_converter::parser::parse_html;

fn main() {
    println!("=== NGINX Markdown Converter - Basic Examples ===\n");

    // Example 1: Simple heading and paragraph
    example_1();

    // Example 2: Multiple sections with headings
    example_2();

    // Example 3: Text normalization
    example_3();

    // Example 4: Script removal (security)
    example_4();

    // Example 5: All heading levels
    example_5();
}

fn example_1() {
    println!("Example 1: Simple heading and paragraph");
    println!("Input HTML:");
    let html = b"<h1>Welcome</h1><p>This is a test document.</p>";
    println!("{}\n", String::from_utf8_lossy(html));

    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Conversion failed");

    println!("Output Markdown:");
    println!("{}", markdown);
    println!("---\n");
}

fn example_2() {
    println!("Example 2: Multiple sections with headings");
    println!("Input HTML:");
    let html = b"<h1>Main Title</h1><h2>Section 1</h2><p>Content here.</p><h2>Section 2</h2><p>More content.</p>";
    println!("{}\n", String::from_utf8_lossy(html));

    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Conversion failed");

    println!("Output Markdown:");
    println!("{}", markdown);
    println!("---\n");
}

fn example_3() {
    println!("Example 3: Text normalization");
    println!("Input HTML:");
    let html = b"<p>Text   with    lots    of    spaces</p>";
    println!("{}\n", String::from_utf8_lossy(html));

    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Conversion failed");

    println!("Output Markdown:");
    println!("{}", markdown);
    println!("---\n");
}

fn example_4() {
    println!("Example 4: Script removal (security)");
    println!("Input HTML:");
    let html = b"<h1>Title</h1><script>alert('xss')</script><p>Safe content</p>";
    println!("{}\n", String::from_utf8_lossy(html));

    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Conversion failed");

    println!("Output Markdown:");
    println!("{}", markdown);
    println!("Note: Script tags are removed for security\n");
    println!("---\n");
}

fn example_5() {
    println!("Example 5: All heading levels");
    println!("Input HTML:");
    let html = b"<h1>H1</h1><h2>H2</h2><h3>H3</h3><h4>H4</h4><h5>H5</h5><h6>H6</h6>";
    println!("{}\n", String::from_utf8_lossy(html));

    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Conversion failed");

    println!("Output Markdown:");
    println!("{}", markdown);
    println!("---\n");
}
