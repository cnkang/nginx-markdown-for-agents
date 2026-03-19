# Performance Metrics Reference

This document defines all performance metrics, sample tiers, and baseline update
procedures for the `nginx-markdown-for-agents` performance gate system.

The canonical machine-readable source is [`perf/metrics-schema.json`](../../perf/metrics-schema.json).
This document provides a human-readable companion.

## Metrics

Each metric has a **direction** that determines how threshold comparisons work:

- **lower_is_better** — a regression means the value *increased* (e.g., latency, memory)
- **higher_is_better** — a regression means the value *decreased* (e.g., throughput)
- **informational** — tracked for analysis only; not subject to threshold gating

| Name | Unit | Collection Method | Aggregation | Direction | Description |
|------|------|-------------------|-------------|-----------|-------------|
| `p50_ms` | milliseconds | percentile | median | lower_is_better | Median (P50) end-to-end conversion latency |
| `p95_ms` | milliseconds | percentile | direct | lower_is_better | 95th-percentile conversion latency |
| `p99_ms` | milliseconds | percentile | direct | lower_is_better | 99th-percentile conversion latency |
| `peak_memory_bytes` | bytes | process_rss | max | lower_is_better | Peak resident set size during the benchmark run |
| `req_per_s` | requests/second | derived | median | higher_is_better | Throughput — conversions completed per second |
| `input_mb_per_s` | MB/second | derived | median | higher_is_better | Input throughput — megabytes of HTML processed per second |
| `stage_parse_pct` | percent | stage_timing | mean | informational | Share of time spent in the HTML parse stage |
| `stage_convert_pct` | percent | stage_timing | mean | informational | Share of time spent in the Markdown conversion stage |
| `stage_etag_pct` | percent | stage_timing | mean | informational | Share of time spent in ETag generation |
| `stage_token_pct` | percent | stage_timing | mean | informational | Share of time spent in token estimation |

### Collection Methods

| Method | Description |
|--------|-------------|
| `percentile` | Computed from the latency distribution across all iterations |
| `process_rss` | Maximum resident set size reported by the OS for the benchmark process |
| `derived` | Calculated from other measurements (e.g., `req_per_s = iterations / elapsed`) |
| `stage_timing` | Per-stage wall-clock timing within a single conversion, averaged across iterations |

### Aggregation Methods

| Method | Used By | Description |
|--------|---------|-------------|
| `median` | p50_ms, req_per_s, input_mb_per_s | Middle value of sorted measurements; primary comparison metric for threshold gating (reduces CI noise) |
| `direct` | p95_ms, p99_ms | Value taken directly from the percentile distribution |
| `max` | peak_memory_bytes | Maximum observed value across the run |
| `mean` | stage_*_pct | Arithmetic mean across iterations |

## Sample Tiers

| Name | Target Size | Source File | Generation Method |
|------|-------------|-------------|-------------------|
| `small` | ~0.4 KB | `tests/corpus/simple/basic.html` | Direct — file used as-is |
| `medium` | ~10 KB | `tests/corpus/complex/blog-post.html` | Content repeated in-memory to reach 10,240 bytes |
| `medium-front-matter` | ~10 KB | `tests/corpus/complex/blog-post.html` | Same as `medium` with YAML front-matter extraction enabled |
| `large-1m` | ~1 MB | `tests/corpus/complex/blog-post.html` | Content repeated in-memory to reach 1,048,576 bytes |

All sample content is deterministic and version-controlled. The tier name `large-1m`
(not `large`) is the canonical key used in JSON reports and baselines. The CLI accepts
`--single large` as a compatibility alias that maps to the `large-1m` key.

### Tier Usage by CI Context

| CI Context | Tiers Run | Repeats |
|------------|-----------|---------|
| PR Smoke (`perf-smoke` job) | small, medium | 1 |
| Nightly Full (`nightly-perf` workflow) | all four tiers | ≥ 3 (median taken) |
| Local runner (default) | all four tiers | 1 |

## Baseline Update Flow

Baselines are platform-specific JSON files stored in `perf/baselines/<platform>.json`.
They contain only core metrics (`p50_ms`, `p95_ms`, `p99_ms`, `peak_memory_bytes`,
`req_per_s`, `input_mb_per_s`) — runtime metadata like `stage_breakdown`, `iterations`,
and `warmup` are excluded.

### Generating or Updating a Baseline

Run from the repository root on the target platform:

```bash
tools/perf/run_perf_baseline.sh --update-baseline
```

This command:

1. Builds the release binary (`cargo build --release`)
2. Runs all sample tiers
3. Extracts core metric fields from the Measurement Report
4. Writes the result to `perf/baselines/<platform>.json`

Submit the generated file via PR so the change is auditable.

### First-Time Bootstrap

When no baseline exists for a platform:

1. The Threshold Engine skips comparison and exits with code `0`
2. The Verdict Report contains `"overall_verdict": "skipped"` with empty `comparison.tiers`
3. PR Smoke and Nightly Full CI jobs pass without blocking
4. A warning is printed to stderr: `No baseline found for platform <platform>, skipping comparison`

To bootstrap, a maintainer runs `--update-baseline` on the target CI runner, or
triggers `nightly-perf` via `workflow_dispatch` with `bootstrap_baseline=true`,
downloads the uploaded `perf-baseline-<platform>.json` artifact, and then submits
the result as a PR.

### Rules

- Baselines **must** be generated by running benchmarks on the actual target platform
- Cross-platform backfilling from other platforms' data is **not permitted**
- Manual creation or editing of baseline files is discouraged — always regenerate

### Running a Single Tier

```bash
tools/perf/run_perf_baseline.sh --tier small --json-output /tmp/report.json
```

### Specifying Report Output

```bash
# Measurement Report to a custom path
tools/perf/run_perf_baseline.sh --json-output /tmp/measurement.json

# Default output: perf/reports/latest-measurement-<platform>.json
tools/perf/run_perf_baseline.sh
```

## Related Documents

- [`perf/metrics-schema.json`](../../perf/metrics-schema.json) — Machine-readable metric definitions
- [`perf/baselines/README.md`](../../perf/baselines/README.md) — Baseline storage and bootstrap details
- [`docs/testing/PERFORMANCE_BASELINES.md`](PERFORMANCE_BASELINES.md) — Historical local baseline data
