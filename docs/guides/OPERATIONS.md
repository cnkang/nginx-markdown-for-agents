# NGINX Markdown Filter Module - Operational Guide

## Table of Contents

1. [Overview](#overview)
2. [Monitoring and Metrics](#monitoring-and-metrics)
3. [Troubleshooting](#troubleshooting)
4. [Performance Tuning](#performance-tuning)
5. [Upgrade Procedures](#upgrade-procedures)
6. [Operational Checklists](#operational-checklists)
7. [Runbooks](#runbooks)

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
| `input_bytes` | Total input bytes processed | Cumulative counter field | N/A (informational) |
| `output_bytes` | Total output bytes generated | Cumulative counter field | N/A (informational) |

#### Derived Metrics

Calculate these metrics from the raw counters:

```
# Failure rate
failure_rate = (conversions_failed / conversions_attempted) * 100

# Average conversion time
avg_conversion_time = conversion_time_sum_ms / conversions_succeeded

# Size reduction (proxy for token reduction trend)
size_reduction = ((input_bytes - output_bytes) / input_bytes) * 100

# Success rate
success_rate = (conversions_succeeded / conversions_attempted) * 100
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
```
Markdown Filter Metrics
-----------------------
Conversions Attempted: 1250
Conversions Succeeded: 1180
Conversions Failed: 70
Conversions Bypassed: 20

Failure Breakdown:
- Conversion Errors: 25
- Resource Limit Exceeded: 40
- System Errors: 5

Performance:
- Total Conversion Time: 45000 ms
- Total Input Bytes: 52428800
- Total Output Bytes: 15728640
```

**Example Output (JSON):**
```json
{
  "conversions_attempted": 1250,
  "conversions_succeeded": 1180,
  "conversions_failed": 70,
  "conversions_bypassed": 20,
  "failures_conversion": 25,
  "failures_resource_limit": 40,
  "failures_system": 5,
  "conversion_time_sum_ms": 45000,
  "input_bytes": 52428800,
  "output_bytes": 15728640
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
rate(conversion_time_sum_ms[5m]) / rate(conversions_succeeded[5m])

# Throughput (conversions per second)
rate(conversions_succeeded[1m])

# Size reduction percentage (proxy for token reduction trend)
(1 - (rate(output_bytes[5m]) / rate(input_bytes[5m]))) * 100
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
- Response not eligible (non-200 status, non-HTML content)
- Response exceeds `markdown_max_size` limit

**Solutions:**
- Enable filter: `markdown_filter on;`
- Verify client sends `Accept: text/markdown`
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
# Calculate: total_conversion_time_ms / conversions_succeeded
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
git clone https://github.com/example/nginx-markdown-for-agents.git
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
| 1.0.x | 1.18.0 - 1.24.x | Supported |
| 1.0.x | 1.25.x+ | Untested |
| 1.0.x | < 1.18.0 | Not supported |

#### Rust Version Compatibility

| Module Version | Rust Version | Status |
|----------------|--------------|--------|
| 1.0.x | 1.70.0+ | Supported |
| 1.0.x | < 1.70.0 | Not supported |

---

### Migration Notes

#### Migrating from v1.0 to v1.1

**New Features:**
- Enhanced token estimation
- Additional metrics
- Performance improvements

**Breaking Changes:**
- None

**Configuration Changes:**
- New directive: `markdown_normalize_output` (default: on)
- New directive: `markdown_validate_commonmark` (default: off)

**Migration Steps:**
1. Review new directives
2. Update configuration if needed
3. Follow standard upgrade procedure
4. No data migration required

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
# Calculate: total_conversion_time_ms / conversions_succeeded
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

## References

- **Configuration Guide:** [CONFIGURATION.md](CONFIGURATION.md)
- **Installation Guide:** [INSTALLATION.md](INSTALLATION.md)
- **Documentation Index:** [../README.md](../README.md)
- **Performance Baselines:** [../testing/PERFORMANCE_BASELINES.md](../testing/PERFORMANCE_BASELINES.md)
- **Integration Tests:** [../testing/INTEGRATION_TESTS.md](../testing/INTEGRATION_TESTS.md)
- **Automatic Decompression:** [../features/AUTOMATIC_DECOMPRESSION.md](../features/AUTOMATIC_DECOMPRESSION.md)
- **E2E Tests:** [../testing/E2E_TESTS.md](../testing/E2E_TESTS.md)
- **Requirements Traceability:** [../project/PROJECT_STATUS.md](../project/PROJECT_STATUS.md)
- **Architecture Design:** [../architecture/REPOSITORY_STRUCTURE.md](../architecture/REPOSITORY_STRUCTURE.md)

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
avg_conversion_time = conversion_time_sum_ms / conversions_succeeded

# Size reduction (%)
size_reduction = ((input_bytes - output_bytes) / input_bytes) * 100

# Throughput (req/s)
throughput = conversions_succeeded / time_period_seconds
```
