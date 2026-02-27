use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::ptr;
use std::time::Instant;

use nginx_markdown_converter::converter::{
    ConversionContext, ConversionOptions, MarkdownConverter, MarkdownFlavor,
};
use nginx_markdown_converter::etag_generator::ETagGenerator;
use nginx_markdown_converter::ffi::{
    markdown_convert, markdown_converter_free, markdown_converter_new, markdown_result_free,
    MarkdownConverterHandle, MarkdownOptions, MarkdownResult, ERROR_SUCCESS,
};
use nginx_markdown_converter::parser::parse_html_with_charset;
use nginx_markdown_converter::token_estimator::TokenEstimator;

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
}

#[derive(Default, Clone)]
struct BreakdownSummary {
    parse_ms: f64,
    convert_ms: f64,
    etag_ms: f64,
    token_ms: f64,
    total_ms: f64,
}

fn percentile_ms(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = ((sorted.len() - 1) as f64 * p).round() as usize;
    sorted[idx]
}

fn summarize(durations_s: &[f64], input_bytes: usize) -> Stats {
    let mut ms: Vec<f64> = durations_s.iter().map(|d| d * 1000.0).collect();
    ms.sort_by(|a, b| a.partial_cmp(b).unwrap());
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
    // examples/ -> components/rust-converter/ -> repo root
    let here = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    here.parent()
        .expect("rust-converter has parent")
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
        },
        Sample {
            name: "medium",
            html: medium,
            target_label: "~10KB",
        },
        Sample {
            name: "large",
            html: large,
            target_label: "~1MB",
        },
    ]
}

fn run_ffi_baseline(sample: &Sample, cfg: RunConfig) -> FfiSummary {
    let content_type = b"text/html; charset=UTF-8";
    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 1,
        estimate_tokens: 1,
        front_matter: 0,
        content_type: content_type.as_ptr(),
        content_type_len: content_type.len(),
        base_url: ptr::null(),
        base_url_len: 0,
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

    let stats = summarize(&durations, sample.html.len());
    FfiSummary {
        stats,
        html_bytes: sample.html.len(),
        markdown_bytes_avg: markdown_len_sum / cfg.iterations.max(1),
        token_estimate_avg: (token_sum / cfg.iterations.max(1) as u64) as u32,
    }
}

fn run_breakdown(sample: &Sample, iterations: usize) -> BreakdownSummary {
    let converter = MarkdownConverter::with_options(ConversionOptions {
        flavor: MarkdownFlavor::CommonMark,
        include_front_matter: false,
        extract_metadata: false,
        simplify_navigation: true,
        preserve_tables: true,
        base_url: None,
        resolve_relative_urls: false,
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

fn print_ffi_table(results: &[(Sample, FfiSummary)]) {
    println!("# FFI Baseline (local, release build)");
    println!();
    println!("| Sample | HTML bytes | Markdown bytes (avg) | Tokens (avg) | Avg ms | P50 ms | P95 ms | P99 ms | Req/s | Input MB/s |");
    println!("|--------|------------|----------------------|--------------|--------|--------|--------|--------|-------|------------|");
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

fn run_single_mode(name: &str) {
    let samples = build_samples();
    let sample = samples
        .into_iter()
        .find(|s| s.name == name)
        .unwrap_or_else(|| panic!("unknown sample: {name}"));
    let cfg = match sample.name {
        "small" => RunConfig {
            warmup: 100,
            iterations: 3000,
        },
        "medium" => RunConfig {
            warmup: 50,
            iterations: 1000,
        },
        "large" => RunConfig {
            warmup: 5,
            iterations: 40,
        },
        _ => unreachable!(),
    };
    let result = run_ffi_baseline(&sample, cfg);
    println!(
        "single_sample={} html_bytes={} avg_ms={:.3} p95_ms={:.3} req_per_s={:.1}",
        sample.name,
        result.html_bytes,
        result.stats.avg_ms,
        result.stats.p95_ms,
        result.stats.req_per_s
    );
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() == 3 && args[1] == "--single" {
        run_single_mode(&args[2]);
        return;
    }

    let samples = build_samples();
    let mut results = Vec::new();

    for sample in &samples {
        let cfg = match sample.name {
            "small" => RunConfig {
                warmup: 100,
                iterations: 3000,
            },
            "medium" => RunConfig {
                warmup: 50,
                iterations: 1000,
            },
            "large" => RunConfig {
                warmup: 5,
                iterations: 40,
            },
            _ => unreachable!(),
        };
        let summary = run_ffi_baseline(sample, cfg);
        results.push((sample.clone(), summary));
    }

    print_ffi_table(&results);

    let medium = samples
        .iter()
        .find(|s| s.name == "medium")
        .expect("medium sample");
    let breakdown = run_breakdown(medium, 200);
    let medium_ffi_avg = results
        .iter()
        .find(|(s, _)| s.name == "medium")
        .map(|(_, r)| r.stats.avg_ms)
        .expect("medium ffi result");
    print_breakdown(medium, &breakdown, medium_ffi_avg);
}
