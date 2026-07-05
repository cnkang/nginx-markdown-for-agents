# Glossary

This glossary standardizes repository terminology used in code comments,
operator documentation, and harness reports.

| Term | Standard Meaning | Preferred Usage |
|---|---|---|
| Module | The NGINX C filter module. | Use when describing NGINX request, header, body, config, and metrics handling. |
| Rust converter | The Rust HTML-to-Markdown library and FFI layer. | Use for parser, converter, charset, sanitizer, streaming, and ABI behavior. |
| Full-buffer path | Conversion path that buffers the complete response before calling Rust. | Do not call this "legacy" unless discussing historical behavior. |
| Incremental path | Large-response path backed by the Rust incremental API. | Use only for `markdown_large_body_threshold` routing. |
| Streaming path | Chunked conversion path enabled by `MARKDOWN_STREAMING_ENABLED`. | Use for Pre-Commit/Post-Commit, backpressure, and streaming budget behavior. |
| Fail-open | Error policy that serves original HTML after conversion cannot complete. | Pair with `markdown_error_policy pass`. |
| Fail-closed | Error policy that returns an error response instead of original HTML. | Pair with `markdown_error_policy fail_closed`. |
| Pre-Commit | Streaming phase before Markdown bytes are sent downstream. | Use for fallback and fail-open paths that can still replay original HTML. |
| Post-Commit | Streaming phase after Markdown bytes have been sent downstream. | Use for truncated-response risks and post-commit error metrics. |
| Decision chain | The ordered request/response checks that produce a reason code. | Use for eligibility, reason-code, and operator troubleshooting docs. |
| Reason code | Stable uppercase snake_case outcome string. | Use exact emitted values such as `SKIP_RANGE` and `STREAMING_CONVERT`. |
| Metrics endpoint | HTTP endpoint enabled by `markdown_metrics`. | Specify output format: plain text, JSON, or Prometheus. |
| Evidence pack | JSON artifact produced by performance evidence tooling. | Use for release-gate evidence, not ad hoc benchmark output. |

## Naming Style

- C functions, fields, and local variables use NGINX-style snake_case.
- Rust public APIs use Rust naming conventions and Rustdoc comments.
- Reason codes use uppercase snake_case and must match emitted log and metric
  labels exactly.
- JSON metric paths use dot notation in prose, for example
  `streaming.postcommit_error_total`.
- Prometheus examples use the full series name and labels, for example
  `nginx_markdown_streaming_total{result="fallback"}`.

## Document Updates

When introducing a new term that appears in code comments and operator-facing
documentation, add it here in the same change set and update affected docs to
use the same spelling.
