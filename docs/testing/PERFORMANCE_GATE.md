# Performance Gate Guide

This document covers the full performance gating system: metrics, thresholds,
CI integration, local reproduction, and troubleshooting.

## Overview

The performance gate automatically detects regressions by comparing current
benchmark results against stored baselines using configurable dual thresholds
(warning / blocking). It runs in two CI contexts:

- **PR Smoke**: small + medium tiers on every PR with Rust/perf changes.
- **Nightly Full**: all tiers × 3 repeats, median aggregation.

## Metrics

See [PERFORMANCE_METRICS.md](PERFORMANCE_METRICS.md) for the full metrics
catalogue. Key metrics used for threshold comparison:

| Metric              | Unit          | Direction         |
|---------------------|---------------|-------------------|
| `p50_ms`            | milliseconds  | lower_is_better   |
| `p95_ms`            | milliseconds  | lower_is_better   |
| `p99_ms`            | milliseconds  | lower_is_better   |
| `peak_memory_bytes` | bytes         | lower_is_better   |
| `req_per_s`         | requests/sec  | higher_is_better  |
| `input_mb_per_s`    | MB/sec        | higher_is_better  |

## Threshold Strategy

See [PERFORMANCE_THRESHOLDS.md](PERFORMANCE_THRESHOLDS.md) for threshold
values, rationale, and update procedures.

## CI Integration

### PR Smoke (`perf-smoke` job in `ci.yml`)

Triggers when Rust code, perf config, or workflow files change. Runs small
and medium tiers, then invokes the threshold engine. Blocking verdicts fail
the job; warnings are logged but the job passes.

Artifacts uploaded:
- `perf-measurement-<platform>.json` (Measurement Report, e.g. `perf-measurement-linux-x86_64.json`)
- `perf-verdict-<platform>.json` (Verdict Report, e.g. `perf-verdict-linux-x86_64.json`)

Platform identifiers use `uname`-style naming: `linux-x86_64` for GitHub
Actions Ubuntu runners, `darwin-arm64` for Apple Silicon macOS, etc.

### Nightly Full (`nightly-perf.yml`)

Runs on a daily schedule (03:00 UTC) and via `workflow_dispatch`. Executes
all sample tiers 3 times each, computes the median, and runs the threshold
engine. Same artifact naming convention as PR Smoke.

When triggered manually with `bootstrap_baseline=true`, the workflow skips
threshold comparison and uploads `perf-baseline-<platform>.json` as an
artifact for baseline bootstrapping.

## Local Reproduction

Run from the repository root:

```bash
# Full run (all tiers)
tools/perf/run_perf_baseline.sh

# Single tier
tools/perf/run_perf_baseline.sh --tier small

# Custom output paths
tools/perf/run_perf_baseline.sh \
  --json-output /tmp/measurement.json \
  --verdict-output /tmp/verdict.json

# Update baseline from current run
tools/perf/run_perf_baseline.sh --update-baseline
```

The script builds the release binary, runs benchmarks, generates a
Measurement Report, invokes the threshold engine for a Verdict Report,
and prints a text summary to stderr.

## Baseline Management

Baselines are stored in `perf/baselines/<platform>.json` and must be
generated on the target platform:

1. Run `tools/perf/run_perf_baseline.sh --update-baseline` on the target
   CI runner (or locally for local baselines), or trigger `nightly-perf`
   via `workflow_dispatch` with `bootstrap_baseline=true` and download the
   uploaded `perf-baseline-<platform>.json` artifact.
2. Submit the generated baseline file via PR.
3. When no baseline exists, the engine skips comparison (exit 0).

## Troubleshooting

### False positives / noisy failures

- Check if the regression is consistent across multiple runs.
- Nightly uses median of 3 runs to reduce noise.
- Consider widening thresholds in `perf/thresholds.json` if a metric
  is inherently noisy on a specific platform.

### Bypassing the gate

Set the environment variable to skip all performance checks:

```bash
PERF_GATE_SKIP=1 tools/perf/run_perf_baseline.sh --tier small
```

In CI, set `PERF_GATE_SKIP: "1"` in the job environment to bypass.
This produces a `"skipped"` verdict with exit code 0.

### Missing baseline

When `perf/baselines/<platform>.json` does not exist, the threshold
engine outputs a warning and exits 0. The Verdict Report will show
`overall_verdict: "skipped"`. Follow the baseline management steps
above to bootstrap.

### Threshold engine errors

- **Schema version mismatch**: The baseline was generated with a different
  schema version. Re-generate the baseline with `--update-baseline`.
- **JSON parse error**: Check that the measurement and baseline files are
  valid JSON. The error message includes the file path and parse details.
