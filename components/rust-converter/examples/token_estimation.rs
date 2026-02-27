//! Token estimation example demonstrating the TokenEstimator

use nginx_markdown_converter::converter::MarkdownConverter;
use nginx_markdown_converter::parser::parse_html;
use nginx_markdown_converter::token_estimator::TokenEstimator;

fn main() {
    println!("=== Token Estimation Examples ===\n");

    // Example 1: Basic token estimation with default settings
    example_1();

    // Example 2: Custom chars_per_token
    example_2();

    // Example 3: Real-world HTML to Markdown conversion with token estimation
    example_3();

    // Example 4: Large document estimation
    example_4();
}

fn example_1() {
    println!("Example 1: Basic token estimation (default: 4 chars/token)");
    let estimator = TokenEstimator::new();

    let text = "This is a simple test.";
    let tokens = estimator.estimate(text);
    println!("Text: \"{}\"", text);
    println!("Characters: {}", text.chars().count());
    println!("Estimated tokens: {}\n", tokens);
}

fn example_2() {
    println!("Example 2: Custom chars_per_token");

    let text = "The quick brown fox jumps over the lazy dog.";
    println!("Text: \"{}\"", text);
    println!("Characters: {}\n", text.chars().count());

    // Default (4.0)
    let estimator_default = TokenEstimator::new();
    println!(
        "With 4.0 chars/token: {} tokens",
        estimator_default.estimate(text)
    );

    // Conservative (3.0 - assumes more tokens)
    let estimator_conservative = TokenEstimator::with_chars_per_token(3.0);
    println!(
        "With 3.0 chars/token: {} tokens",
        estimator_conservative.estimate(text)
    );

    // Optimistic (5.0 - assumes fewer tokens)
    let estimator_optimistic = TokenEstimator::with_chars_per_token(5.0);
    println!(
        "With 5.0 chars/token: {} tokens\n",
        estimator_optimistic.estimate(text)
    );
}

fn example_3() {
    println!("Example 3: HTML to Markdown with token estimation");

    let html = b"<h1>Welcome to NGINX</h1>\
                 <p>This is a <strong>powerful</strong> web server.</p>\
                 <ul>\
                   <li>High performance</li>\
                   <li>Easy to configure</li>\
                   <li>Widely used</li>\
                 </ul>";

    println!("Input HTML:");
    println!("{}\n", String::from_utf8_lossy(html));

    // Convert to Markdown
    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Conversion failed");

    println!("Output Markdown:");
    println!("{}", markdown);

    // Estimate tokens
    let estimator = TokenEstimator::new();
    let tokens = estimator.estimate(&markdown);

    println!("Markdown length: {} characters", markdown.chars().count());
    println!("Estimated tokens: {}", tokens);
    println!(
        "Token reduction: HTML {} chars -> Markdown {} chars ({:.1}% reduction)\n",
        html.len(),
        markdown.len(),
        (1.0 - markdown.len() as f64 / html.len() as f64) * 100.0
    );
}

fn example_4() {
    println!("Example 4: Large document estimation");

    // Simulate a large document
    let large_markdown = format!(
        "# Large Document\n\n{}\n\n## Conclusion\n\nThis is the end.",
        "Lorem ipsum dolor sit amet. ".repeat(100)
    );

    let estimator = TokenEstimator::new();
    let tokens = estimator.estimate(&large_markdown);

    println!(
        "Document length: {} characters",
        large_markdown.chars().count()
    );
    println!("Estimated tokens: {}", tokens);
    println!("Useful for LLM context window planning!");
}
