# Documentation Audit Report - 2026-04-25

## Scope

This audit covered maintained C, Rust, shell, Python, and Markdown surfaces in
the repository, with emphasis on AGENTS.md rules for NGINX lifecycle semantics,
FFI synchronization, streaming fallback/backpressure, metrics accuracy, shell
portability, and English-only canonical documentation outside `README_zh-CN.md`.

Audited code inventory:

| File Type | Approximate Count | Focus |
|---|---:|---|
| C and C headers | 82 | NGINX return codes, header/body ordering, fail-open, metrics, FFI headers |
| Rust | 80 | Rustdoc, streaming algorithms, FFI ABI fields, fuzz invariants |
| Shell | 30 | Usage contracts, stdout/stderr behavior, exit codes, bash portability |
| Python | 80 | CLI/test docstrings, report behavior, defaults, exit codes |
| Markdown | 103 maintained files | Metrics/config/reason-code consistency and terminology |

## Findings and Fixes

| Area | Issue | Fix Applied | Effect |
|---|---|---|---|
| FFI ABI | `MarkdownOptions` and `MarkdownResult` fields lacked ownership, nullability, and unit documentation. | Added Rustdoc field contracts in `src/ffi/abi.rs` and regenerated both tracked C headers with cbindgen 0.29.2. | C and Rust ABI docs now describe pointer lifetimes, result ownership, and `markdown_result_free()` requirements. |
| Streaming charset | Docs described a fixed 1024-byte sniff buffer even though runtime uses the configured budget. | Updated `streaming/charset.rs` and `streaming/budget.rs` to say "configured sniff limit, default 1024 bytes." | Prevents budget documentation from drifting from runtime enforcement. |
| Streaming fail-open | A comment was attached to the wrong helper and late streaming helpers had weak replay/backpressure notes. | Corrected `ngx_http_markdown_streaming_failopen_passthrough()` docs and added contracts for NULL-input resume, chain counting, fail-open tracking, and chain processing. | Documents why buffer positions are recorded and why `NGX_AGAIN` must be propagated. |
| Full-buffer fail-open | Payload helper docs did not explain metric reclassification, header forwarding idempotence, or buffer advancement. | Added comments to `ngx_http_markdown_reclassify_fail_open_path()`, `ngx_http_markdown_append_buffered_chunk()`, and `ngx_http_markdown_forward_headers()`. | Clarifies data-loss and double-header safeguards in AGENTS-sensitive paths. |
| Base URL construction | Docs implied unconditional trust of `X-Forwarded-*`. | Updated conversion helper docs to describe `markdown_trust_forwarded_headers`. | Aligns security documentation with link-poisoning protection. |
| Rust fuzz targets | Fuzz targets lacked module-level invariant descriptions. | Added `//!` module docs and helper Rustdoc for streaming fuzz utilities. | Documents cross-boundary, parity, and no-panic properties covered by fuzzing. |
| Streaming test support | Shared public test helpers lacked field/function docs. | Added Rustdoc for fixture metadata, streaming run results, chunk strategies, and parity conversion helper. | Makes test-side boundary coverage easier to maintain. |
| Fast path | Qualification docs omitted algorithmic bounds. | Added complexity notes for node visit and recursion bounds. | Captures performance expectations for the pre-scan algorithm. |
| Metrics docs | `markdown_metrics_format auto` and Prometheus behavior were documented incorrectly. | Updated `CONFIGURATION.md` and `OPERATIONS.md`; kept Prometheus details aligned with `prometheus-metrics.md`. | Operators now see that Prometheus output requires `markdown_metrics_format prometheus` plus a Prometheus-aware Accept header. |
| Skip metrics | Operations docs claimed skip reasons were absent from metrics. | Replaced log-only guidance with JSON `skips.*` and Prometheus `nginx_markdown_skips_total{reason="..."}` references. | Makes operator troubleshooting reproducible from actual endpoint output. |
| 206 handling | Docs described `SKIP_STATUS` as "not 200 or 206" while code maps 206 to `SKIP_RANGE`. | Updated `OPERATIONS.md` and `prometheus-metrics.md`. | Reason-code docs now match production classification. |
| Shadow metrics | Docs said `shadow_total` counted only successful comparable runs. | Updated `CONFIGURATION.md` to describe shadow comparison attempts. | Aligns docs with unconditional increment at shadow entry. |
| Shell CLI docs | Corpus validation and native-build helpers lacked clear contracts. | Added usage, prerequisites, stdout/stderr, and exit-code notes to `validate_corpus.sh`; added shell-library contracts to `nginx_markdown_native_build.sh`. | Improves script automation safety and portability expectations. |
| Python CLI docs | Perf tools omitted output and exit-code behavior. | Expanded `compare_reports.py`, `run_corpus_benchmark.py`, and `evidence_pack_generator.py` docstrings. | CI callers can distinguish JSON artifacts, stdout summaries, stderr diagnostics, and failure exits. |
| Terminology | No canonical terminology table existed. | Added `docs/glossary.md` and linked it from `docs/README.md`. | Provides a single reference for Module/Rust converter/path/reason-code wording. |

## Consistency Checks

- Han-character scan found no maintained Markdown violations outside
  `README_zh-CN.md`.
- Generated FFI headers were rebuilt from Rust comments rather than edited by
  hand.
- Documentation changes use exact metric paths and Prometheus series names where
  operator commands depend on them.
- `python3 tools/docs/check_docs.py` passed after the documentation updates.
- `python3 -m py_compile` passed for the touched performance Python tools.
- `bash -n` passed for the touched shell scripts.
- Regenerating `markdown_converter.h` with cbindgen 0.29.2 matched both tracked
  header copies.
- `rustfmt --edition 2024 --check` passed for the Rust files touched by this
  documentation pass.

## Remaining Risk

This pass focused on high-risk and high-signal documentation drift instead of
mechanically adding comments to every test helper. Some Python unit tests still
lack per-test docstrings, but the highest-impact CLI and harness contracts were
updated. A future narrow pass should add compact `Purpose / Scenario /
Assertions` docstrings to dense parser-test modules under `tools/docs/tests/`.

Repository-wide `cargo fmt --check` is currently blocked by a pre-existing diff
in `components/rust-converter/tests/converter_regressions.rs`; that file was
already modified outside this documentation pass and was not reformatted here to
avoid rewriting unrelated worktree changes.

## Implementation Effect

The changes are documentation-only or generated-header synchronization from
documentation comments. No production control flow, request handling, metric
increment logic, or test assertions were intentionally changed.
