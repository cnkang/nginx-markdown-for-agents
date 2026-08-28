//! Minimal full-buffer corpus conversion CLI.
//!
//! Usage: `test-corpus-conversion <html_file>`.
//!
//! The tool reads one HTML fixture from disk, parses it with the full-buffer
//! parser, converts it to Markdown, and writes the Markdown to stdout. Usage,
//! read, parse, and conversion errors are written to stderr and exit with code
//! 1. This binary intentionally does not exercise the streaming path.
//!
//! Corpus validators use it as a small full-buffer runtime smoke test.

use nginx_markdown_converter::converter::{ConversionContext, MarkdownConverter};
use nginx_markdown_converter::parser::parse_html;
use std::env;
use std::fs;
use std::process;
use std::time::Duration;

// The offline corpus includes a 64 MiB fixture and permits its bounded
// normalization working set to exceed the request conversion default.
const CORPUS_CONVERSION_BUDGET: usize = 256 * 1024 * 1024;

fn main() {
    let args: Vec<String> = env::args().collect();

    if args.len() < 2 {
        eprintln!("Usage: {} <html_file>", args[0]);
        process::exit(1);
    }

    let filename = &args[1];

    let html = match fs::read(filename) {
        Ok(content) => content,
        Err(e) => {
            eprintln!("Error reading file {}: {}", filename, e);
            process::exit(1);
        }
    };

    let dom = match parse_html(&html) {
        Ok(dom) => dom,
        Err(e) => {
            eprintln!("Error parsing HTML: {}", e);
            process::exit(1);
        }
    };

    let converter = MarkdownConverter::new();
    let mut context =
        ConversionContext::with_output_budget(Duration::ZERO, CORPUS_CONVERSION_BUDGET);
    let markdown = match converter.convert_with_context(&dom, &mut context) {
        Ok(md) => md,
        Err(e) => {
            eprintln!("Error converting to Markdown: {}", e);
            process::exit(1);
        }
    };

    println!("{}", markdown);
}
