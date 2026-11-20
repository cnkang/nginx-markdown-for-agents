//! Stream cache validators, failure policy, and authenticated cache headers.

use crate::fixtures::{FixtureSpec, RouteBehavior, RouteSpec};
use crate::http::HttpResponse;
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport, common};
use crate::{assertions, http};
use anyhow::{Context, Result};
use reqwest::Method;
use std::io::{Read, Seek, SeekFrom};
use std::path::PathBuf;
use std::time::{Duration, Instant};

pub const NAME: &str = "streaming-failure-cache";
const ACCEPT: (&str, &str) = ("Accept", "text/markdown");
const AUTH: (&str, &str) = ("Cookie", "session_id=abc123");
const HTML: &str = "(?i)^text/html";
const MARKDOWN: &str = "(?i)^text/markdown";
const END: &str = "OVERSIZE_STREAM_END_TOKEN";
const HEADING: &str = "# Simple Test Page";
const UPSTREAM_ETAG: &str = "\"upstream-failopen-etag\"";

pub fn fixture_spec(port: u16) -> FixtureSpec {
    let simple = "<!doctype html><html><head><meta charset=\"UTF-8\">\
        <title>Simple</title></head><body><h1>Simple Test Page</h1>\
        <p>This is a simple paragraph for streaming conversion.</p>\
        <p>Visit <a href=\"https://example.com\">Example</a>.</p></body></html>";
    let mut oversize = String::from(
        "<!doctype html><html><head><meta charset=\"UTF-8\">\
        <title>Oversize</title></head><body><h1>Oversize Page</h1><pre>\n",
    );
    let suffix = format!("\n</pre><p>{END}</p></body></html>\n");
    let fill = format!("{}\n", "x".repeat(4096));
    let remaining = 12 * 1024 * 1024 - oversize.len() - suffix.len();
    oversize.push_str(&fill.repeat(remaining / fill.len()));
    oversize.push_str(&fill[..remaining % fill.len()]);
    oversize.push_str(&suffix);
    let partial = format!(
        "<!doctype html><html><head><meta charset=\"UTF-8\">\
         <title>Partial</title></head><body><h1>Partial Page</h1>\
         <p>This content will be followed by an abrupt connection close.</p>{}",
        format!("<p>{}</p>\n", "A".repeat(2048)).repeat(20),
    );

    let routes = vec![
        html("/simple", simple.into(), &[]),
        html(
            "/simple-with-etag",
            simple.into(),
            &[("ETag", "\"upstream-etag-v1\"")],
        ),
        html("/oversize", oversize.clone(), &[]),
        html(
            "/failopen-headers",
            oversize,
            &[
                ("Cache-Control", "public, max-age=600"),
                ("ETag", UPSTREAM_ETAG),
                ("Set-Cookie", "session_id=abc123; Path=/"),
            ],
        ),
        html(
            "/no-cache-quoted",
            simple.into(),
            &[("Cache-Control", "no-cache=\"public\"")],
        ),
        RouteSpec {
            path: "/partial-abort".into(),
            behavior: RouteBehavior::AbortedHtml {
                first_chunk: partial,
            },
        },
    ];
    FixtureSpec {
        listen_port: Some(port),
        routes,
    }
}

fn html(path: &str, body: String, headers: &[(&str, &str)]) -> RouteSpec {
    RouteSpec {
        path: path.into(),
        behavior: RouteBehavior::Html {
            body,
            headers: headers
                .iter()
                .map(|(name, value)| ((*name).into(), (*value).into()))
                .collect(),
        },
    }
}

pub fn nginx_conf(load: &str, port: u16, upstream_port: u16) -> String {
    r#"@LOAD@worker_processes 1;
error_log nginx-error.log debug;
pid nginx.pid;

events { worker_connections 512; }

http {
    access_log nginx-access.log;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout 5;

    server {
        listen 127.0.0.1:@PORT@;
        server_name localhost;

        # 10.1: Streaming success + cache_validation ims_only
        location /t01/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=10m parser_budget=10m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.2: Pre-commit failure + pass
        location /t02/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=20m parser_budget=20m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.3: Pre-commit failure + fail_closed
        location /t03/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=20m parser_budget=20m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy fail_closed;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.4: Post-commit failure (upstream aborts mid-stream)
        location /t04/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=10m parser_budget=10m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.5: full validation selects the full-buffer path in auto mode
        location /t05/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation full;
            markdown_streaming auto;
            markdown_limits conversion_memory=10m parser_budget=10m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.6: cache_validation ims_only + streaming on
        location /t06/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=10m parser_budget=10m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.7: Streaming response headers (same as t01)
        location /t07/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=10m parser_budget=10m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.8: markdown_streaming off + cache_validation full
        location /t08/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation full;
            markdown_streaming off;
            markdown_limits conversion_memory=10m parser_budget=10m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.9a: error_policy pass (streaming pre-commit failure → fail-open)
        location /t09a/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=20m parser_budget=20m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.9b: error_policy fail_closed (streaming pre-commit failure → reject)
        location /t09b/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=20m parser_budget=20m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy fail_closed;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.10: HEAD request should bypass streaming body processing
        # Uses ims_only so streaming is eligible for GET,
        # but HEAD must still skip the streaming body path entirely.
        location /t10/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=10m parser_budget=10m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.11: 304 should bypass streaming path selection
        # Full validation with auto mode generates an ETag for revalidation and
        # keeps this request out of the streaming path.
        location /t11/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation full;
            markdown_streaming auto;
            markdown_limits conversion_memory=10m parser_budget=10m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.12: Authenticated fail-open still applies Cache-Control policy
        # Uses auth_policy allow so the request enters conversion/streaming,
        # then streaming_buffer=64k triggers pre-commit failure for the
        # 12MB fixture -> fail-open.
        # With auth_policy allow + auth_cookies "session*" + Cookie header,
        # the final header path must rewrite public caching to private even
        # when streaming has failed before conversion can commit.
        location /t12/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_streaming force;
            markdown_limits conversion_memory=20m parser_budget=20m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_auth_policy allow;
            markdown_auth_cookies "session*";
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:@UPSTREAM@/;
        }

        # 10.13: no-cache="public" quoted directive not treated as bare public
        location /t13/ {
            markdown_filter on;
            markdown_accept strict;
            markdown_cache_validation ims_only;
            markdown_limits conversion_memory=10m parser_budget=10m streaming_buffer=64k conversion_timeout=120s;
            markdown_error_policy pass;
            markdown_auth_policy allow;
            markdown_auth_cookies "session*";
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
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

struct Run {
    base: String,
    raw: PathBuf,
    log: PathBuf,
    results: Vec<AssertionResult>,
}

impl Run {
    fn request(
        &self,
        name: &str,
        path: &str,
        method: Method,
        extra: &[(&str, &str)],
    ) -> Result<HttpResponse> {
        let mut headers = vec![ACCEPT];
        headers.extend_from_slice(extra);
        let timeout = if path.ends_with("/oversize") || path.ends_with("/failopen-headers") {
            Duration::from_secs(60)
        } else {
            Duration::from_secs(30)
        };
        let response = http::to_http_response(http::send(
            method,
            &format!("{}{path}", self.base),
            &headers,
            timeout,
        )?)
        .with_context(|| format!("{name}: {path}"))?;
        common::save_response(&self.raw, name, &response)?;
        Ok(response)
    }

    fn get(&self, name: &str, path: &str) -> Result<HttpResponse> {
        self.request(name, path, Method::GET, &[])
    }

    fn check(&mut self, name: &str, passed: bool, expected: &str) {
        self.results
            .push(common::assert_condition(name, passed, expected));
    }

    fn status(&mut self, name: &str, response: &HttpResponse, expected: u16) {
        self.results.push(assertions::assert_status(
            &format!("{name}_status"),
            response.status,
            expected,
        ));
    }

    fn header(&mut self, name: &str, response: &HttpResponse, header: &str, pattern: &str) {
        self.results.push(assertions::assert_header_pattern(
            &format!("{name}_{header}"),
            header,
            pattern,
            &response.headers,
        ));
    }

    fn absent(&mut self, name: &str, response: &HttpResponse, header: &str) {
        self.check(
            &format!("{name}_no_{header}"),
            !response.headers.contains_key(header),
            &format!("{header} absent"),
        );
    }

    fn body(&mut self, name: &str, response: &HttpResponse, token: &str) {
        self.results.push(assertions::assert_body_contains(
            &format!("{name}_body"),
            token,
            &response.body,
        ));
    }
}

pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    let start = Instant::now();
    let mut run = Run {
        base: format!("http://127.0.0.1:{}", ctx.port),
        raw: ctx.artifact_dir().join("raw"),
        log: ctx.run_dir.join("runtime/nginx-error.log"),
        results: Vec::new(),
    };
    std::fs::create_dir(&run.raw)?;
    validators(&mut run)?;
    failures(&mut run)?;
    cache_policy(&mut run)?;
    Ok(common::finalize_report(NAME, start, run.results))
}

fn validators(run: &mut Run) -> Result<()> {
    for (name, path) in [
        ("t01", "/t01/simple"),
        ("t01b", "/t01/simple-with-etag"),
        ("t05", "/t05/simple"),
        ("t06", "/t06/simple"),
        ("t07", "/t07/simple"),
        ("t08", "/t08/simple"),
    ] {
        let response = run.get(name, path)?;
        run.status(name, &response, 200);
        run.header(name, &response, "content-type", MARKDOWN);
        run.body(name, &response, HEADING);
        match name {
            "t01" | "t01b" => run.absent(name, &response, "etag"),
            "t05" | "t08" => {
                run.header(name, &response, "etag", ".+");
                run.header(name, &response, "content-length", r"^\d+$");
            }
            "t06" | "t07" => {
                run.absent(name, &response, "content-length");
                run.header(name, &response, "transfer-encoding", "(?i)chunked");
                if name == "t06" {
                    run.absent(name, &response, "etag");
                } else {
                    run.header(name, &response, "vary", "(?i)accept");
                }
            }
            _ => unreachable!(),
        }
    }
    let head = run.request("t10", "/t10/simple", Method::HEAD, &[])?;
    run.status("t10", &head, 200);
    run.header("t10", &head, "content-type", MARKDOWN);
    run.check("t10_body", head.body.is_empty(), "empty HEAD body");

    let path = "/t11/simple-with-etag";
    let first = run.get("t11_first", path)?;
    let etag = common::header_value(&first.headers, "etag");
    run.check(
        "t11_etag",
        !etag.is_empty(),
        "initial response supplies ETag",
    );
    if !etag.is_empty() {
        let response = run.request("t11_304", path, Method::GET, &[("If-None-Match", &etag)])?;
        run.status("t11_304", &response, 304);
        run.check("t11_304_body", response.body.is_empty(), "empty 304 body");
    }
    Ok(())
}

fn failures(run: &mut Run) -> Result<()> {
    let pass = run.get("t02", "/t02/oversize")?;
    run.status("t02", &pass, 200);
    run.header("t02", &pass, "content-type", HTML);
    run.body("t02", &pass, END);

    let pass = run.get("t09a", "/t09a/oversize")?;
    run.check(
        "t09a_success",
        (200..300).contains(&pass.status),
        "successful fail-open response",
    );
    run.body("t09a", &pass, END);

    for (name, path, postcommit) in [
        ("t03", "/t03/oversize", false),
        ("t04", "/t04/partial-abort", true),
        ("t09b", "/t09b/oversize", false),
    ] {
        let response = capture_failure(run, name, path)?;
        if let Some(response) = response {
            if postcommit {
                run.status(name, &response, 200);
            } else {
                run.check(
                    name,
                    response.status >= 400,
                    "rejected response or transport close",
                );
                if name == "t03" {
                    run.check(
                        "t03_body",
                        !response.body.starts_with('#'),
                        "no converted Markdown heading",
                    );
                }
            }
        } else {
            run.check(name, true, "transport closed before headers");
        }
    }
    Ok(())
}

fn capture_failure(run: &Run, name: &str, path: &str) -> Result<Option<HttpResponse>> {
    let url = format!("{}{path}", run.base);
    let mut response = match http::send(Method::GET, &url, &[ACCEPT], Duration::from_secs(60)) {
        Ok(response) => response,
        Err(error) if transport_closed(&error) => {
            std::fs::write(run.raw.join(format!("{name}.err")), format!("{error:?}"))?;
            return Ok(None);
        }
        Err(error) => return Err(error).with_context(|| format!("{name}: {path}")),
    };
    let mut body = Vec::new();
    if let Err(error) = response.read_to_end(&mut body) {
        std::fs::write(run.raw.join(format!("{name}.err")), format!("{error:?}"))?;
    }
    let captured = HttpResponse {
        status: response.status().as_u16(),
        headers: response.headers().clone(),
        body: String::from_utf8_lossy(&body).into_owned(),
    };
    common::save_response(&run.raw, name, &captured)?;
    Ok(Some(captured))
}

fn transport_closed(error: &reqwest::Error) -> bool {
    const CLOSED: [&str; 3] = [
        "connection closed before message completed",
        "connection reset",
        "unexpected eof",
    ];
    if error.is_connect() || error.is_timeout() {
        return false;
    }
    let mut source = std::error::Error::source(error);
    while let Some(cause) = source {
        let display = cause.to_string().to_ascii_lowercase();
        if CLOSED.iter().any(|text| display.contains(text)) {
            return true;
        }
        source = cause.source();
    }
    false
}

fn cache_policy(run: &mut Run) -> Result<()> {
    let mut log = std::fs::File::open(&run.log)?;
    log.seek(SeekFrom::End(0))?;
    let path = "/t12/failopen-headers";
    let auth = run.request("t12", path, Method::GET, &[AUTH])?;
    let mut new_log = String::new();
    log.read_to_string(&mut new_log)?;
    let anonymous = run.get("t12b", path)?;
    for (name, response) in [("t12", &auth), ("t12b", &anonymous)] {
        run.status(name, response, 200);
        run.header(name, response, "content-type", HTML);
        run.body(name, response, END);
    }
    run.check(
        "t12_cache",
        has_token(&auth, "private") && !has_token(&auth, "public"),
        "private cache without bare public",
    );
    run.check(
        "t12_etag",
        common::header_value(&auth.headers, "etag") == UPSTREAM_ETAG,
        "original ETag",
    );
    run.header("t12", &auth, "set-cookie", ".+");
    run.check(
        "t12_log",
        new_log.contains("event=streaming_precommit_failopen"),
        "precommit fail-open decision for this request",
    );
    run.check(
        "t12b_cache",
        has_token(&anonymous, "public")
            && has_token(&anonymous, "max-age=600")
            && !has_token(&anonymous, "private"),
        "original public cache policy",
    );

    let quoted = run.request("t13", "/t13/no-cache-quoted", Method::GET, &[AUTH])?;
    run.status("t13", &quoted, 200);
    run.check(
        "t13_cache",
        has_token(&quoted, "no-cache=\"public\"") && !has_token(&quoted, "public"),
        "quoted public field with no bare public directive",
    );
    Ok(())
}

fn has_token(response: &HttpResponse, token: &str) -> bool {
    response
        .headers
        .get_all("cache-control")
        .iter()
        .filter_map(|value| value.to_str().ok())
        .flat_map(|value| value.split(','))
        .any(|directive| directive.trim().eq_ignore_ascii_case(token))
}
