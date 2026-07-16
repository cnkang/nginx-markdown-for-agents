# Performance Baselines

This directory contains platform-specific performance baselines for the `nginx-markdown-for-agents` converter.

## Overview

Performance baselines are used to:
- Track performance regression across releases
- Compare full-buffer vs streaming engine performance
- Generate evidence packs for release decisions

Module-level percentage comparisons require the current report and baseline to
use the same platform, load generator, NGINX version, and critical-scenario
input sizes. The evidence gate reports `MISSING_EVIDENCE` rather than a
regression verdict when these fields differ. Report-only mode retains exit zero
for visibility; blocking release mode fails until comparable evidence exists.

## Evidence Truth and Conservative Normalization

Module baselines contain two different classes of data:

- Truth evidence must remain verbatim from the canonical run:
  `streaming_path_hits`, `fullbuffer_path_hits`,
  `streaming_requests_total`, `precommit_failopen_total`,
  `decompression_streaming_total`, `decompression_fullbuffer_total`,
  `zero_copy_output_total`, `copied_output_total`, `baseline_rss_bytes`,
  `peak_rss_bytes`, `input_bytes`, scenario status and metadata, platform,
  load generator, and NGINX version.
- Performance thresholds may be conservatively normalized: RPS may only be
  rounded downward or lowered, while latency and TTFB may only be rounded
  upward or raised. RPS must never be increased and latency/TTFB must never be
  decreased to make evidence look better.

Do not fabricate or improve measured evidence. Only documented conservative
normalization of latency/throughput is allowed; path, fallback, output, memory,
and environment evidence must remain verbatim.

Retain the raw workflow artifact and record its run, source Git commit,
adjustment rule, person or reason, and adjustment date in `baseline_policy`.
The current `module-baseline-091.json` records commit `847f9013` and the local
canonical run timestamp `2026-07-16T09:47:06Z`. Its original artifact location
was not recorded, which is explicitly marked as a historical audit gap; future
baseline updates must not replace `source_artifact` with an unlocatable value.

## Baseline Files

Each platform has a dedicated baseline file:
- `darwin-arm64.json` - macOS on Apple Silicon
- `linux-x86_64.json` - Linux on x86_64
- `darwin-x86_64.json` - macOS on Intel
- `corpus-baseline.json` - Linux x86_64 corpus quality and latency baseline
- `module-baseline-091.json` - Linux x86_64 module-level 0.9.1 evidence baseline

## Running Benchmarks

### Basic Usage

```bash
# Run full-buffer benchmark (default)
tools/perf/run_perf_baseline.sh

# Run streaming benchmark
tools/perf/run_perf_baseline.sh --engine streaming

# Run both engines for comparison
tools/perf/run_perf_baseline.sh --engine both

# Run specific tier only
tools/perf/run_perf_baseline.sh --tier small

# Generate measurement report JSON
tools/perf/run_perf_baseline.sh --json-output perf/reports/latest.json
```

### Evidence Pack Generation

To generate a complete evidence pack for release gates evaluation:

```bash
# Generate evidence pack with default settings
tools/perf/run_perf_baseline.sh --engine both --generate-evidence-pack

# Specify custom evidence output path
tools/perf/run_perf_baseline.sh \
  --engine both \
  --generate-evidence-pack \
  --evidence-output perf/reports/evidence-pack-custom.json

# Include parity report for dual-threshold evaluation
tools/perf/run_perf_baseline.sh \
  --engine both \
  --generate-evidence-pack \
  --parity-report tests/corpus/parity-report.json
```

### Updating Baselines

To update the baseline for the current platform:

```bash
tools/perf/run_perf_baseline.sh --update-baseline
```

This extracts core metrics from the latest measurement and saves them as the new baseline.

## Engine Modes

The `--engine` parameter supports three modes:

| Mode | Description | Use Case |
|------|-------------|----------|
| `full-buffer` (default) | Uses existing MarkdownConverter FFI API | Baseline measurements, regression checks |
| `streaming` | Uses StreamingConverter API with 16KB chunks | Streaming performance validation |
| `both` | Runs full-buffer first, then streaming | Evidence pack generation, comparison |

## Sample Tiers

### Standard Tiers

| Tier | Size | Description |
|------|------|-------------|
| `small` | ~0.4KB | Simple HTML document |
| `medium` | ~10KB | Medium-complexity document |
| `medium-front-matter` | ~10KB | Medium document with front matter extraction |
| `large-1m` | ~1MB | Large document |

### Streaming-Focused Tiers

These tiers are specifically designed to validate streaming performance:

| Tier | Size | Description |
|------|------|-------------|
| `large-10m` | ~10MB | Large document for bounded-memory validation |
| `extra-large-64m` | ~64MB | Critical bounded-memory validation point |
| `streaming-chunked` | ~1MB | Multi-chunk HTML document |
| `streaming-tables-heavy` | ~100KB | Table-heavy HTML (expected to trigger fallback) |
| `streaming-code-heavy` | ~100KB | Code-block dense document |
| `streaming-malformed` | ~1MB | Malformed HTML resilience test |
| `streaming-mixed-charset` | ~100KB | Mixed encoding (UTF-8/ISO-8859-1) |

## Metrics Collected

### Standard Metrics (All Engines)

- `p50_ms`, `p95_ms`, `p99_ms` - Latency percentiles
- `peak_memory_bytes` - Peak RSS
- `req_per_s` - Throughput
- `input_mb_per_s` - Input throughput

### Streaming-Specific Metrics

When `--engine streaming` or `--engine both` is used:

- `ttfb_ms` - Time to first Markdown byte
- `ttlb_ms` - Time to last Markdown byte
- `cpu_time_ms` - CPU time consumed
- `flush_count` - Number of flush points
- `fallback_rate` - Pre-commit fallback ratio

## Evidence Pack

An evidence pack is a comprehensive JSON document that includes:
- Full-buffer and streaming measurement reports
- Streaming-specific metrics
- Evidence target evaluation results (PASS/FAIL)
- Release gates status
- Final streaming evidence verdict (GO/NO_GO)

### Evidence Targets

| Target | Threshold | Description |
|--------|-----------|-------------|
| `bounded_memory` | slope < 0.5 bytes/input_byte | Peak RSS doesn't scale linearly |
| `ttfb_improvement` | ratio < 0.5 | Streaming TTFB is 50% faster than full-buffer p50 |
| `no_regression_small_medium` | ratio < 1.3 | Streaming p50 <= full-buffer p50 * 1.3 |
| `streaming_supported_parity` | 100% | Byte-identical output for supported corpus |
| `fallback_expected_correctness` | 100% | Correct fallback for expected corpus |

### Verdict Logic

The `streaming_evidence_verdict` is:
- `"GO"` if and only if ALL release gates are `"PASS"`
- `"NO_GO"` if ANY gate is `"FAIL"` or `"UNKNOWN"`

P1 status fields do NOT affect the verdict.

## File Structure

```text
perf/baselines/
├── README.md                  # This file
├── darwin-arm64.json          # macOS Apple Silicon baseline
├── linux-x86_64.json          # Linux x86_64 baseline
└── darwin-x86_64.json         # macOS Intel baseline (if applicable)

perf/reports/
├── latest-measurement-*.json  # Latest measurement reports
├── latest-verdict-*.json      # Threshold engine verdicts
└── evidence-pack-*.json       # Generated evidence packs
```

## Integration with CI

The baseline system integrates with CI pipelines:
1. Nightly benchmarks run automatically
2. Threshold engine compares against baselines
3. Results are archived in `perf/reports/`
4. Evidence packs generated for release candidates

## See Also

- [perf/thresholds.json](../thresholds.json) - Regression threshold definitions
- [perf/streaming-evidence-targets.json](../streaming-evidence-targets.json) - Streaming evidence targets
- [perf/metrics-schema.json](../metrics-schema.json) - Metric definitions and sample tiers
- [tools/perf/evidence_pack_generator.py](../../tools/perf/evidence_pack_generator.py) - Evidence pack generation logic


## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, added mermaid diagrams where applicable, verified directive accuracy against code, added update tracking section |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
