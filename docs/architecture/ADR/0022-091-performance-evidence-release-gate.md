# ADR-0022: 0.9.1 Performance Evidence Release Gate

## Status

Accepted

## Context

0.9.1 introduces significant performance optimizations (zero-copy, streaming decompression, copy reduction) that could cause regressions in latency or memory stability. Release safety requires module-level benchmark evidence rather than isolated microbenchmarks.

## Decision

Add `evidence_gate.py` as the formal release gate for 0.9.1.

### Operational Modes
- **Non-blocking mode** (`make perf-evidence-check`): Used for PRs to provide visibility.
- **Blocking mode** (`make release-gates-check-091`): Required for RC and release tags.

### Performance Thresholds
The gate fails if the following are exceeded compared to the 0.9.0 baseline:
- **p50 latency**: ≤ +10%
- **p95 latency**: ≤ +15%
- **TTFB**: ≤ +10%
- **Streaming fallback rate**: ≤ 5%
- **Memory slope**: ≤ +20%

### Tooling
- **Benchmark Harness**: `tools/perf/run_module_benchmark.sh` exercises the full NGINX request lifecycle across 7 representative scenarios.
- **Streaming-path evidence**: The `streaming-first` scenario uses the tracked
  large streaming-safe fixture whose metadata declares no expected fallback.
  The mock upstream emits bounded 16 KiB HTTP chunks, and the scenario disables
  proxy buffering over HTTP/1.1 so path hits, TTFB, and zero-copy counters
  represent incremental processing rather than a single buffered body.
- **Compressed streaming decompression evidence**: The `gzip-streaming-first`
  and `deflate-streaming-first` scenarios exercise the gzip and deflate
  streaming decompression paths respectively.  Each uses the large fixture
  with `streaming_first` profile and chunked transfer, confirming that
  `decompression_streaming_total > 0` per codec.  The `gzip-large` scenario
  separately verifies the full-buffer gzip decompression path
  (`decompression_fullbuffer_total > 0`).
- **Diagnostics**: `tools/perf/doctor_advice.py` provides operator diagnostics when thresholds are breached.

## Consequences

### Positive Consequences
- Ensures no performance regression ships to production.
- Provides an evidence pack (benchmark tiers, decompression coverage, fallback rate, memory slope) for every release.

### Negative Consequences
- Blocking RC/release-tag mode requires a module-enabled `NGINX_BIN`; the
  development-only skip is not release evidence.
- Increases the time required for the final release check.

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
| 0.9.1 | 2026-07-08 | Kang | Initial ADR for 0.9.1 Performance Evidence Gate |
| 0.9.1 | 2026-07-14 | Codex | Required a large non-fallback, genuinely chunked streaming-first scenario and real module evidence for blocking release validation |
| 0.9.1 | 2026-07-16 | Kang | Added gzip-streaming-first and deflate-streaming-first scenarios; promoted gzip-large to critical; 7 scenarios total with per-codec decompression path evidence |
