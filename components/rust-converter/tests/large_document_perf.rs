//! Performance verification tests for large document scenarios.
//!
//! **Validates: Requirements 18.4, 18.5**
//!
//! These tests compare memory usage between the full-buffer and incremental
//! conversion paths for documents ~1 MB and above.  The target is a ≥30%
//! reduction in peak RSS when using the incremental path, calibrated per
//! platform and implementation stage — this is a **reference goal, not a
//! hard gate**.
//!
//! ## Current prototype caveat
//!
//! The current `IncrementalConverter` implementation buffers all fed chunks
//! internally and performs a single-pass parse + convert in `finalize()`.
//! Because the full document is still materialised in memory before
//! conversion, the measured peak RSS difference between the two paths may
//! be minimal or even zero.  This is expected for the prototype stage and
//! is documented here so that future iterations (true streaming) can be
//! validated against the same harness.
//!
//! ## Measurement approach
//!
//! Peak RSS is sampled via `getrusage(RUSAGE_SELF)` → `ru_maxrss` before
//! and after each conversion.  On macOS `ru_maxrss` is reported in bytes;
//! on Linux it is in kilobytes.  The delta gives an *approximation* of the
//! memory consumed by the conversion — it is not exact because other
//! allocations may occur between samples, but it is sufficient for
//! order-of-magnitude comparison in an isolated test process.
//!
//! **Important:** `ru_maxrss` is a monotonically increasing high-water mark
//! for the entire process lifetime.  When multiple tests run in the same
//! process (the default for `cargo test`), later tests may report a zero
//! delta because an earlier test already pushed the peak higher.  The
//! `perf_summary_report` test is therefore best run in isolation
//! (`cargo test --test large_document_perf -- perf_summary_report`) for
//! accurate per-scenario deltas.

#![cfg(feature = "incremental")]

use nginx_markdown_converter::converter::{ConversionOptions, MarkdownConverter};
use nginx_markdown_converter::incremental::IncrementalConverter;
use nginx_markdown_converter::parser::parse_html;
use nginx_markdown_converter::token_estimator::TokenEstimator;

// ---------------------------------------------------------------------------
// Platform-specific peak RSS helper
// ---------------------------------------------------------------------------

/// Return the current process peak RSS in bytes.
///
/// Uses `getrusage(RUSAGE_SELF)`.  On macOS `ru_maxrss` is already in
/// bytes; on Linux it is in kilobytes and must be multiplied by 1024.
fn peak_rss_bytes() -> u64 {
    unsafe {
        let mut usage: libc::rusage = std::mem::zeroed();
        let ret = libc::getrusage(libc::RUSAGE_SELF, &mut usage);
        assert_eq!(ret, 0, "getrusage failed");

        let rss = usage.ru_maxrss as u64;

        if cfg!(target_os = "macos") {
            rss // already in bytes
        } else {
            rss * 1024 // Linux reports in KB
        }
    }
}

// ---------------------------------------------------------------------------
// HTML generators
// ---------------------------------------------------------------------------

/// Generate a large HTML document of approximately `target_bytes` by
/// repeating paragraph + heading blocks.
fn generate_large_html(target_bytes: usize) -> String {
    let mut html = String::with_capacity(target_bytes + 1024);
    html.push_str("<!DOCTYPE html><html><head><title>Large Document</title></head><body>\n");

    let mut i = 0u64;
    while html.len() < target_bytes {
        html.push_str(&format!(
            "<h2>Section {i}</h2>\n\
             <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. \
             Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. \
             Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris \
             nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in \
             reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla \
             pariatur.</p>\n\
             <ul><li>Item A-{i}</li><li>Item B-{i}</li><li>Item C-{i}</li></ul>\n\
             <table><thead><tr><th>Key</th><th>Value</th></tr></thead>\
             <tbody><tr><td>row-{i}-a</td><td>data-{i}-a</td></tr>\
             <tr><td>row-{i}-b</td><td>data-{i}-b</td></tr></tbody></table>\n\
             <pre><code>fn example_{i}() {{ println!(\"hello {i}\"); }}</code></pre>\n",
        ));
        i += 1;
    }

    html.push_str("</body></html>");
    html
}

/// Wrap raw HTML body content with YAML-style front matter metadata.
fn wrap_with_front_matter(html: &str) -> String {
    // The front matter is embedded as a leading HTML comment that the
    // converter recognises when `include_front_matter` is enabled.
    // For the purpose of this perf test we simply prepend a <meta> block
    // that exercises the metadata extraction path.
    let mut out = String::with_capacity(html.len() + 512);
    // Insert a <head> section with metadata that triggers front matter
    out.push_str("<!DOCTYPE html><html><head>\n");
    out.push_str("<title>Performance Test Document</title>\n");
    out.push_str("<meta name=\"author\" content=\"Test Author\">\n");
    out.push_str("<meta name=\"description\" content=\"A large document for perf testing\">\n");
    out.push_str("<meta name=\"keywords\" content=\"perf,test,large,document\">\n");
    out.push_str("</head><body>\n");

    // Strip the existing doctype/html/head/body wrapper from the generated
    // HTML so we don't double-wrap.
    let body_start = html.find("<body>").map(|p| p + 6).unwrap_or(0);
    let body_end = html.rfind("</body>").unwrap_or(html.len());
    out.push_str(&html[body_start..body_end]);

    out.push_str("\n</body></html>");
    out
}

// ---------------------------------------------------------------------------
// Conversion helpers
// ---------------------------------------------------------------------------

/// Convert via the full-buffer path and return (result_string, peak_rss_delta).
fn convert_full_buffer(html: &[u8], options: ConversionOptions) -> (String, u64) {
    let rss_before = peak_rss_bytes();

    let dom = parse_html(html).expect("full-buffer: parse_html failed");
    let converter = MarkdownConverter::with_options(options);
    let result = converter.convert(&dom).expect("full-buffer: convert failed");

    let rss_after = peak_rss_bytes();
    let delta = rss_after.saturating_sub(rss_before);
    (result, delta)
}

/// Convert via the incremental path and return (result_string, peak_rss_delta).
fn convert_incremental(html: &[u8], options: ConversionOptions) -> (String, u64) {
    let rss_before = peak_rss_bytes();

    let mut conv = IncrementalConverter::new(options);
    conv.feed_chunk(html).expect("incremental: feed_chunk failed");
    let result = conv.finalize().expect("incremental: finalize failed");

    let rss_after = peak_rss_bytes();
    let delta = rss_after.saturating_sub(rss_before);
    (result, delta)
}

// ---------------------------------------------------------------------------
// Reporting helper
// ---------------------------------------------------------------------------

/// Print a comparison report for a single scenario.  Returns the
/// percentage reduction (may be zero or negative for the prototype).
fn report_comparison(
    scenario: &str,
    html_size: usize,
    full_rss_delta: u64,
    incr_rss_delta: u64,
) -> f64 {
    let reduction_pct = if full_rss_delta > 0 {
        (1.0 - (incr_rss_delta as f64 / full_rss_delta as f64)) * 100.0
    } else {
        0.0
    };

    println!("--- {scenario} ---");
    println!("  HTML input size:       {:.2} MB", html_size as f64 / 1_048_576.0);
    println!("  Full-buffer RSS delta: {} KB", full_rss_delta / 1024);
    println!("  Incremental RSS delta: {} KB", incr_rss_delta / 1024);
    println!("  Reduction:             {reduction_pct:.1}%");

    if reduction_pct >= 30.0 {
        println!("  Status: MEETS reference target (≥30%)");
    } else {
        println!(
            "  Status: Below reference target (≥30%) — expected for \
             current prototype that buffers internally"
        );
    }
    println!();

    reduction_pct
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

const TARGET_SIZE: usize = 1_048_576; // ~1 MB

#[test]
fn perf_large_html_default_options() {
    let html = generate_large_html(TARGET_SIZE);
    let html_bytes = html.as_bytes();
    let html_size = html_bytes.len();

    let options_full = ConversionOptions::default();
    let options_incr = ConversionOptions::default();

    let (full_result, full_delta) = convert_full_buffer(html_bytes, options_full);
    let (incr_result, incr_delta) = convert_incremental(html_bytes, options_incr);

    // Correctness: both paths must produce identical output.
    assert_eq!(
        full_result, incr_result,
        "Full-buffer and incremental outputs must match for default options"
    );

    let _reduction = report_comparison(
        "Large HTML — default options",
        html_size,
        full_delta,
        incr_delta,
    );

    // NOTE: The ≥30% target is a reference goal, not a hard gate.
    // The current prototype buffers all data internally, so the memory
    // difference may be minimal.  We report the result but do not fail.
}

#[test]
fn perf_large_html_with_front_matter() {
    let base_html = generate_large_html(TARGET_SIZE);
    let html = wrap_with_front_matter(&base_html);
    let html_bytes = html.as_bytes();
    let html_size = html_bytes.len();

    let options_full = ConversionOptions {
        include_front_matter: true,
        extract_metadata: true,
        ..ConversionOptions::default()
    };
    let options_incr = ConversionOptions {
        include_front_matter: true,
        extract_metadata: true,
        ..ConversionOptions::default()
    };

    let (full_result, full_delta) = convert_full_buffer(html_bytes, options_full);
    let (incr_result, incr_delta) = convert_incremental(html_bytes, options_incr);

    assert_eq!(
        full_result, incr_result,
        "Full-buffer and incremental outputs must match with front matter enabled"
    );

    let _reduction = report_comparison(
        "Large HTML + front matter",
        html_size,
        full_delta,
        incr_delta,
    );
}

#[test]
fn perf_large_html_with_token_estimation() {
    let html = generate_large_html(TARGET_SIZE);
    let html_bytes = html.as_bytes();
    let html_size = html_bytes.len();

    let options_full = ConversionOptions::default();
    let options_incr = ConversionOptions::default();

    let (full_result, full_delta) = convert_full_buffer(html_bytes, options_full);
    let (incr_result, incr_delta) = convert_incremental(html_bytes, options_incr);

    // Both paths produce the same markdown; run token estimation on both.
    let estimator = TokenEstimator::new();
    let full_tokens = estimator.estimate(&full_result);
    let incr_tokens = estimator.estimate(&incr_result);

    assert_eq!(
        full_tokens, incr_tokens,
        "Token estimates must match between paths"
    );
    assert_eq!(
        full_result, incr_result,
        "Full-buffer and incremental outputs must match with token estimation"
    );

    println!("  Token estimate: {full_tokens} tokens");

    let _reduction = report_comparison(
        "Large HTML + token estimation",
        html_size,
        full_delta,
        incr_delta,
    );
}

#[test]
fn perf_large_html_front_matter_and_token_estimation() {
    let base_html = generate_large_html(TARGET_SIZE);
    let html = wrap_with_front_matter(&base_html);
    let html_bytes = html.as_bytes();
    let html_size = html_bytes.len();

    let options_full = ConversionOptions {
        include_front_matter: true,
        extract_metadata: true,
        ..ConversionOptions::default()
    };
    let options_incr = ConversionOptions {
        include_front_matter: true,
        extract_metadata: true,
        ..ConversionOptions::default()
    };

    let (full_result, full_delta) = convert_full_buffer(html_bytes, options_full);
    let (incr_result, incr_delta) = convert_incremental(html_bytes, options_incr);

    let estimator = TokenEstimator::new();
    let full_tokens = estimator.estimate(&full_result);
    let incr_tokens = estimator.estimate(&incr_result);

    assert_eq!(
        full_tokens, incr_tokens,
        "Token estimates must match between paths"
    );
    assert_eq!(
        full_result, incr_result,
        "Full-buffer and incremental outputs must match (front matter + token estimation)"
    );

    println!("  Token estimate: {full_tokens} tokens");

    let _reduction = report_comparison(
        "Large HTML + front matter + token estimation",
        html_size,
        full_delta,
        incr_delta,
    );
}

/// Summary test that runs all scenarios and prints a consolidated report.
/// This does NOT fail on the ≥30% target — it is purely informational.
#[test]
fn perf_summary_report() {
    println!("\n========================================");
    println!("  Large Document Performance Summary");
    println!("========================================\n");
    println!(
        "NOTE: The current IncrementalConverter prototype buffers all\n\
         data internally (single-pass parse in finalize).  Significant\n\
         memory reduction is expected only after true streaming is\n\
         implemented.  The ≥30% target is a reference goal, not a hard\n\
         gate.\n"
    );

    let html_default = generate_large_html(TARGET_SIZE);
    let html_fm = wrap_with_front_matter(&generate_large_html(TARGET_SIZE));

    let scenarios: Vec<(&str, &[u8], ConversionOptions, bool)> = vec![
        (
            "Plain large HTML",
            html_default.as_bytes(),
            ConversionOptions::default(),
            false,
        ),
        (
            "Large HTML + front matter",
            html_fm.as_bytes(),
            ConversionOptions {
                include_front_matter: true,
                extract_metadata: true,
                ..ConversionOptions::default()
            },
            false,
        ),
        (
            "Large HTML + token estimation",
            html_default.as_bytes(),
            ConversionOptions::default(),
            true,
        ),
        (
            "Large HTML + front matter + token estimation",
            html_fm.as_bytes(),
            ConversionOptions {
                include_front_matter: true,
                extract_metadata: true,
                ..ConversionOptions::default()
            },
            true,
        ),
    ];

    let estimator = TokenEstimator::new();

    for (name, html_bytes, opts, run_token_est) in &scenarios {
        let opts_full = opts.clone();
        let opts_incr = opts.clone();

        let (full_result, full_delta) = convert_full_buffer(html_bytes, opts_full);
        let (incr_result, incr_delta) = convert_incremental(html_bytes, opts_incr);

        assert_eq!(
            full_result, incr_result,
            "Output mismatch in scenario: {name}"
        );

        if *run_token_est {
            let tokens = estimator.estimate(&full_result);
            println!("  [{name}] Token estimate: {tokens}");
        }

        report_comparison(name, html_bytes.len(), full_delta, incr_delta);
    }

    println!("========================================");
    println!("  End of Performance Summary");
    println!("========================================\n");
}
