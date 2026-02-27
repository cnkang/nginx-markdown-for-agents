#!/usr/bin/env rust-script
//! Test corpus conversion validation
//!
//! ```cargo
//! [dependencies]
//! nginx-markdown-converter = { path = "../../components/rust-converter" }
//! ```

use nginx_markdown_converter::converter::MarkdownConverter;
use nginx_markdown_converter::parser::parse_html;
use std::env;
use std::fs;
use std::process;

fn main() {
    let args: Vec<String> = env::args().collect();
    
    if args.len() < 2 {
        eprintln!("Usage: {} <html_file>", args[0]);
        process::exit(1);
    }
    
    let filename = &args[1];
    
    // Read HTML file
    let html = match fs::read(filename) {
        Ok(content) => content,
        Err(e) => {
            eprintln!("Error reading file {}: {}", filename, e);
            process::exit(1);
        }
    };
    
    // Parse HTML
    let dom = match parse_html(&html) {
        Ok(dom) => dom,
        Err(e) => {
            eprintln!("Error parsing HTML: {}", e);
            process::exit(1);
        }
    };
    
    // Convert to Markdown
    let converter = MarkdownConverter::new();
    let markdown = match converter.convert(&dom) {
        Ok(md) => md,
        Err(e) => {
            eprintln!("Error converting to Markdown: {}", e);
            process::exit(1);
        }
    };
    
    // Print result
    println!("{}", markdown);
}
