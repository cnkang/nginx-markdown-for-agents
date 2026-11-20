//! Full-buffer conversion and passthrough of a large static HTML response.

use crate::scenarios::{ScenarioContext, ScenarioReport, common};
use crate::{assertions, http};
use anyhow::{Context, Result};
use std::path::Path;
use std::time::{Duration, Instant};

pub const NAME: &str = "large-response";
const DEADLINE: Duration = Duration::from_secs(20);
const REQUESTS: usize = 30;
const CONCURRENCY: usize = 3;
const ACCEPT: [(&str, &str); 1] = [("Accept", "text/markdown")];

pub fn prepare_fixture(runtime: &Path) -> Result<()> {
    let seed = include_bytes!("../../../../tests/corpus/complex/blog-post.html");
    let mut payload =
        b"<!doctype html><html><head><meta charset=\"UTF-8\"><title>large</title></head><body>\n"
            .to_vec();
    while payload.len() + seed.len() + 32 < 1024 * 1024 {
        payload.extend_from_slice(seed);
        payload.push(b'\n');
    }
    payload.extend_from_slice(b"</body></html>\n");
    for location in ["full", "passthrough"] {
        let directory = runtime.join("html").join(location);
        std::fs::create_dir_all(&directory)?;
        std::fs::write(directory.join("large.html"), &payload)?;
    }
    Ok(())
}

pub fn nginx_conf(load: &str, port: u16) -> String {
    format!(
        r#"{load}worker_processes 1;
error_log nginx-error.log warn;
pid nginx.pid;
events {{ worker_connections 256; }}
http {{
    access_log nginx-access.log;
    sendfile on;
    server {{
        listen 127.0.0.1:{port};
        root html;
        types {{ text/html html; }}
        location /passthrough/ {{
            markdown_filter off;
        }}
        location /full/ {{
            markdown_filter on;
            markdown_accept force;
            markdown_streaming off;
            markdown_cache_validation full;
            markdown_log_verbosity warn;
        }}
    }}
}}
"#
    )
}

pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    let start = Instant::now();
    let raw = ctx.artifact_dir().join("raw");
    std::fs::create_dir(&raw)?;
    let base = format!("http://127.0.0.1:{}", ctx.port);
    let url = format!("{base}/full/large.html");
    let passthrough =
        http::get_with_timeout(&format!("{base}/passthrough/large.html"), &[], DEADLINE)?;
    let markdown = http::get_with_timeout(&url, &ACCEPT, DEADLINE)?;
    common::save_response(&raw, "passthrough_large", &passthrough)?;
    common::save_response(&raw, "markdown_large", &markdown)?;
    let markdown_bytes = markdown.body.len();
    let mut results = vec![
        assertions::assert_status("passthrough_status", passthrough.status, 200),
        assertions::assert_status("markdown_status", markdown.status, 200),
        common::assert_condition(
            "passthrough_size",
            passthrough.body.len() > 65536,
            "more than 64 KiB",
        ),
        common::assert_condition("markdown_size", markdown_bytes > 0, "nonempty response"),
        assertions::assert_header_pattern(
            "markdown_type",
            "Content-Type",
            "(?i)^text/markdown; charset=utf-8",
            &markdown.headers,
        ),
    ];

    let start_load = Instant::now();
    let (failures, non_success) = run_load(&url, markdown_bytes)?;
    let elapsed = start_load.elapsed().as_secs_f64();
    let summary = format!(
        "requests={REQUESTS}\nconcurrency={CONCURRENCY}\nfailed={failures}\nnon_2xx={non_success}\nrps={:.2}\nmean_ms={:.2}\n",
        REQUESTS as f64 / elapsed,
        elapsed * 1_000.0 * CONCURRENCY as f64 / REQUESTS as f64,
    );
    std::fs::write(raw.join("load-summary.txt"), &summary)?;
    println!("{summary}");
    results.push(common::assert_condition(
        "load_failures",
        failures == 0,
        "zero failed requests",
    ));
    results.push(common::assert_condition(
        "load_status",
        non_success == 0,
        "zero non-2xx responses",
    ));
    Ok(common::finalize_report(NAME, start, results))
}

fn run_load(url: &str, expected_bytes: usize) -> Result<(usize, usize)> {
    std::thread::scope(|scope| {
        let workers: Vec<_> = (0..CONCURRENCY)
            .map(|_| {
                scope.spawn(move || {
                    let mut failures = 0;
                    let mut non_success = 0;
                    for _ in 0..REQUESTS / CONCURRENCY {
                        match http::get_with_timeout(
                            url,
                            &[ACCEPT[0], ("Connection", "close")],
                            DEADLINE,
                        ) {
                            Ok(response) if !(200..300).contains(&response.status) => {
                                non_success += 1
                            }
                            Ok(response) if response.body.len() != expected_bytes => failures += 1,
                            Ok(_) => {}
                            Err(_) => failures += 1,
                        }
                    }
                    (failures, non_success)
                })
            })
            .collect();
        workers
            .into_iter()
            .try_fold((0, 0), |(failures, non_success), worker| {
                let (worker_failed, worker_non_success) = worker
                    .join()
                    .ok()
                    .context("large-response load worker panicked")?;
                Ok((failures + worker_failed, non_success + worker_non_success))
            })
    })
}
