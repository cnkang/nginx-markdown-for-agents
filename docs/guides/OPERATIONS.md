# NGINX Markdown Filter Module - Operational Guide

## Table of Contents

1. [Overview](#overview)
2. [Monitoring and Metrics](#monitoring-and-metrics)
3. [Troubleshooting](#troubleshooting)
4. [Performance Tuning](#performance-tuning)
5. [Upgrade Procedures](#upgrade-procedures)
6. [Operational Checklists](#operational-checklists)
7. [Runbooks](#runbooks)
8. [Reason Code Reference for Operators](#reason-code-reference-for-operators)
9. [Decision Logging](#decision-logging)

---

## Overview

This operational guide provides procedures for monitoring, troubleshooting, tuning, and maintaining the NGINX Markdown filter module in production environments. It includes metrics to monitor, alert thresholds, diagnostic procedures, and operational checklists.

### Target Audience

- Site Reliability Engineers (SREs)
- DevOps Engineers
- System Administrators
- Operations Teams

### Prerequisites

- NGINX Markdown filter module installed and configured
- Access to NGINX logs and metrics
- Monitoring system configured (Prometheus, Grafana, etc.)
- Basic understanding of NGINX and HTTP

### Maintenance and Validation Notes

- This guide includes example commands, sample metrics, and suggested thresholds. Validate them in staging before production use.
- Metric field names in this guide should match the current metrics endpoint implementation in `components/nginx-module/src/ngx_http_markdown_filter_module.c`.
- The built-in metrics endpoint returns a human-readable plain-text report or a JSON object (selected by `Accept` header); it is not a native Prometheus exposition endpoint.
- Metrics are aggregated in shared memory across workers; a single endpoint response reflects the whole NGINX instance, not just the worker that served `/markdown-metrics`.
- For “why this request took a specific branch,” use [../architecture/REQUEST_LIFECYCLE.md](../architecture/REQUEST_LIFECYCLE.md) and [../architecture/CONFIG_BEHAVIOR_MAP.md](../architecture/CONFIG_BEHAVIOR_MAP.md) alongside this runbook.

### Terminology and Command Conventions

- **Module** means the NGINX Markdown filter module (the NGINX C component).
- **Rust converter** means the Rust HTML-to-Markdown library and FFI layer.
- **Metrics endpoint** means the HTTP endpoint enabled by `markdown_metrics` (plain text or JSON output).
- Metrics commands in this guide often use `http://localhost/markdown-metrics`; replace it with your actual metrics endpoint path when different.

---

## Monitoring and Metrics

### Key Metrics to Monitor

The module exposes metrics via the `markdown_metrics` endpoint. Configure monitoring to collect these metrics regularly.

#### Conversion Metrics

| Metric | Description | Type | Alert Threshold |
|--------|-------------|------|-----------------|
| `conversions_attempted` | Total conversion attempts | Cumulative counter field | N/A (informational) |
| `conversions_succeeded` | Successful conversions | Cumulative counter field | N/A (informational) |
| `conversions_failed` | Failed conversions | Cumulative counter field | > 5% of attempts |
| `failures_conversion` | Failures due to conversion errors | Cumulative counter field | > 2% of attempts |
| `failures_resource_limit` | Failures due to resource limits | Cumulative counter field | > 3% of attempts |
| `failures_system` | Failures due to system errors | Cumulative counter field | > 0.1% of attempts |

#### Performance Metrics

| Metric | Description | Type | Alert Threshold |
|--------|-------------|------|-----------------|
| `conversion_time_sum_ms` | Total conversion time (sum) | Cumulative counter field | N/A (derive average) |
| `conversion_completed` | Completed conversions (`success + failure`) | Derived cumulative field | N/A (informational) |
| `conversion_time_avg_ms` | Average conversion time across completed conversions | Derived field exposed by endpoint | > 200ms sustained |
| `input_bytes` | Total input bytes processed | Cumulative counter field | N/A (informational) |
| `input_bytes_avg` | Average successful input size | Derived field exposed by endpoint | N/A (informational) |
| `output_bytes` | Total output bytes generated | Cumulative counter field | N/A (informational) |
| `output_bytes_avg` | Average successful output size | Derived field exposed by endpoint | N/A (informational) |
| `conversion_latency_buckets.*` | Completed conversions by discrete latency band (each conversion increments exactly one time-range bucket) | Cumulative counter field | Watch for drift toward slower buckets |

#### Decompression Metrics

| Metric | Description | Type | Alert Threshold |
|--------|-------------|------|-----------------|
| `decompressions_attempted` | Responses decompressed before conversion | Cumulative counter field | N/A (informational) |
| `decompressions_succeeded` | Successful decompressions | Cumulative counter field | N/A (informational) |
| `decompressions_failed` | Failed decompressions | Cumulative counter field | > 1% of decompression attempts |
| `decompressions_gzip` | Successful gzip decompressions | Cumulative counter field | N/A (informational) |
| `decompressions_deflate` | Successful deflate decompressions | Cumulative counter field | N/A (informational) |
| `decompressions_brotli` | Successful brotli decompressions | Cumulative counter field | N/A (informational) |

#### Derived Metrics

Calculate these metrics from the raw counters:

Guard denominator-based formulas so dashboards and alerts emit `0`/`null` instead of `Inf` or `NaN` when the corresponding counter is still zero.

```text
# Failure rate
failure_rate = (conversions_failed / conversions_attempted) * 100

# Average conversion time
avg_conversion_time = conversion_completed > 0 ? (conversion_time_sum_ms / conversion_completed) : 0

# Size reduction (proxy for token reduction trend)
size_reduction = ((input_bytes - output_bytes) / input_bytes) * 100

# Success rate
success_rate = (conversions_succeeded / conversions_attempted) * 100

# Decompression success rate
decompression_success_rate = (decompressions_succeeded / decompressions_attempted) * 100
```


### Accessing Metrics

#### Via HTTP Endpoint

```bash
# Optional: override if your metrics endpoint uses a different path
export METRICS_URL="${METRICS_URL:-http://localhost/markdown-metrics}"

# Plain text format
curl "$METRICS_URL"

# JSON format
curl -H "Accept: application/json" "$METRICS_URL"
```

**Example Output (Plain Text):**
```text
Markdown Filter Metrics
-----------------------
Conversions Attempted: 1250
Conversions Succeeded: 1180
Conversions Failed: 70
Conversions Bypassed: 20
Conversions Completed: 1250

Failure Breakdown:
- Conversion Errors: 25
- Resource Limit Exceeded: 40
- System Errors: 5

Performance:
- Total Conversion Time: 45000 ms
- Average Conversion Time: 36 ms
- Total Input Bytes: 52428800
- Average Input Bytes: 44431
- Total Output Bytes: 15728640
- Average Output Bytes: 13329
- Latency <= 10ms: 140
- Latency <= 100ms: 1030
- Latency <= 1000ms: 80
- Latency > 1000ms: 0
```

**Example Output (JSON):**
```json
{
  "conversions_attempted": 1250,
  "conversions_succeeded": 1180,
  "conversions_failed": 70,
  "conversions_bypassed": 20,
  "conversion_completed": 1250,
  "failures_conversion": 25,
  "failures_resource_limit": 40,
  "failures_system": 5,
  "conversion_time_sum_ms": 45000,
  "conversion_time_avg_ms": 36,
  "input_bytes": 52428800,
  "input_bytes_avg": 44431,
  "output_bytes": 15728640,
  "output_bytes_avg": 13329,
  "conversion_latency_buckets": {
    "le_10ms": 140,
    "le_100ms": 1030,
    "le_1000ms": 80,
    "gt_1000ms": 0
  },
  "decompressions_attempted": 210,
  "decompressions_succeeded": 205,
  "decompressions_failed": 5,
  "decompressions_gzip": 160,
  "decompressions_deflate": 25,
  "decompressions_brotli": 20
}
```

---

### Prometheus Integration (via Adapter or Transformation)

The built-in endpoint does not emit native Prometheus exposition format. To integrate with Prometheus, use an adapter/exporter that converts the JSON or plain-text endpoint output into Prometheus metrics.

**Example `prometheus.yml` (scraping an adapter/exporter):**
```yaml
scrape_configs:
  - job_name: 'nginx-markdown'
    static_configs:
      - targets: ['localhost:9105']  # Example adapter endpoint
    metrics_path: '/metrics'
```

**Grafana Dashboard Queries:**

```promql
# Failure rate
rate(conversions_failed[5m]) / rate(conversions_attempted[5m]) * 100

# Average conversion time
rate(conversion_time_sum_ms[5m]) / clamp_min(rate(conversion_completed[5m]), 1)

# Throughput (conversions per second)
rate(conversions_succeeded[1m])

# Size reduction percentage (proxy for token reduction trend)
(1 - (rate(output_bytes[5m]) / rate(input_bytes[5m]))) * 100

# Decompression failure rate
rate(decompressions_failed[5m]) / rate(decompressions_attempted[5m]) * 100
```

---

### Alert Thresholds

Configure alerts based on these thresholds:

#### Critical Alerts

| Condition | Threshold | Action |
|-----------|-----------|--------|
| Failure rate | > 10% for 5 minutes | Page on-call engineer |
| System error rate | > 1% for 5 minutes | Page on-call engineer |
| Conversion time (p95) | > 500ms for 10 minutes | Page on-call engineer |
| Module crash | Worker restart detected | Page on-call engineer |

#### Warning Alerts

| Condition | Threshold | Action |
|-----------|-----------|--------|
| Failure rate | > 5% for 10 minutes | Notify team channel |
| Resource limit rate | > 5% for 10 minutes | Notify team channel |
| Conversion time (p95) | > 200ms for 15 minutes | Notify team channel |
| Memory usage | > 80% of limit | Notify team channel |

#### Informational Alerts

| Condition | Threshold | Action |
|-----------|-----------|--------|
| Conversion time trend | Increasing over 24h | Log for review |
| Failure rate trend | Increasing over 24h | Log for review |
| Token reduction | < 50% average | Log for review |

---

### Log Monitoring

Monitor NGINX error log for markdown-related messages:

```bash
# Watch for errors
tail -f /var/log/nginx/error.log | grep markdown

# Count errors by type
grep markdown /var/log/nginx/error.log | grep -o 'category=[a-z_]*' | sort | uniq -c

# Find slow conversions
grep "conversion time" /var/log/nginx/error.log | awk '{print $NF}' | sort -n | tail -20
```

**Key Log Patterns:**

| Pattern | Severity | Meaning |
|---------|----------|---------|
| `markdown filter: conversion failed, category=conversion_error` | WARN | HTML parsing or Markdown generation failed |
| `markdown filter: conversion failed, category=resource_limit` | WARN | Size limit or timeout exceeded |
| `markdown filter: conversion failed, category=system_error` | ERROR | Memory allocation or system error |
| `markdown filter: conversion succeeded, time=XXXms` | INFO | Successful conversion with timing |


### Health Checks

Implement health checks to verify module functionality:

```bash
#!/bin/bash
# health_check.sh - Verify markdown filter is working

# Test conversion (use GET, not HEAD; HEAD may be misleading through some proxies)
RESPONSE=$(curl -s -H "Accept: text/markdown" http://localhost/health-test)
CONTENT_TYPE=$(curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/health-test | grep -i "^Content-Type:")

if echo "$CONTENT_TYPE" | grep -q "text/markdown"; then
    echo "OK: Markdown conversion working"
    exit 0
else
    echo "CRITICAL: Markdown conversion not working"
    exit 2
fi
```

**Nagios/Icinga Check:**
```bash
# check_markdown_filter.sh
#!/bin/bash

METRICS=$(curl -s "${METRICS_URL:-http://localhost/markdown-metrics}")
FAILED=$(echo "$METRICS" | grep conversions_failed | awk '{print $2}')
ATTEMPTED=$(echo "$METRICS" | grep conversions_attempted | awk '{print $2}')

if [ "$ATTEMPTED" -eq 0 ]; then
    echo "WARNING: No conversion attempts"
    exit 1
fi

FAILURE_RATE=$(echo "scale=2; ($FAILED / $ATTEMPTED) * 100" | bc)

if (( $(echo "$FAILURE_RATE > 10" | bc -l) )); then
    echo "CRITICAL: Failure rate ${FAILURE_RATE}%"
    exit 2
elif (( $(echo "$FAILURE_RATE > 5" | bc -l) )); then
    echo "WARNING: Failure rate ${FAILURE_RATE}%"
    exit 1
else
    echo "OK: Failure rate ${FAILURE_RATE}%"
    exit 0
fi
```

---

## Troubleshooting

The repository CI now includes a non-blocking Darwin/macOS smoke workflow that exercises the shared native-build helper, real-nginx IMS validation, and chunked native smoke. If a runtime issue reproduces only on macOS, start by comparing its workflow logs with the primary Linux `runtime-regressions` job.

### Common Issues and Solutions

#### Issue 1: Conversion Not Occurring

**Symptoms:**
- Clients receive HTML instead of Markdown
- `conversions_attempted` counter not increasing

**Diagnostic Steps:**

1. **Verify Accept header:**
```bash
curl -v -H "Accept: text/markdown" http://localhost/test
# Check request headers in output
```

2. **Check configuration:**
```bash
nginx -T | grep markdown_filter
# Verify markdown_filter is "on"
```

If you use variable-driven enablement (`markdown_filter $some_var;`), also inspect related `map` blocks:
```bash
nginx -T | sed -n '/map \\$http_accept/,/}/p'
nginx -T | sed -n '/map \\$uri/,/}/p'
```

3. **Verify response eligibility:**
```bash
curl -I http://localhost/test
# Check: Status 200, Content-Type: text/html
```

4. **Check NGINX error log:**
```bash
tail -100 /var/log/nginx/error.log | grep markdown
```

**Common Causes:**
- `markdown_filter off` in configuration
- Accept header missing or incorrect
- `markdown_filter` variable map not matching real `Accept` header format
- Extension/path map uses `$request_uri` and fails when query strings are present
- `text/*` path in map enabled but `markdown_on_wildcard` is still `off`
- Response not eligible (non-200 status, non-HTML content)
- Response exceeds `markdown_max_size` limit

**Solutions:**
- Enable filter: `markdown_filter on;`
- Verify client sends `Accept: text/markdown`
- For map-based config, use regex for `Accept` matching and prefer `$uri` for extension checks
- Enable wildcard support when required: `markdown_on_wildcard on;`
- Check backend returns 200 with `Content-Type: text/html`
- Increase size limit if needed: `markdown_max_size 20m;`

---

#### Issue 2: High Failure Rate

**Symptoms:**
- `conversions_failed` counter increasing rapidly
- Alert: Failure rate > 5%

**Diagnostic Steps:**

1. **Check failure categories:**
```bash
curl "${METRICS_URL:-http://localhost/markdown-metrics}" | grep failures
```

2. **Analyze error logs:**
```bash
grep "conversion failed" /var/log/nginx/error.log | tail -50
```

3. **Identify failure patterns:**
```bash
# Group by category
grep "conversion failed" /var/log/nginx/error.log | \
  grep -o 'category=[a-z_]*' | sort | uniq -c

# Find problematic URLs
grep "conversion failed" /var/log/nginx/error.log | \
  grep -o 'uri=[^ ]*' | sort | uniq -c | sort -rn | head -10
```

**Common Causes:**

| Category | Cause | Solution |
|----------|-------|----------|
| `conversion_error` | Malformed HTML | Investigate HTML source, improve error handling |
| `resource_limit` | Size/timeout exceeded | Increase limits or optimize content |
| `system_error` | Memory allocation failed | Increase system resources, check for leaks |

**Solutions:**

- **For conversion_error:**
  - Inspect failing HTML: `curl http://backend/failing-url`
  - Validate HTML: Use W3C validator
  - Report bug if HTML is valid but conversion fails

- **For resource_limit:**
  - Increase limits: `markdown_max_size 20m; markdown_timeout 10s;`
  - Optimize content: Reduce HTML size at source
  - Use fail-open: `markdown_on_error pass;`

- **For system_error:**
  - Check memory: `free -h`, `top`
  - Check disk space: `df -h`
  - Review system logs: `dmesg | tail -50`
  - Restart NGINX if memory leak suspected


#### Issue 3: Slow Conversion Performance

**Symptoms:**
- High conversion latency (> 200ms p95)
- Slow response times for Markdown requests
- Alert: Conversion time > threshold

**Diagnostic Steps:**

1. **Check average conversion time:**
```bash
curl "${METRICS_URL:-http://localhost/markdown-metrics}"
# Calculate only when conversion_completed > 0:
#   conversion_time_sum_ms / conversion_completed
# Otherwise record 0 (or null in your dashboard) to avoid Inf/NaN.
```

2. **Identify slow conversions:**
```bash
grep "conversion succeeded" /var/log/nginx/error.log | \
  grep -o 'time=[0-9]*ms' | \
  sed 's/time=//;s/ms//' | \
  sort -n | tail -20
```

3. **Profile specific URLs:**
```bash
# Time a specific request
time curl -H "Accept: text/markdown" http://localhost/slow-page
```

4. **Check system load:**
```bash
top
iostat -x 1 10
vmstat 1 10
```

**Common Causes:**
- Large HTML documents
- Complex HTML structure (deeply nested)
- High system load
- Insufficient resources

**Solutions:**

1. **Optimize configuration:**
```nginx
# Reduce timeout for faster failure
markdown_timeout 2s;

# Reduce size limit
markdown_max_size 5m;

# Disable optional features
markdown_token_estimate off;
markdown_front_matter off;

# Use simpler Markdown flavor
markdown_flavor commonmark;
```

2. **Enable caching:**
```nginx
proxy_cache_path /var/cache/nginx/markdown keys_zone=markdown_cache:10m;

location / {
    proxy_cache markdown_cache;
    proxy_cache_valid 200 10m;
    proxy_cache_key "$scheme$request_method$host$request_uri$http_accept";
}
```

3. **Optimize content at source:**
- Reduce HTML size
- Simplify HTML structure
- Remove unnecessary elements

4. **Scale horizontally:**
- Add more NGINX workers: `worker_processes auto;`
- Add more backend servers
- Use load balancing

---

#### Issue 4: Memory Issues

**Symptoms:**
- NGINX worker crashes
- Out of memory errors in logs
- System memory exhaustion

**Diagnostic Steps:**

1. **Check memory usage:**
```bash
# Overall system memory
free -h

# NGINX worker memory
ps aux | grep nginx | awk '{sum+=$6} END {print sum/1024 " MB"}'

# Per-worker memory
ps aux | grep "nginx: worker" | awk '{print $6/1024 " MB"}'
```

2. **Monitor memory over time:**
```bash
# Watch memory usage
watch -n 5 'ps aux | grep "nginx: worker" | awk "{print \$6/1024 \" MB\"}"'
```

3. **Check for memory leaks:**
```bash
# Compare memory before and after load test
ps aux | grep "nginx: worker" | awk '{print $6}'
# Run load test
ab -n 10000 -c 10 -H "Accept: text/markdown" http://localhost/
# Check memory again
ps aux | grep "nginx: worker" | awk '{print $6}'
```

**Common Causes:**
- Memory leak in module
- Large responses buffered in memory
- Too many concurrent conversions
- Insufficient system memory

**Solutions:**

1. **Reduce memory usage:**
```nginx
# Reduce max size
markdown_max_size 5m;

# Reduce buffer sizes
client_body_buffer_size 64k;
```

2. **Limit concurrency:**
```nginx
# Reduce worker connections
events {
    worker_connections 1024;
}

# Add rate limiting
limit_req_zone $binary_remote_addr zone=markdown:10m rate=10r/s;
limit_req zone=markdown burst=20;
```

3. **Restart workers periodically:**
```bash
# Graceful reload
nginx -s reload

# Or use systemd timer for periodic restart
```

4. **Upgrade system resources:**
- Add more RAM
- Use swap (not recommended for production)


#### Issue 5: Incorrect Cache Behavior

**Symptoms:**
- Clients receive wrong variant (HTML when expecting Markdown)
- Cache serving stale content
- Vary header missing

**Diagnostic Steps:**

1. **Check response headers (GET-based):**
```bash
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/test
# Verify: Vary: Accept header present
```

2. **Test cache behavior:**
```bash
# Request Markdown
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/test
# Request HTML
curl -sD - -o /dev/null -H "Accept: text/html" http://localhost/test
# Both should return different Content-Type
```

3. **Check cache key:**
```bash
nginx -T | grep proxy_cache_key
# Should include $http_accept
```

**Common Causes:**
- Cache key doesn't include Accept header
- Vary header not set
- Upstream cache misconfigured

**Solutions:**

1. **Fix cache key:**
```nginx
proxy_cache_key "$scheme$request_method$host$request_uri$http_accept";
```

2. **Verify Vary header:**
```nginx
# Module automatically adds Vary: Accept
# Verify in response headers
```

3. **Clear cache:**
```bash
# Clear NGINX cache
rm -rf /var/cache/nginx/*
nginx -s reload
```

---

#### Issue 6: Upstream/CDN Compression (Automatically Handled)

**Note**: The module automatically detects and decompresses upstream compressed content. This issue should rarely occur with automatic decompression enabled.

**Symptoms (if automatic decompression fails):**
- `Accept: text/markdown` request returns HTML instead of Markdown
- Error log shows decompression failures or invalid compression format
- Responses include `Content-Encoding: gzip`, `br`, or `deflate` on paths that should be converted

**Diagnostic Steps:**

1. **Verify headers using GET (not HEAD):**
```bash
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/test
# Check for Content-Type and Content-Encoding
```

2. **Check for decompression-related errors in log:**
```bash
grep -iE "markdown filter: (decompression|detected compression)" /var/log/nginx/error.log | tail -50
```

3. **Verify automatic decompression is working:**
```bash
# Should see log entries like:
# "markdown filter: detected compression type: gzip"
# "markdown filter: decompression succeeded, compressed=X bytes, decompressed=Y bytes"
```

4. **Optional optimization - disable upstream compression for Markdown requests:**
```nginx
map $http_accept $markdown_requested {
    default 0;
    "~*(^|,)\\s*text/markdown(\\s*;|,|$)" 1;
}

map $markdown_requested $upstream_accept_encoding {
    0 $http_accept_encoding;
    1 "";
}

location / {
    proxy_set_header Accept-Encoding $upstream_accept_encoding;
    proxy_pass http://backend;
}
```

5. **If decompression consistently fails:**
- Check if upstream is using an unsupported compression format
- Verify the compressed data is not corrupted
- Check error logs for specific decompression error messages
- Consider disabling upstream compression as a workaround

6. **Verify fix:**
```bash
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/test
# Expect: Content-Type includes text/markdown
# Expect: successful conversion even with upstream compression
```

---

### Diagnostic Tools

#### Debug Logging

Enable debug logging for detailed information:

```nginx
error_log /var/log/nginx/error.log debug;
```

**Warning:** Debug logging is very verbose. Use only for troubleshooting and disable after.

---

#### Request Tracing

Trace a specific request through the system:

```bash
# Add trace ID to request
curl -v -H "Accept: text/markdown" -H "X-Trace-ID: test123" http://localhost/test

# Search logs for trace ID
grep "test123" /var/log/nginx/error.log
```

---

#### Performance Profiling

Profile conversion performance:

```bash
# Time multiple requests
for i in {1..10}; do
    time curl -s -H "Accept: text/markdown" http://localhost/test > /dev/null
done

# Use Apache Bench for detailed stats
ab -n 100 -c 10 -H "Accept: text/markdown" http://localhost/test
```

---

## Performance Tuning

### Baseline Performance

Establish baseline performance metrics before tuning:

```bash
# Run baseline test
ab -n 1000 -c 10 -H "Accept: text/markdown" http://localhost/test

# Record results
# - Requests per second
# - Time per request (mean)
# - Time per request (95th percentile)
# - Transfer rate
```

**Expected Baselines (reference):**
- Latency overhead: < 50ms for typical pages
- Throughput: > 100 req/s (single worker)
- Token reduction: 70-85%
- Memory per request: < 10MB

---

### Optimization Strategies

#### Strategy 1: Reduce Conversion Overhead

**Goal:** Minimize time spent in conversion

**Actions:**

1. **Use CommonMark instead of GFM:**
```nginx
markdown_flavor commonmark;  # Faster than GFM
```

2. **Disable optional features:**
```nginx
markdown_token_estimate off;
markdown_front_matter off;
```

3. **Reduce timeout:**
```nginx
markdown_timeout 1s;  # Fail fast
```

**Expected Impact:** 10-20% latency reduction

---

#### Strategy 2: Aggressive Caching

**Goal:** Avoid repeated conversions

**Actions:**

1. **Enable proxy caching:**
```nginx
proxy_cache_path /var/cache/nginx/markdown
                 levels=1:2
                 keys_zone=markdown_cache:100m
                 max_size=10g
                 inactive=60m;

location / {
    proxy_cache markdown_cache;
    proxy_cache_valid 200 30m;
    proxy_cache_key "$scheme$request_method$host$request_uri$http_accept";
    proxy_cache_use_stale error timeout updating;
}
```

2. **Monitor cache hit rate:**
```bash
# Add cache status header
add_header X-Cache-Status $upstream_cache_status;

# Check hit rate
curl -I http://localhost/test | grep X-Cache-Status
```

**Expected Impact:** 80-95% cache hit rate, 10x throughput improvement


#### Strategy 3: Resource Limits

**Goal:** Prevent resource exhaustion

**Actions:**

1. **Set conservative limits:**
```nginx
markdown_max_size 2m;   # Smaller limit
markdown_timeout 1s;    # Faster timeout
```

2. **Add rate limiting:**
```nginx
limit_req_zone $binary_remote_addr zone=markdown:10m rate=10r/s;

location / {
    limit_req zone=markdown burst=20 nodelay;
}
```

**Expected Impact:** Prevent resource exhaustion, stable performance under load

---

#### Strategy 4: Worker Tuning

**Goal:** Optimize NGINX worker configuration

**Actions:**

1. **Match CPU cores:**
```nginx
worker_processes auto;  # Matches CPU cores
```

2. **Increase connections:**
```nginx
events {
    worker_connections 2048;
    use epoll;  # Linux
}
```

3. **Optimize buffers:**
```nginx
client_body_buffer_size 128k;
proxy_buffer_size 4k;
proxy_buffers 8 4k;
```

**Expected Impact:** Better CPU utilization, higher throughput

---

### Benchmarking

#### Benchmark Procedure

1. **Establish baseline:**
```bash
# HTML passthrough (baseline)
ab -n 1000 -c 10 -H "Accept: text/html" http://localhost/test
```

2. **Test Markdown conversion:**
```bash
# Markdown conversion
ab -n 1000 -c 10 -H "Accept: text/markdown" http://localhost/test
```

3. **Calculate overhead:**
```
overhead_ms = markdown_latency - html_latency
overhead_pct = (overhead_ms / html_latency) * 100
```

4. **Test with caching:**
```bash
# First request (cache miss)
curl -w "@curl-format.txt" -H "Accept: text/markdown" http://localhost/test

# Second request (cache hit)
curl -w "@curl-format.txt" -H "Accept: text/markdown" http://localhost/test
```

**curl-format.txt:**
```
time_namelookup:  %{time_namelookup}\n
time_connect:     %{time_connect}\n
time_starttransfer: %{time_starttransfer}\n
time_total:       %{time_total}\n
```

---

#### Performance Targets

| Metric | Target | Acceptable | Action Required |
|--------|--------|------------|-----------------|
| Latency overhead | < 50ms | < 100ms | > 100ms |
| Throughput | > 100 req/s | > 50 req/s | < 50 req/s |
| Cache hit rate | > 80% | > 60% | < 60% |
| Failure rate | < 1% | < 5% | > 5% |
| Memory per worker | < 100MB | < 200MB | > 200MB |

---

## Upgrade Procedures

### Pre-Upgrade Checklist

- [ ] Review release notes and changelog
- [ ] Check version compatibility (NGINX, Rust, dependencies)
- [ ] Backup current configuration
- [ ] Backup current binary and library
- [ ] Test upgrade in staging environment
- [ ] Schedule maintenance window
- [ ] Notify stakeholders

---

### Upgrade Steps

#### Step 1: Backup Current Installation

```bash
# Backup NGINX binary
cp /usr/local/nginx/sbin/nginx /usr/local/nginx/sbin/nginx.backup

# Backup module library
cp /usr/local/nginx/modules/ngx_http_markdown_filter_module.so \
   /usr/local/nginx/modules/ngx_http_markdown_filter_module.so.backup

# Backup Rust library
cp /usr/local/lib/libnginx_markdown_converter.a \
   /usr/local/lib/libnginx_markdown_converter.a.backup

# Backup configuration
cp -r /usr/local/nginx/conf /usr/local/nginx/conf.backup
```

---

#### Step 2: Build New Version

```bash
# Download new version
git clone https://github.com/cnkang/nginx-markdown-for-agents.git
cd nginx-markdown-for-agents
git checkout v1.1.0  # New version

# Build Rust library
cd components/rust-converter
cargo build --release
cd ..

# Build NGINX module
cd /tmp/nginx-1.24.0
./configure --add-dynamic-module=/path/to/nginx-markdown-for-agents/components/nginx-module
make
```

---

#### Step 3: Test New Version

```bash
# Test configuration with new binary
/tmp/nginx-1.24.0/objs/nginx -t -c /usr/local/nginx/conf/nginx.conf

# Start test instance
/tmp/nginx-1.24.0/objs/nginx -c /tmp/test-nginx.conf

# Run smoke tests
curl -H "Accept: text/markdown" http://localhost:8888/test

# Run full test suite
cd components/nginx-module/tests/integration
./run_integration_tests.sh
```

---

#### Step 4: Deploy New Version

```bash
# Stop NGINX gracefully
nginx -s quit

# Wait for workers to finish
sleep 5

# Install new binary
sudo make install

# Start NGINX
sudo nginx

# Verify startup
tail -f /var/log/nginx/error.log
```

---

#### Step 5: Verify Deployment

```bash
# Check version
nginx -V

# Test conversion
curl -v -H "Accept: text/markdown" http://localhost/test

# Check metrics
curl "${METRICS_URL:-http://localhost/markdown-metrics}"

# Monitor logs
tail -f /var/log/nginx/error.log | grep markdown
```


#### Step 6: Rollback Procedure (if needed)

```bash
# Stop NGINX
nginx -s quit

# Restore backup binary
cp /usr/local/nginx/sbin/nginx.backup /usr/local/nginx/sbin/nginx

# Restore backup module
cp /usr/local/nginx/modules/ngx_http_markdown_filter_module.so.backup \
   /usr/local/nginx/modules/ngx_http_markdown_filter_module.so

# Restore backup library
cp /usr/local/lib/libnginx_markdown_converter.a.backup \
   /usr/local/lib/libnginx_markdown_converter.a

# Restore configuration (if changed)
cp -r /usr/local/nginx/conf.backup/* /usr/local/nginx/conf/

# Start NGINX
nginx

# Verify rollback
nginx -V
curl -H "Accept: text/markdown" http://localhost/test
```

---

### Version Compatibility

#### NGINX Version Compatibility

| Module Version | NGINX Version | Status |
|----------------|---------------|--------|
| 0.3.x | 1.24.0+ | Supported |
| 0.3.x | < 1.24.0 | Not supported |

#### Rust Version Compatibility

| Module Version | Rust Version | Status |
|----------------|--------------|--------|
| 0.3.x | 1.85.0+ | Supported (edition 2024) |
| 0.3.x | < 1.85.0 | Not supported |

---

### Migration Notes

#### Upgrading to 0.3.x

- `fullbuffer_path_hits` and `incremental_path_hits` have been moved to the end of `ngx_http_markdown_metrics_t`. If you use shared-memory metrics, a graceful reload is sufficient; no data migration is needed.
- The `incremental` feature is off by default. Enable it with `--features incremental` when building the Rust converter to use the new `markdown_large_body_threshold` directive.
- `X-Forwarded-Host` and `X-Forwarded-Proto` headers are no longer trusted by default for base URL construction. If NGINX sits behind a trusted reverse proxy that sets these headers, add `markdown_trust_forwarded_headers on;` to restore the previous behavior.

#### Upgrading to 0.2.x

No public directive renames are introduced. If you relied on older documentation, review the updated guides for clarified installation paths, compression rollout guidance, metrics fields, and architecture references.

Variable-driven `markdown_filter` support is new in 0.2.0. Existing static `on`/`off` configurations continue to work without changes.

---

## Operational Checklists

### Daily Operations Checklist

- [ ] Check metrics dashboard for anomalies
- [ ] Review failure rate (should be < 5%)
- [ ] Check conversion latency (p95 should be < 100ms)
- [ ] Verify no critical alerts
- [ ] Review error log for new issues
- [ ] Check system resource usage (CPU, memory, disk)

---

### Weekly Operations Checklist

- [ ] Review performance trends over past week
- [ ] Analyze failure patterns and categories
- [ ] Check cache hit rate (should be > 80%)
- [ ] Review slow conversion logs
- [ ] Verify backup procedures working
- [ ] Update documentation if needed
- [ ] Review and close resolved incidents

---

### Monthly Operations Checklist

- [ ] Review capacity planning metrics
- [ ] Analyze performance trends over past month
- [ ] Review and update alert thresholds
- [ ] Test disaster recovery procedures
- [ ] Review security updates and patches
- [ ] Conduct performance tuning review
- [ ] Update operational runbooks
- [ ] Review and optimize configuration

---

### Pre-Deployment Checklist

- [ ] Configuration tested in staging
- [ ] All tests passing (unit, integration, E2E)
- [ ] Performance benchmarks acceptable
- [ ] Rollback procedure documented
- [ ] Monitoring and alerts configured
- [ ] Stakeholders notified
- [ ] Maintenance window scheduled
- [ ] Backup completed

---

### Post-Deployment Checklist

- [ ] Verify NGINX started successfully
- [ ] Check error log for startup issues
- [ ] Test conversion functionality
- [ ] Verify metrics endpoint accessible
- [ ] Check cache behavior
- [ ] Monitor performance for 1 hour
- [ ] Verify alerts working
- [ ] Document any issues encountered
- [ ] Notify stakeholders of completion

---

## Runbooks

### Runbook 1: High Failure Rate

**Trigger:** Failure rate > 5% for 10 minutes

**Severity:** Warning

**Steps:**

1. **Assess impact:**
```bash
curl "${METRICS_URL:-http://localhost/markdown-metrics}"
# Check: conversions_failed, failure categories
```

2. **Identify failure category:**
```bash
grep "conversion failed" /var/log/nginx/error.log | tail -50
# Look for: category=conversion_error|resource_limit|system_error
```

3. **Take action based on category:**

   **If conversion_error:**
   - Investigate failing URLs
   - Check HTML validity
   - Report bug if needed

   **If resource_limit:**
   - Increase limits temporarily: `markdown_max_size 20m; markdown_timeout 10s;`
   - Reload NGINX: `nginx -s reload`
   - Investigate root cause

   **If system_error:**
   - Check system resources: `free -h`, `df -h`
   - Check for memory leaks
   - Consider restart if needed

4. **Monitor for improvement:**
```bash
watch -n 30 'curl -s "${METRICS_URL:-http://localhost/markdown-metrics}" | grep failed'
```

5. **Document incident:**
- Record failure rate and duration
- Document root cause
- Document resolution
- Update runbook if needed


### Runbook 2: Slow Conversion Performance

**Trigger:** Conversion time p95 > 200ms for 15 minutes

**Severity:** Warning

**Steps:**

1. **Verify issue:**
```bash
# Check average conversion time
curl "${METRICS_URL:-http://localhost/markdown-metrics}"
# Calculate only when conversion_completed > 0:
#   conversion_time_sum_ms / conversion_completed
# Otherwise record 0 (or null in your dashboard) to avoid Inf/NaN.
```

2. **Identify slow requests:**
```bash
grep "conversion succeeded" /var/log/nginx/error.log | \
  grep -o 'time=[0-9]*ms' | \
  sed 's/time=//;s/ms//' | \
  sort -n | tail -20
```

3. **Check system load:**
```bash
top
iostat -x 1 5
```

4. **Take immediate action:**

   **If system overloaded:**
   - Reduce timeout: `markdown_timeout 2s;`
   - Enable rate limiting
   - Scale horizontally if possible

   **If specific URLs slow:**
   - Investigate those URLs
   - Consider excluding from conversion
   - Optimize content at source

5. **Enable caching if not already:**
```nginx
proxy_cache_path /var/cache/nginx/markdown keys_zone=markdown_cache:10m;
location / {
    proxy_cache markdown_cache;
    proxy_cache_valid 200 10m;
}
```

6. **Monitor for improvement:**
```bash
# Run benchmark
ab -n 100 -c 10 -H "Accept: text/markdown" http://localhost/test
```

---

### Runbook 3: NGINX Worker Crash

**Trigger:** Worker restart detected in logs

**Severity:** Critical

**Steps:**

1. **Check error log:**
```bash
tail -100 /var/log/nginx/error.log
# Look for: segfault, core dump, worker process exited
```

2. **Check system log:**
```bash
dmesg | tail -50
# Look for: Out of memory, segmentation fault
```

3. **Identify cause:**

   **If out of memory:**
   - Check memory usage: `free -h`
   - Reduce `markdown_max_size`
   - Add more RAM or swap

   **If segmentation fault:**
   - Check for module bug
   - Review recent changes
   - Enable core dumps for analysis

4. **Take immediate action:**
```bash
# Restart NGINX
nginx -s reload

# Or full restart if needed
systemctl restart nginx
```

5. **Prevent recurrence:**
```nginx
# Reduce resource limits
markdown_max_size 5m;
markdown_timeout 3s;

# Enable fail-open
markdown_on_error pass;
```

6. **Collect diagnostics:**
```bash
# Enable core dumps
ulimit -c unlimited

# Reproduce issue if possible
# Analyze core dump with gdb
```

7. **Report bug:**
- Collect error logs
- Collect core dump
- Document reproduction steps
- Report to maintainers

---

### Runbook 4: Cache Serving Wrong Variant

**Trigger:** Clients report receiving HTML when expecting Markdown

**Severity:** High

**Steps:**

1. **Verify issue:**
```bash
# Request Markdown
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/test

# Request HTML
curl -sD - -o /dev/null -H "Accept: text/html" http://localhost/test

# Both should return different Content-Type
```

2. **Check Vary header:**
```bash
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/test | grep -i '^Vary:'
# Should include: Vary: Accept
```

3. **Check cache key:**
```bash
nginx -T | grep proxy_cache_key
# Should include: $http_accept
```

4. **Fix cache key if needed:**
```nginx
proxy_cache_key "$scheme$request_method$host$request_uri$http_accept";
```

5. **Clear cache:**
```bash
rm -rf /var/cache/nginx/*
nginx -s reload
```

6. **Verify fix:**
```bash
# Request Markdown (cache miss)
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/test

# Request Markdown again (cache hit)
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/test

# Request HTML (cache miss)
curl -sD - -o /dev/null -H "Accept: text/html" http://localhost/test

# All should return correct Content-Type
```

---

### Runbook 5: Module Not Loading

**Trigger:** NGINX fails to start, error about markdown module

**Severity:** Critical

**Steps:**

1. **Check error message:**
```bash
nginx -t
# Look for: module not found, symbol not found, version mismatch
```

2. **Verify module file:**
```bash
ls -lh /usr/local/nginx/modules/ngx_http_markdown_filter_module.so
# Should exist and have correct permissions
```

3. **Verify Rust library:**
```bash
ls -lh /usr/local/lib/libnginx_markdown_converter.a
# Should exist
```

4. **Check library dependencies:**
```bash
ldd /usr/local/nginx/modules/ngx_http_markdown_filter_module.so
# All dependencies should be found
```

5. **Fix common issues:**

   **If module not found:**
   ```bash
   # Check load_module path
   nginx -T | grep load_module
   # Verify path is correct
   ```

   **If symbol not found:**
   ```bash
   # Rebuild module with correct NGINX version
   cd /tmp/nginx-1.24.0
   ./configure --add-dynamic-module=/path/to/module
   make
   sudo make install
   ```
   - If the missing symbol is `_markdown_convert` (Rust FFI), ensure the dynamic module link step includes the Rust converter static library. Use the repository's current `components/nginx-module/config` (older versions may only populate `CORE_LIBS`, which is insufficient for dynamic-module linkage).

   **If version mismatch:**
   ```bash
   # Rebuild module with matching NGINX version
   nginx -V  # Check NGINX version
   # Download matching NGINX source
   # Rebuild module
   ```

6. **Verify fix:**
```bash
nginx -t
nginx -s reload
```

---

### Runbook 6: Upstream/CDN Compression (Automatically Handled)

**Trigger:** Markdown requests fail conversion due to upstream/CDN compressed HTML (rare with automatic decompression)

**Severity:** Medium (automatic decompression should handle most cases)

**Steps:**

1. **Reproduce and check for decompression:**
```bash
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/test
# Check Content-Type and Content-Encoding headers
```

2. **Check logs for decompression status:**
```bash
grep -iE "markdown filter: (detected compression|decompression)" /var/log/nginx/error.log | tail -50
# Should see: "detected compression type: gzip" and "decompression succeeded"
```

3. **If decompression is failing, check error details:**
```bash
grep -iE "markdown filter: decompression failed" /var/log/nginx/error.log | tail -20
# Look for specific error messages (invalid format, corrupted data, etc.)
```

4. **Optional optimization - disable upstream compression for Markdown requests:**
```nginx
map $http_accept $markdown_requested {
    default 0;
    "~*(^|,)\\s*text/markdown(\\s*;|,|$)" 1;
}

map $markdown_requested $upstream_accept_encoding {
    0 $http_accept_encoding;
    1 "";
}

location / {
    proxy_set_header Accept-Encoding $upstream_accept_encoding;
    proxy_pass http://backend;
}
```

5. **Reload and re-test:**
```bash
nginx -t && nginx -s reload
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/test
# Verify successful conversion
```

6. **If issues persist:**
- Verify the compression format is supported (gzip, deflate, br)
- Check if compressed data might be corrupted
- Review error logs for specific decompression error codes
- Consider reporting the issue with sample data

**Note:** Automatic decompression eliminates the need for CDN bypass or special routing in most cases.

---

## Reason Code Reference for Operators

Every request that enters the module's decision chain receives a reason code that explains the outcome. These reason codes appear in decision log entries and Prometheus metrics labels using the same strings, so you can correlate a log entry directly with a metric counter without translation.

For the full decision chain model (check order, flowchart, and outcome determination logic), see [Decision Chain Model](../features/DECISION_CHAIN.md).

### Reason Code Table

The table below maps each reason code to its internal enum, error category, request state, description, and the action you should take when you see it.

| Reason Code | Eligibility Enum | Error Category | Request State | Description | Suggested Operator Action |
|---|---|---|---|---|---|
| `SKIP_CONFIG` | `NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG` | — | NOT_ENABLED | Module disabled by configuration for this scope | Expected for scopes where you have not enabled conversion. If unexpected, check `markdown_filter` in the relevant `location`/`server` block. |
| `SKIP_METHOD` | `NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD` | — | SKIPPED | Request method is not GET or HEAD | Expected for POST/PUT/DELETE requests. No action needed. |
| `SKIP_STATUS` | `NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS` | — | SKIPPED | Upstream response status is not 200 OK | Check upstream health if you see many non-200 responses for pages you expect to convert. |
| `SKIP_RANGE` | `NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE` | — | SKIPPED | Client sent a Range request header | Expected behavior — partial content cannot be converted. No action needed. |
| `SKIP_STREAMING` | `NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING` | — | SKIPPED | Response matches `markdown_stream_types` (unbounded streaming) | Expected for SSE/streaming endpoints. If a static page triggers this, check your `markdown_stream_types` configuration. |
| `SKIP_CONTENT_TYPE` | `NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE` | — | SKIPPED | Upstream Content-Type is not `text/html` | Expected for JSON, XML, image, and other non-HTML responses. If an HTML page triggers this, check the upstream `Content-Type` header. |
| `SKIP_SIZE` | `NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE` | — | SKIPPED | Response body exceeds `markdown_max_size` | Increase `markdown_max_size` if the page should be converted, or exclude oversized pages from conversion scope. |
| `SKIP_AUTH` | `NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH` | — | SKIPPED | Auth policy denies conversion for authenticated requests | Expected when `markdown_auth_policy deny` is configured. If you see it unexpectedly, check whether the request is authenticated and whether the location/server block should allow conversion. |
| `SKIP_ACCEPT` | _(Accept negotiation)_ | — | SKIPPED | Accept header does not include `text/markdown` | Expected for normal browser traffic. If an AI agent triggers this, verify the client sends `Accept: text/markdown`. Check `markdown_on_wildcard` if using `*/*`. |
| `ELIGIBLE_CONVERTED` | `NGX_HTTP_MARKDOWN_ELIGIBLE` | — | CONVERTED | All checks passed, conversion succeeded | No action needed — this is the success path. |
| `ELIGIBLE_FAILED_OPEN` | `NGX_HTTP_MARKDOWN_ELIGIBLE` | _(any)_ | FAILED | Conversion attempted but failed; original HTML served (`markdown_on_error pass`) | Investigate the failure sub-classification (see below). The client received HTML, so no user impact. Review failure rate trends. |
| `ELIGIBLE_FAILED_CLOSED` | `NGX_HTTP_MARKDOWN_ELIGIBLE` | _(any)_ | FAILED | Conversion attempted but failed; 502 returned (`markdown_on_error reject`) | Urgent — clients are receiving errors. Switch to `markdown_on_error pass` or disable conversion for the affected scope. Investigate root cause. |

#### Failure Sub-Classification Codes

When conversion fails (`ELIGIBLE_FAILED_OPEN` or `ELIGIBLE_FAILED_CLOSED`), the decision log also records a failure sub-classification that provides more detail:

| Failure Code | Error Category Enum | Description | Suggested Operator Action |
|---|---|---|---|
| `FAIL_CONVERSION` | `NGX_HTTP_MARKDOWN_ERROR_CONVERSION` | HTML parse or conversion error | Inspect the failing HTML with `curl`. Check if the upstream changed its HTML structure. Report a bug if the HTML is valid. |
| `FAIL_RESOURCE_LIMIT` | `NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT` | Timeout (`markdown_timeout` exceeded) or memory limit reached | Increase `markdown_timeout` or `markdown_max_size`, or exclude large/complex pages from conversion scope. |
| `FAIL_SYSTEM` | `NGX_HTTP_MARKDOWN_ERROR_SYSTEM` | Internal or system error | This should not occur in normal operation. Check system resources (`free -h`, `dmesg`). If persistent, report a bug with logs. |

### Request States

Every request ends in one of four mutually exclusive states, derived from its reason code:

| Request State | Reason Codes | Meaning |
|---|---|---|
| NOT_ENABLED | `SKIP_CONFIG` | Module is disabled for this scope. The request was never evaluated. |
| SKIPPED | `SKIP_METHOD`, `SKIP_STATUS`, `SKIP_RANGE`, `SKIP_STREAMING`, `SKIP_CONTENT_TYPE`, `SKIP_SIZE`, `SKIP_AUTH`, `SKIP_ACCEPT` | Module is enabled but the request did not pass an eligibility check. |
| CONVERTED | `ELIGIBLE_CONVERTED` | All checks passed and conversion succeeded. |
| FAILED | `ELIGIBLE_FAILED_OPEN`, `ELIGIBLE_FAILED_CLOSED` | All checks passed, conversion was attempted, but it did not succeed. |

#### Deriving Request State Counts from Metrics

You can determine the count of requests in each state using the metrics endpoint and decision log entries.

**From the metrics endpoint** (`curl -s http://localhost/markdown-metrics`):

```text
CONVERTED   = conversions_succeeded

FAILED      = conversions_failed
              (breakdown: failures_conversion + failures_resource_limit + failures_system)
```

> **Note:** `conversions_failed` counts all requests that entered the FAILED state, including conversion errors, decompression failures, and system errors. The breakdown counters (`failures_conversion`, `failures_resource_limit`, `failures_system`) provide the sub-classification.

**From decision log entries** (skip reason distribution is not in the metrics endpoint — use log grep):

```text
NOT_ENABLED = count of "reason=SKIP_CONFIG" in decision log entries

SKIPPED     = count of "reason=SKIP_*" (excluding SKIP_CONFIG) in decision log entries
              (i.e., SKIP_METHOD + SKIP_STATUS + SKIP_RANGE + SKIP_STREAMING
               + SKIP_CONTENT_TYPE + SKIP_SIZE + SKIP_AUTH + SKIP_ACCEPT)
```

> **Note:** Per-skip-reason counters are not currently in the metrics endpoint; use decision log grep patterns to derive skip reason distribution.

Example commands to check each state:

```bash
# Check metrics endpoint
curl -s http://localhost/markdown-metrics

# Count NOT_ENABLED from logs
grep "markdown decision:" /var/log/nginx/error.log | grep -c "reason=SKIP_CONFIG"

# Count SKIPPED from logs (all SKIP_* except SKIP_CONFIG)
grep "markdown decision:" /var/log/nginx/error.log | \
  grep "reason=SKIP_" | grep -vc "reason=SKIP_CONFIG"

# Count CONVERTED from logs
grep "markdown decision:" /var/log/nginx/error.log | grep -c "reason=ELIGIBLE_CONVERTED"

# Count FAILED from logs
grep "markdown decision:" /var/log/nginx/error.log | grep -c "reason=ELIGIBLE_FAILED"
```

### Reason Code and Metrics Label Alignment

Reason codes use the same uppercase snake_case strings in both decision log entries and Prometheus metrics labels. This means you can go from a metric spike to the corresponding log entries without any translation.

The alignment works as follows:

| Reason Code Category | Metrics Endpoint Field | Log Correlation | Example |
|---|---|---|---|
| Skip codes (`SKIP_*`) | _(not in metrics endpoint — use log grep)_ | `reason` field in decision log | `grep "reason=SKIP_METHOD" error.log` |
| Failure codes (`FAIL_*`) | `failures_conversion`, `failures_resource_limit`, `failures_system` | `category` field in decision log | `grep "category=FAIL_CONVERSION" error.log` |
| `ELIGIBLE_CONVERTED` | `conversions_succeeded` | `reason` field in decision log | `grep "reason=ELIGIBLE_CONVERTED" error.log` |
| `ELIGIBLE_FAILED_OPEN` | `conversions_failed` (aggregate) | `reason` field in decision log | `grep "reason=ELIGIBLE_FAILED_OPEN" error.log` |
| `ELIGIBLE_FAILED_CLOSED` | `conversions_failed` (aggregate) | `reason` field in decision log | `grep "reason=ELIGIBLE_FAILED_CLOSED" error.log` |

#### Correlating a Metric Spike with Logs

When you see a spike in a metric, use the same reason code string to find the corresponding log entries:

```bash
# Example: you see failures_conversion increasing in the metrics endpoint
# Find the matching log entries:
grep "markdown decision:" /var/log/nginx/error.log | grep "category=FAIL_CONVERSION"

# Example: you see conversions_failed increasing
# Find the matching log entries:
grep "markdown decision:" /var/log/nginx/error.log | grep "reason=ELIGIBLE_FAILED"

# See the full reason code distribution:
grep "markdown decision:" /var/log/nginx/error.log | \
  grep -oP 'reason=\K[A-Z_]+' | sort | uniq -c | sort -rn
```

### Related Documentation

- [Decision Chain Model](../features/DECISION_CHAIN.md) — full check order, flowchart, outcome determination, and implementation details
- [Rollout Cookbook](ROLLOUT_COOKBOOK.md) — staged rollout procedures with observation checkpoints
- [Rollback Guide](ROLLBACK_GUIDE.md) — how to disable or narrow conversion scope

---

## Decision Logging

The module emits structured decision log entries to the NGINX error log for every request that enters the [decision chain](../features/DECISION_CHAIN.md). Each entry records the reason code and request context, giving operators a per-request view of why the module converted, skipped, or failed a request.

Decision logging is controlled by the `markdown_log_verbosity` directive. No separate directive is needed — the existing verbosity knob gates which outcomes produce log entries and how much detail they contain.

### Log Entry Format

Every decision log entry uses a consistent, parseable structure with space-separated `key=value` pairs. The prefix `markdown decision:` identifies these entries in the NGINX error log.

#### Base Format (info verbosity)

```
markdown decision: reason=<REASON_CODE> method=<METHOD> uri=<URI> content_type=<TYPE>
```

Fields:

| Field | Description | Example Values |
|---|---|---|
| `reason` | The [reason code](#reason-code-table) for this request's outcome | `ELIGIBLE_CONVERTED`, `SKIP_ACCEPT`, `ELIGIBLE_FAILED_OPEN` |
| `method` | HTTP request method | `GET`, `HEAD`, `POST` |
| `uri` | Request URI (path only, no query string) | `/docs/api`, `/help/getting-started` |
| `content_type` | Upstream response Content-Type, or `-` if absent | `text/html`, `application/json`, `-` |

#### Extended Format (debug verbosity)

When `markdown_log_verbosity` is set to `debug`, three additional fields are appended:

```
markdown decision: reason=<REASON_CODE> method=<METHOD> uri=<URI> content_type=<TYPE> filter_value=<VALUE> accept=<ACCEPT> status=<STATUS>
```

Additional fields:

| Field | Description | Example Values |
|---|---|---|
| `filter_value` | Resolved value of the `markdown_filter` directive | `on`, `off`, `$variable` |
| `accept` | Client's Accept header value, or `-` if absent | `text/markdown`, `text/html, text/markdown;q=0.9`, `-` |
| `status` | Upstream response HTTP status code | `200`, `404`, `500` |

### Concrete Log Line Examples

These examples show what operators will see in `/var/log/nginx/error.log`. The NGINX timestamp, log level, PID, and connection fields are included for realism.

#### Successful conversion (info verbosity)

```
2025/01/15 14:30:25 [info] 1234#0: *567 markdown decision: reason=ELIGIBLE_CONVERTED method=GET uri=/docs/api content_type=text/html while sending to client, client: 10.0.0.5, server: example.com, request: "GET /docs/api HTTP/1.1", upstream: "http://127.0.0.1:8080/docs/api", host: "example.com"
```

#### Skipped — Accept header does not request Markdown (info verbosity)

```
2025/01/15 14:30:26 [info] 1234#0: *568 markdown decision: reason=SKIP_ACCEPT method=GET uri=/docs/api content_type=text/html while sending to client, client: 10.0.0.5, server: example.com, request: "GET /docs/api HTTP/1.1", host: "example.com"
```

#### Conversion failed open (warn verbosity or higher)

```
2025/01/15 14:30:27 [warn] 1234#0: *569 markdown decision: reason=ELIGIBLE_FAILED_OPEN method=GET uri=/blog/post-1 content_type=text/html while sending to client, client: 10.0.0.5, server: example.com, request: "GET /blog/post-1 HTTP/1.1", upstream: "http://127.0.0.1:8080/blog/post-1", host: "example.com"
```

#### Debug extended format

```
2025/01/15 14:30:28 [info] 1234#0: *570 markdown decision: reason=SKIP_METHOD method=POST uri=/api/submit content_type=text/html filter_value=on accept=text/markdown status=200 while sending to client, client: 10.0.0.5, server: example.com, request: "POST /api/submit HTTP/1.1", host: "example.com"
```

### Verbosity Gating

The `markdown_log_verbosity` directive controls which decision outcomes produce log entries and how much detail they contain. The default is `info`.

| Verbosity Level | Outcomes Logged | Format | Use Case |
|---|---|---|---|
| `error` | `ELIGIBLE_FAILED_OPEN`, `ELIGIBLE_FAILED_CLOSED` only | Base | Production with minimal log volume — only conversion failures appear |
| `warn` | All failure outcomes: `ELIGIBLE_FAILED_*` and `FAIL_*` | Base | Production monitoring — see all failures including sub-classifications |
| `info` (default) | All outcomes: `SKIP_*`, `ELIGIBLE_CONVERTED`, `ELIGIBLE_FAILED_*` | Base | Recommended for rollout — full visibility into every decision |
| `debug` | All outcomes | Extended (adds `filter_value`, `accept`, `status`) | Troubleshooting — maximum detail for diagnosing specific requests |

At `error` and `warn` levels, non-failure outcomes (`SKIP_*` and `ELIGIBLE_CONVERTED`) are silently suppressed. This keeps log volume low in production while still surfacing problems.

#### Configuration examples

```nginx
# Default — log all outcomes (recommended during rollout)
markdown_log_verbosity info;

# Production steady-state — log only failures
markdown_log_verbosity warn;

# Minimal — log only conversion failures (ELIGIBLE_FAILED_*)
markdown_log_verbosity error;

# Troubleshooting — log everything with extended fields
markdown_log_verbosity debug;
```

### NGINX Log Level Mapping

The module maps decision outcomes to NGINX log levels so that NGINX's own `error_log` level acts as an outer filter:

| Outcome Type | NGINX Log Level | Reason Codes |
|---|---|---|
| Non-failure | `NGX_LOG_INFO` | All `SKIP_*` codes, `ELIGIBLE_CONVERTED` |
| Failure | `NGX_LOG_WARN` | `ELIGIBLE_FAILED_OPEN`, `ELIGIBLE_FAILED_CLOSED` |

This means:
- If your NGINX `error_log` level is set to `warn`, you will only see failure decision entries regardless of `markdown_log_verbosity`.
- If your NGINX `error_log` level is set to `info` or `debug`, the `markdown_log_verbosity` directive controls which entries appear.
- For full decision logging visibility, ensure `error_log` is at `info` level or lower.

### Parsing Decision Log Entries

Decision log entries use a consistent `key=value` format designed for easy parsing with standard Unix tools.

#### Find all decision log entries

```bash
grep "markdown decision:" /var/log/nginx/error.log
```

#### Count entries by reason code

```bash
grep "markdown decision:" /var/log/nginx/error.log | \
  grep -oP 'reason=\K[A-Z_]+' | sort | uniq -c | sort -rn
```

Example output:

```
   4521 SKIP_ACCEPT
   1203 ELIGIBLE_CONVERTED
    342 SKIP_CONFIG
     18 SKIP_CONTENT_TYPE
      3 ELIGIBLE_FAILED_OPEN
      1 SKIP_METHOD
```

#### Find all failures

```bash
grep "markdown decision:" /var/log/nginx/error.log | \
  grep "reason=ELIGIBLE_FAILED"
```

#### Extract URIs that failed conversion

```bash
grep "markdown decision:" /var/log/nginx/error.log | \
  grep "reason=ELIGIBLE_FAILED" | \
  grep -oP 'uri=\K[^ ]+' | sort | uniq -c | sort -rn
```

#### Show reason code distribution per hour

```bash
grep "markdown decision:" /var/log/nginx/error.log | \
  awk '{print substr($1,1,13), $0}' | \
  grep -oP '^\S+ .*reason=\K[A-Z_]+' | sort | uniq -c
```

#### Extract full decision fields with awk

```bash
grep "markdown decision:" /var/log/nginx/error.log | \
  awk -F'markdown decision: ' '{print $2}' | \
  awk -F' (while|,) ' '{print $1}'
```

This extracts just the `key=value` portion, stripping the NGINX boilerplate. Example output:

```
reason=ELIGIBLE_CONVERTED method=GET uri=/docs/api content_type=text/html
reason=SKIP_ACCEPT method=GET uri=/index.html content_type=text/html
reason=ELIGIBLE_FAILED_OPEN method=GET uri=/blog/post-1 content_type=text/html
```

#### Monitor decisions in real time

```bash
tail -f /var/log/nginx/error.log | grep "markdown decision:"
```

### Related Documentation

- [Decision Chain Model](../features/DECISION_CHAIN.md) — check order, reason code definitions, and outcome determination
- [Reason Code Reference](#reason-code-reference-for-operators) — complete reason code table with operator actions
- [Rollout Cookbook](ROLLOUT_COOKBOOK.md) — observation checkpoints that use decision log patterns
- [Rollback Guide](ROLLBACK_GUIDE.md) — verification steps that check decision log entries after rollback

---

## References

- **Configuration Guide:** [CONFIGURATION.md](CONFIGURATION.md)
- **Installation Guide:** [INSTALLATION.md](INSTALLATION.md)
- **Documentation Index:** [../README.md](../README.md)
- **Performance Baselines:** [../testing/PERFORMANCE_BASELINES.md](../testing/PERFORMANCE_BASELINES.md)
- **Integration Tests:** [../testing/INTEGRATION_TESTS.md](../testing/INTEGRATION_TESTS.md)
- **Automatic Decompression:** [../features/AUTOMATIC_DECOMPRESSION.md](../features/AUTOMATIC_DECOMPRESSION.md)
- **E2E Tests:** [../testing/E2E_TESTS.md](../testing/E2E_TESTS.md)
- **Requirements Traceability:** [../project/PROJECT_STATUS.md](../project/PROJECT_STATUS.md)
- **Architecture Index:** [../architecture/README.md](../architecture/README.md)
- **Request Lifecycle:** [../architecture/REQUEST_LIFECYCLE.md](../architecture/REQUEST_LIFECYCLE.md)
- **Configuration to Behavior Map:** [../architecture/CONFIG_BEHAVIOR_MAP.md](../architecture/CONFIG_BEHAVIOR_MAP.md)
- **Decision Chain Model:** [../features/DECISION_CHAIN.md](../features/DECISION_CHAIN.md)

---

## Appendix: Metrics Reference

### Complete Metrics List

| Metric Name | Type | Description |
|-------------|------|-------------|
| `conversions_attempted` | Cumulative counter field | Total conversion attempts |
| `conversions_succeeded` | Cumulative counter field | Successful conversions |
| `conversions_failed` | Cumulative counter field | Failed conversions |
| `conversions_bypassed` | Cumulative counter field | Bypassed responses (ineligible/passthrough) |
| `failures_conversion` | Cumulative counter field | Conversion errors (HTML parsing, Markdown generation, etc.) |
| `failures_resource_limit` | Cumulative counter field | Resource limit violations (size, timeout) |
| `failures_system` | Cumulative counter field | System errors (memory/internal failures) |
| `input_bytes` | Cumulative counter field | Total input bytes processed |
| `output_bytes` | Cumulative counter field | Total output bytes generated |
| `conversion_time_sum_ms` | Cumulative counter field | Total conversion time in milliseconds |

### Derived Metrics Formulas

```
# Success rate (%)
success_rate = (conversions_succeeded / conversions_attempted) * 100

# Failure rate (%)
failure_rate = (conversions_failed / conversions_attempted) * 100

# Average conversion time (ms)
avg_conversion_time = conversion_completed > 0 ? (conversion_time_sum_ms / conversion_completed) : 0

# Size reduction (%)
size_reduction = ((input_bytes - output_bytes) / input_bytes) * 100

# Throughput (req/s)
throughput = conversions_succeeded / time_period_seconds
```
