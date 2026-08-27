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

The blocking 0.9.2 release gate also binds both
`module_benchmark.git_commit` and
`baseline_policy.source_git_commit` to the current full 40-character
`git HEAD`. A stale baseline is release evidence failure, not a candidate
for silent reuse or metadata-only normalization; run the module-enabled
canonical benchmark again at the frozen candidate commit.

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
  latency, TTFB, and TTLB upward or raise them. RPS must never increase and
  latency/TTFB/TTLB must never decrease to make evidence look better.

Do not fabricate or improve measured evidence. Conservative normalization
may adjust latency and throughput only. Path, fallback, output, memory,
`input_bytes`, and environment evidence must remain verbatim.

Retain the raw workflow artifact and record its run, source Git commit,
adjustment rule, person or reason, and adjustment date in `baseline_policy`.
The checked-in 0.9.1 snapshot is a verbatim eight-scenario run from commit
`0847c287c1b744a3f80b7b7fe6ccf3e897223377`, measured by canonical workflow
run `32398065263/attempts/1`. Its raw artifact has SHA-256
`006bb70b6029a639068c50b532d1c8119c8e54b96775ff8c73e62223ba8cadc4`.
The checked-in 0.9.2 snapshot is a verbatim run from commit
`712c5300228373677c367e6c0d83ac9d729ff63b`, measured by canonical workflow
run `32394531042/attempts/1`; its raw artifact has SHA-256
`52ed157609da6fc6ecd9e3b2b64a3f1e524c2227c27b44ea62e0c4f3237e63b2`.
Both runs use the NGINX 1.30.4 environment. The validator requires each
source commit, run attempt, timestamp, retained raw artifact, and digest to
remain mutually consistent.

The 0.9.2 release path regenerates the checked-out 0.9.2 snapshot in the
release container at the candidate SHA before running the blocking gate. This
is necessary because a Git commit cannot contain a baseline whose provenance
hash is the hash of that same commit. The checked-in copy remains auditable
context; the candidate-bound workflow artifact is the release comparison
evidence.

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
  `input_bytes`, metric keys, and `decompression_coverage`) must remain
  identical to the raw report. The policy adjustment ledger must exactly
  describe every changed adjustable metric and its delta. The finalizer
  records the adjustment rule, reason, and date. The validator
  machine-verifies the full relationship against the raw artifact.

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

## Measurement Commit Durability

Every finalized baseline records the commit its benchmark ran against, and
that commit always predates the commit that records it. A Git commit cannot
contain a baseline whose provenance hash is the hash of that same commit, so
a checked-in baseline always points backwards in history. The binding stays
verifiable only while some ref still reaches that older commit.

A history rewrite breaks that reachability. During the 2026-08-20/21
evidence churn, rebase and force-push moved the measurement commits behind
`module-baseline-091.json` and `module-baseline-092.json` out of every
branch and tag. The objects survived on the server and stayed fetchable by
explicit SHA, but no ref reached them. A clean clone could not obtain them,
so the provenance gate failed on every clone topology, not only on shallow
checkouts.

Running the benchmark again does not remove this failure mode. It only
swaps in another anchor that happens to be reachable at that moment, and the
next rewrite strips that anchor the same way. A durable measurement ref is
the mechanism that removes the failure class.

### Naming and lifecycle policy

- **Namespace**: `refs/tags/perf-baseline/<baseline-stem>`, where
  `<baseline-stem>` is the finalized baseline file name without `.json`.
  This namespace stays disjoint from the release tag namespace
  `v<MAJOR>.<MINOR>.<PATCH>`, so release tooling, formula resolution, and
  the tag release gates never select a measurement ref.
- **Tag object**: annotated, never lightweight. The tag message carries the
  audit record.
- **Immutability**: a pushed `perf-baseline/*` ref is immutable. Never move
  it and never delete it. Correct a tag message only while the tag stays
  local and unpushed.
- **Creation trigger**: create a ref when the finalizer produces a new
  canonical baseline and no existing immutable or protected ref already
  anchors its measurement commit. A mutable branch, including `main`, does
  not count as a durable anchor; a commit reached only through such a branch
  still receives the dedicated `perf-baseline/*` ref.
- **Message contents**: the message must record the baseline stem,
  `source_run`, and `source_artifact_sha256`, so the ref audits itself
  without opening the baseline file. The two live tags carry a superset of
  that minimum. They add `baseline_file`, `source_git_commit`,
  `source_artifact`, and an explicit statement that the ref turns immutable
  once pushed.

### Current anchors

| Baseline stem | Measurement commit | Anchoring ref |
|---------------|--------------------|---------------|
| `module-baseline-091` | `0847c287c1b744a3f80b7b7fe6ccf3e897223377` | `refs/tags/perf-baseline/module-baseline-091` |
| `module-baseline-092` | `712c5300228373677c367e6c0d83ac9d729ff63b` | `refs/tags/perf-baseline/module-baseline-092` |
| `module-baseline-brotli-091` | `9734d12e4d1c4fb6e6e25852badf0ee614b170c8` | `refs/heads/main` and `refs/tags/v0.9.1` |

`module-baseline-brotli-091` is the live example of the creation trigger.
Its measurement commit already sits in `main` and in the `v0.9.1` release
tag, so the policy forbids a dedicated `perf-baseline/*` ref for it. The
anchoring requirement still holds for it: an archival `verbatim_import`
pack receives no exemption.

### How the anchor check resolves a commit

`tools/harness/detect_baseline_hand_edit.py` decides anchoring in
`repo_commit_anchored(sha, stem)` in this order:

1. **Canonical fast path**: `refs/tags/perf-baseline/<stem>` resolves to
   exactly `sha`. This runs in constant time and proves the per-baseline
   binding.
2. **Fallback**: `git for-each-ref --contains <sha>` finds a ref whose
   history contains the object. This proves reachability from some ref, and
   nothing more. It cannot prove the per-baseline binding, because
   `712c5300...` is an ancestor of `0847c287...`, so a `--contains` query
   for the 0.9.2 commit also matches the 0.9.1 tag. The detector therefore
   never reports the matched refname as the anchor of that stem.
3. **Indeterminate**: a shallow checkout, or a checkout that carries no
   tags, cannot decide reachability. The detector prints an explicit SKIP
   and still records a finding, so it fails closed instead of accepting an
   anchor that a full clone would reject.
4. **Unanchored**: the object exists but no ref reaches it. The finding
   names the ref to create, `perf-baseline/<stem>`, and points back to this
   section.

### Continuous integration

The `harness-tooling` job runs a `Prepare baseline measurement provenance`
step ahead of `make harness-security-checks`. The step first fetches
`+refs/tags/perf-baseline/*:refs/tags/perf-baseline/*`. It then proves each
`source_git_commit` present with `git cat-file -e`, and exits 1 with the
missing SHA plus the baselines that need it.

Fetching the anchor tags first is what keeps the step sound. An object-only
fetch by explicit SHA lands the commit as a second shallow root that no ref
reaches, which satisfies presence but never anchoring. The step only makes
objects queryable. The verdict stays with the detector, which re-checks
presence, anchoring, and artifact digests on its own.

## Environment Consistency

A single baseline file must never mix scenarios measured under different
environments. Every scenario in `module-baseline-091.json` and
`module-baseline-092.json` shares the top-level canonical environment
(`linux-x86_64`, `ab`, NGINX 1.30.4). When
the finalizer merges a scenario from a different run,
`baseline_policy.scenario_sources` must declare structured `platform`,
`load_generator`, and `nginx_version` fields for it, and the evidence gate
rejects the baseline unless each of them matches the top-level environment.
A scenario measured in a diverging environment must instead live in its own
environment-truthful baseline file and must never enter comparisons against
reports from the canonical environment.

The active canonical run includes `brotli-streaming-first` under NGINX 1.30.4
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
and uploads both the finalized and raw files as retained artifacts. The
release-package workflow repeats the generation in its candidate runtime
before the blocking comparison; verify
`baseline_policy.source_artifact_sha256`, the raw-artifact digest, after
either materialization.

## Baseline Files

Each platform has a dedicated baseline file:
- `darwin-arm64.json` - macOS on Apple Silicon
- `linux-x86_64.json` - Linux on x86_64
- `darwin-x86_64.json` - macOS on Intel
- `corpus-baseline.json` - Linux x86_64 corpus quality and latency baseline
- `module-baseline-091.json` - Linux x86_64 module-level 0.9.1 evidence
  baseline (canonical NGINX 1.30.4 environment)
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
  baseline. The checked-in copy is an auditable snapshot; the nightly and
  release workflows generate a candidate-bound copy at the exact checkout
  before running the blocking 0.9.2 gate.
- `module-baseline-092-raw.json` - retained raw report for the 0.9.2
  baseline (provenance artifact referenced by `module-baseline-092.json`,
  regenerated by the local gate container or the nightly workflow)
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
| 0.9.2 | 2026-08-22 | Kang | Added Measurement Commit Durability: durable measurement ref naming and lifecycle policy, current anchors, anchor resolution order, CI provenance preparation |
| 0.9.2 | 2026-08-08 | Kang | Pointed active baseline references at module-baseline-092 |
| 0.9.1 | 2026-07-28 | Codex | Documented strict raw-artifact provenance, exact historical-exception binding, and conservative normalization rules. |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, added mermaid diagrams where applicable, verified directive accuracy against code, added update tracking section |
