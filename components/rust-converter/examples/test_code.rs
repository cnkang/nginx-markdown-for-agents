use nginx_markdown_converter::converter::MarkdownConverter;
use nginx_markdown_converter::parser::parse_html;

fn main() {
    let html = br#"<!DOCTYPE html>
<html>
<head><title>Code Examples</title></head>
<body>
<h1>Code Examples</h1>
<p>Here's how to use the <code>print()</code> function in Python:</p>
<pre><code class="language-python">def greet(name):
    print(f"Hello, {name}!")
    
greet("World")</code></pre>
<p>You can also use <code>console.log()</code> in JavaScript:</p>
<pre><code class="language-javascript">function greet(name) {
  console.log(`Hello, ${name}!`);
}

greet("World");</code></pre>
<h2>Inline Code</h2>
<p>Use <code>const</code> for constants and <code>let</code> for variables.</p>
<ul>
<li>Use <code>git add</code> to stage files</li>
<li>Use <code>git commit</code> to commit changes</li>
</ul>
</body>
</html>"#;

    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::new();
    let result = converter.convert(&dom).expect("Conversion failed");

    println!("Markdown Output:");
    println!("{}", result);
}
