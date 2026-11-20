//! Error handling and conversion-memory boundary coverage.

use crate::assertions;
use crate::fixtures::{FixtureSpec, RouteBehavior, RouteSpec};
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport, common};
use anyhow::Result;
use std::collections::HashMap;
use std::path::Path;
use std::time::Instant;

const SCENARIO: &str = "error-handling";
const MEMORY_LIMIT: usize = 64 * 1024;
const HTML: &str = "(?i)^text/html";
const MARKDOWN: &str = "(?i)^text/markdown";

pub fn fixture_spec(port: u16) -> FixtureSpec {
    let valid = format!(
        "<html><head><title>Valid</title></head>\n<body><h1>Valid Page</h1>{}</body></html>\n",
        "<p>Content here.</p>".repeat(80)
    );
    let large = format!(
        "<html><head><title>Large</title></head>\n<body><h1>Large Page</h1>{}</body></html>\n",
        "<p>Oversize content.</p>".repeat(4000)
    );
    let malformed = r#"<html><head><title>Bad
<body><h1>Unclosed heading
<div><div><div>nested without closing
<p>paragraph without closing
<a href="link without quote>text</a>
</html>
"#;
    let small = "<html><head><title>Small</title></head>\n\
                 <body><h1>Small Page</h1><p>Content.</p></body></html>\n";
    let routes = [
        ("/valid", 200, valid),
        ("/malformed", 200, malformed.into()),
        ("/empty", 200, String::new()),
        (
            "/error500",
            500,
            "<html><body><h1>Server Error</h1></body></html>".into(),
        ),
        (
            "/error502",
            502,
            "<html><body><h1>Bad Gateway</h1></body></html>".into(),
        ),
        (
            "/partial206",
            206,
            "<html><body><h1>Partial</h1></body></html>".into(),
        ),
        ("/small", 200, small.into()),
        ("/large", 200, large),
    ];
    FixtureSpec {
        listen_port: Some(port),
        routes: routes
            .into_iter()
            .map(|(path, status, body)| RouteSpec {
                path: path.into(),
                behavior: RouteBehavior::Fixed {
                    status,
                    body,
                    content_type: "text/html; charset=UTF-8".into(),
                    etag: None,
                },
            })
            .collect(),
    }
}

pub fn nginx_conf(
    load_module: &str,
    error_log: &Path,
    access_log: &Path,
    pid_path: &Path,
    port: u16,
    upstream_port: u16,
) -> String {
    format!(
        r#"{load_module}worker_processes 1;
error_log "{}" info;
pid "{}";
events {{ worker_connections 512; }}
http {{
    access_log "{}";
    server {{
        listen 127.0.0.1:{port};
        markdown_filter on;
        markdown_accept force;
        markdown_limits conversion_memory=10m parser_budget=10m conversion_timeout=120s;
        markdown_error_policy pass;
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        location /md/ {{
            proxy_pass http://127.0.0.1:{upstream_port}/;
        }}
        location /md-small/ {{
            markdown_limits conversion_memory=64k parser_budget=64k streaming_buffer=64k conversion_timeout=120s;
            proxy_pass http://127.0.0.1:{upstream_port}/;
        }}
    }}
}}
"#,
        error_log.display(),
        pid_path.display(),
        access_log.display(),
    )
}

pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    let start = Instant::now();
    let mut results = Vec::new();
    let headers = HashMap::from([("Accept".into(), "text/markdown".into())]);
    let base_url = format!("http://127.0.0.1:{}", ctx.port);
    let raw = ctx.artifact_dir().join("raw");
    std::fs::create_dir(&raw)?;

    let upstream_url = format!("http://127.0.0.1:{}/large", ctx.upstream_port);
    let oversize = common::try_get_with_headers(
        &upstream_url,
        &HashMap::new(),
        &mut results,
        "case8_oversize_fixture",
    );
    if let Some(response) = &oversize {
        let bytes = response.body.len();
        results.push(AssertionResult {
            name: "case8_oversize_fixture".into(),
            passed: bytes > MEMORY_LIMIT,
            expected: format!("more than {MEMORY_LIMIT} bytes"),
            actual: bytes.to_string(),
            message: (bytes <= MEMORY_LIMIT).then(|| format!("oversize fixture has {bytes} bytes")),
        });
    }

    let cases = [
        ("/md/valid", 200, Some(MARKDOWN)),
        ("/md/malformed", 200, None),
        ("/md/empty", 200, None),
        ("/md/error500", 500, Some(HTML)),
        ("/md/error502", 502, Some(HTML)),
        ("/md/partial206", 206, Some(HTML)),
        ("/md-small/small", 200, Some(MARKDOWN)),
        ("/md-small/large", 200, Some(HTML)),
    ];
    for (index, (path, status, content_type)) in cases.into_iter().enumerate() {
        let case = format!("case{}", index + 1);
        let url = format!("{base_url}{path}");
        let Some(response) = common::try_get_with_headers(&url, &headers, &mut results, &case)
        else {
            continue;
        };
        common::save_response(&raw, &case, &response)?;
        results.push(assertions::assert_status(
            &format!("{case}_status"),
            response.status,
            status,
        ));
        if let Some(pattern) = content_type {
            results.push(assertions::assert_header_pattern(
                &format!("{case}_content_type"),
                "Content-Type",
                pattern,
                &response.headers,
            ));
        }
        if index == 7
            && let Some(upstream) = &oversize
        {
            let matches = response.body == upstream.body;
            results.push(AssertionResult {
                name: "case8_fail_open_body".into(),
                passed: matches,
                expected: "original upstream body".into(),
                actual: format!("{} bytes", response.body.len()),
                message: (!matches).then(|| "fail-open changed the upstream body".into()),
            });
        }
    }
    Ok(common::finalize_report(SCENARIO, start, results))
}
