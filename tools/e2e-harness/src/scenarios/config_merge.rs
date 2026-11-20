//! HTTP, server, and location configuration precedence.

use crate::fixtures::{EncodingFault, EncodingLayer, FixtureSpec, RouteBehavior, RouteSpec};
use crate::scenarios::{ScenarioContext, ScenarioReport, common};
use crate::{assertions, http};
use anyhow::Result;
use std::time::{Duration, Instant};

pub const NAME: &str = "config-merge";
const HTML: &str = "(?i)^text/html";
const MARKDOWN: &str = "(?i)^text/markdown";
#[path = "config_merge_nginx.rs"]
mod nginx;

const ACCEPT: (&str, &str) = ("Accept", "text/markdown");

pub fn fixture_spec(port: u16) -> FixtureSpec {
    let valid = concat!(
        "<!doctype html>\n",
        "<html><head><meta charset=\"UTF-8\"><title>Config Merge Test</title></head>\n",
        "<body><h1>Config Merge Test Page</h1><p>Testing configuration merging.</p></body></html>\n",
    );
    let malformed = concat!(
        "<html><head><title>Bad\n",
        "<body><h1>Unclosed heading\n",
        "<div><div><div>nested without closing\n",
        "<p>paragraph without closing\n",
        "<a href=\"link without quote>text</a>\n",
        "</html>\n",
    );
    let large = format!(
        concat!(
            "<!doctype html>\n",
            "<html><head><meta charset=\"UTF-8\"><title>Large</title></head>\n",
            "<body>\n",
            "{}</body></html>\n",
        ),
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
    nginx::CONFIG
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
