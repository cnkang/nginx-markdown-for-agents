# Rollback Guide — Disabling or Narrowing Markdown Conversion

## Table of Contents

1. [Overview](#overview)
2. [Key Principle: Config Change + Reload Only](#key-principle-config-change--reload-only)
3. [Rollback Methods](#rollback-methods)
   - [Method A: Disable in Scope (Fastest)](#method-a-disable-in-scope-fastest)
   - [Method B: Narrow the Map Variable](#method-b-narrow-the-map-variable)
   - [Method C: Restore Fail-Open Behavior](#method-c-restore-fail-open-behavior)
4. [Rollback Trigger Conditions](#rollback-trigger-conditions)
5. [Verification Steps](#verification-steps)
6. [Quick Reference](#quick-reference)

---

## Overview

This guide documents how to disable or narrow Markdown conversion scope when problems arise. All rollback methods take effect within seconds and require only an NGINX configuration change and reload.

Use this guide when an [observation checkpoint](ROLLOUT_COOKBOOK.md#observation-guidance) reveals unhealthy behavior, or when operator judgment calls for reducing conversion scope.

### Target Audience

- Site Reliability Engineers (SREs)
- DevOps Engineers
- System Administrators

### Related Documents

- [Decision Chain Model](../features/DECISION_CHAIN.md) — check order, reason codes, and outcome determination
- [ROLLOUT_COOKBOOK.md](ROLLOUT_COOKBOOK.md) — rollout stages, selective enablement patterns, observation guidance
- [CONFIGURATION.md](CONFIGURATION.md) — full directive reference
- [OPERATIONS.md](OPERATIONS.md) — operational guide and metrics reference

---

## Key Principle: Config Change + Reload Only

Rollback requires only an NGINX configuration edit and a graceful reload. Specifically:

- No module uninstallation
- No binary replacement
- No NGINX restart (full stop/start)
- No recompilation
- No downtime

The `nginx -s reload` command performs a graceful reload: NGINX re-reads the configuration, spawns new worker processes with the updated config, and drains existing connections on old workers. In-flight requests complete on the old configuration; new requests use the updated configuration immediately.

---

## Rollback Methods

Three methods are listed in order of speed. Pick the one that matches your situation.

| Method | Speed | Scope | When to Use |
|--------|-------|-------|-------------|
| [A: Disable in scope](#method-a-disable-in-scope-fastest) | Fastest (seconds) | All conversion stops in the affected scope | Widespread failures, need to stop all conversion immediately |
| [B: Narrow the map variable](#method-b-narrow-the-map-variable) | Fast (seconds) | Specific traffic segments excluded | One path or host is failing, others are healthy |
| [C: Restore fail-open](#method-c-restore-fail-open-behavior) | Fast (seconds) | Failure handling changes, conversion continues | Conversion works for most requests but `reject` mode is returning 502s on edge cases |

---

### Method A: Disable in Scope (Fastest)

Set `markdown_filter off` in the affected `location`, `server`, or `http` block. This stops all conversion in that scope immediately after reload.

#### When to Use

- Conversion failure rate is high across all enabled paths
- You need to stop all conversion immediately
- You are unsure which traffic segment is causing problems

#### Configuration Change


**Before (conversion enabled):**

```nginx
server {
    listen 80;
    server_name www.example.com;

    location /docs {
        markdown_filter on;
        proxy_pass http://backend;
    }

    location /blog {
        markdown_filter on;
        proxy_pass http://backend;
    }

    location / {
        proxy_pass http://backend;
    }
}
```

**After (conversion disabled):**

```nginx
server {
    listen 80;
    server_name www.example.com;

    location /docs {
        markdown_filter off;
        proxy_pass http://backend;
    }

    location /blog {
        markdown_filter off;
        proxy_pass http://backend;
    }

    location / {
        proxy_pass http://backend;
    }
}
```

To disable conversion across an entire server block, set `markdown_filter off` at the server level:

```nginx
server {
    listen 80;
    server_name www.example.com;

    markdown_filter off;

    location /docs {
        proxy_pass http://backend;
    }

    location /blog {
        proxy_pass http://backend;
    }

    location / {
        proxy_pass http://backend;
    }
}
```

#### Apply

```bash
nginx -t && nginx -s reload
```

#### Verify

Follow the [Verification Steps](#verification-steps) section. The key signal is `SKIP_CONFIG` appearing in decision logs for the affected traffic.

---

### Method B: Narrow the Map Variable

Adjust the `map` directive to exclude the problematic traffic segment while keeping conversion active for healthy paths. Use this when one path or host is failing but others are fine.

#### When to Use

- A specific path shows high failure rates while others are healthy
- A specific host is experiencing issues
- You want to reduce scope without fully disabling conversion

#### Configuration Change — Exclude a Path

**Before (broad scope):**

```nginx
http {
    map $uri $markdown_enabled {
        default         off;
        "~^/docs"       on;
        "~^/help"       on;
        "~^/blog"       on;
        "~^/guides"     on;
    }

    server {
        listen 80;
        server_name www.example.com;

        location / {
            markdown_filter $markdown_enabled;
            proxy_pass http://backend;
        }
    }
}
```

**After (problematic path removed):**

```nginx
http {
    map $uri $markdown_enabled {
        default         off;
        "~^/docs"       on;
        "~^/help"       on;
        # "~^/blog"     on;   # disabled — high failure rate
        "~^/guides"     on;
    }

    server {
        listen 80;
        server_name www.example.com;

        location / {
            markdown_filter $markdown_enabled;
            proxy_pass http://backend;
        }
    }
}
```

#### Configuration Change — Exclude a Host

**Before (multiple hosts enabled):**

```nginx
http {
    map $host $markdown_by_host {
        default                 off;
        staging.example.com     on;
        www.example.com         on;
    }

    server {
        listen 80;
        server_name staging.example.com www.example.com;

        location / {
            markdown_filter $markdown_by_host;
            proxy_pass http://backend;
        }
    }
}
```

**After (problematic host removed):**

```nginx
http {
    map $host $markdown_by_host {
        default                 off;
        staging.example.com     on;
        # www.example.com       on;   # disabled — investigating failures
    }

    server {
        listen 80;
        server_name staging.example.com www.example.com;

        location / {
            markdown_filter $markdown_by_host;
            proxy_pass http://backend;
        }
    }
}
```

#### Apply

```bash
nginx -t && nginx -s reload
```

#### Verify

Follow the [Verification Steps](#verification-steps) section. Confirm that the excluded path or host now produces `SKIP_CONFIG` in decision logs, while other paths continue converting.

---

### Method C: Restore Fail-Open Behavior

If you changed `markdown_on_error` to `reject` and conversion failures are returning 502 errors to clients, switch back to `pass`. This restores fail-open behavior: failed conversions serve the original HTML instead of an error response. Conversion continues for requests that succeed.

#### When to Use

- `markdown_on_error reject` is active and conversion failures are causing 502 responses
- Most conversions succeed, but edge cases in certain HTML structures cause failures
- You want to keep conversion running while investigating failures

#### Configuration Change

**Before (fail-closed):**

```nginx
server {
    listen 80;
    server_name www.example.com;

    location /docs {
        markdown_filter on;
        markdown_on_error reject;
        proxy_pass http://backend;
    }
}
```

**After (fail-open restored):**

```nginx
server {
    listen 80;
    server_name www.example.com;

    location /docs {
        markdown_filter on;
        markdown_on_error pass;
        proxy_pass http://backend;
    }
}
```

#### Apply

```bash
nginx -t && nginx -s reload
```

#### Verify

Follow the [Verification Steps](#verification-steps) section. The key signal is that `ELIGIBLE_FAILED_CLOSED` entries stop appearing in decision logs and are replaced by `ELIGIBLE_FAILED_OPEN` entries. Clients receive original HTML instead of 502 errors when conversion fails.

---

## Rollback Trigger Conditions

Roll back (or narrow scope) when any of the following conditions occur. These align with the "stop and investigate" triggers in the [Rollout Cookbook observation guidance](ROLLOUT_COOKBOOK.md#stop-and-investigate-triggers).

### Failure Rate Threshold

Conversion failure rate exceeds 5% of conversion attempts over any 1-hour window.

```bash
# Check failure count vs. total conversion attempts
curl -s http://localhost/markdown-metrics | \
  grep -E "conversions_(attempted|succeeded|failed)"
```

If `conversions_failed` is growing faster than expected relative to `conversions_attempted`, roll back.

### Latency Exceeding Timeout

Conversion latency approaches or exceeds the configured `markdown_timeout`. Check the latency bucket distribution:

```bash
curl -s http://localhost/markdown-metrics | \
  grep "conversion_latency"
```

If conversions are clustering in the highest latency buckets or you see timeout-related failures in logs, roll back.

### Upstream Errors

Upstream error rate increases after enabling the module. Compare upstream 5xx rates before and after enablement. The module should not cause upstream errors, but interactions with decompression or buffering could surface latent issues.

### Unexpected Content

Clients receive unexpected content types or malformed responses. Verify with:

```bash
curl -sD - -o /dev/null \
  -H "Accept: text/markdown" \
  http://www.example.com/docs/
# Expected: Content-Type: text/markdown; charset=utf-8
```

If the Content-Type is wrong or the response body is unexpected, roll back.

### Operator Judgment

Any observation checkpoint result that does not meet the "safe to continue" criteria documented in the [Rollout Cookbook](ROLLOUT_COOKBOOK.md#rollout-stages) is grounds for rollback. Trust your judgment — if something looks wrong, narrow scope first and investigate second.

---

## Verification Steps

After applying any rollback method, verify that the change took effect. Run these checks in order.

### 1. Check Logs for SKIP_CONFIG

After disabling conversion (Methods A and B), the decision log should show `SKIP_CONFIG` for affected traffic:

```bash
# Watch for new SKIP_CONFIG entries after reload
grep "markdown decision:" /var/log/nginx/error.log | \
  grep "reason=SKIP_CONFIG" | tail -10
```

For Method C (restoring fail-open), check that `ELIGIBLE_FAILED_CLOSED` entries stop and `ELIGIBLE_FAILED_OPEN` entries appear instead:

```bash
grep "markdown decision:" /var/log/nginx/error.log | \
  grep -E "reason=ELIGIBLE_FAILED_(OPEN|CLOSED)" | tail -10
```

### 2. Confirm Metrics Stop Incrementing

For Methods A and B, conversion metrics for the affected scope should stop incrementing:

```bash
# Take a snapshot
curl -s http://localhost/markdown-metrics | \
  grep -E "conversions_(attempted|succeeded|failed)"

# Wait 60 seconds, then compare
sleep 60

curl -s http://localhost/markdown-metrics | \
  grep -E "conversions_(attempted|succeeded|failed)"
```

The counters should remain unchanged (or increase only for scopes that are still enabled).

### 3. Confirm Clients Receive HTML

Send a test request to a rolled-back path and verify the response is HTML, not Markdown:

```bash
curl -sD - \
  -H "Accept: text/markdown" \
  http://www.example.com/docs/ | head -20
# Expected: Content-Type: text/html (not text/markdown)
```

For Method C, send a request that you know triggers a conversion failure and verify the client receives HTML (not a 502):

```bash
curl -sD - -o /dev/null \
  -H "Accept: text/markdown" \
  http://www.example.com/docs/problematic-page
# Expected: HTTP/1.1 200 OK (not 502 Bad Gateway)
# Expected: Content-Type: text/html
```

---

## Quick Reference

Copy-paste rollback sequence for the most common scenario (disable all conversion and verify):

```bash
# 1. Edit config: set markdown_filter off in the affected scope
#    (see Method A above for the exact change)

# 2. Test and reload
nginx -t && nginx -s reload

# 3. Verify: check for SKIP_CONFIG in logs
grep "markdown decision:" /var/log/nginx/error.log | \
  grep "reason=SKIP_CONFIG" | tail -5

# 4. Verify: confirm conversion metrics stopped
curl -s http://localhost/markdown-metrics | \
  grep -E "conversions_(attempted|succeeded|failed)"

# 5. Verify: confirm client receives HTML
curl -sD - -o /dev/null \
  -H "Accept: text/markdown" \
  http://www.example.com/docs/
# Expected: Content-Type: text/html
```
