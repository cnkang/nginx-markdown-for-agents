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
# Check shadow runs are happening (JSON format)
curl -s -H 'Accept: application/json' \
  http://localhost/markdown-metrics | \
  python3 -c "import sys,json; d=json.load(sys.stdin)['streaming']; \
  print(f\"shadow_total={d['shadow_total']}\")"

# Check diff rate (JSON format)
curl -s -H 'Accept: application/json' \
  http://localhost/markdown-metrics | \
  python3 -c "import sys,json; d=json.load(sys.stdin)['streaming']; \
  t=d['shadow_total']; df=d['shadow_diff_total']; \
  rate=(df/t*100 if t>0 else 0); \
  print(f\"shadow_diff_total={df} shadow_total={t} diff_rate={rate:.2f}%\")"

# Or use Prometheus format
curl -s -H 'Accept: text/plain; version=0.0.4' \
  http://localhost/markdown-metrics | \
  grep nginx_markdown_streaming_shadow
```

### Thresholds

| Metric | Continue | Rollback |
|--------|----------|----------|
| `shadow_diff_rate` (compute: `shadow_diff_total / shadow_total`) | ≤ 0.1% | > 1% |

Shadow latency values in debug logs are for diagnostic
reference only — they are not gating criteria.

### Proceed to Phase 1 when

- `shadow_diff_rate` ≤ 0.1% over at least 1 hour of traffic
- No unexpected streaming errors in debug logs

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
# Streaming success rate (JSON format)
curl -s -H 'Accept: application/json' \
  http://localhost/markdown-metrics | \
  python3 -c "import sys,json; d=json.load(sys.stdin)['streaming']; \
  print(f\"succeeded={d['succeeded_total']} failed={d['failed_total']}\")"

# Check for fallbacks (JSON streaming object)
curl -s -H 'Accept: application/json' \
  http://localhost/markdown-metrics | \
  python3 -c "import sys,json; d=json.load(sys.stdin)['streaming']; \
  print(f\"fallback={d['fallback_total']}\")"

# Check for post-commit errors (JSON streaming object)
curl -s -H 'Accept: application/json' \
  http://localhost/markdown-metrics | \
  python3 -c "import sys,json; d=json.load(sys.stdin)['streaming']; \
  print(f\"postcommit_error={d['postcommit_error_total']}\")"

# Or use Prometheus format
curl -s -H 'Accept: text/plain; version=0.0.4' \
  http://localhost/markdown-metrics | \
  grep nginx_markdown_streaming_total
```

### Thresholds

| Metric | Continue | Pause | Rollback |
|--------|----------|-------|----------|
| Success rate (`succeeded_total / (succeeded_total + failed_total)`) | ≥ 99.9% | < 99.5% | < 99% |
| `fallback_total` growth rate | acceptable | — | — |
| Post-commit error rate (`postcommit_error_total / succeeded_total`) | ≤ 0.01% | > 0.01% | > 0.1% |

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
   # Wait 30 seconds, then check (JSON format)
   curl -s -H 'Accept: application/json' \
     http://localhost/markdown-metrics | \
     python3 -c "import sys,json; d=json.load(sys.stdin)['streaming']; \
     print(json.dumps(d, indent=2))"
   ```
4. Confirm all streaming counters are stable (no new increments)

## When to Continue / Pause / Rollback

| Condition | Action |
|-----------|--------|
| Success rate ≥ 99.9% (`streaming.succeeded_total / (succeeded + failed)`) | Continue to next phase |
| TTFB improvement measurable (large responses) | Continue |
| Failure rate > 0.5% (`streaming.failed_total / requests_total`) | Pause, investigate |
| Post-commit error rate > 0.01% (`streaming.postcommit_error_total / succeeded_total`) | Pause, investigate |
| Failure rate > 1% | Rollback |
| Post-commit error rate > 0.1% | Rollback |
| Shadow diff rate > 1% (`streaming.shadow_diff_total / shadow_total`) | Rollback (Phase 0) |

## Safety Guarantees

- **No silent fallback**: Every streaming-to-fullbuffer fallback is
  recorded in the decision log and metrics.
- **No silent truncation**: Every post-commit error is recorded with
  `STREAMING_FAIL_POSTCOMMIT` reason code and
  `streaming.postcommit_error_total` counter (JSON path:
  `streaming.postcommit_error_total`; Prometheus:
  `nginx_markdown_streaming_total{result="postcommit_error"}`).
- **No silent semantic drift**: Shadow mode detects output differences
  between engines and records them in `streaming.shadow_diff_total`
  (Prometheus: `nginx_markdown_streaming_shadow_diff_total`).

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
