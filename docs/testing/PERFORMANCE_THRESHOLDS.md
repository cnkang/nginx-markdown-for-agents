# Performance Threshold Strategy

This document describes the dual-threshold strategy used by the performance
gate to classify metric deviations as **PASS**, **WARN**, or **FAIL**.

## Threshold Levels

| Level    | Meaning                                      | CI Effect          |
|----------|----------------------------------------------|--------------------|
| PASS     | Deviation within normal range                | Job passes         |
| WARN     | Exceeds warning threshold but not blocking   | Job passes (alert) |
| FAIL     | Exceeds blocking threshold                   | Job fails          |

## Default Thresholds

| Metric              | Direction         | Warning | Blocking | Rationale                                    |
|---------------------|-------------------|---------|----------|----------------------------------------------|
| `p50_ms`            | lower_is_better   | +15%    | +30%     | Primary comparison metric; stable across runs |
| `p95_ms`            | lower_is_better   | +20%    | +40%     | Tail latency has higher natural variance      |
| `p99_ms`            | lower_is_better   | +20%    | +40%     | Highest variance; matches P95 tolerance       |
| `peak_memory_bytes` | lower_is_better   | +10%    | +25%     | Memory regressions compound and are hard to reverse |
| `req_per_s`         | higher_is_better  | -15%    | -30%     | Throughput drop mirrors latency regression    |
| `input_mb_per_s`    | higher_is_better  | -15%    | -30%     | Processing efficiency regression indicator    |

Informational metrics (`stage_*_pct`) are not subject to threshold checks.

## Deviation Formula

```
deviation_pct = (current - baseline) / baseline * 100
```

For `lower_is_better` metrics, a positive deviation means regression.
For `higher_is_better` metrics, a negative deviation means regression
(thresholds are expressed as negative numbers).

## Configuration File

Thresholds are stored in `perf/thresholds.json`, organized by platform,
sample tier, and metric. Each entry includes a `rationale` field explaining
the threshold choice.

The `platforms` object contains explicit per-platform sections keyed by the
`uname`-style platform identifier (e.g., `linux-x86_64`, `darwin-arm64`).
The `"default"` section serves as a fallback for platforms without an
explicit entry. The threshold engine resolves thresholds in this order:

1. Exact platform match (e.g., `linux-x86_64`)
2. `"default"` platform entry
3. Built-in hardcoded defaults

Currently configured platforms:

| Platform Key     | Environment                        |
|------------------|------------------------------------|
| `linux-x86_64`  | GitHub Actions CI runners (Ubuntu) |
| `default`        | Fallback for all other platforms   |

### Updating Thresholds

1. Edit `perf/thresholds.json` with the new values.
2. Submit a PR so the change is auditable.
3. Include justification in the PR description (e.g., CI noise analysis,
   workload characteristic change).

To add thresholds for a new platform, add a new key under `platforms`
matching the platform's `uname`-style identifier (`<os>-<arch>`, e.g.,
`darwin-arm64` for Apple Silicon macOS).

## CI Noise Mitigation

- **Median aggregation**: Nightly runs execute each tier 3 times and take
  the median, reducing the impact of outlier runs.
- **Platform-stratified baselines**: Each CI platform has its own baseline
  file (`perf/baselines/<platform>.json`), so cross-platform variance does
  not affect threshold judgements.
- **Tail-latency tolerance**: P95/P99 thresholds are wider than P50 to
  account for their inherently higher variance.
- **Skip on missing baseline**: When no baseline exists for a platform,
  the engine skips comparison (exit 0) rather than producing false failures.
