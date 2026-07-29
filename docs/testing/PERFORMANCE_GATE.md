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

### 0.9.1 Release Gate Evidence
For the 0.9.1 release, evidence is gathered via the following targets:
- `make release-gates-check-091`: (Blocking) Verifies all core architectural and functional requirements.
- `make perf-evidence-check`: (Non-blocking) Verifies that 0.9.1 meets performance baselines across the target matrix.

For deep analysis, use:
- `python3 tools/perf/doctor_advice.py`: Analyzes measurement reports and suggests configuration tuning.
- `tools/perf/run_module_benchmark.sh`: Runs a standalone benchmark of the module.

When invoked directly, `threshold_engine.py` emits the Verdict Report JSON to
stdout and diagnostics to stderr. Redirect stdout to the intended artifact;
the Python process does not accept a caller-controlled output path.

## Baseline Management

Baselines are stored in `perf/baselines/<platform>.json` and must be
generated on the target platform:

1. Run `tools/perf/run_perf_baseline.sh --update-baseline` on the target
   CI runner (or locally for local baselines), or trigger `nightly-perf`
   via `workflow_dispatch` with `bootstrap_baseline=true` and download the
   uploaded `perf-baseline-<platform>.json` artifact.
2. Submit the generated baseline file via PR.
3. When no baseline exists, the engine skips comparison (exit 0).

### Canonical Module Baseline Policy

Do not fabricate or improve measured evidence. Only documented conservative
normalization of latency/throughput is allowed; path, fallback, output, memory,
and environment evidence must remain verbatim.

The immutable truth fields are `streaming_path_hits`, `fullbuffer_path_hits`,
`streaming_requests_total`, `precommit_failopen_total`,
`decompression_streaming_total`, `decompression_fullbuffer_total`,
`zero_copy_output_total`, `copied_output_total`, `baseline_rss_bytes`,
`peak_rss_bytes`, `input_bytes`, scenario status and metadata, platform, load
generator, and NGINX version. RPS may only be rounded downward or lowered;
latency and TTFB may only be rounded upward or raised. Never increase RPS,
decrease latency/TTFB, or alter truth evidence.

Keep the raw workflow artifact and record the artifact/run, source Git commit,
adjustment rule, person or reason, and date in `baseline_policy`. Missing raw
artifact provenance is an audit failure to disclose, not a reason to invent a
workflow identifier.

The checked-in 0.9.1 baseline is now a verbatim eight-scenario canonical run.
Its `baseline_policy` binds the measured data to source commit
`cab92df229b0b68cb02d88817a208e009f3ce106`, workflow run
`30405031983/attempts/1`, measurement timestamp `2026-07-28T22:41:12Z`, and
the retained raw artifact SHA-256
`a511b90f82d05f827ea011faccec3ff5b3aead892943180f98e617c6c09aad12`.
Machine validation recomputes that digest and requires the finalized report to
match the raw report exactly apart from `baseline_policy`.

The evidence objects remain layered: `baseline_policy` carries policy
provenance, top-level `module_benchmark` carries platform/load-generator/NGINX
environment plus `git_commit` and `timestamp`, and each scenario carries its
metadata, `load_integrity`, `metrics`, and `response_correctness`. Optional
`baseline_policy.scenario_sources` entries are checked for environment
consistency only when supplied.

The canonical workflow retains response probes at
`perf/baselines/module-baseline-091-raw-probes/`, derived from the raw report
path. It validates every scenario's non-empty `.headers`, `.body`, and `.json`
files, requires a passing probe with `curl_exit_code == 0`, verifies the body
SHA-256, validates the complete response-correctness schema, parses the final
HTTP response block from each `.headers` file, and requires its status,
normalized headers, Markdown content type, and empty content encoding to match
the probe JSON. It then requires exact finalized/probe
`response_correctness` object equality and enforces strict boolean/integer
tail and curl fields before
running the performance evidence and release gates. The canonical upload is
performed only after those checks pass and contains the finalized JSON, raw
JSON, and this probe directory. Canonical artifacts are retained for 30 days;
failure-only debug artifacts use the shorter diagnostic retention period.

The former `historical_audit_exception` is retained only for historical
validator coverage and is not used by the active release baseline. Future
baselines must identify a repository-contained raw artifact and must not use
an empty, `unknown`, or `not-recorded` `source_artifact`.

For module reports, `fallback_rate` is the pre-commit fail-open ratio,
calculated from `precommit_failopen_total` and `streaming_requests_total`.
`streaming_fallback_total` is a separate path-routing counter and does not
enter that ratio.

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


## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.1 | 2026-07-28 | Codex | Documented exact baseline provenance validation and the zero-entry complexity release gate. |
| 0.9.1 | 2026-07-08 | Agent | Updated performance gate evidence and tool references for 0.9.1 release |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, added mermaid diagrams where applicable, verified directive accuracy against code, added update tracking section |
