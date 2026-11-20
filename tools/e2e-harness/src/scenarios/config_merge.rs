//! HTTP, server, and location configuration precedence.

use crate::fixtures::{EncodingFault, EncodingLayer, FixtureSpec, RouteBehavior, RouteSpec};
use crate::scenarios::{ScenarioContext, ScenarioReport, common};
use crate::{assertions, http};
use anyhow::Result;
use std::time::{Duration, Instant};

pub const NAME: &str = "config-merge";
const HTML: &str = "(?i)^text/html";
const MARKDOWN: &str = "(?i)^text/markdown";
const ACCEPT: (&str, &str) = ("Accept", "text/markdown");

pub fn fixture_spec(port: u16) -> FixtureSpec {
    let valid = r#"<!doctype html>
<html><head><meta charset="UTF-8"><title>Config Merge Test</title></head>
<body><h1>Config Merge Test Page</h1><p>Testing configuration merging.</p></body></html>
"#;
    let malformed = r#"<html><head><title>Bad
<body><h1>Unclosed heading
<div><div><div>nested without closing
<p>paragraph without closing
<a href="link without quote>text</a>
</html>
"#;
    let large = format!(
        r#"<!doctype html>
<html><head><meta charset="UTF-8"><title>Large</title></head>
<body>
{}</body></html>
"#,
        "<p>x</p>\n".repeat(100_000)
    );
    let mut routes = [
        ("/html", valid.to_owned()),
        ("/large", large),
        ("/malformed", malformed.to_owned()),
    ]
    .into_iter()
    .map(|(path, body)| RouteSpec {
        path: path.into(),
        behavior: RouteBehavior::Fixed {
            status: 200,
            body,
            content_type: "text/html; charset=UTF-8".into(),
            etag: None,
        },
    })
    .collect::<Vec<_>>();
    routes.push(RouteSpec {
        path: "/invalid-encoding".into(),
        behavior: RouteBehavior::EncodingChain {
            body: valid.into(),
            chain: vec![EncodingLayer::Gzip],
            fault: EncodingFault::MalformedGrammar,
        },
    });
    FixtureSpec {
        listen_port: Some(port),
        routes,
    }
}

pub fn nginx_conf(load: &str, port: u16, upstream_port: u16) -> String {
    r#"@LOAD@worker_processes 1;
error_log "nginx-error.log" info;
pid "nginx.pid";

events { worker_connections 512; }

http {
    access_log nginx-access.log;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout 5;

    # http-level defaults
    markdown_error_policy pass;

    server {
        listen 127.0.0.1:@PORT@;
        server_name localhost;

        # server-level settings
        markdown_accept force;
        markdown_cache_validation off;

        # Case 1: location overrides error_policy to fail_closed
        location /md/reject/ {
            markdown_filter on;
            markdown_error_policy fail_closed;
            markdown_limits conversion_memory=64k parser_budget=64k streaming_buffer=64k conversion_timeout=120s;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # Case 1b: location inherits error_policy pass from http level
        location /md/pass/ {
            markdown_filter on;
            markdown_limits conversion_memory=10m parser_budget=10m conversion_timeout=120s;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # Case 3: markdown_filter off disables conversion
        location /nomd/ {
            markdown_filter off;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # Case 4: markdown_accept strict at location
        location /no-wildcard/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_limits conversion_memory=10m parser_budget=10m conversion_timeout=120s;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # Case 5: cache_validation full at location overrides server off
        location /etag-on/ {
            markdown_filter on;
            markdown_streaming off;
            markdown_cache_validation full;
            markdown_limits conversion_memory=10m parser_budget=10m conversion_timeout=120s;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # Case 6: cache_validation ims_only override at location
        location /cond-ims/ {
            markdown_filter on;
            markdown_cache_validation ims_only;
            markdown_limits conversion_memory=10m parser_budget=10m conversion_timeout=120s;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # Case 7: flavor override at location
        location /flavor/ {
            markdown_filter on;
            markdown_flavor gfm;
            markdown_limits conversion_memory=10m parser_budget=10m conversion_timeout=120s;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }
    }
}
"#
        .replace("@LOAD@", load)
        .replace("@PORT@", &port.to_string())
        .replace("@UPSTREAM@", &upstream_port.to_string())
}

struct Case<'a> {
    name: &'a str,
    path: &'a str,
    headers: &'a [(&'a str, &'a str)],
    status: Option<u16>,
    content_type: Option<&'a str>,
    etag: Option<bool>,
}

pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    let start = Instant::now();
    let raw = ctx.artifact_dir().join("raw");
    std::fs::create_dir(&raw)?;
    let base = format!("http://127.0.0.1:{}", ctx.port);
    let cases = [
        Case {
            name: "case1",
            path: "/md/reject/html",
            headers: &[ACCEPT],
            status: Some(200),
            content_type: Some(MARKDOWN),
            etag: None,
        },
        Case {
            name: "case1b",
            path: "/md/pass/html",
            headers: &[ACCEPT],
            status: Some(200),
            content_type: Some(MARKDOWN),
            etag: None,
        },
        Case {
            name: "case1c",
            path: "/md/reject/large",
            headers: &[ACCEPT],
            status: Some(200),
            content_type: Some(HTML),
            etag: None,
        },
        Case {
            name: "case1d",
            path: "/md/pass/malformed",
            headers: &[ACCEPT],
            status: Some(200),
            content_type: None,
            etag: None,
        },
        Case {
            name: "case3",
            path: "/nomd/html",
            headers: &[ACCEPT],
            status: Some(200),
            content_type: Some(HTML),
            etag: None,
        },
        Case {
            name: "case4",
            path: "/no-wildcard/html",
            headers: &[("Accept", "*/*")],
            status: Some(200),
            content_type: Some(HTML),
            etag: None,
        },
        Case {
            name: "case5",
            path: "/etag-on/html",
            headers: &[ACCEPT],
            status: Some(200),
            content_type: None,
            etag: Some(true),
        },
        Case {
            name: "case5b",
            path: "/md/pass/html",
            headers: &[ACCEPT],
            status: None,
            content_type: None,
            etag: Some(false),
        },
        Case {
            name: "case6",
            path: "/cond-ims/html",
            headers: &[
                ACCEPT,
                ("If-Modified-Since", "Mon, 01 Jan 2030 00:00:00 GMT"),
            ],
            status: Some(200),
            content_type: Some(MARKDOWN),
            etag: None,
        },
        Case {
            name: "case7",
            path: "/flavor/html",
            headers: &[ACCEPT],
            status: Some(200),
            content_type: Some(MARKDOWN),
            etag: None,
        },
        Case {
            name: "error_policy_override",
            path: "/md/reject/invalid-encoding",
            headers: &[ACCEPT],
            status: Some(502),
            content_type: None,
            etag: None,
        },
        Case {
            name: "error_policy_inherited",
            path: "/md/pass/invalid-encoding",
            headers: &[ACCEPT],
            status: Some(200),
            content_type: Some(HTML),
            etag: None,
        },
    ];
    let mut results = Vec::new();
    for case in cases {
        let response = match http::get_with_timeout(
            &format!("{base}{}", case.path),
            case.headers,
            Duration::from_secs(30),
        ) {
            Ok(response) => response,
            Err(error) => {
                results.push(common::failed_request_assertion(
                    case.name,
                    &error.to_string(),
                ));
                continue;
            }
        };
        common::save_response(&raw, case.name, &response)?;
        if let Some(status) = case.status {
            results.push(assertions::assert_status(
                &format!("{}_status", case.name),
                response.status,
                status,
            ));
        }
        if let Some(pattern) = case.content_type {
            results.push(assertions::assert_header_pattern(
                &format!("{}_content_type", case.name),
                "Content-Type",
                pattern,
                &response.headers,
            ));
        }
        if let Some(present) = case.etag {
            results.push(common::assert_condition(
                &format!("{}_etag", case.name),
                response.headers.contains_key("etag") == present,
                if present {
                    "ETag present"
                } else {
                    "ETag absent"
                },
            ));
        }
        if case.name == "case1d" {
            results.push(common::assert_condition(
                "case1d_body",
                !response.body.is_empty(),
                "nonempty response",
            ));
        }
    }
    Ok(common::finalize_report(NAME, start, results))
}
