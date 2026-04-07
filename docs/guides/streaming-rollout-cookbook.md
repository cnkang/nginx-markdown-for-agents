# Streaming Rollout Cookbook

This guide describes how to safely enable the streaming conversion engine
using a phased rollout approach.  Each phase includes configuration
examples, verification steps, and thresholds for deciding whether to
continue, pause, or roll back.

> **Important:** The 0.5.0 shadow mode is an *engine parity shadow*.  It
> verifies that the streaming engine produces the same Markdown output as
> the full-buffer engine by feeding the same decompressed HTML to both
> engines after full-buffer conversion completes.  Shadow mode does **not**
> exercise chunk boundaries, streaming decompression, backpressure, commit
> boundaries, or partial flush — those runtime behaviors are covered by
> the integration tests and chunk-boundary fuzzing in sub-spec #16.
> Shadow mode should **not** be treated as the sole evidence for runtime
> streaming correctness.

## Prerequisites

- NGINX built with the Markdown module and `MARKDOWN_STREAMING_ENABLED`
- Rust converter library compiled with the `streaming` feature
- Metrics endpoint enabled (`markdown_metrics;` in a location block)

## Phase Overview

| Phase | Configuration | Purpose |
|-------|--------------|---------|
| 0 | `engine off` + `shadow on` | Verify engine output parity |
| 1 | `engine on` for one location | Canary on a single path |
| 2 | `engine on` for 10% traffic | Gradual percentage rollout |
| 3 | `engine on` for 50% traffic | Wider rollout |
| 4 | `engine on` for 100% traffic | Full rollout |

## Phase 0: Shadow Mode Verification

### Configuration

```nginx
http {
    server {
        location / {
            markdown_filter on;
            markdown_streaming_engine off;
            markdown_streaming_shadow on;
        }
    }
}
```

### Verification

```bash
# Check shadow runs are happening
curl -s http://localhost/markdown-metrics | grep shadow_total

# Check diff rate
curl -s http://localhost/markdown-metrics | grep shadow_diff_total
```

### Thresholds

| Metric | Continue | Rollback |
|--------|----------|----------|
| `shadow_diff_rate` | ≤ 0.1% | > 1% |
| `shadow_engine_error_rate` | ≤ 0.1% | > 1% |

Shadow latency and memory values in debug logs are for diagnostic
reference only — they are not gating criteria.

### Proceed to Phase 1 when

- `shadow_diff_rate` ≤ 0.1% over at least 1 hour of traffic
- `shadow_engine_error_rate` ≤ 0.1%

## Phase 1: Single Location

### Configuration

```nginx
location /api/ {
    markdown_filter on;
    markdown_streaming_engine on;
}

location / {
    markdown_filter on;
    markdown_streaming_engine off;
}
```

### Verification

```bash
# Streaming success rate
curl -s http://localhost/markdown-metrics | grep streaming

# Check for fallbacks
curl -s http://localhost/markdown-metrics | grep fallback_total

# Check for post-commit errors
curl -s http://localhost/markdown-metrics | grep postcommit_error_total
```

### Thresholds

| Metric | Continue | Pause | Rollback |
|--------|----------|-------|----------|
| Streaming success rate | ≥ 99.9% | < 99.5% | < 99% |
| `fallback_rate` | acceptable | — | — |
| `postcommit_error_rate` | ≤ 0.01% | > 0.01% | > 0.1% |

## Phase 2: 10% Traffic (split_clients)

### Configuration

```nginx
split_clients $request_id $markdown_streaming_engine {
    10%  on;
    *    off;
}

server {
    location / {
        markdown_filter on;
        markdown_streaming_engine $markdown_streaming_engine;
    }
}
```

## Phase 3: 50% Traffic

```nginx
split_clients $request_id $markdown_streaming_engine {
    50%  on;
    *    off;
}
```

## Phase 4: 100% Traffic

```nginx
location / {
    markdown_filter on;
    markdown_streaming_engine on;
    markdown_streaming_shadow off;
}
```

## Alternative Rollout Strategies

### By Host (map-driven)

```nginx
map $host $markdown_streaming_engine {
    default          off;
    canary.example   on;
    staging.example  on;
}
```

### By Header (canary flag)

```nginx
map $http_x_canary $markdown_streaming_engine {
    default  off;
    "true"   on;
}
```

### By User-Agent

```nginx
map $http_user_agent $markdown_streaming_engine {
    default                off;
    "~*GPTBot"             on;
    "~*ClaudeBot"          on;
}
```

## Rollback Procedure

1. Set `markdown_streaming_engine off` in the affected location/server
2. Reload NGINX: `nginx -s reload`
3. Verify streaming metrics stop incrementing:
   ```bash
   # Wait 30 seconds, then check
   curl -s http://localhost/markdown-metrics | grep streaming
   ```
4. Confirm all streaming counters are stable (no new increments)

## When to Continue / Pause / Rollback

| Condition | Action |
|-----------|--------|
| Streaming success rate ≥ 99.9% | Continue to next phase |
| TTFB improvement measurable (large responses) | Continue |
| Streaming failure rate > 0.5% | Pause, investigate |
| `postcommit_error_rate` > 0.01% | Pause, investigate |
| Streaming failure rate > 1% | Rollback |
| `postcommit_error_rate` > 0.1% | Rollback |
| `shadow_diff_rate` > 1% | Rollback (Phase 0) |

## Safety Guarantees

- **No silent fallback**: Every streaming-to-fullbuffer fallback is
  recorded in the decision log and metrics.
- **No silent truncation**: Every post-commit error is recorded with
  `STREAMING_FAIL_POSTCOMMIT` reason code and
  `streaming_postcommit_error_total` counter.
- **No silent semantic drift**: Shadow mode detects output differences
  between engines and records them in `streaming_shadow_diff_total`.

## Reason Code Quick Reference

| Code | Stage | Meaning |
|------|-------|---------|
| `SKIP_STREAMING` | Header filter | Request ineligible, never entered streaming |
| `STREAMING_SKIP_UNSUPPORTED` | Engine selector | Selected streaming but rejected (unsupported capability) |
| `ENGINE_STREAMING` | Engine selector | Streaming path selected |
| `STREAMING_CONVERT` | Body filter | Streaming conversion succeeded |
| `STREAMING_FALLBACK_PREBUFFER` | Pre-Commit | Fell back to full-buffer (Rust fallback signal) |
| `STREAMING_PRECOMMIT_FAILOPEN` | Pre-Commit | Error + fail-open policy |
| `STREAMING_PRECOMMIT_REJECT` | Pre-Commit | Error + fail-closed policy |
| `STREAMING_FAIL_POSTCOMMIT` | Post-Commit | Error after headers sent |
| `STREAMING_BUDGET_EXCEEDED` | Any | Memory budget exceeded (auxiliary, terminal state varies) |
| `STREAMING_SHADOW` | Shadow mode | Shadow comparison ran |

## Common Scenarios

**Scenario: High fallback rate in Phase 1**
- Check `STREAMING_FALLBACK_PREBUFFER` in logs
- This indicates the Rust engine detected unsupported content features
- Expected for some content types; monitor but don't necessarily rollback

**Scenario: Post-commit errors appearing**
- Check `STREAMING_FAIL_POSTCOMMIT` in logs
- These indicate errors after headers were already sent to the client
- Rollback if rate exceeds 0.1%

**Scenario: Shadow diff rate > 0 but < 0.1%**
- Review debug logs for diff details (output length differences)
- Small diffs may be acceptable (whitespace normalization differences)
- Investigate if diffs affect semantic content
