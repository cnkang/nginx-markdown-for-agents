# Benchmark Corpus and Evidence

The benchmark corpus system provides reproducible, quantifiable evidence of conversion quality, performance, and fallback behavior for the nginx-markdown-for-agents project.

## Overview

The system evaluates the existing buffer-based conversion path. It complements the latency/throughput pipeline (`tools/perf/run_perf_baseline.sh`) with quality-focused metrics: fallback rate, token reduction estimates, and per-fixture conversion outcomes.

> **Token reduction values are estimates derived from byte-size ratios, not precise token counts or LLM billing data.**

## Reference Corpus

### Structure

HTML fixtures live under `tests/corpus/`, organized by category:

```
tests/corpus/
  corpus-version.json          # Corpus version (semver)
  simple/                      # Clean, well-structured HTML
  complex/                     # Real-world pages with varying complexity
  malformed/                   # Error recovery testing
  edge-cases/                  # Boundary conditions
  encoding/                    # Character encoding variations
```

### Page Types

Each fixture is classified into one of five page types via its `.meta.json` sidecar:

| Page Type | Description |
|-----------|-------------|
| `clean-article` | Simple, well-structured HTML with clear content |
| `documentation` | Technical docs with code examples and structured sections |
| `nav-heavy` | Pages with significant navigation elements |
| `boilerplate-heavy` | Pages where boilerplate outweighs content |
| `complex-common` | Mixed complexity, common real-world patterns |

### Fixture Metadata

Each HTML fixture has a companion `.meta.json` sidecar:

```json
{
  "fixture-id": "simple/basic",
  "page-type": "clean-article",
  "expected-conversion-result": "converted",
  "input-size-bytes": 379,
  "source-description": "Minimal HTML with title and paragraph",
  "failure-corpus": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `fixture-id` | string | Unique ID: `<category>/<name>` |
| `page-type` | enum | One of the five page types above |
| `expected-conversion-result` | enum | `converted`, `skipped`, or `failed-open` |
| `input-size-bytes` | integer | Approximate file size |
| `source-description` | string | Human-readable provenance |
| `failure-corpus` | boolean | Part of the failure/tricky-case collection |

### Adding New Fixtures

1. Add the HTML file to the appropriate `tests/corpus/<category>/` directory.
2. Create a `.meta.json` sidecar with all required fields.
3. If the fixture is a known-difficult case, set `"failure-corpus": true`.
4. Update `tests/corpus/corpus-version.json`:
   - Patch bump for metadata-only changes.
   - Minor bump for adding fixtures.
   - Major bump for removing or changing existing fixtures.
5. Run `tools/corpus/validate_corpus.sh` to verify.

### Failure Corpus

Fixtures marked `"failure-corpus": true` are always included in benchmark runs and aggregate metrics. They prevent cherry-picking favorable examples. The corpus requires at least 3 failure fixtures covering distinct failure modes.

## Unified Report Schema

The benchmark produces a JSON report with schema version `1.0.0`. All keys use lowercase kebab-case.

### Top-Level Structure

```json
{
  "schema-version": "1.0.0",
  "metadata": { ... },
  "summary": { ... },
  "fixtures": [ ... ]
}
```

### Metadata Fields

| Field | Description |
|-------|-------------|
| `corpus-version` | Semver of the reference corpus used |
| `git-commit` | Git commit hash of the converter |
| `platform` | Platform identifier (e.g., `darwin-arm64`) |
| `timestamp` | ISO 8601 UTC timestamp |
| `converter-version` | Semantic version of the converter |
| `token-approx-factor` | Approximation factor for token estimation (default 1.0) |

### Summary Fields

| Field | Description |
|-------|-------------|
| `total-fixtures` | Total fixtures processed |
| `converted-count` | Successfully converted |
| `skipped-count` | Skipped (ineligible) |
| `failed-open-count` | Conversion failed (fail-open) |
| `fallback-rate` | `failed-open-count / total-fixtures * 100` |
| `token-reduction-percent` | Weighted average token reduction across converted fixtures |
| `input-bytes-total` | Sum of all input bytes |
| `output-bytes-total` | Sum of output bytes for converted fixtures |
| `p50-latency-ms` | P50 latency in milliseconds |
| `p95-latency-ms` | P95 latency |
| `p99-latency-ms` | P99 latency |

### Per-Fixture Fields

| Field | Description |
|-------|-------------|
| `fixture-id` | Unique fixture identifier |
| `page-type` | Page type classification |
| `conversion-result` | `converted`, `skipped`, or `failed-open` |
| `input-bytes` | HTML input size |
| `output-bytes` | Markdown output size (0 if not converted) |
| `latency-ms` | Wall-clock conversion time |
| `token-reduction-percent` | Per-fixture token reduction estimate |
| `failure-corpus` | Whether this is a failure corpus fixture |

## Token Reduction Estimation

Token reduction uses a byte-ratio heuristic:

```
token-reduction-percent = (1 - output_bytes / input_bytes) * 100 * token_approx_factor
```

- `token_approx_factor` defaults to 1.0 (configurable via `--token-approx-factor`).
- Non-converted fixtures have `token-reduction-percent = 0`.
- The aggregate is the input-bytes-weighted average across converted fixtures.

> **These are estimates, not precise token counts or LLM billing data.**

## Running Benchmarks Locally

### Full Benchmark

```bash
make test-benchmark
```

This builds the `test-corpus-conversion` binary, runs the converter on every corpus fixture, and writes the Unified Report to `perf/reports/corpus-report.json` with before/after examples in `perf/reports/examples/`.

### Compare Reports

```bash
make test-benchmark-compare
```

Compares `perf/reports/corpus-baseline.json` against `perf/reports/corpus-report.json` using thresholds from `perf/quality-thresholds.json`. Produces a verdict JSON and exits with code 1 on failure.

### Generate PR Summary

```bash
make test-benchmark-summary
```

Prints a markdown summary of the latest report to stdout.

### Manual Invocation

```bash
# Run benchmark with custom options
python3 tools/perf/run_corpus_benchmark.py \
    --corpus-dir tests/corpus \
    --converter-bin <path-to-binary> \
    --output perf/reports/corpus-report.json \
    --token-approx-factor 0.75 \
    --examples-dir perf/reports/examples

# Compare two reports
python3 tools/perf/compare_reports.py \
    --baseline perf/reports/baseline.json \
    --current perf/reports/current.json \
    --thresholds perf/quality-thresholds.json \
    --output perf/reports/verdict.json

# Validate a report
python3 tools/perf/report_schema.py perf/reports/corpus-report.json
```

## CI and Release Gates

The corpus benchmark runs in CI alongside the existing latency/throughput pipeline:

- **Existing pipeline** (`run_perf_baseline.sh`): Gates on latency and throughput regressions.
- **Corpus benchmark** (`run_corpus_benchmark.py`): Gates on fallback rate, token reduction, and conversion outcome regressions.

Both contribute independently to the CI pass/fail decision.

### Quality Thresholds

Defined in `perf/quality-thresholds.json`:

| Metric | Warning | Blocking | Direction |
|--------|---------|----------|-----------|
| `fallback-rate` | +5 pp | +10 pp | Lower is better |
| `token-reduction-percent` | -5 pp | -10 pp | Higher is better |
| `p50-latency-ms` | +15% | +30% | Lower is better |
| `p95-latency-ms` | +20% | +40% | Lower is better |
| `p99-latency-ms` | +20% | +40% | Lower is better |

### Verdict

- `pass`: No metric exceeds any threshold.
- `warn`: At least one metric exceeds warning but none exceed blocking. Exit code 0.
- `fail`: At least one metric exceeds blocking threshold. Exit code 1.

### Evidence Pack

CI uploads these artifacts:
- `corpus-report-<platform>.json` — Unified Report
- `corpus-verdict-<platform>.json` — Comparison verdict
- `examples/` — Before/after example pairs

## Interpreting Results

- **Fallback rate** indicates what percentage of fixtures failed conversion. Lower is better.
- **Token reduction** shows estimated byte savings. Higher is better. Values above 50% are typical for content-rich pages.
- **Latency percentiles** measure conversion speed. P50 is the typical case; P95/P99 catch tail latency.
- **Per-fixture details** in the report show exactly which fixtures converted, skipped, or failed.
