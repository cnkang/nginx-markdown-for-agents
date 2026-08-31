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
- Monitoring system configured (Prometheus, Grafana, and so on)
- Basic understanding of NGINX and HTTP

### Maintenance and Validation Notes

- This guide includes example commands, sample metrics, and suggested thresholds. Validate them in staging before production use.
- Metric field names in this guide should match the current metrics endpoint implementation in `components/nginx-module/src/ngx_http_markdown_filter_module.c`.
- The built-in metrics endpoint returns the frozen Prometheus exposition.
  Send an explicit Prometheus `Accept` header. See
  [Prometheus Metrics Guide](prometheus-metrics.md).
- Metrics aggregate in shared memory across workers. A single endpoint response reflects the whole NGINX instance, not just the worker that served `/markdown-metrics`.
- For “why this request took a specific branch,” use [../architecture/REQUEST_LIFECYCLE.md](../architecture/REQUEST_LIFECYCLE.md) and [../architecture/CONFIG_BEHAVIOR_MAP.md](../architecture/CONFIG_BEHAVIOR_MAP.md) alongside this runbook.

### Terminology and Command Conventions

- **Module** means the NGINX Markdown filter module (the NGINX C component).
- **Rust converter** means the Rust HTML-to-Markdown library and FFI layer.
  - **Metrics endpoint** means the HTTP endpoint enabled by `markdown_metrics` (Prometheus text 0.0.4 only).
- Metrics commands in this guide often use `http://localhost/markdown-metrics`. Replace it with your actual metrics endpoint path when different.

---

## Monitoring and Metrics

### Key Metrics to Monitor

The endpoint emits exactly the twelve bounded Prometheus families defined in
the [Prometheus Metrics Guide](prometheus-metrics.md). Monitor the labeled
request outcomes, conversion attempts and successful deliveries, the duration
histogram, byte counters, inflight gauge, streaming/decompression/dynconf
events, and `build_info`. Do not derive dashboards from removed JSON fields or
legacy family names.

The conservation checks to use after the system is quiescent are:

```text
sum(requests_total) >= sum(conversion_attempts_total)
sum(conversion_attempts_total) >= sum(conversion_deliveries_total)
conversion_duration_seconds_count <= sum(conversion_attempts_total)
inflight_requests == 0
```


### Accessing Metrics

#### Via HTTP Endpoint

```bash
# Optional: override if your metrics endpoint uses a different path
export METRICS_URL="${METRICS_URL:-http://localhost/markdown-metrics}"

# Prometheus text exposition format 0.0.4 (the only public format)
curl --fail-with-body -H "Accept: text/plain; version=0.0.4" "$METRICS_URL"
```

**Example Output:**
```text
# HELP nginx_markdown_requests_total Requests entering the decision chain
# TYPE nginx_markdown_requests_total counter
nginx_markdown_requests_total{outcome="converted",stage="delivery",reason="converted"} 1180
# TYPE nginx_markdown_conversion_duration_seconds histogram
nginx_markdown_conversion_duration_seconds_count{engine="full_buffer"} 1180
```

---

### Prometheus Integration

The built-in endpoint emits native Prometheus exposition format. Send an
explicit Prometheus `Accept` header from the scraper. See
[Prometheus Metrics Guide](prometheus-metrics.md) for the full metric catalog.

**Example `prometheus.yml`:**
```yaml
scrape_configs:
  - job_name: 'nginx-markdown'
    static_configs:
      - targets: ['localhost:80']
    metrics_path: '/markdown-metrics'
    # Prometheus sends a Prometheus/OpenMetrics Accept header by default.
```

**Grafana Dashboard Queries:**

```promql
# Failed request rate (denominator: all request samples, including skipped)
sum(rate(nginx_markdown_requests_total{outcome=~"failed_.*"}[5m]))
/ clamp_min(sum(rate(nginx_markdown_requests_total[5m])), 1e-10) * 100

# Slow conversion bucket share (> 1s)
(sum(rate(nginx_markdown_conversion_duration_seconds_bucket{le="+Inf"}[5m]))
  - sum(rate(nginx_markdown_conversion_duration_seconds_bucket{le="1.0"}[5m])))
/ clamp_min(sum(rate(nginx_markdown_conversion_duration_seconds_count[5m])), 1e-10) * 100

# Throughput (conversions per second)
sum(rate(nginx_markdown_conversion_deliveries_total[1m]))

# Size reduction percentage (proxy for token reduction trend)
(1 - (rate(nginx_markdown_output_bytes_total[5m])
  / clamp_min(rate(nginx_markdown_input_bytes_total[5m]), 1))) * 100

# Decompression failure rate
sum(rate(nginx_markdown_decompression_events_total{outcome="failure"}[5m]))
/ clamp_min(sum(rate(nginx_markdown_decompression_events_total[5m])), 1e-10) * 100
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
| `markdown: outcome=failed_closed stage=... reason=conversion_error category=conversion` | WARN | HTML parsing or Markdown generation failed |
| `markdown: outcome=failed_closed stage=... reason=memory_budget_exceeded category=resource_limit` | WARN | Memory limit reached (buffered path) |
| `markdown: outcome=failed_closed stage=... reason=timeout category=resource_limit` | WARN | Parser or conversion deadline exceeded (buffered path) |
| `markdown: outcome=failed_closed stage=... reason=ffi_panic category=system` | ERROR | Internal/system error (Rust↔C panic) |
| `markdown: outcome=converted stage=... reason=converted event=...` | INFO | Successful conversion with timing |

`category=` is the high-level failure class (`conversion`, `resource_limit`,
`system`), and `reason=` carries the specific reason-registry key
(`conversion_error`, `memory_budget_exceeded`, `timeout`, `ffi_panic`, ...).
Decision-log lines start with the `markdown:` prefix — matching on
`grep "conversion failed"` will not find them.


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

# Use curl with explicit timeouts and fail-closed behavior
set -e
if METRICS=$(curl --fail-with-body --max-time 10 --connect-timeout 5 \
    -H "Accept: text/plain; version=0.0.4" \
    "${METRICS_URL:-http://localhost/markdown-metrics}" 2>/dev/null); then
    :
else
    CURL_EXIT=$?
    echo "CRITICAL: Metrics retrieval failed (curl exit $CURL_EXIT)"
    exit 2
fi

FAILED=$(echo "$METRICS" | grep -E 'nginx_markdown_requests_total\{.*outcome="(failed_[^"]+|aborted)"' | awk '{sum += $2} END {print sum + 0}')
# All-request failure rate: numerator and denominator come from the same
# nginx_markdown_requests_total family, matching the Grafana "Failed request
# rate" PromQL definition above (denominator: every request sample,
# including skipped requests).  Do not divide by
# conversion_attempts_total here: a failure before engine selection would
# otherwise inflate the rate above 100 percent.
TOTAL_REQUESTS=$(echo "$METRICS" | grep 'nginx_markdown_requests_total' | awk '{sum += $2} END {print sum + 0}')

if [ "$TOTAL_REQUESTS" -eq 0 ]; then
    echo "WARNING: No requests observed"
    exit 1
fi

FAILURE_RATE=$(echo "scale=2; ($FAILED / $TOTAL_REQUESTS) * 100" | bc)

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

The repository CI now includes a non-blocking Darwin/macOS smoke workflow. It exercises the shared native-build helper, real-nginx IMS validation, and chunked native smoke. If a runtime issue reproduces only on macOS, start by comparing its workflow logs with the primary Linux `runtime-regressions` job.

### Common Issues and Solutions

#### Issue 1: Conversion Not Occurring

**Symptoms:**
- Clients receive HTML instead of Markdown
- `nginx_markdown_conversion_attempts_total` is not increasing

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
- `text/*` path in map enabled but `markdown_accept` is still `strict`
- Response not eligible (non-200 status, non-HTML content)
- Response exceeds `markdown_limits conversion_memory=...` limit

**Solutions:**
- Enable filter: `markdown_filter on;`
- Verify client sends `Accept: text/markdown`
- For map-based config, use regex for `Accept` matching and prefer `$uri` for extension checks
- Enable wildcard support when required: `markdown_accept wildcard;`
- Check backend returns 200 with `Content-Type: text/html`
- Increase size limit if needed: `markdown_limits conversion_memory=20m;`

---

#### Issue 2: High Failure Rate

**Symptoms:**
- `nginx_markdown_requests_total{outcome=~"failed_.*"}` increasing rapidly
- Alert: Failure rate > 5%

**Diagnostic Steps:**

1. **Check failure categories:**
```bash
curl -H "Accept: text/plain; version=0.0.4" "${METRICS_URL:-http://localhost/markdown-metrics}" | grep 'outcome="failed_'
```

2. **Analyze error logs:**
```bash
grep "markdown:" /var/log/nginx/error.log | \
  grep -E "outcome=failed_(open|closed)" | tail -50
```

3. **Identify failure patterns:**
```bash
# Group by category
grep "markdown:" /var/log/nginx/error.log | \
  grep -E "outcome=failed_(open|closed)" | \
  grep -o 'category=[a-z_]*' | sort | uniq -c

# Find problematic URLs
grep "markdown:" /var/log/nginx/error.log | \
  grep -E "outcome=failed_(open|closed)" | \
  grep -o 'uri=[^ ]*' | sort | uniq -c | sort -rn | head -10
```

**Common Causes:**

| Reason | Cause | Solution |
|----------|-------|----------|
| `conversion_error` | Malformed HTML | Investigate HTML source, improve error handling |
| `memory_budget_exceeded` | Memory limit reached (`markdown_limits conversion_memory=...`) | Increase the relevant `markdown_limits` key, or exclude large/complex pages from conversion scope |
| `timeout` | Parser execution exceeded `markdown_limits parser_timeout=...` | Increase `markdown_limits parser_timeout=...` or exclude slow pages |
| `ffi_panic` | Internal/system error (Rust↔C panic) | Collect logs (`dmesg`) and report a bug |

**Solutions:**

- **For conversion_error:**
  - Inspect failing HTML: `curl http://backend/failing-url`
  - Validate HTML: Use W3C validator
  - Report bug if HTML is valid but conversion fails

- **For memory_budget_exceeded:**
  - Increase limits: `markdown_limits conversion_memory=20m conversion_timeout=10s;`
  - Optimize content: Reduce HTML size at source
  - Use fail-open: `markdown_error_policy pass;`

- **For timeout:**
  - Increase `markdown_limits parser_timeout=...`
  - Exclude slow pages from conversion scope
  - Use fail-open: `markdown_error_policy pass;`

- **For ffi_panic:**
  - Check memory: `free -h`, `top`
  - Check disk space: `df -h`
  - Review system logs: `dmesg | tail -50`
  - Report a bug with the collected logs


#### Issue 3: Slow Conversion Performance

**Symptoms:**
- High conversion latency (> 200ms p95)
- Slow response times for Markdown requests
- Alert: Conversion time > threshold

**Diagnostic Steps:**

1. **Check p95 conversion time:**
```bash
curl -H "Accept: text/plain; version=0.0.4" "${METRICS_URL:-http://localhost/markdown-metrics}"
# Use the histogram's rate/quantile functions; do not parse removed JSON fields.
```

```promql
histogram_quantile(0.95,
  sum by (le) (rate(nginx_markdown_conversion_duration_seconds_bucket[5m])))
```

2. **Monitor logs:**
```bash
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

> **Note**: The 0.3.x rows are historical. The authoritative, machine-readable
> compatibility projection comes from the generator that reads `tools/release-matrix.json`
> (see `docs/releases/release-matrix.json` for the generated document and
> its `generated_from` binding).

#### Rust Version Compatibility

| Module Version | Rust Version | Status |
|----------------|--------------|--------|
| 0.9.1+ | 1.97.1+ | Supported (edition 2024) |
| 0.9.1+ | < 1.97.1 | Not supported for source builds |

---

### Migration Notes

#### Upgrading to 0.9.x

- The 0.9.2 metrics layout is a breaking reset. Install the matching module
  and allow a graceful reload to create the new shared-memory layout. The
  frozen v1 metrics surface exposes bounded request and conversion counters.
- The streaming path (`markdown_streaming off|auto|force`) controls runtime
  selection. `auto` uses an internal bounded heuristic. There is no
  operator-facing response-size threshold.
- `X-Forwarded-Host` and `X-Forwarded-Proto` headers are no longer trusted by default for base URL construction. If NGINX sits behind a trusted reverse proxy that sets these headers, add its proxy range in the `http` context. For example, use `markdown_trusted_proxies 10.0.0.0/8;`. Forwarded headers remain ignored for direct peers outside the configured CIDRs. Trusted proxies keep base URLs correct. Configure them explicitly. This restores the previous behavior.

#### Upgrading to 0.2.x

The project introduces no public directive renames. If you relied on older documentation, review the updated guides. They clarify installation paths, compression rollout guidance, metrics fields, and architecture references. The guides now describe the current directive names.

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
curl -H "Accept: text/plain; version=0.0.4" "${METRICS_URL:-http://localhost/markdown-metrics}"
# Check: nginx_markdown_requests_total{outcome=~"failed_.*"}
```

2. **Identify failure category:**
```bash
grep "markdown:" /var/log/nginx/error.log | grep -E "outcome=failed_(open|closed)" | tail -50
# Look for: reason=conversion_error|memory_budget_exceeded|timeout|ffi_panic
```

3. **Take action based on category:**

   **If conversion_error:**
   - Investigate failing URLs
   - Check HTML validity
   - Report bug if needed

   **If memory_budget_exceeded:**
   - Increase limits temporarily: `markdown_limits conversion_memory=20m conversion_timeout=10s;`
   - Reload NGINX: `nginx -s reload`
   - Investigate root cause

   **If timeout:**
   - Increase `markdown_limits parser_timeout=...`
   - Exclude slow pages from conversion scope

   **If ffi_panic:**
   - Check system resources: `free -h`, `df -h`
   - Check for memory leaks
   - Report a bug with collected logs

4. **Monitor for improvement:**
```bash
watch -n 30 'curl -s -H "Accept: text/plain; version=0.0.4" "${METRICS_URL:-http://localhost/markdown-metrics}" | grep failed'
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
# Check p95 conversion time
curl -H "Accept: text/plain; version=0.0.4" "${METRICS_URL:-http://localhost/markdown-metrics}"
# Use histogram_quantile over nginx_markdown_conversion_duration_seconds.
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
   - Reduce timeout: `markdown_limits conversion_timeout=2s parser_timeout=2s;`
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
   - Reduce `markdown_limits conversion_memory=` value
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
    markdown_limits conversion_memory=5m parser_memory=5m conversion_timeout=3s parser_timeout=3s;

# Enable fail-open
markdown_error_policy pass;
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
- Verify that the module supports the compression format (gzip, deflate, br). The module supports these three formats. Check the upstream Content-Encoding header.
- Check whether compressed data might have corruption. Corrupted streams fail decompression. A partial transfer often causes this. Verify the upstream transfer completed. Compare the received size with Content-Length.
- Review error logs for specific decompression error codes
- Consider reporting the issue with sample data

**Note:** Automatic decompression eliminates the need for CDN bypass or special routing in most cases. The module handles decompression internally.

---

## Reason Code Reference for Operators

Every request that enters the module's decision chain receives a canonical
reason code that explains the outcome. These reason codes appear in decision
log entries using the `reason=` field and in Prometheus metrics as label
values. The decision-log `reason=` field and the Prometheus label value use the
same string for each reason code. Streaming path transitions use a separate
bounded `event=` field. They are not a second reason-code taxonomy. You can
correlate a log entry directly with a metric counter without translation. The
request-level outcome classifications (`converted`, `failed_open`,
`failed_closed`, `aborted`, `skipped`) appear in `nginx_markdown_requests_total` as
`outcome` (the terminal classification) and `reason` (the outcome-specific
code) label values. For `skipped`, the reason label uses the specific skip
code (`disabled`, `not_eligible`, `skipped_accept`, and so on), not the
literal value `skipped`. The finer failure sub-classification (the
decision-log `category` field, for example `conversion`, `resource_limit`,
or `system`) is intentionally
**not** exposed as a metric label value. See [Failure Sub-Classification
Codes](#failure-sub-classification-codes) below.

For the full decision chain model (check order, flowchart, and outcome determination logic), see [Decision Chain Model](../features/DECISION_CHAIN.md).

### Reason Code Table

The table below maps each reason code to its internal enum, error category, request state, and description. It also lists the action you should take when you see it.

| Reason Code | Request State | Description | Suggested Operator Action |
|---|---|---|---|
| `disabled` | NOT_ENABLED | Module disabled by configuration for this scope | Expected for scopes where you have not enabled conversion. If unexpected, check `markdown_filter` in the relevant `location`/`server` block. |
| `not_eligible` | SKIPPED | Request not eligible (method not GET/HEAD, non-200/206 status, Range request, non-`text/html` Content-Type, exceeds `markdown_limits conversion_memory=`, or auth policy denies) | The individual failing check is in the structured log metadata. Most are expected (POST/PUT/DELETE, non-HTML, partial content). If an HTML GET page triggers this, check the failing check field in the log. |
| `skipped_accept` | SKIPPED | `Accept` header present but does not request Markdown | Expected for normal browser traffic. If an AI agent triggers this, verify the client sends `Accept: text/markdown`. Check `markdown_accept` if using `*/*`. |
| `skipped_no_accept` | SKIPPED | No `Accept` header and `markdown_accept` is `strict` | Expected when clients omit `Accept`. Relax `markdown_accept` to `wildcard` if you want to convert such traffic. |
| `skipped_accept_reject` | SKIPPED | `Accept` explicitly rejects Markdown (`text/markdown;q=0` or wildcard with `q=0`) | Expected when a client signals it does not want Markdown. No action needed. |
| `skipped_conditional` | SKIPPED | Conditional request matched (If-None-Match / If-Modified-Since) → 304 Not Modified | Expected for conditional revalidation. No action needed. |
| `bypass_no_transform` | SKIPPED | `no-transform` Cache-Control directive present | Expected when upstream forbids transformation. No action needed. |
| `converted` | CONVERTED | All checks passed, conversion succeeded | No action needed — this is the success path. |
| `failed_open` | FAILED | Conversion attempted but failed; original HTML served (`markdown_error_policy pass`) | Investigate the failure sub-classification (see below). The client received HTML, so no user impact. Review failure rate trends. |
| `failed_closed` | FAILED | Conversion attempted but failed; 502 returned (`markdown_error_policy fail_closed`) | Urgent — clients are receiving errors. Switch to `markdown_error_policy pass` or disable conversion for the affected scope. Investigate root cause. |

#### Streaming Implementation Events

When the streaming engine is active (`markdown_streaming auto` or `force`),
bounded implementation transitions appear in the `event=` field. They are
not reason codes, are not Prometheus reason labels, and do not require a
second registry.

| Event | Stage | Description | Suggested Operator Action |
|---|---|---|---|
| `engine_streaming` / `streaming_path_selection` | `eligibility` | The streaming path was selected | Informational — correlate with the request outcome. |
| `streaming_convert` | `postcommit` | Streaming conversion delivered its terminal output | No action needed — this is the streaming success path. |
| `streaming_skip_unsupported` | `eligibility` | The request cannot use streaming | Informational — the full-buffer path handles the request. |
| `streaming_fallback_prebuffer` | `precommit` | Streaming fell back before committing output | Monitor the fallback rate and investigate recurring conversion causes. |
| `streaming_budget_exceeded` | `precommit` or `postcommit` | A streaming resource limit was reached | Review the relevant `markdown_limits` setting and the canonical `reason=` value. |
| `streaming_precommit_failopen` / `streaming_precommit_reject` | `precommit` | Policy selected pass-through or rejection | Investigate the canonical failure reason and configured error policy. |
| `streaming_postcommit_failure` / `streaming_fail_postcommit` | `postcommit` | Streaming failed after output commitment | Treat as urgent because the response may be truncated. Inspect the canonical terminal reason. |

#### Failure Sub-Classification Codes

When conversion fails (`failed_open` or `failed_closed`), the decision log also
records a bounded failure sub-classification in its `category` field. The
`nginx_markdown_requests_total` family intentionally exposes only its canonical
outcome/reason labels. It does not expose these sub-classifications as metric
reason values.

The `category` field is the broad class (`conversion`, `resource_limit`,
`system`), and the `reason` field is the specific reason-registry key.  The
table
below lists reasons by their category — a reason is a value of the `reason=`
field, never a value of `category=`.

| Category | Reason Code | Description | Suggested Operator Action |
|---|---|---|---|
| `conversion` | `conversion_error` | HTML parse or conversion error | Inspect the failing HTML with `curl`. Check if the upstream changed its HTML structure. Report a bug if the HTML is valid. |
| `resource_limit` | `memory_budget_exceeded` | Memory limit reached (`markdown_limits conversion_memory=...`) | Increase the relevant `markdown_limits` key, or exclude large/complex pages from conversion scope. |
| `resource_limit` | `timeout` | Parser execution exceeded `markdown_limits parser_timeout=...` | Increase `markdown_limits parser_timeout=...` or exclude slow pages. |
| `system` | `ffi_panic` | Internal/system error (Rust↔C panic) | Urgent — indicates an unexpected internal failure. Collect logs (`dmesg`) and report a bug. |

### Request States

Every request ends in one of four mutually exclusive states, derived from its reason code:

| Request State | Reason Codes | Meaning |
|---|---|---|
| NOT_ENABLED | `disabled` | Module is disabled for this scope. The request was never evaluated. |
| SKIPPED | `not_eligible`, `skipped_accept`, `skipped_no_accept`, `skipped_accept_reject`, `skipped_conditional`, `bypass_no_transform` | Module is enabled but the request did not pass an eligibility check. |
| CONVERTED | `converted` | All checks passed and conversion succeeded. |
| FAILED | `failed_open`, `failed_closed`, `aborted` | All checks passed, conversion was attempted, but it did not succeed. `aborted` covers streaming conversions terminated as an incomplete terminal state. |

#### Deriving Request State Counts from Metrics

You can determine the count of requests in each state using the metrics endpoint and decision log entries.

**From the metrics endpoint** (`curl -s -H "Accept: text/plain; version=0.0.4" http://localhost/markdown-metrics`):

```text
NOT_ENABLED = sum(nginx_markdown_requests_total{outcome="skipped",reason="disabled"})

CONVERTED   = sum(nginx_markdown_requests_total{outcome="converted"})

SKIPPED     = sum(nginx_markdown_requests_total{outcome="skipped",reason=~"not_eligible|skipped_.*|bypass_no_transform"})

FAILED      = sum(nginx_markdown_requests_total{outcome=~"failed_.*"})
            + sum(nginx_markdown_requests_total{outcome="aborted"})
```

> **Note:** The outcome, stage, and reason labels are the authoritative request-level classification. They stay bounded and do not include a path or URI dimension.

**From decision log entries** (useful for request-level correlation):

```text
NOT_ENABLED = count of "reason=disabled" in decision log entries

SKIPPED     = count of "reason=not_eligible" (and other skipped_* codes) in decision log entries
              (i.e., not_eligible + skipped_accept + skipped_no_accept
               + skipped_accept_reject + skipped_conditional + bypass_no_transform)
```

> **Note:** The module exposes request outcomes as Prometheus
> `nginx_markdown_requests_total{outcome=...,stage=...,reason=...}` series. Use decision
> log grep patterns when you need request URIs, status details, or correlation
> with a specific upstream response.

Example commands to check each state:

```bash
# Check metrics endpoint
curl -s -H "Accept: text/plain; version=0.0.4" http://localhost/markdown-metrics

# Count NOT_ENABLED from logs
grep "markdown:" /var/log/nginx/error.log | grep -c "reason=disabled"

# Count SKIPPED from logs (excluding the disabled/NOT_ENABLED state)
grep "markdown:" /var/log/nginx/error.log | \
  grep -cE "reason=(not_eligible|skipped_accept|skipped_no_accept|skipped_conditional|skipped_accept_reject|bypass_no_transform)"

# Count CONVERTED from logs
grep "markdown:" /var/log/nginx/error.log | grep -c "reason=converted"

# Count FAILED from logs
grep "markdown:" /var/log/nginx/error.log | grep -cE 'outcome=(failed_open|failed_closed|aborted)'
```

### Reason Code and Metrics Label Alignment

Reason codes use lowercase snake_case strings in decision log entries (for
example `not_eligible`, `converted`, `failed_open`) and the same strings as
Prometheus `reason` label values. This means you can go from a metric spike to
the corresponding log entries without any translation. Streaming transition
details use lowercase bounded `event` values and never appear as `reason`
labels.

The alignment works as follows:

| Reason Code Category | Metrics Endpoint Field | Log Correlation | Example |
|---|---|---|---|
| Skip codes (`not_eligible`, `skipped_*`, `bypass_no_transform`) | `nginx_markdown_requests_total{outcome="skipped",reason="..."}` | `reason` field in decision log | `grep "reason=not_eligible" error.log` |
| Failure categories (`conversion`, `resource_limit`, `system`) | Canonical failed outcome in `nginx_markdown_requests_total{outcome=~"failed_(open|closed)"}` | `category` field in decision log | `grep -E "category=(conversion|resource_limit|system)" error.log` |
| `converted` | `nginx_markdown_requests_total{outcome="converted"}` | `reason` field in decision log | `grep "reason=converted" error.log` |
| `failed_open` | `nginx_markdown_requests_total{outcome="failed_open"}` | `outcome` field in decision log | `grep "outcome=failed_open" error.log` |
| `failed_closed` | `nginx_markdown_requests_total{outcome="failed_closed"}` | `outcome` field in decision log | `grep "outcome=failed_closed" error.log` |

#### Correlating a Metric Spike with Logs

When you see a spike in a metric, use the same reason code string to find the corresponding log entries:

```bash
# Example: you see failed request samples increasing in the metrics endpoint
# Find the matching log entries:
grep "markdown:" /var/log/nginx/error.log | grep -E "category=(conversion|resource_limit|system)"

# Example: you see failed_open or failed_closed samples increasing
# Find the matching log entries (outcome is the terminal classification):
grep "markdown:" /var/log/nginx/error.log | grep -E 'outcome=(failed_open|failed_closed|aborted)'

# See the full reason code distribution:
grep "markdown:" /var/log/nginx/error.log | \
  grep -oP 'reason=\K[a-z_]+' | sort | uniq -c | sort -rn
```

### Related Documentation

- [Decision Chain Model](../features/DECISION_CHAIN.md) — full check order, flowchart, outcome determination, and implementation details
- [Rollout Cookbook](ROLLOUT_COOKBOOK.md) — staged rollout procedures with observation checkpoints
- [Rollback Guide](OPERATIONAL_ROLLBACK.md) — how to disable or narrow conversion scope

---

## Decision Logging

The module emits structured decision log entries to the NGINX error log for requests that enter the [decision chain](../features/DECISION_CHAIN.md). Not every request produces a log entry. The `markdown_log_verbosity` directive gates which outcomes produce entries, so logging follows the decision chain processing rather than emitting unconditionally. Each entry records the reason code and request context, giving operators a per-decision view of why the module converted, skipped, or failed a request.

The `markdown_log_verbosity` directive controls decision logging. You need no separate directive. The existing verbosity knob gates which outcomes produce log entries and how much detail they contain. The directive name reflects this control. Set it to `debug` for full detail. The knob controls both presence and verbosity of entries.

### Log Entry Format

Every decision log entry uses a consistent, parseable structure with
space-separated `key=value` pairs. The prefix `markdown:` identifies these
entries in the NGINX error log.

#### Base Format (info verbosity)

```text
markdown: outcome=<OUTCOME> stage=<STAGE> reason=<REASON_CODE> event=<EVENT> method=<METHOD> uri=<URI> content_type=<TYPE>
```

Fields:

| Field | Description | Example Values |
|---|---|---|
| `outcome` | Request-level terminal outcome from the canonical registry | `converted`, `failed_open`, `failed_closed`, `aborted`, `-` |
| `stage` | Decision-chain stage that emitted the entry | `eligibility`, `conversion`, `precommit`, `postcommit` |
| `reason` | The [reason code](#reason-code-table) for this request's outcome | `converted`, `skipped_accept`, `failed_open` |
| `event` | Bounded streaming implementation event, or `-` for a reason-only entry | `engine_streaming`, `streaming_convert`, `-` |
| `method` | HTTP request method | `GET`, `HEAD`, `POST` |
| `uri` | Request URI (path only, no query string) | `/docs/api`, `/help/getting-started` |
| `content_type` | Upstream response Content-Type, or `-` if absent | `text/html`, `application/json`, `-` |

#### Extended Format (debug verbosity)

When `markdown_log_verbosity` is set to `debug`, three additional fields append:

```text
markdown: outcome=<OUTCOME> stage=<STAGE> reason=<REASON_CODE> event=<EVENT> method=<METHOD> uri=<URI> content_type=<TYPE> filter_value=<VALUE> accept=<ACCEPT> status=<STATUS>
```

Additional fields:

| Field | Description | Example Values |
|---|---|---|
| `filter_value` | Resolved value of the `markdown_filter` directive | `on`, `off`, `$variable` |
| `accept` | Client's Accept header value, or `-` if absent | `text/markdown`, `text/html, text/markdown;q=0.9`, `-` |
| `status` | Upstream response HTTP status code | `200`, `404`, `500` |

### Concrete Log Line Examples

These examples show what operators will see in `/var/log/nginx/error.log`. The examples include the NGINX timestamp, log level, PID, and connection fields for realism.

#### Successful conversion (info verbosity)

```text
2025/01/15 14:30:25 [info] 1234#0: *567 markdown: outcome=converted stage=conversion reason=converted event=- method=GET uri=/docs/api content_type=text/html while sending to client, client: 10.0.0.5, server: example.com, request: "GET /docs/api HTTP/1.1", upstream: "http://127.0.0.1:8080/docs/api", host: "example.com"
```

#### Skipped — Accept header does not request Markdown (info verbosity)

```text
2025/01/15 14:30:26 [info] 1234#0: *568 markdown: outcome=skipped stage=eligibility reason=skipped_accept event=- method=GET uri=/docs/api content_type=text/html while sending to client, client: 10.0.0.5, server: example.com, request: "GET /docs/api HTTP/1.1", host: "example.com"
```

#### Conversion failed open (warn verbosity or higher)

```text
2025/01/15 14:30:27 [warn] 1234#0: *569 markdown: outcome=failed_open stage=conversion reason=failed_open event=- method=GET uri=/blog/post-1 content_type=text/html while sending to client, client: 10.0.0.5, server: example.com, request: "GET /blog/post-1 HTTP/1.1", upstream: "http://127.0.0.1:8080/blog/post-1", host: "example.com"
```

#### Debug extended format

```text
2025/01/15 14:30:28 [info] 1234#0: *570 markdown: outcome=skipped stage=eligibility reason=not_eligible event=- method=POST uri=/api/submit content_type=text/html filter_value=on accept=text/markdown status=200 while sending to client, client: 10.0.0.5, server: example.com, request: "POST /api/submit HTTP/1.1", host: "example.com"
```

### Verbosity Gating

The `markdown_log_verbosity` directive controls which decision outcomes produce log entries and how much detail they contain. The default is `info`.

| Verbosity Level | Outcomes Logged | Format | Use Case |
|---|---|---|---|
| `error` | Failure outcomes only | Base | Production with minimal log volume |
| `warn` | Failure outcomes only | Base | Production monitoring |
| `info` (default) | All outcomes | Base | Recommended for rollout — full visibility into every decision |
| `debug` | All outcomes | Extended (adds `filter_value`, `accept`, `status`) | Troubleshooting — maximum detail for diagnosing specific requests |

At `error` and `warn` levels, non-failure outcomes (`not_eligible`, `skipped_*`, `disabled`, and `converted`) are silently suppressed. Both levels only emit failure outcomes such as `failed_open`, `failed_closed`, and `aborted`. At `info` and `debug` levels, the full bounded outcome set is available. Decision logs retain specific reason codes such as `memory_budget_exceeded`, `timeout`, or `ffi_panic`. The Prometheus `nginx_markdown_requests_total` family carries the terminal classification in its `outcome` label (`converted`, `failed_open`, `failed_closed`, `aborted`, `skipped`) and the outcome-specific code in its `reason` label. Specific failure reason codes such as `memory_budget_exceeded` and `timeout` appear in decision log entries, not in the `requests_total` `reason` label.

#### Configuration examples

```nginx
# Default — log all outcomes (recommended during rollout)
markdown_log_verbosity info;

# Production steady-state — log only failures
markdown_log_verbosity warn;

# Minimal — log only conversion failures (failed_open / failed_closed / aborted)
markdown_log_verbosity error;

# Troubleshooting — log everything with extended fields
markdown_log_verbosity debug;
```

### NGINX Log Level Mapping

The module maps decision outcomes to NGINX log levels so that NGINX's own `error_log` level acts as an outer filter:

| Outcome Type | NGINX Log Level | Reason Codes |
|---|---|---|
| Non-failure | `NGX_LOG_INFO` | `converted`, `skipped_*`, `disabled`, and non-terminal `event=` entries |
| Failure | `NGX_LOG_WARN` | `failed_open`, `failed_closed`, `aborted`, and canonical failure reason codes |

This means:
- If your NGINX `error_log` level is set to `warn`, you will only see failure decision entries (including streaming failures) regardless of `markdown_log_verbosity`.
- If your NGINX `error_log` level is set to `info` or `debug`, the `markdown_log_verbosity` directive controls which entries appear. The module emits streaming non-failure entries at `info` level.
- For full decision logging visibility (including non-terminal streaming events), ensure `error_log` is at `info` level or lower.

### Parsing Decision Log Entries

Decision log entries use a consistent `key=value` format designed for easy parsing with standard Unix tools.

#### Find all decision log entries

```bash
grep "markdown:" /var/log/nginx/error.log
```

#### Count entries by reason code

```bash
grep "markdown:" /var/log/nginx/error.log | \
  grep -oP 'reason=\K[A-Za-z_]+' | sort | uniq -c | sort -rn
```

Example output:

```text
   4521 skipped_accept
   1203 converted
    342 disabled
     18 not_eligible
      3 failed_open
      1 not_eligible
```

#### Find all failures
```bash
# Find all failures
grep "markdown:" /var/log/nginx/error.log | \
  grep -E 'outcome=(failed_open|failed_closed|aborted)'
```

#### Extract URIs that failed conversion
```bash
# Extract URIs that failed conversion
grep "markdown:" /var/log/nginx/error.log | \
  grep -E 'reason=(failed_open|failed_closed)' | \
  sed -nE 's/.*uri=([^ ]+).*/\1/p' | sort | uniq -c | sort -rn
```

#### Show reason code distribution per hour

```bash
grep "markdown:" /var/log/nginx/error.log | \
  awk '{print substr($1,1,13), $0}' | \
  grep -oP '^\S+ .*reason=\K[A-Za-z_]+' | sort | uniq -c
```

#### Extract full decision fields with awk

```bash
grep "markdown:" /var/log/nginx/error.log | \
  awk -F'markdown: ' '{print $2}' | \
  awk -F' (while|,) ' '{print $1}'
```

This extracts just the `key=value` portion, stripping the NGINX boilerplate. Example output:

```text
reason=converted method=GET uri=/docs/api content_type=text/html
reason=skipped_accept method=GET uri=/index.html content_type=text/html
reason=failed_open method=GET uri=/blog/post-1 content_type=text/html
```

#### Monitor decisions in real time

```bash
tail -f /var/log/nginx/error.log | grep "markdown:"
```

### Related Documentation

- [Decision Chain Model](../features/DECISION_CHAIN.md) — check order, reason code definitions, and outcome determination
- [Reason Code Reference](#reason-code-reference-for-operators) — complete reason code table with operator actions
- [Rollout Cookbook](ROLLOUT_COOKBOOK.md) — observation checkpoints that use decision log patterns
- [Rollback Guide](OPERATIONAL_ROLLBACK.md) — verification steps that check decision log entries after rollback

---

## References

- **Configuration Guide:** [CONFIGURATION.md](CONFIGURATION.md)
- **Installation Guide:** [INSTALLATION.md](INSTALLATION.md)
- **Documentation Index:** [../README.md](../README.md)
- **Performance Baselines:** [../testing/PERFORMANCE_BASELINES.md](../testing/PERFORMANCE_BASELINES.md)
- **Integration Tests:** [../testing/INTEGRATION_TESTS.md](../testing/INTEGRATION_TESTS.md)
- **Decompression:** [../features/DECOMPRESSION.md](../features/DECOMPRESSION.md)
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
| `nginx_markdown_requests_total` | Counter | One terminal request outcome; labels: outcome, stage, reason |
| `nginx_markdown_conversion_attempts_total` | Counter | Committed conversion attempts; label: engine |
| `nginx_markdown_conversion_deliveries_total` | Counter | Successful converted Markdown deliveries; label: engine |
| `nginx_markdown_conversion_duration_seconds` | Histogram | Conversion duration by engine |
| `nginx_markdown_input_bytes_total` | Counter | Input bytes read for conversion |
| `nginx_markdown_output_bytes_total` | Counter | Converted bytes delivered downstream |
| `nginx_markdown_inflight_requests` | Gauge | Current in-flight conversions |
| `nginx_markdown_streaming_peak_memory_bytes` | Gauge | Peak streaming working-memory high-water mark |
| `nginx_markdown_streaming_events_total` | Counter | Bounded streaming transitions |
| `nginx_markdown_decompression_events_total` | Counter | Bounded decompression events |
| `nginx_markdown_dynconf_reloads_total` | Counter | Dynamic-configuration reload outcomes |
| `nginx_markdown_build_info` | Gauge | Build identity; value is always `1` |

The authoritative labels, values, histogram boundaries, and help text are in
the [Prometheus Metrics Guide](prometheus-metrics.md). No JSON or legacy
plain-text metric fields are part of the 0.9.2 contract.


## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-09-01 | Hermes | Align failed-outcome queries and outcome field with aborted; memory_budget_exceeded refers only to conversion_memory; parser_memory maps to budget_exceeded |
| 0.9.2 | 2026-08-24 | Hermes | memory_budget_exceeded log pattern description now refers only to memory-limit failures |
| 0.9.2 | 2026-08-15 | Hermes | Update failure categories to conversion_error, memory_budget_exceeded, timeout, and ffi_panic |
| 0.9.2 | 2026-08-08 | Kang | Added missing nginx_markdown_streaming_peak_memory_bytes metric row |
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation). Retire the large-response threshold directive. |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, added mermaid diagrams where applicable, verified directive accuracy against code, added update tracking section |
