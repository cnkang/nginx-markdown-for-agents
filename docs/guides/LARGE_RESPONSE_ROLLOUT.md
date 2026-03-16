# Large Response Incremental Path — Rollout and Rollback Playbook

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Phased Rollout Plan](#phased-rollout-plan)
4. [Monitoring During Rollout](#monitoring-during-rollout)
5. [Rollback Procedure](#rollback-procedure)
6. [Rollback Trigger Conditions](#rollback-trigger-conditions)
7. [Post-Rollback Verification](#post-rollback-verification)
8. [Appendix: Quick Reference](#appendix-quick-reference)

---

## Overview

This playbook describes how to gradually enable the large-response incremental processing path controlled by the `markdown_large_body_threshold` directive. The rollout follows a phased approach — single location, single server, then global — with verification checkpoints at each stage and a one-command rollback that requires no code changes.

### Target Audience

- Site Reliability Engineers (SREs)
- DevOps Engineers
- System Administrators

### Related Documents

- [LARGE_RESPONSE_DESIGN.md](../architecture/LARGE_RESPONSE_DESIGN.md) — architecture and design rationale
- [REQUEST_LIFECYCLE.md](../architecture/REQUEST_LIFECYCLE.md) — request flow and path selection logic
- [OPERATIONS.md](OPERATIONS.md) — general operational guide and metrics reference
- [CONFIGURATION.md](CONFIGURATION.md) — full directive reference

---

## Prerequisites

Before starting the rollout, verify:

1. **Rust `incremental` feature is compiled in.** The NGINX module binary must be built with the Rust converter's `incremental` feature enabled. If the feature is not compiled but a threshold is configured, the module logs a warning and falls back to the full-buffer path — the incremental path will not activate.

   ```bash
   # Build with incremental support
   cd components/rust-converter
   cargo build --release --features incremental
   ```

2. **Metrics endpoint is accessible.** The `markdown_metrics` location must be configured and reachable from your monitoring system.

   ```bash
   curl http://localhost/markdown-metrics
   # Verify output includes "fullbuffer_path_hits" and "incremental_path_hits"
   ```

3. **Baseline metrics are recorded.** Capture current conversion error rate, P95 latency, and memory usage before enabling the threshold. These serve as the comparison baseline for each rollout phase.

   ```bash
   # Record baseline snapshot (JSON)
   curl -s -H "Accept: application/json" http://localhost/markdown-metrics > /tmp/baseline-metrics.json
   ```

4. **NGINX configuration is tested and backed up.**

   ```bash
   nginx -t
   cp /usr/local/nginx/conf/nginx.conf /usr/local/nginx/conf/nginx.conf.pre-rollout
   ```

---

## Phased Rollout Plan

### Phase 1: Single Location

Enable the incremental path for one low-risk location to validate basic functionality.

**Configuration change:**

```nginx
# Pick a low-traffic, non-critical location
location /docs/internal {
    markdown_filter on;
    markdown_large_body_threshold 512k;
}
```

**Apply:**

```bash
nginx -t && nginx -s reload
```

**Verification checkpoint (wait 15–30 minutes):**

| Check | Command | Expected |
|-------|---------|----------|
| Config syntax valid | `nginx -t` | `syntax is ok` |
| Incremental path hits increasing | See [Monitoring](#monitoring-during-rollout) | `incremental_path_hits > 0` for the target location |
| Conversion error rate stable | See [Monitoring](#monitoring-during-rollout) | Error rate delta < 0.1% vs baseline |
| No new errors in log | `tail -200 /var/log/nginx/error.log \| grep markdown` | No new `conversion failed` entries |
| P95 latency stable | See [Monitoring](#monitoring-during-rollout) | No significant increase vs baseline |

**Proceed to Phase 2 only after all checks pass.**

---

### Phase 2: Single Server

Expand to all locations within one server block.

**Configuration change:**

```nginx
server {
    listen 80;
    server_name docs.example.com;

    # Server-level threshold — applies to all locations in this server
    markdown_large_body_threshold 512k;

    location /docs {
        markdown_filter on;
    }

    location /api/render {
        markdown_filter on;
    }

    # Locations that should NOT use incremental path can override:
    # location /legacy {
    #     markdown_large_body_threshold off;
    # }
}
```

**Apply:**

```bash
nginx -t && nginx -s reload
```

**Verification checkpoint (wait 30–60 minutes):**

| Check | Command | Expected |
|-------|---------|----------|
| Config syntax valid | `nginx -t` | `syntax is ok` |
| Path hit ratio reasonable | See [Monitoring](#monitoring-during-rollout) | `incremental_path_hits` growing proportionally to large-response traffic |
| Conversion error rate stable | See [Monitoring](#monitoring-during-rollout) | Error rate delta < 0.1% vs baseline |
| No worker crashes | `grep "worker process" /var/log/nginx/error.log` | No unexpected restarts |
| Memory usage stable | `ps aux \| grep "nginx: worker" \| awk '{print $6/1024 " MB"}'` | No significant increase vs baseline |
| P95 latency stable | See [Monitoring](#monitoring-during-rollout) | No significant increase vs baseline |

**Proceed to Phase 3 only after all checks pass.**

---

### Phase 3: Global

Enable the threshold at the `http` block level for all servers.

**Configuration change:**

```nginx
http {
    # Global threshold — all servers and locations inherit this
    markdown_large_body_threshold 512k;

    # ... existing server blocks ...
}
```

**Apply:**

```bash
nginx -t && nginx -s reload
```

**Verification checkpoint (wait 1–2 hours):**

| Check | Command | Expected |
|-------|---------|----------|
| Config syntax valid | `nginx -t` | `syntax is ok` |
| Path routing distribution correct | See [Monitoring](#monitoring-during-rollout) | Both `fullbuffer_path_hits` and `incremental_path_hits` incrementing as expected |
| Conversion error rate stable | See [Monitoring](#monitoring-during-rollout) | Error rate delta < 0.1% vs baseline |
| No worker crashes | `grep "worker process" /var/log/nginx/error.log` | No unexpected restarts |
| Memory peak reduced for large docs | See [Monitoring](#monitoring-during-rollout) | Memory usage for large responses lower than full-buffer baseline |
| P95 latency stable | See [Monitoring](#monitoring-during-rollout) | No significant increase vs baseline |

---

## Monitoring During Rollout

### Key Metrics

Monitor these four metrics throughout every rollout phase:

#### 1. Path Hit Ratio

Tracks how traffic is distributed between the full-buffer and incremental paths.

```bash
# Snapshot (JSON)
curl -s -H "Accept: application/json" http://localhost/markdown-metrics | \
  python3 -c "
import sys, json
m = json.load(sys.stdin)
fb = m['fullbuffer_path_hits']
inc = m['incremental_path_hits']
total = fb + inc
print(f'Full-buffer: {fb} ({fb*100//max(total,1)}%)')
print(f'Incremental: {inc} ({inc*100//max(total,1)}%)')
"
```

If using Prometheus (via adapter):

```promql
# Incremental path ratio
rate(incremental_path_hits[5m]) / (rate(fullbuffer_path_hits[5m]) + rate(incremental_path_hits[5m]))
```

#### 2. Conversion Error Rate

The primary rollback trigger. Must stay below 0.1% delta from baseline.

```bash
# Compute current error rate
curl -s -H "Accept: application/json" http://localhost/markdown-metrics | \
  python3 -c "
import sys, json
m = json.load(sys.stdin)
attempted = m['conversions_attempted']
failed = m['conversions_failed']
rate = (failed / max(attempted, 1)) * 100
print(f'Error rate: {rate:.3f}% ({failed}/{attempted})')
"
```

If using Prometheus (via adapter):

```promql
# Conversion error rate (5-minute window)
rate(conversions_failed[5m]) / rate(conversions_attempted[5m]) * 100
```

#### 3. P95 Latency

Use the latency bucket counters from the metrics endpoint to approximate percentiles.

```bash
# Latency distribution snapshot
curl -s -H "Accept: application/json" http://localhost/markdown-metrics | \
  python3 -c "
import sys, json
m = json.load(sys.stdin)
b = m['conversion_latency_buckets']
total = sum(b.values())
print(f'<= 10ms:   {b[\"le_10ms\"]}  ({b[\"le_10ms\"]*100//max(total,1)}%)')
print(f'<= 100ms:  {b[\"le_100ms\"]}  ({b[\"le_100ms\"]*100//max(total,1)}%)')
print(f'<= 1000ms: {b[\"le_1000ms\"]} ({b[\"le_1000ms\"]*100//max(total,1)}%)')
print(f'>  1000ms: {b[\"gt_1000ms\"]} ({b[\"gt_1000ms\"]*100//max(total,1)}%)')
"
```

If using Prometheus (via adapter):

```promql
# Approximate P95 from latency buckets
histogram_quantile(0.95, rate(conversion_latency_bucket[5m]))
```

#### 4. Memory Peak

Monitor NGINX worker memory during the rollout, especially when processing large documents.

```bash
# Current worker memory (RSS in MB)
ps aux | grep "nginx: worker" | awk '{printf "PID %s: %.1f MB\n", $2, $6/1024}'

# Continuous monitoring (every 10 seconds)
watch -n 10 'ps aux | grep "nginx: worker" | awk "{printf \"PID %s: %.1f MB\n\", \$2, \$6/1024}"'
```

For detailed memory profiling of large-document processing, use the memory observer script:

```bash
# Observe memory during a large-document conversion
tools/perf/memory_observer.sh --pid <nginx_worker_pid> --interval 100 --output /tmp/memory-rollout.json
```

### Monitoring Dashboard Checklist

If using Grafana or a similar tool, create a rollout dashboard with these panels:

- Incremental path hit ratio (target: matches expected large-response traffic proportion)
- Conversion error rate with 0.1% threshold line
- P95 conversion latency with baseline reference line
- Worker memory RSS (per-worker)
- `conversions_failed` rate (absolute, for alerting)

---

## Rollback Procedure

Rollback requires **no code changes** — only a configuration update and reload.

### One-Command Rollback

**Step 1: Disable the threshold.**

Set `markdown_large_body_threshold off` at the same scope where it was enabled.

For a global rollout (Phase 3), edit the `http` block:

```nginx
http {
    markdown_large_body_threshold off;
    # ...
}
```

For a server-level rollout (Phase 2):

```nginx
server {
    markdown_large_body_threshold off;
    # ...
}
```

For a location-level rollout (Phase 1):

```nginx
location /docs/internal {
    markdown_large_body_threshold off;
    # ...
}
```

**Step 2: Test and reload.**

```bash
nginx -t && nginx -s reload
```

This immediately routes all requests back to the full-buffer path. In-flight requests on the incremental path complete normally; new requests use the full-buffer path.

### Emergency Rollback (Single Command)

If you need to roll back without editing the config file (e.g., during an incident), restore the pre-rollout backup:

```bash
cp /usr/local/nginx/conf/nginx.conf.pre-rollout /usr/local/nginx/conf/nginx.conf && \
nginx -t && nginx -s reload
```

---

## Rollback Trigger Conditions

Initiate an **immediate rollback** if any of the following conditions are observed:

| Condition | Threshold | Action |
|-----------|-----------|--------|
| Conversion error rate increase | > 0.1% above baseline | **Immediate rollback** |
| NGINX worker crashes | Any unexpected worker restart | **Immediate rollback** |
| P95 latency spike | > 2x baseline for 5+ minutes | Investigate; rollback if not resolved in 10 minutes |
| Memory usage spike | Worker RSS > 2x baseline | Investigate; rollback if not resolved in 10 minutes |
| New error patterns in log | New `conversion failed` categories | Investigate; rollback if cause is unclear |

### Automated Alert Example

```bash
#!/bin/bash
# rollout_watchdog.sh — run periodically (e.g., every 60s via cron)
# Checks conversion error rate and triggers alert if above threshold.

METRICS_URL="${METRICS_URL:-http://localhost/markdown-metrics}"
BASELINE_ERROR_RATE="${BASELINE_ERROR_RATE:-0.0}"  # Set to pre-rollout value
THRESHOLD_DELTA="0.1"

METRICS=$(curl -s -H "Accept: application/json" "$METRICS_URL")
ATTEMPTED=$(echo "$METRICS" | python3 -c "import sys,json; print(json.load(sys.stdin)['conversions_attempted'])")
FAILED=$(echo "$METRICS" | python3 -c "import sys,json; print(json.load(sys.stdin)['conversions_failed'])")

if [ "$ATTEMPTED" -eq 0 ]; then
    echo "No conversions yet, skipping check"
    exit 0
fi

CURRENT_RATE=$(python3 -c "print(round($FAILED / $ATTEMPTED * 100, 3))")
DELTA=$(python3 -c "print(round($CURRENT_RATE - $BASELINE_ERROR_RATE, 3))")

if python3 -c "exit(0 if $DELTA > $THRESHOLD_DELTA else 1)"; then
    echo "ALERT: Error rate delta ${DELTA}% exceeds threshold ${THRESHOLD_DELTA}%"
    echo "Current: ${CURRENT_RATE}%, Baseline: ${BASELINE_ERROR_RATE}%"
    echo "ACTION: Initiate rollback per LARGE_RESPONSE_ROLLOUT.md"
    # Add your alerting integration here (PagerDuty, Slack, etc.)
    exit 1
fi

echo "OK: Error rate delta ${DELTA}% within threshold"
exit 0
```

---

## Post-Rollback Verification

After rolling back, verify the system has returned to its pre-rollout state:

### Step 1: Confirm Configuration

```bash
# Verify the directive is off
nginx -T | grep markdown_large_body_threshold
# Expected: "markdown_large_body_threshold off;" or no output (default is off)
```

### Step 2: Confirm Path Routing

```bash
# All new traffic should go to full-buffer path
# Watch incremental_path_hits — it should stop increasing
curl -s -H "Accept: application/json" http://localhost/markdown-metrics | \
  python3 -c "
import sys, json
m = json.load(sys.stdin)
print(f'Full-buffer hits: {m[\"fullbuffer_path_hits\"]}')
print(f'Incremental hits: {m[\"incremental_path_hits\"]}')
"
# Wait 60 seconds, run again — incremental_path_hits should not change
```

### Step 3: Confirm Error Rate Recovery

```bash
# Monitor error rate for 10–15 minutes after rollback
# It should return to baseline levels
curl -s -H "Accept: application/json" http://localhost/markdown-metrics | \
  python3 -c "
import sys, json
m = json.load(sys.stdin)
attempted = m['conversions_attempted']
failed = m['conversions_failed']
rate = (failed / max(attempted, 1)) * 100
print(f'Error rate: {rate:.3f}%')
"
```

### Step 4: Confirm No Residual Issues

```bash
# Check for new errors in the last 15 minutes
tail -500 /var/log/nginx/error.log | grep markdown | grep -i "error\|fail\|crash"

# Verify worker processes are stable
ps aux | grep "nginx: worker" | awk '{printf "PID %s: uptime check — RSS %.1f MB\n", $2, $6/1024}'
```

### Step 5: Document the Rollback

Record the following for post-incident review:

- Timestamp of rollback
- Phase at which rollback was triggered
- Trigger condition observed (error rate, latency, etc.)
- Metrics snapshot before and after rollback
- Any error log entries related to the issue

---

## Appendix: Quick Reference

### Directive Summary

| Directive | Syntax | Default | Context |
|-----------|--------|---------|---------|
| `markdown_large_body_threshold` | `off \| <size>` | `off` | http, server, location |

When set to `off` (default), all requests use the full-buffer path. Behavior is identical to a build without this feature.

### Rollback Cheat Sheet

```bash
# 1. Edit config: set "markdown_large_body_threshold off;" at the relevant scope
# 2. Test and reload:
nginx -t && nginx -s reload
# 3. Verify: incremental_path_hits stops increasing
```

### Metrics Endpoint Fields for Rollout Monitoring

| JSON Field | Description |
|------------|-------------|
| `fullbuffer_path_hits` | Requests routed to the full-buffer (existing) path |
| `incremental_path_hits` | Requests routed to the incremental processing path |
| `conversions_attempted` | Total conversion attempts |
| `conversions_failed` | Total failed conversions |
| `conversion_latency_buckets` | Latency distribution across time-range buckets |

### Key Thresholds

| Metric | Rollback Threshold |
|--------|--------------------|
| Conversion error rate increase | > 0.1% above baseline |
| Worker crashes | Any unexpected restart |
| P95 latency | > 2x baseline sustained 5+ min |
| Worker memory | > 2x baseline |
