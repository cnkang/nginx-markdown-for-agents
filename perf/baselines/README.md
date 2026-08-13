# Performance Baselines

This directory contains platform-specific performance baselines for the `nginx-markdown-for-agents` converter.

## Overview

Performance baselines provide these functions:
- Track performance regression across releases
- Compare full-buffer vs streaming engine performance
- Generate evidence packs for release decisions

Module-level percentage comparisons require the current report and baseline to
use the same platform, load generator, NGINX version, and critical-scenario
input sizes. The evidence gate reports `MISSING_EVIDENCE` rather than a
regression verdict when these fields differ. Report-only mode retains exit zero
for visibility. Blocking release mode fails until comparable evidence exists.

## Evidence Truth and Conservative Normalization

Module baselines contain two different classes of data:

- Truth evidence must remain verbatim from the canonical run:
  `streaming_path_hits`, `fullbuffer_path_hits`,
  `streaming_requests_total`, `precommit_failopen_total`,
  `decompression_streaming_total`, `decompression_fullbuffer_total`,
  `zero_copy_output_total`, `copied_output_total`, `baseline_rss_bytes`,
  `peak_rss_bytes`, `input_bytes`, scenario status and metadata, platform,
  load generator, and NGINX version.
- Performance thresholds may round RPS downward or lower it. They may round
  latency and TTFB upward or raise them. RPS must never increase and
  latency/TTFB must never decrease to make evidence look better.

Do not fabricate or improve measured evidence. Conservative normalization
may adjust latency and throughput only. Path, fallback, output, memory, and
environment evidence must remain verbatim.

Retain the raw workflow artifact and record its run, source Git commit,
adjustment rule, person or reason, and adjustment date in `baseline_policy`.
The current `module-baseline-092.json` records a verbatim eight-scenario run
from commit `97672b57d4479febbf6d9a3d947a339236b698f3`, measured at
`2026-07-31T04:36:09Z` by canonical workflow run `30604344481/attempts/1`.
Its retained raw artifact has SHA-256
`ebcd73ec55c4e85a6cdbc85371832342d97b2edcdf06c56cf5bb7a91f0ab5c92`.
The validator requires this source commit, run attempt, timestamp, retained
raw artifact, and digest to remain mutually consistent.
That run used Rust 1.93.1 while the project requires Rust 1.97 or newer, so
its policy explicitly sets `release_gate_eligible: false` with a remeasurement
reason. It remains auditable regression context; the release evidence gate
skips percentage comparisons until a current-toolchain baseline is checked in.

Provenance layers by object: `baseline_policy` carries policy provenance,
top-level `module_benchmark` carries `platform`, `load_generator`,
`nginx_version`, `git_commit`, and `timestamp`, and each scenario carries its
metadata, `load_integrity`, `metrics`, and `response_correctness`. The
validator checks the optional `baseline_policy.scenario_sources` object for
environment consistency only when present.

The module benchmark derives its retained probe directory from the report
output path. For the canonical raw report this is
`perf/baselines/module-baseline-092-raw-probes/`, containing non-empty
`.headers`, `.body`, and `.json` files for each of the eight scenarios.
`tools/perf/validate_module_probe_artifacts.py` verifies that complete
triplet, its response verdict and digest, and the finalized baseline's
complete `response_correctness` object before the system uploads the
canonical artifact. The validator parses the final HTTP response block in each `.headers`
file and binds its status, normalized headers, Markdown content type, and empty
content encoding to the probe JSON. The system retains the canonical artifact
for 30 days. Failure-only debug artifacts remain on the shorter diagnostic
retention period.

## Raw Artifact Binding and Digest Verification

Every new canonical baseline must be bound to its retained raw report via a
SHA-256 digest. The `baseline_policy` block records:

- `source_artifact`: the repository-relative path to the retained raw report.
- `source_artifact_sha256`: the 64-character lowercase hex SHA-256 of that
  raw file's bytes.

The finalizer (`tools/perf/finalize_module_baseline.py`) computes the digest
from the actual raw file at finalization time. The validator
(`tools/perf/evidence_gate.py`) recomputes the digest and rejects any
mismatch. Do not write a digest without verifying the file.

### verbatim_run vs conservative_normalized

- **`verbatim_run`**: the finalized baseline is the raw report plus a
  `baseline_policy` block. The finalizer must not modify raw evidence. The
  validator compares the entire finalized report with `baseline_policy`
  removed against the raw report.
- **`conservative_normalized`**: RPS may only round downward or lower.
  Latency, TTFB, and TTLB may only round upward or increase. Truth evidence
  (path, fallback, output, memory, environment, scenario status, metadata,
  metric keys, and `decompression_coverage`) must remain identical to the raw
  report. The policy adjustment ledger must exactly describe every changed
  adjustable metric and its delta. The finalizer records the adjustment rule,
  reason, and date. The validator machine-verifies the full relationship
  against the raw artifact.

The `baseline_policy.type` permits only `verbatim_run` or
`conservative_normalized`. Any other value (or a missing type) fail-closes
the release gate. Both policy types require the full 40-character lowercase
source Git SHA. The validator rejects short SHAs, `unknown`, or placeholders.
The historical exception at commit `847f9013` survives only as an audit
record in repository history. It no longer applies to the current canonical
baseline, and new baselines must not extend it.

### Historical audit record

The original pre-regeneration baseline at commit `847f9013` remains relevant
only to historical validator tests and audit comparisons. It is not the active
release baseline. The active 0.9.2 baseline uses `verbatim_run` provenance and
does not use `historical_audit_exception`.

## Environment Consistency

A single baseline file must never mix scenarios measured under different
environments. Every scenario in `module-baseline-092.json` shares the
top-level canonical environment (`linux-x86_64`, `ab`, NGINX 1.24.0). When
the finalizer merges a scenario from a different run,
`baseline_policy.scenario_sources` must declare structured `platform`,
`load_generator`, and `nginx_version` fields for it, and the evidence gate
rejects the baseline unless each of them matches the top-level environment.
A scenario measured in a diverging environment must instead live in its own
environment-truthful baseline file and must never enter comparisons against
reports from the canonical environment.

The active canonical run includes `brotli-streaming-first` under NGINX 1.24.0
with the other seven scenarios. The older
`module-baseline-brotli-091.json` remains as a truthful NGINX 1.30.4 archival
artifact and is never mixed into current comparisons.

Regeneration path: run the `nightly-perf.yml` workflow with
`workflow_dispatch` and `bootstrap_module_baseline=true` (do not also set
`bootstrap_baseline=true`). The job builds the canonical NGINX version
natively on `ubuntu-24.04` (x86_64) with Brotli support (`libbrotli-dev`
plus the pinned Python `Brotli==1.2.0`), produces all eight scenarios in one
consistent environment, finalizes the baseline from the retained raw report
(computing the raw SHA-256 and taking the measurement timestamp from
`module_benchmark.timestamp`), validates the result against the evidence
integrity contract (including raw-digest and content-binding verification),
and uploads both the finalized and raw files as retained artifacts. Commit
both artifacts before tagging a release and verify the policy digest after
materialization.

## Baseline Files

Each platform has a dedicated baseline file:
- `darwin-arm64.json` - macOS on Apple Silicon
- `linux-x86_64.json` - Linux on x86_64
- `darwin-x86_64.json` - macOS on Intel
- `corpus-baseline.json` - Linux x86_64 corpus quality and latency baseline
- `module-baseline-091.json` - Linux x86_64 module-level 0.9.1 evidence
  baseline (canonical NGINX 1.24.0 environment)
- `module-baseline-091-raw.json` - retained raw report for the canonical
  0.9.1 baseline (provenance artifact referenced by
  `module-baseline-091.json`, generated by the nightly workflow)
- `module-baseline-091-raw-probes/` - retained response probe artifacts
  derived from `module-baseline-091-raw.json`. The canonical workflow
  validates all eight scenario triplets before upload.
- `module-baseline-brotli-091.json` - Brotli streaming evidence measured on
  NGINX 1.30.4 (archival only, excluded from cross-environment comparisons)
- `module-baseline-brotli-091-raw.json` - retained raw report for the Brotli
  run (provenance artifact referenced by `module-baseline-brotli-091.json`)
- `module-baseline-092.json` - Linux x86_64 module-level 0.9.2 evidence
  baseline (canonical NGINX environment), the blocking evidence source for
  the 0.9.2 release gates (`make release-gates-check-092`)
- `module-baseline-092-raw.json` - retained raw report for the canonical
  0.9.2 baseline (provenance artifact referenced by
  `module-baseline-092.json`, generated by the nightly workflow)
- `module-baseline-092-raw-probes/` - retained response probe artifacts
  derived from `module-baseline-092-raw.json`. The canonical workflow
  validates all eight scenario triplets before upload.

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

When you use `--engine streaming` or `--engine both`:

- `ttfb_ms` - Time to first Markdown byte
- `ttlb_ms` - Time to last Markdown byte
- `cpu_time_ms` - CPU time consumed
- `flush_count` - Number of flush points
- `fallback_rate` - Pre-commit fail-open ratio
  (`precommit_failopen_total / streaming_requests_total`). This value is
  distinct from `streaming_fallback_total`, the path-routing fallback counter.

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
3. The pipeline archives results in `perf/reports/`
4. Evidence packs generated for release candidates

## See Also

- [perf/thresholds.json](../thresholds.json) - Regression threshold definitions
- [perf/streaming-evidence-targets.json](../streaming-evidence-targets.json) - Streaming evidence targets
- [perf/metrics-schema.json](../metrics-schema.json) - Metric definitions and sample tiers
- [tools/perf/evidence_pack_generator.py](../../tools/perf/evidence_pack_generator.py) - Evidence pack generation logic


## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-08 | Kang | Pointed active baseline references at module-baseline-092 |
| 0.9.1 | 2026-07-28 | Codex | Documented strict raw-artifact provenance, exact historical-exception binding, and conservative normalization rules. |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, added mermaid diagrams where applicable, verified directive accuracy against code, added update tracking section |
