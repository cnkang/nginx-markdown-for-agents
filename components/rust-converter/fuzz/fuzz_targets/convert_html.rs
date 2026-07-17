#![no_main]

//! Fuzz the complete HTML-to-Markdown conversion pipeline.
//!
//! # Input Model
//!
//! The first 4 bytes of the fuzzer input are used to deterministically derive
//! [`ConversionOptions`] (front_matter, markdown_flavor, prune_noise, and other
//! boolean toggles).  The remaining bytes are treated as raw HTML content and
//! fed through `parse_html` → `MarkdownConverter::convert`.
//!
//! # Invariants
//!
//! 1. **No panic** — any arbitrary input must not trigger a panic.
//! 2. **No sanitizer report** — no ASan/UBSan/MSan violations.
//! 3. **Completion within 60 s** — no infinite loops (enforced by `-timeout=60`).
//! 4. **Output size bound** — `output_len ≤ input_len × 10 + 4096`.
//!
//! # Failure Definitions
//!
//! - Panic → logic defect (P1)
//! - Sanitizer report → security defect (走安全漏洞报告流程)
//! - Timeout (>60 s) → performance defect (P2)
//! - Output size exceeds bound → needs investigation (logged, not aborted)

use libfuzzer_sys::fuzz_target;
use nginx_markdown_converter::converter::pruning::PruneConfig;
use nginx_markdown_converter::converter::{ConversionOptions, MarkdownConverter, MarkdownFlavor};
use nginx_markdown_converter::parser::parse_html;

/// Deterministically derive [`ConversionOptions`] from the first 4 bytes of input.
///
/// Uses an `option_bits`-style hash (consistent with `ffi_convert` target) so that
/// even very short inputs produce varied option combinations.
///
/// # Bit Layout
///
/// - bit 0: `include_front_matter`
/// - bit 1: `extract_metadata`
/// - bit 2: `MarkdownFlavor` (0=CommonMark, 1=GFM)
/// - bit 4: `simplify_navigation`
/// - bit 5: `preserve_tables`
/// - bit 6: `prune_noise` (enabled/disabled)
/// - bit 7: `resolve_relative_urls`
fn derive_options(data: &[u8]) -> (ConversionOptions, &[u8]) {
    let bits = data.iter().take(4).fold(0u32, |acc, &b| {
        acc.wrapping_add(u32::from(b)) ^ u32::from(b).rotate_left(3)
    });

    let flavor = if (bits >> 2) & 1 == 0 {
        MarkdownFlavor::CommonMark
    } else {
        MarkdownFlavor::GitHubFlavoredMarkdown
    };

    let prune_config = if ((bits >> 6) & 1) != 0 {
        PruneConfig::default_enabled()
    } else {
        PruneConfig::disabled()
    };

    let options = ConversionOptions {
        flavor,
        include_front_matter: (bits & 1) != 0,
        extract_metadata: ((bits >> 1) & 1) != 0,
        simplify_navigation: ((bits >> 4) & 1) != 0,
        preserve_tables: ((bits >> 5) & 1) != 0,
        base_url: None,
        resolve_relative_urls: ((bits >> 7) & 1) != 0,
        prune_config,
    };

    let html_start = data.len().min(4);
    (options, &data[html_start..])
}

fuzz_target!(|data: &[u8]| {
    let (options, html_bytes) = derive_options(data);

    // Parse HTML (may return Err for malformed input — that's fine).
    let dom = match parse_html(html_bytes) {
        Ok(dom) => dom,
        Err(_) => return,
    };

    // Convert DOM to Markdown.
    let converter = MarkdownConverter::with_options(options);
    let result = converter.convert(&dom);

    // Output size bound check (soft — log but do not abort the fuzzer).
    if let Ok(ref markdown) = result {
        let bound = html_bytes.len() * 10 + 4096;
        if markdown.len() > bound {
            eprintln!(
                "[convert_html] output size {} exceeds bound {} (input_len={})",
                markdown.len(),
                bound,
                html_bytes.len(),
            );
        }
    }
});
