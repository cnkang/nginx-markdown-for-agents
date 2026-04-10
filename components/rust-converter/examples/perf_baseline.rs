use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::ptr;
use std::time::Instant;

use nginx_markdown_converter::converter::{
    ConversionContext, ConversionOptions, MarkdownConverter, MarkdownFlavor,
};
use nginx_markdown_converter::etag_generator::ETagGenerator;
use nginx_markdown_converter::ffi::{
    ERROR_SUCCESS, MarkdownConverterHandle, MarkdownOptions, MarkdownResult, markdown_convert,
    markdown_converter_free, markdown_converter_new, markdown_result_free,
};
use nginx_markdown_converter::parser::parse_html_with_charset;
use nginx_markdown_converter::token_estimator::TokenEstimator;

#[cfg(feature = "streaming")]
use nginx_markdown_converter::error::ConversionError;
#[cfg(feature = "streaming")]
use nginx_markdown_converter::streaming::budget::MemoryBudget;
#[cfg(feature = "streaming")]
use nginx_markdown_converter::streaming::converter::StreamingConverter;
#[cfg(feature = "streaming")]
use nginx_markdown_converter::streaming::types::{StreamingResult, StreamingStats};

/// Chunk size for streaming simulation (~16KB).
#[cfg(feature = "streaming")]
const CHUNK_SIZE: usize = 16 * 1024;

#[derive(Clone, Copy)]
struct RunConfig {
    warmup: usize,
    iterations: usize,
}

#[derive(Clone)]
struct Sample {
    name: &'static str,
    html: Vec<u8>,
    target_label: &'static str,
    front_matter: bool,
    base_url: Option<&'static str>,
}

#[derive(Default, Clone)]
struct Stats {
    avg_ms: f64,
    p50_ms: f64,
    p95_ms: f64,
    p99_ms: f64,
    req_per_s: f64,
    input_mb_per_s: f64,
}

#[derive(Default, Clone)]
struct FfiSummary {
    stats: Stats,
    html_bytes: usize,
    markdown_bytes_avg: usize,
    token_estimate_avg: u32,
    peak_memory_bytes: u64,
}

/// Streaming benchmark result with streaming-specific metrics.
#[derive(Default, Clone)]
struct StreamingSummary {
    stats: Stats,
    html_bytes: usize,
    markdown_bytes_avg: usize,
    token_estimate_avg: u32,
    peak_memory_bytes: u64,
    /// Time to first byte: first non-empty ChunkOutput.markdown.
    ttfb_ms: f64,
    /// Time to last byte: finalize completion.
    ttlb_ms: f64,
    /// Wall-clock time for the conversion (approximates CPU time; equal to TTLB
    /// since Rust stdlib lacks cross-platform per-process CPU time without extra
    /// dependencies).
    cpu_time_ms: f64,
    /// Total flush points emitted across all chunks.
    flush_count: u32,
    /// Number of StreamingFallback events encountered.
    fallback_count: u32,
    /// Total feed_chunk attempts (including fallbacks).
    total_attempts: u32,
}

#[derive(Default, Clone)]
struct BreakdownSummary {
    parse_ms: f64,
    convert_ms: f64,
    etag_ms: f64,
    token_ms: f64,
    total_ms: f64,
}

/// Engine selection for benchmark comparison.
#[derive(Clone, Copy, PartialEq, Eq)]
enum BenchmarkEngine {
    FullBuffer,
    Streaming,
    Both,
}

impl std::fmt::Display for BenchmarkEngine {
    /// Formats a `BenchmarkEngine` as its canonical identifier string.
    ///
    /// # Examples
    ///
    /// ```
    /// use std::fmt::Write;
    /// let mut s = String::new();
    /// write!(&mut s, "{}", crate::BenchmarkEngine::FullBuffer).unwrap();
    /// assert_eq!(s, "full-buffer");
    /// ```
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            BenchmarkEngine::FullBuffer => write!(f, "full-buffer"),
            BenchmarkEngine::Streaming => write!(f, "streaming"),
            BenchmarkEngine::Both => write!(f, "both"),
        }
    }
}

/// Parsed command-line arguments.
struct CliArgs {
    /// Run only a single sample tier by name.
    single: Option<String>,
    /// Path to write the Measurement Report JSON.
    json_output: Option<PathBuf>,
    /// Override the platform identifier (defaults to auto-detected).
    platform_override: Option<String>,
    /// Which engine to benchmark: full-buffer, streaming, or both.
    engine: BenchmarkEngine,
}

/// Parse command-line options for the benchmark runner.
///
/// Recognizes the following flags and their required values:
/// - `--single <name>`: run a single sample by name
/// - `--json-output <path>`: write a measurement report to the given file path
/// - `--platform <id>`: override the detected platform identifier
/// - `--engine <mode>`: select benchmark engine (`full-buffer`, `streaming`, or `both`; default: `full-buffer`)
///
/// Unknown arguments are ignored with a warning printed to stderr.
///
/// # Examples
///
/// ```ignore
/// let cli = parse_args();
/// if let Some(path) = cli.json_output {
///     // write report to `path`
/// }
/// ```
///
/// # Returns
///
/// A `CliArgs` struct with `single`, `json_output`, `platform_override`, and `engine` set according to the parsed flags; fields are `None` when the corresponding flag was not provided.
fn parse_args() -> CliArgs {
    let args: Vec<String> = env::args().collect();
    let mut single = None;
    let mut json_output = None;
    let mut platform_override = None;
    let mut engine = BenchmarkEngine::FullBuffer;
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--single" => {
                i += 1;
                single = Some(args.get(i).expect("--single requires a value").clone());
            }
            "--json-output" => {
                i += 1;
                json_output = Some(PathBuf::from(
                    args.get(i).expect("--json-output requires a path"),
                ));
            }
            "--platform" => {
                i += 1;
                platform_override = Some(args.get(i).expect("--platform requires a value").clone());
            }
            "--engine" => {
                i += 1;
                let val = args.get(i).expect("--engine requires a value").clone();
                engine = match val.as_str() {
                    "full-buffer" => BenchmarkEngine::FullBuffer,
                    "streaming" => BenchmarkEngine::Streaming,
                    "both" => BenchmarkEngine::Both,
                    other => {
                        eprintln!("warning: unknown engine mode: {other}, using full-buffer");
                        BenchmarkEngine::FullBuffer
                    }
                };
            }
            other => {
                eprintln!("warning: unknown argument: {other}");
            }
        }
        i += 1;
    }
    CliArgs {
        single,
        json_output,
        platform_override,
        engine,
    }
}

/// Compute the p-th percentile from a slice of millisecond values sorted in ascending order.
///
/// If `sorted` is empty, returns `0.0`. The parameter `p` is a fraction in the range `0.0..=1.0` where
/// `0.0` selects the minimum and `1.0` selects the maximum; the function selects the element at
/// index `round((n - 1) * p)`.
///
/// # Examples
///
/// ```ignore
/// let v = [10.0, 20.0, 30.0];
/// assert_eq!(percentile_ms(&v, 0.5), 20.0);
/// assert_eq!(percentile_ms(&v, 1.0), 30.0);
/// assert_eq!(percentile_ms(&[], 0.5), 0.0);
/// ```
fn percentile_ms(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = ((sorted.len() - 1) as f64 * p).round() as usize;
    sorted[idx]
}

fn summarize(durations_s: &[f64], input_bytes: usize) -> Stats {
    let mut ms: Vec<f64> = durations_s.iter().map(|d| d * 1000.0).collect();
    ms.sort_by(|a, b| a.total_cmp(b));
    let total_s: f64 = durations_s.iter().sum();
    let total_ms = total_s * 1000.0;
    let avg_ms = if durations_s.is_empty() {
        0.0
    } else {
        total_ms / durations_s.len() as f64
    };
    let req_per_s = if total_s > 0.0 {
        durations_s.len() as f64 / total_s
    } else {
        0.0
    };
    let input_mb_per_s = if total_s > 0.0 {
        (input_bytes as f64 * durations_s.len() as f64) / (1024.0 * 1024.0) / total_s
    } else {
        0.0
    };

    Stats {
        avg_ms,
        p50_ms: percentile_ms(&ms, 0.50),
        p95_ms: percentile_ms(&ms, 0.95),
        p99_ms: percentile_ms(&ms, 0.99),
        req_per_s,
        input_mb_per_s,
    }
}

fn repo_root() -> PathBuf {
    // `CARGO_MANIFEST_DIR` points at `components/rust-converter`, so walk up
    // through `components/` to reach the repository root.
    let here = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    here.parent()
        .and_then(Path::parent)
        .expect("rust-converter crate lives under components/")
        .to_path_buf()
}

fn read_file(path: &Path) -> Vec<u8> {
    fs::read(path).unwrap_or_else(|e| panic!("failed to read {}: {e}", path.display()))
}

fn repeat_to_size(seed: &[u8], target_size: usize) -> Vec<u8> {
    let mut out = Vec::with_capacity(target_size + 256);
    out.extend_from_slice(
        b"<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><title>bench</title></head><body>\n",
    );
    while out.len() + seed.len() + 32 < target_size {
        out.extend_from_slice(seed);
        out.extend_from_slice(b"\n");
    }
    out.extend_from_slice(b"\n</body></html>\n");
    out
}

/// Builds a fixed set of benchmark samples used by the harness.
///
/// The returned collection contains four prepared `Sample` entries:
/// - "small": a small HTML sample (~0.4KB).
/// - "medium": a medium-sized HTML sample (~10KB).
/// - "medium-front-matter": the same medium HTML with front matter enabled and a base URL.
/// - "large": a large HTML sample (~1MB).
///
/// Each sample contains the HTML payload (`html`), a human-readable `target_label`,
/// and flags controlling front-matter handling and an optional `base_url`.
///
/// # Examples
///
/// ```ignore
/// let samples = build_samples();
/// assert_eq!(samples.len(), 4);
/// assert_eq!(samples[0].name, "small");
/// assert_eq!(samples[2].front_matter, true);
/// ```
fn build_samples() -> Vec<Sample> {
    let root = repo_root();
    let small_path = root.join("tests/corpus/simple/basic.html");
    let medium_seed_path = root.join("tests/corpus/complex/blog-post.html");

    let small = read_file(&small_path);
    let medium_seed = read_file(&medium_seed_path);
    let medium = repeat_to_size(&medium_seed, 10 * 1024);
    let large = repeat_to_size(&medium_seed, 1024 * 1024);

    vec![
        Sample {
            name: "small",
            html: small,
            target_label: "~0.4KB",
            front_matter: false,
            base_url: None,
        },
        Sample {
            name: "medium",
            html: medium.clone(),
            target_label: "~10KB",
            front_matter: false,
            base_url: None,
        },
        Sample {
            name: "medium-front-matter",
            html: medium,
            target_label: "~10KB + front matter",
            front_matter: true,
            base_url: Some("https://example.com/articles/bench.html"),
        },
        Sample {
            name: "large",
            html: large,
            target_label: "~1MB",
            front_matter: false,
            base_url: None,
        },
    ]
}

/// Get the current process peak RSS (resident set size) in bytes.
///
/// On Unix this queries `getrusage`. On macOS the reported value is already
/// in bytes; on other Unix platforms the value is treated as kilobytes and
/// converted to bytes. On non-Unix platforms or on error the function returns `0`.
///
/// # Examples
///
/// ```ignore
/// let peak = peak_rss_bytes();
/// // The value may be 0 on unsupported platforms or if the query fails.
/// assert!(peak >= 0);
/// ```
fn peak_rss_bytes() -> u64 {
    #[cfg(unix)]
    {
        let mut usage: libc::rusage = unsafe { std::mem::zeroed() };
        let ret = unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut usage) };
        if ret == 0 {
            // macOS reports in bytes, Linux in kilobytes.
            let raw = usage.ru_maxrss as u64;
            if cfg!(target_os = "macos") {
                raw
            } else {
                raw * 1024
            }
        } else {
            0
        }
    }
    #[cfg(not(unix))]
    {
        0
    }
}

/// Maps an internal sample name to the canonical JSON tier key.
///
/// The `large` sample is mapped to `large-1m`; all other names are returned unchanged.
///
/// # Examples
///
/// ```ignore
/// assert_eq!(tier_key("large"), "large-1m");
/// assert_eq!(tier_key("medium"), "medium");
/// ```
fn tier_key(sample_name: &str) -> &str {
    match sample_name {
        "large" => "large-1m",
        other => other,
    }
}

/// Map a CLI `--single` value to the internal sample name.
///
/// Specifically, translating the JSON tier key `"large-1m"` to the internal sample
/// name `"large"`. All other input values are returned unchanged.
///
/// # Examples
///
/// ```ignore
/// assert_eq!(resolve_single_name("large-1m"), "large");
/// assert_eq!(resolve_single_name("large"), "large");
/// assert_eq!(resolve_single_name("medium"), "medium");
/// ```
fn resolve_single_name(cli_value: &str) -> &str {
    match cli_value {
        // Canonical tier name → internal sample name.
        "large-1m" => "large",
        // Everything else (including the legacy `large` alias) passes through.
        other => other,
    }
}

/// Run the FFI-based Markdown converter on a sample and collect performance and size metrics.
///
/// The converter is executed for `cfg.warmup + cfg.iterations` rounds; the first `cfg.warmup`
/// iterations are used only for warmup and are excluded from the reported aggregates.
///
/// # Returns
///
/// `FfiSummary` containing aggregated latency statistics, average generated markdown size,
/// average token estimate, and the process peak RSS sampled after the run.
///
/// # Examples
///
/// ```no_run
/// let sample = Sample {
///     name: "small".into(),
///     html: b"<p>Hello</p>".to_vec(),
///     target_label: "small".into(),
///     front_matter: false,
///     base_url: None,
/// };
/// let cfg = RunConfig { warmup: 1, iterations: 3 };
/// let summary = run_ffi_baseline(&sample, cfg);
/// eprintln!("avg_ms = {}", summary.stats.avg_ms);
/// ```
fn run_ffi_baseline(sample: &Sample, cfg: RunConfig) -> FfiSummary {
    let content_type = b"text/html; charset=UTF-8";
    let base_url = sample.base_url.map(str::as_bytes);
    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 1,
        estimate_tokens: 1,
        front_matter: u8::from(sample.front_matter),
        content_type: content_type.as_ptr(),
        content_type_len: content_type.len(),
        base_url: base_url.map_or(ptr::null(), |value| value.as_ptr()),
        base_url_len: base_url.map_or(0, |value| value.len()),
        streaming_budget: 0,
    };

    let handle: *mut MarkdownConverterHandle = markdown_converter_new();
    assert!(!handle.is_null(), "failed to create FFI converter handle");

    let mut durations = Vec::with_capacity(cfg.iterations);
    let mut markdown_len_sum = 0usize;
    let mut token_sum = 0u64;

    let total_iters = cfg.warmup + cfg.iterations;
    for i in 0..total_iters {
        let mut result = MarkdownResult {
            markdown: ptr::null_mut(),
            markdown_len: 0,
            etag: ptr::null_mut(),
            etag_len: 0,
            token_estimate: 0,
            error_code: 0,
            error_message: ptr::null_mut(),
            error_len: 0,
            peak_memory_estimate: 0,
        };

        let start = Instant::now();
        unsafe {
            markdown_convert(
                handle,
                sample.html.as_ptr(),
                sample.html.len(),
                &options,
                &mut result,
            );
        }
        let elapsed = start.elapsed().as_secs_f64();

        assert_eq!(
            result.error_code, ERROR_SUCCESS,
            "ffi conversion failed for {} with code {}",
            sample.name, result.error_code
        );

        if i >= cfg.warmup {
            durations.push(elapsed);
            markdown_len_sum += result.markdown_len;
            token_sum += u64::from(result.token_estimate);
        }

        unsafe { markdown_result_free(&mut result) };
    }

    unsafe { markdown_converter_free(handle) };

    let peak_mem = peak_rss_bytes();
    let stats = summarize(&durations, sample.html.len());
    FfiSummary {
        stats,
        html_bytes: sample.html.len(),
        markdown_bytes_avg: markdown_len_sum / cfg.iterations.max(1),
        token_estimate_avg: (token_sum / cfg.iterations.max(1) as u64) as u32,
        peak_memory_bytes: peak_mem,
    }
}

/// Construct streaming `ConversionOptions` tailored for a given `Sample`.
///
/// The returned options enable CommonMark parsing, metadata extraction, simplified navigation,
/// and table preservation. If `sample.front_matter` is true, front-matter handling is enabled;
/// if `sample.base_url` is present it is copied into `base_url` and `resolve_relative_urls` is enabled.
///
/// # Examples
///
/// ```
/// # #[cfg(feature = "streaming")] fn _example() {
/// let sample = Sample {
///     name: "example".to_string(),
///     html: vec![],
///     target_label: "small".to_string(),
///     front_matter: true,
///     base_url: Some("https://example.com".to_string()),
/// };
/// let opts = build_streaming_options(&sample);
/// assert!(opts.include_front_matter);
/// assert_eq!(opts.base_url.as_deref(), Some("https://example.com"));
/// # }
/// ```
#[cfg(feature = "streaming")]
fn build_streaming_options(sample: &Sample) -> ConversionOptions {
    ConversionOptions {
        flavor: MarkdownFlavor::CommonMark,
        include_front_matter: sample.front_matter,
        extract_metadata: true,
        simplify_navigation: true,
        preserve_tables: true,
        base_url: sample.base_url.map(|s| s.to_string()),
        resolve_relative_urls: sample.base_url.is_some(),
    }
}

/// Benchmarks a sample using the streaming converter and returns aggregated streaming metrics.
///
/// Performs the configured warmup and measured iterations and collects latency percentiles,
/// average throughput, average generated markdown size, token estimates, and streaming-specific
/// metrics such as TTFB (time to first non-empty chunk), TTLB (time to finalize), flush counts,
/// and fallback counts.
///
/// Returns a `StreamingSummary` containing latency/throughput `Stats`, average sizes/token
/// estimates, peak RSS, and the streaming metrics described above.
///
/// # Examples
///
/// ```no_run
/// let sample = Sample {
///     name: "small".into(),
///     html: b"<p>Hello</p>".to_vec(),
///     target_label: "small".into(),
///     front_matter: false,
///     base_url: None,
/// };
/// let cfg = RunConfig { warmup: 1, iterations: 3 };
/// let summary = run_streaming_benchmark(&sample, cfg);
/// eprintln!("avg_ms = {}, ttfb_ms = {}", summary.stats.avg_ms, summary.ttfb_ms);
/// ```
#[cfg(feature = "streaming")]
fn run_streaming_benchmark(sample: &Sample, cfg: RunConfig) -> StreamingSummary {
    run_streaming_benchmark_chunked(sample, cfg, CHUNK_SIZE)
}

/// Runs streaming conversion with chunked input simulation.
///
/// Splits the HTML sample into chunks of the specified size, feeds each chunk to
/// `StreamingConverter::feed_chunk()`, and calls `finalize()` at the end. Measures
/// TTFB (time to first non-empty ChunkOutput.markdown), TTLB (time to finalize
/// completion), and accumulates flush counts.
///
/// # Notes
/// - StreamingFallback errors are caught, counted, and do not panic the benchmark.
/// - Other errors panic with a clear message.
/// - Peak RSS is sampled after all iterations complete.
///
/// # Examples
///
/// ```no_run
/// let sample = Sample {
///     name: "small".into(),
///     html: b"<p>Hello</p>".to_vec(),
///     target_label: "small".into(),
///     front_matter: false,
///     base_url: None,
/// };
/// let cfg = RunConfig { warmup: 1, iterations: 3 };
/// let summary = run_streaming_benchmark_chunked(&sample, cfg, 1024);
/// eprintln!("avg_ms = {}, chunks_processed = {}", summary.stats.avg_ms, summary.flush_count);
/// ```
#[cfg(feature = "streaming")]
fn run_streaming_benchmark_chunked(
    sample: &Sample,
    cfg: RunConfig,
    chunk_size: usize,
) -> StreamingSummary {
    let options = build_streaming_options(sample);
    let budget = MemoryBudget::default();

    let mut durations = Vec::with_capacity(cfg.iterations);
    let mut markdown_len_sum = 0usize;
    let mut token_sum = 0u64;
    let mut flush_count_sum = 0u32;
    let mut fallback_count = 0u32;
    let mut total_attempts = 0u32;
    let mut ttfb_ms_sum = 0.0;
    let mut ttlb_ms_sum = 0.0;
    // Track whether we recorded TTFB in any iteration (for averaging).
    let mut ttfb_recorded_count = 0usize;

    let total_iters = cfg.warmup + cfg.iterations;
    for iter_idx in 0..total_iters {
        let mut conv = StreamingConverter::new(options.clone(), budget.clone());
        conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));

        let iter_start = Instant::now();
        let ttfb_start = Instant::now();
        let mut ttfb_recorded = false;
        let mut ttfb_ms = 0.0;
        let mut iter_flush_count = 0u32;
        let mut iter_fallback = false;
        let mut iter_total_attempts = 0u32;
        let mut iter_markdown_len = 0usize;

        // Feed chunks in chunks of `chunk_size`.
        for chunk in sample.html.chunks(chunk_size) {
            iter_total_attempts += 1;
            match conv.feed_chunk(chunk) {
                Ok(output) => {
                    if !ttfb_recorded && !output.markdown.is_empty() {
                        ttfb_ms = ttfb_start.elapsed().as_secs_f64() * 1000.0;
                        ttfb_recorded = true;
                    }
                    iter_flush_count += output.flush_count;
                    iter_markdown_len += output.markdown.len();
                }
                Err(ConversionError::StreamingFallback { .. }) => {
                    // Expected for some inputs during pre-commit phase.
                    fallback_count += 1;
                    iter_fallback = true;
                    // Continue feeding — the converter may still produce output later.
                }
                Err(e) => panic!(
                    "unexpected streaming error for {} on iteration {}: {:?}",
                    sample.name, iter_idx, e
                ),
            }
        }

        // Finalize to get remaining output and stats.
        let result = match conv.finalize() {
            Ok(r) => r,
            Err(ConversionError::StreamingFallback { .. }) => {
                // Fallback at finalize is unusual but not a benchmark failure.
                fallback_count += 1;
                iter_fallback = true;
                StreamingResult {
                    final_markdown: Vec::new(),
                    token_estimate: None,
                    etag: None,
                    stats: StreamingStats::default(),
                }
            }
            Err(e) => panic!(
                "unexpected streaming error at finalize for {} on iteration {}: {:?}",
                sample.name, iter_idx, e
            ),
        };

        let elapsed = iter_start.elapsed().as_secs_f64();

        if ttfb_recorded {
            ttfb_ms_sum += ttfb_ms;
            ttfb_recorded_count += 1;
        }
        let ttlb_ms = elapsed * 1000.0;
        ttlb_ms_sum += ttlb_ms;

        iter_flush_count += result.stats.flush_count;
        flush_count_sum += iter_flush_count;
        total_attempts += iter_total_attempts;

        if iter_idx >= cfg.warmup && !iter_fallback {
            // Only include non-warmup, non-fallback iterations in measured stats.
            durations.push(elapsed);
            markdown_len_sum += iter_markdown_len + result.final_markdown.len();
            if let Some(tokens) = result.token_estimate {
                token_sum += u64::from(tokens);
            }
        }
    }

    let peak_mem = peak_rss_bytes();
    let measured_iters = durations.len().max(1);
    let stats = summarize(&durations, sample.html.len());

    StreamingSummary {
        stats,
        html_bytes: sample.html.len(),
        markdown_bytes_avg: markdown_len_sum / measured_iters,
        token_estimate_avg: (token_sum / measured_iters as u64) as u32,
        peak_memory_bytes: peak_mem,
        ttfb_ms: if ttfb_recorded_count > 0 {
            ttfb_ms_sum / ttfb_recorded_count as f64
        } else {
            0.0
        },
        ttlb_ms: ttlb_ms_sum / total_iters.max(1) as f64,
        // cpu_time_ms is approximated by wall-clock TTLB since Rust stdlib
        // doesn't provide cross-platform per-process CPU time without extra
        // dependencies. This is a placeholder — real CPU time will be ≤ TTLB.
        cpu_time_ms: ttlb_ms_sum / total_iters.max(1) as f64,
        flush_count: if measured_iters > 0 {
            flush_count_sum / measured_iters as u32
        } else {
            0
        },
        fallback_count,
        total_attempts,
    }
}

/// Measures average per-stage timings for converting the sample's HTML to Markdown.
///
/// The returned BreakdownSummary contains average durations per iteration (in milliseconds)
/// for parsing the HTML, converting to Markdown, generating the ETag, estimating tokens,
/// and the total end-to-end time.
///
/// # Parameters
///
/// - `sample`: the benchmark sample containing HTML and conversion options.
/// - `iterations`: number of iterations used to compute the averages; each iteration runs
///   the full parse → convert → etag → token-estimate sequence.
///
/// # Examples
///
/// ```no_run
/// let samples = build_samples();
/// let sample = &samples[1]; // e.g., "medium"
/// let breakdown = run_breakdown(sample, 100);
/// assert!(breakdown.total_ms > 0.0);
/// ```
fn run_breakdown(sample: &Sample, iterations: usize) -> BreakdownSummary {
    let converter = MarkdownConverter::with_options(ConversionOptions {
        flavor: MarkdownFlavor::CommonMark,
        include_front_matter: sample.front_matter,
        extract_metadata: false,
        simplify_navigation: true,
        preserve_tables: true,
        base_url: sample.base_url.map(|s| s.to_string()),
        resolve_relative_urls: sample.base_url.is_some(),
    });
    let etag = ETagGenerator::new();
    let token = TokenEstimator::new();

    let mut parse = 0.0;
    let mut convert = 0.0;
    let mut etag_gen = 0.0;
    let mut token_est = 0.0;
    let mut total = 0.0;

    for _ in 0..iterations {
        let t0 = Instant::now();
        let dom = parse_html_with_charset(&sample.html, Some("text/html; charset=UTF-8"))
            .expect("parse_html_with_charset failed");
        let t1 = Instant::now();

        let mut ctx = ConversionContext::new(std::time::Duration::from_millis(5000));
        let markdown = converter
            .convert_with_context(&dom, &mut ctx)
            .expect("convert_with_context failed");
        let t2 = Instant::now();

        let _etag = etag.generate(markdown.as_bytes());
        let t3 = Instant::now();

        let _tokens = token.estimate(&markdown);
        let t4 = Instant::now();

        parse += (t1 - t0).as_secs_f64();
        convert += (t2 - t1).as_secs_f64();
        etag_gen += (t3 - t2).as_secs_f64();
        token_est += (t4 - t3).as_secs_f64();
        total += (t4 - t0).as_secs_f64();
    }

    let n = iterations as f64;
    BreakdownSummary {
        parse_ms: parse * 1000.0 / n,
        convert_ms: convert * 1000.0 / n,
        etag_ms: etag_gen * 1000.0 / n,
        token_ms: token_est * 1000.0 / n,
        total_ms: total * 1000.0 / n,
    }
}

/// Prints a Markdown table summarizing FFI benchmark results.
///
/// Each row corresponds to a sample and shows HTML bytes, average Markdown bytes,
/// average token estimate, latency statistics (avg, p50, p95, p99), requests per second,
/// and input throughput in MB/s.
///
/// # Parameters
///
/// - `results`: slice of `(Sample, FfiSummary)` pairs where each pair contains the
///   sample metadata and its measured FFI summary.
///
/// # Examples
///
/// ```
/// // Print an empty table (no rows)
/// let results: Vec<(Sample, FfiSummary)> = Vec::new();
/// print_ffi_table(&results);
/// ```
fn print_ffi_table(results: &[(Sample, FfiSummary)]) {
    println!("# FFI Baseline (local, release build)");
    println!();
    println!(
        "| Sample | HTML bytes | Markdown bytes (avg) | Tokens (avg) | Avg ms | P50 ms | P95 ms | P99 ms | Req/s | Input MB/s |"
    );
    println!(
        "|--------|------------|----------------------|--------------|--------|--------|--------|--------|-------|------------|"
    );
    for (s, r) in results {
        println!(
            "| {} ({}) | {} | {} | {} | {:.3} | {:.3} | {:.3} | {:.3} | {:.1} | {:.2} |",
            s.name,
            s.target_label,
            r.html_bytes,
            r.markdown_bytes_avg,
            r.token_estimate_avg,
            r.stats.avg_ms,
            r.stats.p50_ms,
            r.stats.p95_ms,
            r.stats.p99_ms,
            r.stats.req_per_s,
            r.stats.input_mb_per_s
        );
    }
    println!();
}

/// Prints a markdown-formatted table summarizing streaming benchmark results.
///
/// The table includes per-sample sizes, averaged markdown and token counts,
/// latency percentiles (avg/p50/p95/p99), streaming-specific metrics (TTFB, TTLB),
/// flush and fallback counts, and throughput (requests/sec and MB/sec).
///
/// # Examples
///
/// ```no_run
/// // Assuming `results` is a `Vec<(Sample, StreamingSummary)>` produced by the benchmark:
/// // print_streaming_table(&results);
/// ```
#[cfg(feature = "streaming")]
fn print_streaming_table(results: &[(Sample, StreamingSummary)]) {
    println!("# Streaming Engine (local, release build)");
    println!();
    println!(
        "| Sample | HTML bytes | Markdown bytes (avg) | Tokens (avg) | Avg ms | P50 ms | P95 ms | P99 ms | TTFB ms | TTLB ms | Flushes | Fallbacks | Req/s | Input MB/s |"
    );
    println!(
        "|--------|------------|----------------------|--------------|--------|--------|--------|--------|---------|---------|---------|-----------|-------|------------|"
    );
    for (s, r) in results {
        println!(
            "| {} ({}) | {} | {} | {} | {:.3} | {:.3} | {:.3} | {:.3} | {:.3} | {:.3} | {} | {} | {:.1} | {:.2} |",
            s.name,
            s.target_label,
            r.html_bytes,
            r.markdown_bytes_avg,
            r.token_estimate_avg,
            r.stats.avg_ms,
            r.stats.p50_ms,
            r.stats.p95_ms,
            r.stats.p99_ms,
            r.ttfb_ms,
            r.ttlb_ms,
            r.flush_count,
            r.fallback_count,
            r.stats.req_per_s,
            r.stats.input_mb_per_s
        );
    }
    println!();
}

/// Prints a markdown-formatted stage timing breakdown for a sample and reports the inferred FFI/runtime overhead.
///
/// The output includes a table of stage names, their average durations (ms), each stage's share of the measured
/// total, the direct total of measured stages, the observed FFI end-to-end average, and the inferred overhead
/// (the positive difference between the FFI end-to-end average and the sum of measured stage times).
///
/// # Parameters
///
/// - `sample`: the sample whose `name` and `target_label` are printed in the section header.
/// - `b`: per-stage and total timings (milliseconds) used to compute shares and totals.
/// - `ffi_avg_ms`: observed end-to-end average FFI timing in milliseconds used to compute inferred overhead.
///
/// # Examples
///
/// ```no_run
/// // Construct sample and breakdown values appropriate for your test harness,
/// // then call print_breakdown to emit the markdown table to stdout.
/// let sample = Sample {
///     name: "medium".into(),
///     html: Vec::new(),
///     target_label: "medium-100kb".into(),
///     front_matter: false,
///     base_url: None,
/// };
/// let breakdown = BreakdownSummary {
///     parse_ms: 1.2,
///     convert_ms: 3.4,
///     etag_ms: 0.1,
///     token_ms: 0.5,
///     total_ms: 5.2,
/// };
/// print_breakdown(&sample, &breakdown, 6.0);
/// ```
fn print_breakdown(sample: &Sample, b: &BreakdownSummary, ffi_avg_ms: f64) {
    let known = b.parse_ms + b.convert_ms + b.etag_ms + b.token_ms;
    let ffi_overhead = (ffi_avg_ms - known).max(0.0);
    println!(
        "## Stage Breakdown ({}, {})",
        sample.name, sample.target_label
    );
    println!();
    println!("| Stage | Avg ms | Share (direct stage timing) |");
    println!("|-------|--------|-----------------------------|");
    for (name, v) in [
        ("parse_html_with_charset", b.parse_ms),
        ("convert_with_context", b.convert_ms),
        ("etag.generate", b.etag_ms),
        ("token_estimate", b.token_ms),
    ] {
        let share = if b.total_ms > 0.0 {
            v / b.total_ms * 100.0
        } else {
            0.0
        };
        println!("| {} | {:.3} | {:.1}% |", name, v, share);
    }
    println!("| direct total | {:.3} | 100.0% |", b.total_ms);
    println!("| ffi end-to-end avg | {:.3} | - |", ffi_avg_ms);
    println!(
        "| inferred ffi/runtime overhead | {:.3} | - |",
        ffi_overhead
    );
    println!();
}

/// Get the short Git commit hash for the current HEAD.
///
/// If the `git` command cannot be executed, returns invalid data, or exits with a
/// non-zero status, this function yields the string `"unknown"`.
///
/// # Examples
///
/// ```ignore
/// let hash = git_commit_hash();
/// // Typical short hashes are at least 7 characters, but the function may
/// // return "unknown" when Git is not available.
/// assert!(hash == "unknown" || hash.len() >= 7);
/// ```
fn git_commit_hash() -> String {
    Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .ok()
        .and_then(|o| {
            if o.status.success() {
                String::from_utf8(o.stdout)
                    .ok()
                    .map(|s| s.trim().to_string())
            } else {
                None
            }
        })
        .unwrap_or_else(|| "unknown".to_string())
}

/// Constructs a platform identifier string in "{os}-{arch}" form.
///
/// The function normalizes common Rust std constants to uname-style names
/// (e.g., "macos" -> "darwin", "aarch64" -> "arm64") and returns the
/// concatenation "{os}-{arch}".
///
/// # Examples
///
/// ```ignore
/// let platform = detect_platform();
/// assert!(platform.contains('-'));
/// ```
fn detect_platform() -> String {
    // Use the shared uname-style naming convention from tools/perf/report_utils.py.
    // Rust's env::consts::OS returns "macos" but uname gives "darwin";
    // env::consts::ARCH returns "aarch64" but uname gives "arm64".
    let os = match env::consts::OS {
        "macos" => "darwin",
        other => other,
    };
    let arch = match env::consts::ARCH {
        "aarch64" => "arm64",
        other => other,
    };
    format!("{os}-{arch}")
}

/// Produce the current UTC timestamp in ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ).
///
/// The string uses whole-second precision and always ends with the literal `Z` to
/// indicate UTC; fractional seconds and time zone offsets are not included.
///
/// # Examples
///
/// ```ignore
/// let ts = iso8601_now();
/// // Example format: "2026-03-17T12:34:56Z"
/// assert!(ts.ends_with('Z'));
/// assert_eq!(ts.len(), 20);
/// ```
fn iso8601_now() -> String {
    use std::time::SystemTime;
    let dur = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default();
    let secs = dur.as_secs();

    // Convert epoch seconds to date-time components.
    let days = secs / 86400;
    let time_of_day = secs % 86400;
    let hours = time_of_day / 3600;
    let minutes = (time_of_day % 3600) / 60;
    let seconds = time_of_day % 60;

    // Compute year/month/day from days since epoch (1970-01-01).
    let (year, month, day) = epoch_days_to_ymd(days);

    format!("{year:04}-{month:02}-{day:02}T{hours:02}:{minutes:02}:{seconds:02}Z")
}

/// Convert days since the Unix epoch to a Gregorian calendar date as (year, month, day).
///
/// The returned tuple is in the order `(year, month, day)` using the proleptic Gregorian calendar.
///
/// # Examples
///
/// ```ignore
/// // 1970-01-01 is day 0
/// assert_eq!(epoch_days_to_ymd(0), (1970, 1, 1));
/// // 2000-01-01
/// // Compute days since epoch for 2000-01-01 via known value: 10957
/// assert_eq!(epoch_days_to_ymd(10957), (2000, 1, 1));
/// ```
fn epoch_days_to_ymd(days: u64) -> (u64, u64, u64) {
    // Algorithm from Howard Hinnant's `chrono`-compatible date library.
    let z = days + 719468;
    let era = z / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

/// Assembles a JSON measurement report from FFI results, optional streaming results, and optional per-tier stage breakdowns.
///
/// The returned `serde_json::Value` follows the measurement report schema and includes top-level metadata
/// (`schema_version`, `report_type`, `timestamp`, `git_commit`, `platform`, `engine`) and a `tiers` object
/// that maps tier keys to per-tier metrics (size, latency percentiles, throughput, peak memory, and run counts).
/// When streaming results are provided, a `streaming_metrics` object is also included with streaming-specific
/// fields such as `ttfb_ms`, `ttlb_ms`, `cpu_time_ms`, `flush_count`, `fallback_count`, and `fallback_rate`.
///
/// # Examples
///
/// ```ignore
/// // Prepare or reuse collected results in tests; this example demonstrates the call site.
/// let report = build_measurement_report(&[], &None, &[], "linux-x86_64", BenchmarkEngine::FullBuffer);
/// assert_eq!(report["schema_version"], "1.0.0");
/// assert!(report.get("tiers").and_then(|t| t.as_object()).is_some());
/// ```
fn build_measurement_report(
    ffi_results: &[(Sample, FfiSummary, RunConfig)],
    streaming_results: &Option<Vec<(Sample, StreamingSummary, RunConfig)>>,
    breakdowns: &[(&str, BreakdownSummary)],
    platform: &str,
    engine: BenchmarkEngine,
) -> serde_json::Value {
    let mut tiers = serde_json::Map::new();

    // Build FFI tier data.
    for (sample, ffi, cfg) in ffi_results {
        let key = tier_key(sample.name);

        // Look up the stage breakdown for this sample, if available.
        let breakdown_opt = breakdowns.iter().find(|(name, _)| *name == sample.name);

        let stage_breakdown = if let Some((_, bd)) = breakdown_opt {
            let total = bd.total_ms;
            serde_json::json!({
                "parse_pct": if total > 0.0 { bd.parse_ms / total * 100.0 } else { 0.0 },
                "convert_pct": if total > 0.0 { bd.convert_ms / total * 100.0 } else { 0.0 },
                "etag_pct": if total > 0.0 { bd.etag_ms / total * 100.0 } else { 0.0 },
                "token_pct": if total > 0.0 { bd.token_ms / total * 100.0 } else { 0.0 },
            })
        } else {
            serde_json::json!({
                "parse_pct": 0.0,
                "convert_pct": 0.0,
                "etag_pct": 0.0,
                "token_pct": 0.0,
            })
        };

        let tier_value = serde_json::json!({
            "html_bytes": ffi.html_bytes,
            "markdown_bytes_avg": ffi.markdown_bytes_avg,
            "token_estimate_avg": ffi.token_estimate_avg,
            "p50_ms": ffi.stats.p50_ms,
            "p95_ms": ffi.stats.p95_ms,
            "p99_ms": ffi.stats.p99_ms,
            "peak_memory_bytes": ffi.peak_memory_bytes,
            "req_per_s": ffi.stats.req_per_s,
            "input_mb_per_s": ffi.stats.input_mb_per_s,
            "stage_breakdown": stage_breakdown,
            "iterations": cfg.iterations,
            "warmup": cfg.warmup,
        });

        tiers.insert(key.to_string(), tier_value);
    }

    // Build streaming tier data if available.
    let streaming_metrics = if let Some(streaming_results) = streaming_results {
        let mut metrics_map = serde_json::Map::new();
        for (sample, streaming, cfg) in streaming_results {
            let key = tier_key(sample.name);
            let fallback_rate = if streaming.total_attempts > 0 {
                streaming.fallback_count as f64 / streaming.total_attempts as f64
            } else {
                0.0
            };
            let tier_value = serde_json::json!({
                "html_bytes": streaming.html_bytes,
                "markdown_bytes_avg": streaming.markdown_bytes_avg,
                "token_estimate_avg": streaming.token_estimate_avg,
                "p50_ms": streaming.stats.p50_ms,
                "p95_ms": streaming.stats.p95_ms,
                "p99_ms": streaming.stats.p99_ms,
                "peak_memory_bytes": streaming.peak_memory_bytes,
                "ttfb_ms": streaming.ttfb_ms,
                "ttlb_ms": streaming.ttlb_ms,
                "cpu_time_ms": streaming.cpu_time_ms,
                "flush_count": streaming.flush_count,
                "fallback_count": streaming.fallback_count,
                "fallback_rate": fallback_rate,
                "req_per_s": streaming.stats.req_per_s,
                "input_mb_per_s": streaming.stats.input_mb_per_s,
                "iterations": cfg.iterations,
                "warmup": cfg.warmup,
            });
            metrics_map.insert(key.to_string(), tier_value);
        }
        Some(serde_json::Value::Object(metrics_map))
    } else {
        None
    };

    let mut report = serde_json::json!({
        "schema_version": "1.0.0",
        "report_type": "measurement",
        "timestamp": iso8601_now(),
        "git_commit": git_commit_hash(),
        "platform": platform,
        "engine": engine.to_string(),
        "tiers": tiers,
    });

    // Add streaming metrics if available.
    if let Some(metrics) = streaming_metrics {
        report["streaming_metrics"] = metrics;
    }

    report
}

/// Write a measurement report as pretty-printed JSON to the specified file path.
///
/// Creates parent directories if necessary and panics on serialization or I/O failures.
///
/// # Examples
///
/// ```ignore
/// use std::path::Path;
/// use serde_json::json;
///
/// let report = json!({ "schema_version": "1", "tiers": {} });
/// let path = Path::new("/tmp/measurement_report_example.json");
/// // Writes the JSON report to the given path (creates parent directories if needed).
/// write_json_report(path, &report);
/// ```
fn write_json_report(path: &Path, report: &serde_json::Value) {
    let json_str =
        serde_json::to_string_pretty(report).expect("failed to serialize measurement report");
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .unwrap_or_else(|e| panic!("failed to create directory {}: {e}", parent.display()));
    }
    fs::write(path, json_str).unwrap_or_else(|e| panic!("failed to write {}: {e}", path.display()));
    eprintln!("Measurement report written to {}", path.display());
}

/// Selects the benchmark run configuration for a sample name.
///
/// Returns a `RunConfig` with warmup and iteration counts for the provided sample name.
/// Recognized names:
/// - `"small"` → warmup 100, iterations 3000
/// - `"medium"` and `"medium-front-matter"` → warmup 50, iterations 1000
/// - `"large"` → warmup 5, iterations 40
///
/// Panics if `name` is not one of the recognized sample identifiers.
///
/// # Examples
///
/// ```ignore
/// let cfg = config_for_sample("medium");
/// assert_eq!(cfg.warmup, 50);
/// assert_eq!(cfg.iterations, 1000);
/// ```
fn config_for_sample(name: &str) -> RunConfig {
    match name {
        "small" => RunConfig {
            warmup: 100,
            iterations: 3000,
        },
        "medium" | "medium-front-matter" => RunConfig {
            warmup: 50,
            iterations: 1000,
        },
        "large" => RunConfig {
            warmup: 5,
            iterations: 40,
        },
        _ => panic!("unknown sample: {name}"),
    }
}

/// Run a single benchmark sample by name, print a concise summary line, and optionally write a measurement-report JSON.
///
/// The provided `name` may be an alias (e.g., `large-1m`) and will be resolved to an internal sample. The function executes the selected engine (`FullBuffer`, `Streaming`, or `Both`), prints a compact one-line summary to stdout for the measured engine(s), and, when `json_output` is `Some(path)`, writes a Measurement Report JSON to `path` containing the measured tier(s) and metadata that include the supplied `platform`.
///
/// # Examples
///
/// ```
/// // Print results for the "small" sample using the full-buffer engine only.
/// run_single_mode("small", None, "linux-x86_64", BenchmarkEngine::FullBuffer);
///
/// // Run the "large-1m" alias with both engines and write a JSON report.
/// run_single_mode(
///     "large-1m",
///     Some(std::path::Path::new("/tmp/report.json")),
///     "darwin-arm64",
///     BenchmarkEngine::Both,
/// );
/// ```
fn run_single_mode(
    name: &str,
    json_output: Option<&Path>,
    platform: &str,
    engine: BenchmarkEngine,
) {
    let resolved = resolve_single_name(name);
    let samples = build_samples();
    let sample = samples
        .into_iter()
        .find(|s| s.name == resolved)
        .unwrap_or_else(|| panic!("unknown sample: {resolved}"));
    let cfg = config_for_sample(sample.name);

    match engine {
        BenchmarkEngine::FullBuffer => {
            let result = run_ffi_baseline(&sample, cfg);
            println!(
                "single_sample={} html_bytes={} avg_ms={:.3} p95_ms={:.3} req_per_s={:.1}",
                sample.name,
                result.html_bytes,
                result.stats.avg_ms,
                result.stats.p95_ms,
                result.stats.req_per_s
            );

            if let Some(path) = json_output {
                let report = build_measurement_report(
                    &[(sample, result, cfg)],
                    &None,
                    &[],
                    platform,
                    engine,
                );
                write_json_report(path, &report);
            }
        }
        BenchmarkEngine::Streaming => {
            #[cfg(feature = "streaming")]
            {
                let result = run_streaming_benchmark(&sample, cfg);
                println!(
                    "single_sample={} html_bytes={} avg_ms={:.3} p95_ms={:.3} ttfb_ms={:.3} ttlb_ms={:.3} req_per_s={:.1}",
                    sample.name,
                    result.html_bytes,
                    result.stats.avg_ms,
                    result.stats.p95_ms,
                    result.ttfb_ms,
                    result.ttlb_ms,
                    result.stats.req_per_s
                );

                if let Some(path) = json_output {
                    let report = build_measurement_report(
                        &[],
                        &Some(vec![(sample, result, cfg)]),
                        &[],
                        platform,
                        engine,
                    );
                    write_json_report(path, &report);
                }
            }
            #[cfg(not(feature = "streaming"))]
            {
                eprintln!(
                    "warning: streaming engine requested but streaming feature is not enabled"
                );
            }
        }
        BenchmarkEngine::Both => {
            let ffi_result = run_ffi_baseline(&sample, cfg);
            println!(
                "single_sample={} engine=full-buffer html_bytes={} avg_ms={:.3} p95_ms={:.3} req_per_s={:.1}",
                sample.name,
                ffi_result.html_bytes,
                ffi_result.stats.avg_ms,
                ffi_result.stats.p95_ms,
                ffi_result.stats.req_per_s
            );

            #[cfg(feature = "streaming")]
            {
                let streaming_result = run_streaming_benchmark(&sample, cfg);
                println!(
                    "single_sample={} engine=streaming html_bytes={} avg_ms={:.3} p95_ms={:.3} ttfb_ms={:.3} ttlb_ms={:.3} req_per_s={:.1}",
                    sample.name,
                    streaming_result.html_bytes,
                    streaming_result.stats.avg_ms,
                    streaming_result.stats.p95_ms,
                    streaming_result.ttfb_ms,
                    streaming_result.ttlb_ms,
                    streaming_result.stats.req_per_s
                );

                if let Some(path) = json_output {
                    let report = build_measurement_report(
                        &[(sample.clone(), ffi_result, cfg)],
                        &Some(vec![(sample, streaming_result, cfg)]),
                        &[],
                        platform,
                        engine,
                    );
                    write_json_report(path, &report);
                }
            }
            #[cfg(not(feature = "streaming"))]
            {
                eprintln!(
                    "warning: streaming engine requested but streaming feature is not enabled"
                );
                if let Some(path) = json_output {
                    let report = build_measurement_report(
                        &[(sample, ffi_result, cfg)],
                        &None,
                        &[],
                        platform,
                        engine,
                    );
                    write_json_report(path, &report);
                }
            }
        }
    }
}

/// Entry point that runs the configured Markdown conversion benchmarks, prints summaries, and optionally writes a JSON measurement report.
///
/// The program behavior is controlled by command-line flags:
/// - `--single <name>` runs only the specified sample and exits.
/// - `--engine <full-buffer|streaming|both>` selects which benchmark engine(s) to run.
/// - `--json-output <path>` writes a structured measurement report to the given path when provided.
///
/// When run without `--single`, the harness runs all samples, prints result tables, and performs a stage breakdown for the `medium` sample. When `--json-output` is specified, stage breakdowns for all tiers are computed and included in the output file.
///
/// # Examples
///
/// ```ignore
/// // Run the CLI entrypoint as a testable example.
/// // (In practice this program is driven by command-line arguments.)
/// main();
/// ```
fn main() {
    let cli = parse_args();
    let platform = cli.platform_override.unwrap_or_else(detect_platform);

    if let Some(ref single_name) = cli.single {
        run_single_mode(
            single_name,
            cli.json_output.as_deref(),
            &platform,
            cli.engine,
        );
        return;
    }

    let samples = build_samples();

    match cli.engine {
        BenchmarkEngine::FullBuffer => {
            let mut results: Vec<(Sample, FfiSummary, RunConfig)> = Vec::new();

            for sample in &samples {
                let cfg = config_for_sample(sample.name);
                let summary = run_ffi_baseline(sample, cfg);
                results.push((sample.clone(), summary, cfg));
            }

            // Existing text output (backward compatible).
            let table_results: Vec<(Sample, FfiSummary)> = results
                .iter()
                .map(|(s, f, _)| (s.clone(), f.clone()))
                .collect();
            print_ffi_table(&table_results);

            let medium = samples
                .iter()
                .find(|s| s.name == "medium")
                .expect("medium sample");
            let breakdown = run_breakdown(medium, 200);
            let medium_ffi_avg = results
                .iter()
                .find(|(s, _, _)| s.name == "medium")
                .map(|(_, r, _)| r.stats.avg_ms)
                .expect("medium ffi result");
            print_breakdown(medium, &breakdown, medium_ffi_avg);

            if let Some(ref path) = cli.json_output {
                // Run breakdowns for all tiers to populate stage_breakdown in JSON.
                let mut breakdowns: Vec<(&str, BreakdownSummary)> = Vec::new();
                for sample in &samples {
                    let bd_iters = match sample.name {
                        "small" => 500,
                        "medium" | "medium-front-matter" => 200,
                        "large" => 10,
                        _ => 100,
                    };
                    let bd = run_breakdown(sample, bd_iters);
                    breakdowns.push((sample.name, bd));
                }
                let report =
                    build_measurement_report(&results, &None, &breakdowns, &platform, cli.engine);
                write_json_report(path, &report);
            }
        }
        BenchmarkEngine::Streaming => {
            #[cfg(feature = "streaming")]
            {
                let mut results: Vec<(Sample, StreamingSummary, RunConfig)> = Vec::new();

                for sample in &samples {
                    let cfg = config_for_sample(sample.name);
                    let summary = run_streaming_benchmark(sample, cfg);
                    results.push((sample.clone(), summary, cfg));
                }

                // Text output for streaming engine.
                let table_results: Vec<(Sample, StreamingSummary)> = results
                    .iter()
                    .map(|(s, f, _)| (s.clone(), f.clone()))
                    .collect();
                print_streaming_table(&table_results);

                if let Some(ref path) = cli.json_output {
                    let report =
                        build_measurement_report(&[], &Some(results), &[], &platform, cli.engine);
                    write_json_report(path, &report);
                }
            }
            #[cfg(not(feature = "streaming"))]
            {
                eprintln!(
                    "warning: streaming engine requested but streaming feature is not enabled"
                );
            }
        }
        BenchmarkEngine::Both => {
            // Run full-buffer first.
            let mut ffi_results: Vec<(Sample, FfiSummary, RunConfig)> = Vec::new();
            for sample in &samples {
                let cfg = config_for_sample(sample.name);
                let summary = run_ffi_baseline(sample, cfg);
                ffi_results.push((sample.clone(), summary, cfg));
            }

            // Print FFI table.
            let ffi_table_results: Vec<(Sample, FfiSummary)> = ffi_results
                .iter()
                .map(|(s, f, _)| (s.clone(), f.clone()))
                .collect();
            print_ffi_table(&ffi_table_results);

            // Then run streaming.
            #[cfg(feature = "streaming")]
            {
                let mut streaming_results: Vec<(Sample, StreamingSummary, RunConfig)> = Vec::new();
                for sample in &samples {
                    let cfg = config_for_sample(sample.name);
                    let summary = run_streaming_benchmark(sample, cfg);
                    streaming_results.push((sample.clone(), summary, cfg));
                }

                // Print streaming table.
                let streaming_table_results: Vec<(Sample, StreamingSummary)> = streaming_results
                    .iter()
                    .map(|(s, f, _)| (s.clone(), f.clone()))
                    .collect();
                print_streaming_table(&streaming_table_results);

                // Print breakdown for medium sample.
                let medium = samples
                    .iter()
                    .find(|s| s.name == "medium")
                    .expect("medium sample");
                let breakdown = run_breakdown(medium, 200);
                let medium_ffi_avg = ffi_results
                    .iter()
                    .find(|(s, _, _)| s.name == "medium")
                    .map(|(_, r, _)| r.stats.avg_ms)
                    .expect("medium ffi result");
                print_breakdown(medium, &breakdown, medium_ffi_avg);

                if let Some(ref path) = cli.json_output {
                    // Run breakdowns for all tiers to populate stage_breakdown in JSON.
                    let mut breakdowns: Vec<(&str, BreakdownSummary)> = Vec::new();
                    for sample in &samples {
                        let bd_iters = match sample.name {
                            "small" => 500,
                            "medium" | "medium-front-matter" => 200,
                            "large" => 10,
                            _ => 100,
                        };
                        let bd = run_breakdown(sample, bd_iters);
                        breakdowns.push((sample.name, bd));
                    }
                    let report = build_measurement_report(
                        &ffi_results,
                        &Some(streaming_results),
                        &breakdowns,
                        &platform,
                        cli.engine,
                    );
                    write_json_report(path, &report);
                }
            }
            #[cfg(not(feature = "streaming"))]
            {
                eprintln!(
                    "warning: streaming engine requested but streaming feature is not enabled"
                );
                if let Some(ref path) = cli.json_output {
                    let report =
                        build_measurement_report(&ffi_results, &None, &[], &platform, cli.engine);
                    write_json_report(path, &report);
                }
            }
        }
    }
}
