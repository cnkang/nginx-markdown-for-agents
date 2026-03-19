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
#![cfg(unix)]

use nginx_markdown_converter::converter::{ConversionOptions, MarkdownConverter};
use nginx_markdown_converter::incremental::IncrementalConverter;
use nginx_markdown_converter::parser::parse_html;
use nginx_markdown_converter::token_estimator::TokenEstimator;

// ---------------------------------------------------------------------------
// Platform-specific peak RSS helper
// ---------------------------------------------------------------------------

/// Returns the current process peak resident set size (RSS) in bytes.
///
/// This queries `getrusage(RUSAGE_SELF)` and normalizes `ru_maxrss` to bytes:
/// on macOS `ru_maxrss` is already in bytes; on Linux `ru_maxrss` is reported
/// in kilobytes and is multiplied by 1024.
///
/// # Panics
///
/// Panics if `getrusage` fails.
///
/// # Examples
///
/// ```
/// let peak = peak_rss_bytes();
/// println!("peak RSS: {} bytes", peak);
/// ```
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

/// Generate a large HTML document approximately the given number of bytes by repeating
/// section blocks (heading, paragraph, list, table, and code sample) until the document
/// reaches or exceeds `target_bytes`.
///
/// The produced HTML is a complete document (includes `<!DOCTYPE html>`, `<head>`, and
/// `<body>`). The final string length will be greater than or equal to `target_bytes`.
///
/// # Examples
///
/// ```
/// let html = generate_large_html(1024);
/// assert!(html.len() >= 1024);
/// assert!(html.contains("<!DOCTYPE html>"));
/// assert!(html.contains("<h2>Section 0</h2>"));
/// ```
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

/// Wraps HTML body content in a complete HTML document with a head containing
/// meta tags that exercise front-matter / metadata extraction.
///
/// Returns a `String` containing a full HTML document: a DOCTYPE, an HTML head
/// with title and meta tags, and a body whose contents are taken from the
/// provided `html`'s `<body>` section (or the entire input if no `<body>` tag
/// is found).
///
/// # Examples
///
/// ```
/// let small = "<!DOCTYPE html><html><body><p>Hello</p></body></html>";
/// let wrapped = wrap_with_front_matter(small);
/// assert!(wrapped.contains("<meta name=\"author\" content=\"Test Author\">"));
/// assert!(wrapped.contains("<p>Hello</p>"));
/// ```
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

/// Converts the provided HTML using the full-buffer conversion path and measures peak RSS change.
///
/// # Returns
///
/// A tuple with the converted Markdown string and the observed peak RSS increase in bytes during conversion.
///
/// # Examples
///
/// ```
/// let html = b"<h1>Hello</h1><p>world</p>";
/// let options = ConversionOptions::default();
/// let (markdown, rss_delta) = convert_full_buffer(html, options);
/// assert!(markdown.contains("Hello"));
/// assert!(rss_delta >= 0);
/// ```
fn convert_full_buffer(html: &[u8], options: ConversionOptions) -> (String, u64) {
    let rss_before = peak_rss_bytes();

    let dom = parse_html(html).expect("full-buffer: parse_html failed");
    let converter = MarkdownConverter::with_options(options);
    let result = converter
        .convert(&dom)
        .expect("full-buffer: convert failed");

    let rss_after = peak_rss_bytes();
    let delta = rss_after.saturating_sub(rss_before);
    (result, delta)
}

/// Converts HTML to Markdown using the incremental conversion path and returns the converted text along with the observed peak RSS change.
///
/// # Parameters
///
/// - `html`: HTML document bytes to feed to the incremental converter.
/// - `options`: Conversion options that control output formatting and metadata extraction.
///
/// # Returns
///
/// `(String, u64)` — the converted Markdown string and the change in peak RSS (in bytes) observed during the conversion.
///
/// # Examples
///
/// ```
/// let html = b"<h1>Hello</h1><p>world</p>";
/// let options = ConversionOptions::default();
/// let (md, rss_delta) = convert_incremental(html, options);
/// assert!(md.contains("Hello"));
/// assert!(rss_delta >= 0);
/// ```
fn convert_incremental(html: &[u8], options: ConversionOptions) -> (String, u64) {
    let rss_before = peak_rss_bytes();

    let mut conv = IncrementalConverter::new(options);
    conv.feed_chunk(html)
        .expect("incremental: feed_chunk failed");
    let result = conv.finalize().expect("incremental: finalize failed");

    let rss_after = peak_rss_bytes();
    let delta = rss_after.saturating_sub(rss_before);
    (result, delta)
}

// ---------------------------------------------------------------------------
// Reporting helper
// ---------------------------------------------------------------------------

/// Prints a formatted comparison report for a conversion scenario and returns the measured RSS reduction percentage.
///
/// The reduction percentage is computed as 1 - (incremental_delta / full_delta) expressed as a percent. If `full_rss_delta` is zero, the function returns `0.0`. The printed report includes the scenario name, input size in megabytes, full-buffer and incremental RSS deltas in kilobytes, the reduction percentage, and a status line indicating whether the reference target (≥30%) was met.
///
/// # Returns
///
/// The reduction percentage as a `f64`: `100.0` represents a 100% reduction, `0.0` indicates no reduction (or `full_rss_delta == 0`), and negative values indicate higher incremental RSS than full-buffer RSS.
///
/// # Examples
///
/// ```
/// let pct = report_comparison("example", 1_048_576, 200 * 1024, 100 * 1024);
/// // full_rss_delta = 200 KB, incr_rss_delta = 100 KB => 50% reduction
/// assert!((pct - 50.0).abs() < 1e-9);
/// ```
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
    println!(
        "  HTML input size:       {:.2} MB",
        html_size as f64 / 1_048_576.0
    );
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

/// Compares peak RSS and correctness of full-buffer vs incremental conversion on a large HTML input using default conversion options.
///
/// Measures peak RSS before and after each conversion, asserts both conversion outputs are identical, and prints a per-scenario memory reduction report (reference target: 30%).
///
/// # Examples
///
/// ```
/// // Generates ~1MB HTML, runs both conversion paths, and verifies identical outputs:
/// perf_large_html_default_options();
/// ```
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

/// Verifies that full-buffer and incremental conversion produce identical Markdown for a large HTML document
/// that includes front-matter metadata, and reports peak RSS deltas for both paths.
///
/// This test generates a ~1MiB HTML document, wraps it with a head that triggers front-matter extraction,
/// runs both the full-buffer and incremental conversion paths with `include_front_matter` and
/// `extract_metadata` enabled, asserts that their outputs match, and emits a comparison of memory
/// (peak RSS) deltas for the two approaches.
///
/// # Examples
///
/// ```
/// // Runs the test which performs the round-trip comparison and prints a memory usage report.
/// // (In test execution this is invoked by the test harness.)
/// perf_large_html_with_front_matter();
/// ```
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

/// Verifies that full-buffer and incremental conversion produce identical markdown and token estimates for a large HTML input.
///
/// Generates a ~1 MB HTML document, converts it using the full-buffer and incremental paths with default options, asserts
/// that both the converted markdown and the TokenEstimator results match, prints the token estimate, and reports peak RSS deltas
/// for each path.
///
/// # Examples
///
/// ```
/// // This test uses internal helpers; shown here for illustration:
/// let html = generate_large_html(TARGET_SIZE);
/// let (full_result, _full_delta) = convert_full_buffer(html.as_bytes(), ConversionOptions::default());
/// let (incr_result, _incr_delta) = convert_incremental(html.as_bytes(), ConversionOptions::default());
/// let estimator = TokenEstimator::new();
/// assert_eq!(estimator.estimate(&full_result), estimator.estimate(&incr_result));
/// assert_eq!(full_result, incr_result);
/// ```
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

/// Performs a performance comparison between full-buffer and incremental conversion for a large HTML document that includes front matter and token estimation.
///
/// The test generates a ~1 MiB HTML document, wraps it with front matter metadata, runs both conversion paths with front matter extraction enabled, verifies that token estimates and conversion outputs are identical, prints the token estimate, and reports peak RSS deltas for both paths.
///
/// # Examples
///
/// ```
/// // Execute the test logic (generates content, converts, compares, and reports).
/// perf_large_html_front_matter_and_token_estimation();
/// ```
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

/// Prints a consolidated performance report across predefined large-document scenarios.
///
/// Runs a set of scenarios (plain HTML, front matter, token estimation, and combinations),
/// executes both full-buffer and incremental conversion paths for each, asserts their outputs
/// match, optionally reports token estimates, and prints per-scenario peak RSS deltas and
/// the relative memory reduction. This test is informational and does not fail if the
/// provisional ≥30% memory-reduction target is not met.
///
/// # Examples
///
/// ```
/// // The test harness normally runs this; it can also be invoked directly in a debug session.
/// perf_summary_report();
/// ```
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
