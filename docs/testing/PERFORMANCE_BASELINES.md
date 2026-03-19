# Performance Baselines (Local FFI + Conditional Microbench + NGINX E2E)

**Measurement Date:** 2026-02-26  
**Scope:** Local release-build microbenchmarks for the Rust converter FFI path, the C conditional-request handler (`If-None-Match`) using the standalone test harness with real Rust FFI, and local real-NGINX HTTP E2E baselines.

## Summary

This file replaces the previous placeholder template with real local baseline data.

CI now also records non-blocking performance artifacts from the same `perf_baseline` example. The workflow stores the full benchmark output plus `/usr/bin/time -v` captures for the medium, medium-front-matter, and large single-sample runs. Those artifacts are for regression comparison and trend review; they are not merge-blocking thresholds yet.

Key findings from this run:

- FFI end-to-end conversion is very fast for small/medium HTML and scales roughly linearly to ~1MB input.
- On the medium (~10KB) sample, `parse_html_with_charset` dominates runtime (~76.8%), while Markdown conversion is ~20.9%.
- ETag generation and token estimation are negligible compared to parse/convert time.
- `markdown_conditional_requests if_modified_since_only` is dramatically cheaper than `full_support` in the conditional-request microbench (as expected, because `full_support` performs conversion to generate/compare ETag).

## Environment

### Hardware / OS

- OS: macOS 26.3 (`BuildVersion 25D125`)
- CPU: Apple M4 Pro
- RAM: 24 GiB (`25769803776` bytes)

### Toolchain

- `rustc 1.93.1 (01f6ddf75 2026-02-11)`
- `cargo 1.93.1 (083ac5135 2025-12-15)`

### NGINX Runtime

- Not part of this microbenchmark run (no full NGINX HTTP benchmark executed in this snapshot)

## Methodology

### FFI conversion benchmark

Command:

```bash
cd components/rust-converter
cargo run --release --example perf_baseline
```

What it measures:

- `markdown_convert()` end-to-end latency (parse + convert + ETag + token estimate + FFI marshaling/allocation)
- p50/p95/p99 latency
- throughput (`req/s`, input `MB/s`)
- medium-sample internal stage timing breakdown (`parse`, `convert`, `etag`, `token`)

Sample definitions in the benchmark example:

- `small`: `tests/corpus/simple/basic.html` (379 bytes)
- `medium (~10KB)`: generated in-memory by repeating `tests/corpus/complex/blog-post.html`
- `medium-front-matter (~10KB + metadata/front matter)`: generated from the same complex seed with front matter enabled and a base URL supplied for URL resolution
- `large (~1MB)`: generated in-memory by repeating the same complex seed

### Conditional request microbench (real Rust FFI)

Command:

```bash
cd components/nginx-module/tests
make -C components/nginx-module/tests unit-conditional_requests
./test_conditional_requests_real --bench-conditional
```

What it measures:

- `if_modified_since_only` path (declines early; no conversion)
- `full_support` path with matching `If-None-Match` (conversion + ETag compare + 304 path)
- `full_support` path with non-matching `If-None-Match` (conversion + ETag compare + decline)

## FFI Baselines (Release)

Source output from `perf_baseline` example:

| Sample | HTML bytes | Markdown bytes (avg) | Tokens (avg) | Avg ms | P50 ms | P95 ms | P99 ms | Req/s | Input MB/s |
|--------|------------|----------------------|--------------|--------|--------|--------|--------|-------|------------|
| small (~0.4KB) | 379 | 149 | 38 | 0.012 | 0.012 | 0.013 | 0.017 | 80986.9 | 29.27 |
| medium (~10KB) | 6624 | 2639 | 659 | 0.162 | 0.160 | 0.174 | 0.240 | 6181.7 | 39.05 |
| large (~1MB) | 1044099 | 421286 | 105202 | 26.267 | 26.276 | 26.680 | 26.815 | 38.1 | 37.91 |

### Interpretation

- Throughput stays in the same ballpark (~29-39 MB/s input) across sample sizes in this local run.
- The ~1MB sample shows stable p95/p99 latency, which suggests no large tail spikes in the measured loop.

## Stage Breakdown (Medium Sample, ~10KB)

Source output from `perf_baseline` example:

| Stage | Avg ms | Share (direct stage timing) |
|-------|--------|-----------------------------|
| `parse_html_with_charset` | 0.108 | 76.8% |
| `convert_with_context` | 0.029 | 20.9% |
| `etag.generate` | 0.003 | 2.3% |
| `token_estimate` | 0.000 | 0.1% |
| direct total | 0.141 | 100.0% |
| FFI end-to-end avg | 0.162 | - |
| inferred FFI/runtime overhead | 0.021 | - |

### Bottleneck answer (for this run)

Primary bottleneck is **HTML parsing (`parse_html_with_charset`)**, not ETag/token logic and not the FFI boundary itself.

## Conditional Request Microbench (Real Rust FFI)

Source output from `./test_conditional_requests_real --bench-conditional`:

- `if_modified_since_only_avg_ms = 0.000147` (5000 iterations)
- `full_support_match_avg_ms = 0.007670` (300 iterations)
- `full_support_no_match_avg_ms = 0.003023` (300 iterations)
- `ratio_match_vs_ims_only = 52.0x`
- `ratio_no_match_vs_ims_only = 20.5x`

### Interpretation

- `full_support` is significantly more expensive than `if_modified_since_only`, which is expected because it performs conversion to generate/compare ETag.
- The matching path is slower than the non-matching path in this microbench due to the extra 304 response setup path in the harness.

## Memory Usage (Process-Level, `/usr/bin/time -l`)

Commands used (one per sample):

```bash
cd components/rust-converter
/usr/bin/time -l cargo run --release --example perf_baseline -- --single <small|medium|medium-front-matter|large>
```

Measured `maximum resident set size` (converted to MiB):

| Scenario | Max RSS (bytes) | Max RSS (MiB) | Notes |
|----------|------------------|---------------|-------|
| FFI single-sample benchmark (small) | 7163904 | 6.83 | Includes process startup and benchmark loop |
| FFI single-sample benchmark (medium) | 7979008 | 7.61 | Includes process startup and benchmark loop |
| FFI single-sample benchmark (large ~1MB) | 50458624 | 48.12 | Includes process startup and benchmark loop |

Additional macOS `time -l` signal (`peak memory footprint`) from the same runs:

- small: 4.98 MiB (`5219192` bytes)
- medium: 5.70 MiB (`5972928` bytes)
- large: 46.49 MiB (`48747792` bytes)

## Baseline Commands (Reproducible)

```bash
# FFI latency/throughput + stage breakdown
cd components/rust-converter
cargo run --release --example perf_baseline

# Conditional mode microbench (real Rust FFI via C harness)
cd components/nginx-module/tests
make -C components/nginx-module/tests unit-conditional_requests
./test_conditional_requests_real --bench-conditional

# Header sync guard (generated header vs mirror copy)
cd ..
make check-headers
```

## CI Artifact Capture

The `Perf Baseline Artifact` job in `.github/workflows/ci.yml` records:

- full `cargo run --release --example perf_baseline` output
- `--single medium` output plus `/usr/bin/time -v`
- `--single medium-front-matter` output plus `/usr/bin/time -v`
- `--single large` output plus `/usr/bin/time -v`

This keeps performance references attached to PRs and workflow runs without turning the current baseline into a hard pass/fail gate.

The historical latency tables below were captured before the extra front-matter sample was added to the harness. Treat the new sample as a regression-comparison artifact until a fresh full baseline snapshot is recorded.

## FFI Baselines — Large Tiers (Placeholder)

The following tiers were added to `perf/metrics-schema.json` as part of the large-response optimization work. Baseline data will be populated once the incremental processing path is enabled and profiled end-to-end.

Samples are generated by `tools/corpus/generate_large_samples.sh` in four variants per tier: `plain-html`, `front-matter`, `token-estimation`, `nested-tables-code`.

### large-100k (~100KB)

| Variant | HTML bytes | Markdown bytes (avg) | Tokens (avg) | Avg ms | P50 ms | P95 ms | P99 ms | Req/s | Input MB/s | Peak RSS (bytes) |
|---------|------------|----------------------|--------------|--------|--------|--------|--------|-------|------------|------------------|
| plain-html | — | — | — | — | — | — | — | — | — | — |
| front-matter | — | — | — | — | — | — | — | — | — | — |
| token-estimation | — | — | — | — | — | — | — | — | — | — |
| nested-tables-code | — | — | — | — | — | — | — | — | — | — |

### large-5m (~5MB)

| Variant | HTML bytes | Markdown bytes (avg) | Tokens (avg) | Avg ms | P50 ms | P95 ms | P99 ms | Req/s | Input MB/s | Peak RSS (bytes) |
|---------|------------|----------------------|--------------|--------|--------|--------|--------|-------|------------|------------------|
| plain-html | — | — | — | — | — | — | — | — | — | — |
| front-matter | — | — | — | — | — | — | — | — | — | — |
| token-estimation | — | — | — | — | — | — | — | — | — | — |
| nested-tables-code | — | — | — | — | — | — | — | — | — | — |

## Memory Peak Sampling Methodology

Memory peak values in this document are collected using `tools/perf/memory_observer.sh`. The script supports two platforms with different collection strategies:

### Linux — `os_reported_peak`

On Linux, the script reads `VmHWM` from `/proc/<pid>/status` after the target process exits. `VmHWM` is the kernel-tracked high-water mark for resident set size over the entire process lifetime. This is an exact peak value maintained by the OS.

### macOS — `sampled_peak`

On macOS, the script polls `ps -o rss` at a configurable interval (e.g., `--interval 100` for 100 ms) and records the maximum observed value. Because this is a polling-based approach, the reported peak may be lower than the true peak if a short-lived memory spike occurs between samples.

### Cross-Platform Comparison Caveat

Memory values collected on Linux (`os_reported_peak`) and macOS (`sampled_peak`) use fundamentally different collection methods. **Do not compare absolute memory values across platforms.** Instead, compare relative changes (e.g., incremental vs. full-buffer path) within the same platform and collection method.

### Usage

```bash
# Start the target process, then observe it:
tools/perf/memory_observer.sh --pid <pid> --interval 100 --output /tmp/mem-report.json
```

The output JSON includes a `memory_peak_method` field (`os_reported_peak` or `sampled_peak`) so downstream tooling can distinguish the collection strategy.

### Memory Baselines — Large Tiers (Placeholder)

| Tier | Variant | Path | Peak RSS (bytes) | Peak RSS (MiB) | Method | Platform | Notes |
|------|---------|------|------------------|----------------|--------|----------|-------|
| large-100k | plain-html | full-buffer | — | — | — | — | Pending measurement |
| large-100k | plain-html | incremental | — | — | — | — | Pending measurement |
| large-5m | plain-html | full-buffer | — | — | — | — | Pending measurement |
| large-5m | plain-html | incremental | — | — | — | — | Pending measurement |

## Next Actions (Data-Driven)

1. If optimizing for request latency, prioritize parser-path profiling/optimization before FFI micro-optimizations.
2. Keep `if_modified_since_only` as a documented performance tradeoff for deployments that can accept weaker Markdown-variant conditional semantics.
3. Only evaluate zero-copy C/Rust buffer handoff if profiling shows copy cost is a material share of end-to-end latency on target workloads.
4. Add a separate full NGINX HTTP E2E baseline report (real server, real client load) when validating production rollout capacity.
5. Populate large-100k and large-5m baseline tables once incremental path profiling is complete.
6. Collect memory peak baselines for large tiers using `tools/perf/memory_observer.sh`.

## Real NGINX HTTP E2E Baselines (Local, NGINX 1.28.2 Stable)

### Why this section was added

- A real HTTP E2E benchmark was executed against **NGINX stable `1.28.2`** (released 2026-02-04).
- During this work, a correctness bug was found and fixed before final measurements:
  - Large Markdown responses (~1MB HTML input) stalled and timed out with `0` bytes sent.
  - Root cause: the body filter copied bytes into its accumulation buffer but did **not** mark incoming copy-filter temp buffers as consumed, causing upstream buffering to stall after ~`2 x 32KB`.
  - Fix: after copying a chunk into the module-owned buffer, advance the input buffer (`buf->pos = buf->last`) so NGINX copy filter can recycle temp buffers.

### Methodology (E2E)

- Real NGINX built locally with the module (`--add-module=.../components/nginx-module`)
- `worker_processes 1`
- Local loopback traffic (`127.0.0.1`)
- `ab` used for repeated runs (3 runs per scenario in the summary tables below)
- Config mode approximates production behavior for this module path:
  - `markdown_filter on`
  - `markdown_etag on`
  - `markdown_conditional_requests` = `full_support` or `if_modified_since_only`
  - normal `warn` logging (no debug logging during performance runs)

### x86_64 (Rosetta) - NGINX 1.28.2

Source: `/tmp/nginx-e2e-bench.nIJCEi`

| Scenario | Avg req/s | Avg mean ms |
|---|---:|---:|
| passthrough_small | 44758.5 | 1.124 |
| markdown_small | 24578.2 | 2.047 |
| passthrough_medium | 46724.7 | 0.428 |
| markdown_medium | 4909.6 | 4.074 |
| markdown_medium_ims_nomatch | 4906.7 | 4.077 |
| markdown_medium_full_nomatch | 4937.0 | 4.051 |

Large scenario (manual `ab`, 3 runs, `n=90 c=3` after the stall fix):

| Scenario | Avg req/s | Avg mean ms | Avg p95 ms | Avg p99 ms |
|---|---:|---:|---:|---:|
| passthrough_large | 5087.5 | 0.693 | 1.3 | 1.7 |
| markdown_large | 36.8 | 81.534 | 108.0 | 122.7 |

Single-request validation (`curl`, `Accept: text/markdown`) after the fix:

- `GET /full/large.html` => `HTTP 200`, body `421286` bytes, total `~45.2 ms`

### arm64 Native (Non-Rosetta) - NGINX 1.28.2

Source: `/tmp/nginx-e2e-bench.IHKff8`

| Scenario | Avg req/s | Avg mean ms |
|---|---:|---:|
| passthrough_small | 41933.9 | 1.201 |
| markdown_small | 32339.4 | 1.546 |
| passthrough_medium | 36169.2 | 0.604 |
| markdown_medium | 7756.8 | 2.580 |
| markdown_medium_ims_nomatch | 7731.0 | 2.588 |
| markdown_medium_full_nomatch | 7777.0 | 2.573 |

Large scenario (manual `ab`, 3 runs, `n=90 c=3`):

| Scenario | Avg req/s | Avg mean ms | Avg p95 ms | Avg p99 ms |
|---|---:|---:|---:|---:|
| passthrough_large | 5099.7 | 0.691 | 1.3 | 1.7 |
| markdown_large | 64.0 | 46.918 | 53.0 | 67.7 |

Single-request validation (`curl`, `Accept: text/markdown`):

- `GET /full/large.html` => `HTTP 200`, body `421286` bytes, total `~19.1 ms`

### E2E Interpretation (Post-Fix)

- The large-response hang was a **C filter buffering integration bug**, not a Rust conversion throughput problem.
- Local FFI microbench and real HTTP E2E still agree on the main optimization direction:
  - HTML parsing dominates the Rust-side cost.
  - Conditional mode (`full_support` vs `if_modified_since_only`) has a measurable microbench difference, but little E2E impact on medium no-match requests compared to overall conversion cost.
- Native arm64 materially improves Markdown conversion scenarios versus Rosetta:
  - `markdown_medium`: ~`1.58x` req/s improvement (`7756.8 / 4909.6`)
  - `markdown_large`: ~`1.74x` req/s improvement (`64.0 / 36.8`)
  - passthrough large is essentially unchanged, which isolates the gain to conversion-heavy paths rather than raw static file serving.

### Updated Optimization Priority (After E2E + Bug Fix)

1. Keep the C+Rust split (architecture remains valid).
2. Preserve and regression-test the body-filter buffer-consumption fix for large responses.
3. Prioritize parser-path optimization in Rust (`parse_html_with_charset`) before high-complexity zero-copy C/Rust output handoff.
4. Use native arm64 (not Rosetta) for macOS benchmark baselines and capacity estimates.
