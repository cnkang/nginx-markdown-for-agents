use nginx_markdown_converter::converter::MarkdownConverter;
use nginx_markdown_converter::parser::parse_html;

fn main() {
    let html = r#"<html><body>
        <p>Before script</p>
        <script>alert('xss')</script>
        <p>After script</p>
    </body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    println!("Markdown output:");
    println!("{}", markdown);
    println!("\nContains 'script': {}", markdown.contains("script"));

    println!("\n\n--- Test 2: iframe ---");
    let html2 = r#"<html><body>
        <p>Before iframe</p>
        <iframe src="https://evil.com/malicious"></iframe>
        <p>After iframe</p>
    </body></html>"#;

    let dom2 = parse_html(html2.as_bytes()).expect("Failed to parse HTML");
    let markdown2 = converter.convert(&dom2).expect("Failed to convert");

    println!("Markdown output:");
    println!("{}", markdown2);
    println!("\nContains 'iframe': {}", markdown2.contains("iframe"));
}
