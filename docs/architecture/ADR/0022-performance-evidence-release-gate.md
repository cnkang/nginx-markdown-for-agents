# ADR-0022: 0.9.1 Performance Evidence Release Gate

## Status

Accepted

## Context

0.9.1 introduces significant performance optimizations (zero-copy, streaming decompression, copy reduction) that could cause regressions in latency or memory stability. Release safety requires module-level benchmark evidence rather than isolated microbenchmarks.

## Decision

Add `evidence_gate.py` as the formal release gate for 0.9.1.

### Operational Modes
- **Non-blocking mode** (`make perf-evidence-check`): Used for PRs to provide visibility.
- **Blocking mode** (`make release-gates-check-091`): Required for release tags.

### Performance Thresholds
The latency, TTFB, and memory thresholds are relative to the recorded 0.9.0
baseline. The streaming fallback rate is an absolute cap, not a percentage
relative to a baseline:
- **p50 latency**: ≤ +10%
- **p95 latency**: ≤ +15%
- **TTFB**: ≤ +10%
- **Streaming fallback rate**: ≤ 5% absolute. Numerator: streaming responses that emit `fallback_to_buffered` or `fallback_to_full_buffer`. Denominator: responses that actually attempted streaming (selected the streaming engine via `markdown_streaming auto|force` and passed the pre-attempt hard gates). Excluded: responses that never attempted streaming — routed directly to full-buffer by `markdown_streaming off`, profile, decision-chain `not_eligible`, or a pre-attempt hard gate such as `markdown_cache_validation full` (which blocks streaming before any attempt). Measurement window: per benchmark scenario run. Aggregation: per streaming-attempted response (not per chunk or attempt). The window combines results by summing numerator and denominator across all iterations within a scenario before computing the rate.
- **Memory slope**: ≤ +20%

### Tooling
- **Benchmark Harness**: `tools/perf/run_module_benchmark.sh` exercises the full NGINX request lifecycle across 8 representative scenarios.
- **Streaming-path evidence**: The `streaming-first` scenario uses the tracked
  large streaming-safe fixture whose metadata declares no expected fallback.
  The mock upstream emits bounded 16 KiB HTTP chunks, and the scenario disables
  proxy buffering over HTTP/1.1 so path hits, TTFB, and zero-copy counters
  represent incremental processing rather than a single buffered body.
- **Compressed streaming decompression evidence**: The `gzip-streaming-first`,
  `deflate-streaming-first`, and `brotli-streaming-first` scenarios exercise
  their respective streaming decompression paths. Each uses the large fixture
  with `streaming_first` profile and chunked transfer, confirming that
  `decompression_streaming_total > 0` per codec.  The `gzip-large` scenario
  separately verifies the full-buffer gzip decompression path
  (`decompression_fullbuffer_total > 0`).
- **Diagnostics**: `tools/perf/doctor_advice.py` provides operator diagnostics when thresholds get breached.

## Consequences

### Positive Consequences
- Ensures no performance regression ships to production.
- Provides an evidence pack (benchmark tiers, decompression coverage, fallback rate, memory slope) for every release.

### Negative Consequences
- Blocking RC/release-tag mode requires a module-enabled `NGINX_BIN`. The
  development-only skip is not release evidence.
- Increases the time required for the final release check.

### Toolchain Security
- All external helpers executed by the benchmark harness and the evidence gate
  (`bash`, `git`, `curl`, `python3`, `ps`, `awk`, `cat`, `cp`, `cut`, `head`, `mkdir`,
  `mktemp`, `rm`, `sleep`, `tr`, `uname`, `wc`, `date`, and the load generator)
  pass through a trusted-root resolver that returns approved absolute paths.
  The harness and gate never invoke a bare PATH-shadowable executable, so
  evidence cannot come from a hijacked binary.  The toolchain paths appear in
  the report and evidence pack.

## Alternatives Considered
- **Microbenchmarks (Criterion/etc)**: Rejected because they fail to capture NGINX's interaction with the OS network stack and pool management.
- **Manual Verification**: Rejected as it is non-repeatable and prone to operator error.

## References
- [ADR-0019: 0.9.0 Production Readiness Release Gate Framework](0019-090-production-readiness-release-gates.md)
- `tools/perf/` directory

## Date

2026-07-08

## Authors

Kang

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.1 | 2026-07-19 | Kang | Added Brotli streaming evidence; 8 scenarios total with per-codec path and response-equivalence checks |
| 0.9.1 | 2026-07-16 | Kang | Added gzip-streaming-first and deflate-streaming-first scenarios; promoted gzip-large to critical; 7 scenarios total with per-codec decompression path evidence |
| 0.9.1 | 2026-07-14 | Codex | Required a large non-fallback, genuinely chunked streaming-first scenario and real module evidence for blocking release validation |
| 0.9.1 | 2026-07-08 | Kang | Initial ADR for 0.9.1 Performance Evidence Gate |
