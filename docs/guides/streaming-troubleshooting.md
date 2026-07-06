# Streaming Troubleshooting Guide

Quick-reference diagnostics for the streaming conversion engine.  Each scenario
follows: **Symptom → Likely Cause → Diagnosis Steps → Resolution**.

> **Audience**: Operators under pressure.  Copy-paste the commands, read the
> output, follow the action.

Related docs:
- [Streaming Rollout Cookbook](streaming-rollout-cookbook.md) — phased rollout,
  monitoring, emergency disable, rollback decision table
- [Configuration Reference — Streaming Directives](CONFIGURATION.md)
- [Prometheus Metrics Guide](prometheus-metrics.md)

---

## Scenario 1: "Why isn't my response streaming?"

### Symptom

Responses are converted via full-buffer even though you expected streaming.
The `streaming_engine_choice_total{engine="streaming"}` metric stays at zero
or does not increment for the path in question.

### Likely Cause

One of: engine disabled, `conditional_requests full_support` (default) blocking
streaming, response below threshold, Content-Length present and below threshold,
content type excluded.

### Diagnosis Steps

**Step 1 — Check engine setting:**

```bash
nginx -T 2>/dev/null | grep -i markdown_streaming_engine
```

Expected: `markdown_streaming_engine auto;` or `on;` for the target location.
If you see `off`, streaming is explicitly disabled.

**Step 1b — Check cache_validation setting:**

```bash
nginx -T 2>/dev/null | grep -i markdown_cache_validation
```

Default is `ims_only` (built-in and `balanced` profile). When
`markdown_cache_validation` is `full` (the `strict_cache` profile default),
the streaming selector always selects full-buffer because full ETag support
requires the complete converted output before headers. To activate streaming
in `auto` mode, use `markdown_cache_validation ims_only` or `off`.

**Step 2 — Check threshold vs response size:**

```bash
nginx -T 2>/dev/null | grep -i markdown_stream_threshold
```

Default is `1m` (1 MiB).  In `auto` mode, responses smaller than this use
full-buffer.  Verify your test response exceeds the threshold:

```bash
curl -s -o /dev/null -w '%{size_download}\n' http://localhost/target-path
```

**Step 3 — Check for Content-Length header:**

```bash
curl -sI http://localhost/target-path | grep -i content-length
```

When the upstream sends `Content-Length` and the value is below
`markdown_stream_threshold`, the engine selects full-buffer (reason code
`content_length_known`).

**Step 4 — Check content type exclusion:**

```bash
nginx -T 2>/dev/null | grep -i markdown_stream_excluded_types
```

If your response's `Content-Type` matches an excluded type, it bypasses
streaming entirely (reason code `excluded_content_type`).

**Step 5 — Check reason codes in metrics:**

```bash
curl -s -H 'Accept: application/json' http://localhost/markdown-metrics | \
  python3 -c "
import sys, json
d = json.load(sys.stdin)
# Print all reason code counters
for k, v in sorted(d.items()):
    if 'reason' in k or 'choice' in k or 'candidate' in k:
        print(f'{k}: {v}')
"
```

**Step 6 — Check diagnostics endpoint:**

```bash
curl -s http://localhost/nginx-markdown/diagnostics | python3 -m json.tool
```

Look for the `streaming` section showing current configuration state.

### Resolution

| Finding | Fix |
|---------|-----|
| Engine is `off` | Set `markdown_streaming_engine auto;` and reload |
| `cache_validation` is `full` (`strict_cache` default) | Set `markdown_cache_validation ims_only;` or `off;` to allow streaming |
| Response below threshold | Lower `markdown_stream_threshold` or accept full-buffer for small responses |
| Content-Length below threshold | Expected behavior in `auto` mode; use `on` to force streaming regardless |
| Content type excluded | Remove type from `markdown_stream_excluded_types` if streaming is desired |

---

## Scenario 2: "Why is streaming falling back to full-buffer?"

### Symptom

Streaming is engaged but JSON `streaming.fallback_total` or Prometheus
`nginx_markdown_streaming_fallback_total` is growing.  Clients still get correct Markdown
(no truncation), but via the full-buffer path instead of streaming.

### Likely Cause

A pre-commit error triggered a safe fallback.  The three pre-commit reason
codes are:
- `precommit_html_error` — HTML could not be parsed in the pre-commit window
- `precommit_budget` — memory budget exceeded before commit point
- `precommit_timeout` — parse timeout before commit point

### Diagnosis Steps

**Step 1 — Identify which reason code dominates:**

```bash
curl -s -H 'Accept: application/json' http://localhost/markdown-metrics | \
  python3 -c "
import sys, json
d = json.load(sys.stdin)
for code in ['precommit_html_error', 'precommit_budget', 'precommit_timeout']:
    key = f'streaming_reason_{code}'
    alt_key = code
    val = d.get(key, d.get(alt_key, 'not found'))
    print(f'{code}: {val}')
"
```

**Step 2 — Check structured logs for details:**

```bash
grep -E 'reason="?precommit' /var/log/nginx/error.log | tail -20
```

Look for the URI and any additional context (HTML snippet, budget value).

**Step 3 — Check budget settings:**

```bash
nginx -T 2>/dev/null | grep -iE 'markdown_stream_precommit_buffer|markdown_parser_budget|markdown_parse_timeout'
```

**Step 4 — Inspect the failing content (if `precommit_html_error`):**

```bash
# Fetch the raw upstream response for a known-failing path
curl -s http://backend-host/problem-path | head -200
```

Look for malformed HTML, unclosed tags, or unusual document structure that
the parser cannot handle in the bounded pre-commit window.

### Resolution

| Reason Code | Action |
|-------------|--------|
| `precommit_html_error` | Investigate upstream HTML quality; consider excluding the path from streaming |
| `precommit_budget` | Increase `markdown_stream_precommit_buffer` (e.g., `256k` → `512k`) or raise `markdown_parser_budget` |
| `precommit_timeout` | Increase `markdown_parse_timeout`; investigate if the content is unusually complex |

> **Note**: Fallbacks are safe — the client receives correct output via
> full-buffer.  A high fallback rate means streaming is not effective for that
> traffic, not that anything is broken.  See
> [Rollout Cookbook — Monitoring Guidance](streaming-rollout-cookbook.md#monitoring-guidance)
> for acceptable thresholds.

---

## Scenario 3: "Post-commit failure — client got truncated output"

### Symptom

JSON `streaming.postcommit_error_total` or Prometheus
`nginx_markdown_streaming_failure_total` is incrementing.  Clients may report incomplete
Markdown responses.  Logs show `postcommit_*` reason codes.

### What Happened

After the streaming engine committed response headers to the client (sent
`200 OK` with `Content-Type: text/markdown`), an unrecoverable error
occurred.  Because headers are already on the wire, the module cannot
transparently fall back to full-buffer.  The client receives whatever was
flushed before the error, then the connection closes.

### Why This Is Critical

Unlike pre-commit fallbacks, post-commit failures are **not recoverable**.
The client sees partial content.  Any sustained rate above 0.01% warrants
immediate investigation and likely rollback.

### Diagnosis Steps

**Step 1 — Confirm post-commit failures are occurring:**

```bash
curl -s -H 'Accept: application/json' http://localhost/markdown-metrics | \
  python3 -c "
import sys, json
d = json.load(sys.stdin)
for code in ['postcommit_parse_error', 'postcommit_budget_exceeded', 'postcommit_io_error']:
    key = f'streaming_reason_{code}'
    alt_key = code
    val = d.get(key, d.get(alt_key, 'not found'))
    print(f'{code}: {val}')
"
```

**Step 2 — Check logs for affected paths:**

```bash
grep -E 'reason="?postcommit' /var/log/nginx/error.log | \
  grep -oP 'uri=[^ ]*' | sort | uniq -c | sort -rn | head -10
```

**Step 3 — Determine failure rate:**

```bash
curl -s -H 'Accept: application/json' http://localhost/markdown-metrics | \
  python3 -c "
import sys, json
d = json.load(sys.stdin)
streaming = d.get('streaming', {})
selected = streaming.get('requests_total', 0)
failed = streaming.get('postcommit_error_total', 0)
rate = (failed / selected * 100) if selected > 0 else 0
print(f'Post-commit failure rate: {rate:.4f}% ({failed}/{selected})')
if rate > 0.01:
    print('ACTION: Consider immediate rollback')
elif rate > 0:
    print('ACTION: Investigate; monitor closely')
else:
    print('STATUS: Healthy')
"
```

**Step 4 — Identify the specific failure type:**

| Reason Code | Cause | Investigation |
|-------------|-------|---------------|
| `postcommit_parse_error` | HTML parsing failed mid-stream | Check if upstream HTML changes structure mid-response (e.g., chunked transfer with invalid fragments) |
| `postcommit_budget_exceeded` | Memory budget exhausted after commit | The response grew beyond `markdown_parser_budget` after the pre-commit window passed |
| `postcommit_io_error` | I/O failure writing to client or reading from upstream | Check network stability, upstream health, client disconnects |

### Resolution

**Immediate action** — disable streaming for affected paths:

```bash
# Edit nginx.conf — add to affected location:
#   markdown_streaming_engine off;
nginx -t && nginx -s reload
```

**If widespread** — disable globally:

```bash
# In http {} block:
#   markdown_streaming_engine off;
nginx -t && nginx -s reload
```

**After stabilization**, investigate root cause:

| Reason Code | Long-term Fix |
|-------------|---------------|
| `postcommit_parse_error` | Exclude problematic paths; report HTML issues to content team |
| `postcommit_budget_exceeded` | Increase `markdown_parser_budget`; raise `markdown_stream_threshold` so large responses stay in full-buffer |
| `postcommit_io_error` | Fix upstream connectivity; check client timeout settings |

See [Rollout Cookbook — Emergency Disable](streaming-rollout-cookbook.md#emergency-disable)
for the full disable procedure.

> **Prevention is the only cure.** Post-commit failures cannot be undone for
> the affected client.  Minimize their likelihood by:
> - Testing in staging with realistic HTML before enabling streaming in production
> - Using `auto` mode with a conservative `markdown_stream_threshold` (e.g., `2m`)
> - Setting `markdown_parser_budget` high enough for your largest known documents
> - Monitoring `nginx_markdown_streaming_failure_total` with a zero-tolerance alert (any non-zero → page)

---

## Scenario 6: "Clients get HTTP 500 errors when streaming is enabled"

### Symptom

After enabling streaming, clients that previously received HTML or full-buffer
Markdown now get HTTP 500 (or 502/503) error responses.  The
JSON `streaming.precommit_reject_total` or Prometheus
`nginx_markdown_streaming_fallback_total{phase="precommit",action="reject"}`
metric is incrementing.

### What Happened

The streaming engine encountered a pre-commit error, but the error policy is
set to `fail_closed` instead of the default `pass`:

```text
markdown_error_policy fail_closed;
```

With `reject`, when a pre-commit error occurs (HTML parse failure, budget
exceeded, timeout), the module does **not** replay the original HTML to the
client.  Instead, it returns an HTTP error.  This is a deliberate "fail-closed"
configuration — but it means any pre-commit error becomes a client-visible
outage.

### Why This Happens

| Configuration | Pre-Commit Error | Client Sees |
|---------------|-----------------|-------------|
| `markdown_error_policy pass;` (default) | Error triggers replay of buffered HTML | Original HTML (correct, safe) |
| `markdown_error_policy fail_closed;` | Error triggers HTTP error | 500 Internal Server Error |

The `reject` policy is intended for strict environments where serving
unconverted HTML is unacceptable (e.g., API documentation endpoints that
must always return Markdown).  However, it converts any pre-commit failure
into a client-facing outage.

### Diagnosis Steps

**Step 1 — Confirm reject policy is active:**

```bash
nginx -T 2>/dev/null | grep -i markdown_error_policy
```

If you see `reject`, this is the cause.

**Step 2 — Check pre-commit rejection metrics:**

```bash
curl -s -H 'Accept: application/json' http://localhost/markdown-metrics | \
  python3 -c "
import sys, json
d = json.load(sys.stdin)
for code in ['precommit_html_error', 'precommit_budget', 'precommit_timeout']:
    reject_key = f'streaming_reason_{code}_reject'
    fallback_key = f'streaming_reason_{code}'
    val = d.get(reject_key, d.get(fallback_key, 'not found'))
    print(f'{code}: {val}')
streaming = d.get('streaming', {})
total_reject = streaming.get('precommit_reject_total', 0)
total_fallback = streaming.get('fallback_total', 0)
print(f'\\nTotal pre-commit rejects: {total_reject}')
print(f'Total pre-commit fallbacks: {total_fallback}')
"
```

**Step 3 — Check logs for the reject action:**

```bash
grep -E 'reason="?precommit.*action="?reject' /var/log/nginx/error.log | tail -20
```

Example log line:

```text
2026/06/04 14:32:17 [error] 1234#0: *567 markdown streaming: pre-commit error,
  reason="precommit_html_error", action="reject", uri="/api/docs/complex-page",
  category="STREAMING_PRECOMMIT_REJECT", client: 10.0.1.50, server: api.example.com
```

**Step 4 — Identify affected URIs:**

```bash
grep -E 'STREAMING_PRECOMMIT_REJECT' /var/log/nginx/error.log | \
  grep -oP 'uri=[^ ]*' | sort | uniq -c | sort -rn | head -10
```

### Resolution

**Immediate action — restore fail-open behavior:**

```bash
# Change from reject to pass (restores HTML fallback for pre-commit errors)
# Edit nginx.conf:
#   markdown_error_policy pass;
nginx -t && nginx -s reload
```

**Alternative — disable streaming entirely:**

```bash
# Edit nginx.conf:
#   markdown_streaming_engine off;
nginx -t && nginx -s reload
```

**Verify the fix:**

```bash
# After reload, confirm rejects have stopped
sleep 10
curl -s -H 'Accept: application/json' http://localhost/markdown-metrics | \
  python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Reject count: {d.get(\"streaming\", {}).get(\"precommit_reject_total\", 0)}')
"
# Value should stop incrementing
```

### When to Escalate to Support

Escalate if **any** of the following are true:

1. **Errors persist after disabling streaming** — if HTTP errors continue after
   `markdown_streaming_engine off`, the issue is not streaming-specific.  It may
   be in the full-buffer path, the upstream, or NGINX itself.

2. **Errors occur in the full-buffer path too** — check:
   ```bash
   curl -s -H 'Accept: application/json' http://localhost/markdown-metrics | \
     python3 -c "
    import sys, json
    d = json.load(sys.stdin)
    streaming = d.get(\"streaming\", {})
    print(f'Full-buffer failures: {d.get(\"conversions_failed\", 0)}')
    print(f'Streaming failures:   {streaming.get(\"postcommit_error_total\", 0)}')
    "
   ```
   If both are incrementing, the issue is broader than the streaming engine.

3. **The module cannot be disabled** — if even `markdown_filter off` does not
   resolve the error, the problem is outside the Markdown module entirely.

For escalation, collect:
```bash
# Diagnostic bundle for support
nginx -T > /tmp/nginx-full-config.txt 2>&1
cp /var/log/nginx/error.log /tmp/nginx-error-snapshot.log
curl -s http://localhost/nginx-markdown/diagnostics > /tmp/diagnostics.json
curl -s -H 'Accept: application/json' http://localhost/markdown-metrics > /tmp/metrics.json
```

---

## Escalation Decision Tree

Use this tree when streaming errors occur in production.  Follow the first
matching branch.

```text
┌─────────────────────────────────────────────────────────────────────┐
│                    STREAMING ERROR DETECTED                          │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  Is it a post-commit    │
                    │  error? (postcommit_*)  │
                    └────────────┬────────────┘
                           yes/  \no
                          /       \
            ┌────────────▼─┐    ┌──▼──────────────────┐
            │ ACTION 1:    │    │ Is the error policy  │
            │ Disable      │    │ set to "reject"?     │
            │ streaming    │    └──────────┬───────────┘
            │ immediately  │          yes/  \no
            └──────────────┘         /       \
                              ┌─────▼──────┐  ┌──▼──────────────┐
                              │ ACTION 2:  │  │ Safe fallback   │
                              │ Switch to  │  │ is working.     │
                              │ pass policy│  │ Monitor; no     │
                              └────────────┘  │ immediate action│
                                              └──────┬──────────┘
                                                     │
                              ┌───────────────────────▼──────────────┐
                              │ Do errors persist after ALL streaming │
                              │ is disabled?                          │
                              └───────────────────┬──────────────────┘
                                            yes/  \no
                                           /       \
                             ┌────────────▼─┐    ┌──▼──────────────┐
                             │ ACTION 3:    │    │ Streaming was   │
                             │ Escalate to  │    │ the cause.      │
                             │ support —    │    │ Keep it off;    │
                             │ issue is not │    │ investigate     │
                             │ streaming-   │    │ root cause.     │
                             │ specific     │    └─────────────────┘
                             └──────────────┘
```

### Decision Summary

| Condition | Action | Command |
|-----------|--------|---------|
| Post-commit error (`postcommit_*`) | **Disable streaming** | See below |
| Pre-commit error + `reject` policy | **Switch to `pass`** | See below |
| Errors persist after streaming off | **Escalate to support** | Collect diagnostic bundle |

### Commands for Each Action

**ACTION 1 — Disable streaming (post-commit failure):**

```bash
# 1. Disable streaming
sed -i 's/markdown_streaming_engine.*/markdown_streaming_engine off;/' /etc/nginx/nginx.conf
# Or edit manually and set: markdown_streaming_engine off;

# 2. Validate and reload
nginx -t && nginx -s reload

# 3. Verify no new post-commit failures
sleep 30
curl -s -H 'Accept: application/json' http://localhost/markdown-metrics | \
  python3 -c "
import sys, json
d = json.load(sys.stdin)
failures = d.get('streaming', {}).get('postcommit_error_total', 0)
print(f'Post-commit failures (should stop growing): {failures}')
"
```

**ACTION 2 — Switch to pass policy (pre-commit reject):**

```bash
# 1. Change error policy to fail-open (migrate from old directive if needed)
sed -i 's/markdown_streaming_on_error.*/markdown_error_policy pass;/' /etc/nginx/nginx.conf
sed -i 's/markdown_on_error.*/markdown_error_policy pass;/' /etc/nginx/nginx.conf
# Or edit manually and set: markdown_error_policy pass;

# 2. Validate and reload
nginx -t && nginx -s reload

# 3. Verify clients are no longer getting HTTP errors
sleep 10
curl -s -H 'Accept: text/markdown' http://localhost/previously-failing-path \
  -o /dev/null -w 'HTTP %{http_code}\n'
# Should return 200, not 500
```

**ACTION 3 — Escalate (errors persist after streaming is off):**

```bash
# 1. Confirm streaming is fully disabled
nginx -T 2>/dev/null | grep markdown_streaming_engine
# Should show only "off" entries

# 2. Confirm errors are still occurring
tail -20 /var/log/nginx/error.log | grep -i 'markdown\|error'

# 3. Collect diagnostic bundle
mkdir -p /tmp/markdown-escalation
nginx -T > /tmp/markdown-escalation/nginx-config.txt 2>&1
tail -1000 /var/log/nginx/error.log > /tmp/markdown-escalation/error-log-tail.txt
curl -s http://localhost/nginx-markdown/diagnostics > /tmp/markdown-escalation/diagnostics.json 2>&1
curl -s -H 'Accept: application/json' http://localhost/markdown-metrics > /tmp/markdown-escalation/metrics.json 2>&1

# 4. Report to support with bundle
echo "Escalation bundle saved to /tmp/markdown-escalation/"
echo "Include: error description, timeline, and the files above."
```

---

## Scenario 4: "How do I check if streaming is working?"

### Symptom

You enabled streaming and want to confirm it is actually engaged and
producing output.

### Diagnosis Steps

**Step 1 — Check metrics for streaming selections:**

```bash
curl -s -H 'Accept: application/json' http://localhost/markdown-metrics | \
  python3 -c "
import sys, json
d = json.load(sys.stdin)
streaming = d.get(\"streaming\", {})
print(f'Streaming path hits:   {d.get(\"streaming_path_hits\", 0)}')
print(f'Streaming selected:    {streaming.get(\"requests_total\", 0)}')
print(f'Streaming succeeded:   {streaming.get(\"succeeded_total\", 0)}')
print(f'Streaming fallbacks:   {streaming.get(\"fallback_total\", 0)}')
print(f'Streaming failures:    {streaming.get(\"postcommit_error_total\", 0)}')
"
```

If JSON `streaming.requests_total` > 0 and growing, streaming is engaged.

**Step 2 — Inspect logs for streaming decisions:**

```bash
grep -E 'streaming.*(eligible|selected|engine)' /var/log/nginx/error.log | tail -10
```

**Step 3 — Check diagnostics endpoint for runtime state:**

```bash
curl -s http://localhost/nginx-markdown/diagnostics | \
  python3 -c "
import sys, json
d = json.load(sys.stdin)
streaming = d.get('streaming', d)
print(json.dumps(streaming, indent=2))
"
```

**Step 4 — Send a test request that should trigger streaming:**

```bash
# Generate a large HTML response (must exceed markdown_stream_threshold)
# Then request conversion:
curl -s -H 'Accept: text/markdown' \
  -o /dev/null -w 'HTTP %{http_code}, %{size_download} bytes, %{time_total}s\n' \
  http://localhost/large-document-path
```

**Step 5 — Verify with Prometheus (if configured):**

```bash
curl -s -H 'Accept: text/plain; version=0.0.4' \
  http://localhost/markdown-metrics | \
  grep -E '^nginx_markdown_(streaming_|true_streaming_)'
```

### What "Working" Looks Like

- JSON `streaming.requests_total` > 0 and growing
- JSON `streaming.succeeded_total` growing (requests completing via streaming)
- JSON `streaming.fallback_total` low (< 5% of streaming requests)
- JSON `streaming.postcommit_error_total` zero or near-zero
- diagnostics `streaming_metrics.output_bytes_total` growing when inspecting
  `/nginx-markdown/diagnostics`

---

## Scenario 5: "How do I disable streaming?"

### Symptom

You need to turn off streaming immediately (incident response) or
permanently (policy decision).

### Quick Reference

**Per-location disable:**

```nginx
location /affected-path/ {
    markdown_streaming_engine off;
}
```

**Global disable:**

```nginx
http {
    markdown_streaming_engine off;
}
```

**Apply the change:**

```bash
nginx -t && nginx -s reload
```

**Verify streaming has stopped:**

```bash
# Record current value
curl -s -H 'Accept: application/json' http://localhost/markdown-metrics | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('streaming', {}).get('requests_total', 0))"

# Wait 30 seconds, then check again — value should not increase
sleep 30

curl -s -H 'Accept: application/json' http://localhost/markdown-metrics | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('streaming', {}).get('requests_total', 0))"
```

### Additional Options

| Goal | Configuration |
|------|---------------|
| Disable for one location | `markdown_streaming_engine off;` in the location block |
| Disable globally | `markdown_streaming_engine off;` in the `http` block |
| Raise threshold (fewer streaming candidates) | `markdown_stream_threshold 10m;` |
| Exclude specific content types | `markdown_stream_excluded_types text/event-stream application/pdf;` |

For the full emergency disable procedure including verification and rollback,
see [Rollout Cookbook — Emergency Disable](streaming-rollout-cookbook.md#emergency-disable).

---

## Log and Metrics Examples

This section provides concrete examples of what operators see in production.
Each example shows the raw output, annotates the key fields, and explains
what action to take.

### NGINX Error Log: Streaming Decisions (Info Level)

These entries appear at `info` level when `markdown_log_verbosity info` (the
default during rollout).  They confirm normal streaming operation.

#### Successful streaming selection

```text
2025/01/15 14:30:25 [info] 1234#0: *8901 markdown decision: reason=ENGINE_STREAMING engine=streaming phase=header_filter action=stream content_type=text/html chunked=1 content_length_known=0 while sending to client, client: 10.0.0.5, server: docs.example.com, request: "GET /api/reference HTTP/1.1", upstream: "http://127.0.0.1:8080/api/reference", host: "docs.example.com"
```

**Field breakdown:**

| Field | Value | Meaning |
|-------|-------|---------|
| `reason=ENGINE_STREAMING` | Reason code | Streaming engine was selected for this request |
| `engine=streaming` | Engine path | True streaming conversion will be used |
| `phase=header_filter` | Decision point | Decision made during the header filter phase |
| `action=stream` | Outcome | Request will be streamed |
| `content_type=text/html` | Upstream type | Response is HTML (eligible for conversion) |
| `chunked=1` | Transfer encoding | Response uses chunked transfer (no known size) |
| `content_length_known=0` | Size known? | No Content-Length header — streaming candidate |

**Action**: None.  This is healthy streaming operation.

#### Streaming completed successfully

```text
2025/01/15 14:30:26 [info] 1234#0: *8901 markdown decision: reason=STREAMING_CONVERT engine=streaming phase=postcommit action=complete content_type=text/html chunked=1 content_length_known=0 while sending to client, client: 10.0.0.5, server: docs.example.com, request: "GET /api/reference HTTP/1.1", upstream: "http://127.0.0.1:8080/api/reference", host: "docs.example.com"
```

**Field breakdown:**

| Field | Value | Meaning |
|-------|-------|---------|
| `reason=STREAMING_CONVERT` | Reason code | Streaming conversion finished successfully |
| `phase=postcommit` | Phase | Conversion completed after headers were sent |
| `action=complete` | Outcome | Full Markdown response delivered to client |

**Action**: None.  Streaming worked end-to-end.

#### Pre-commit fallback (safe)

```text
2025/01/15 14:30:27 [info] 1234#0: *8902 markdown decision: reason=STREAMING_FALLBACK_PREBUFFER engine=full_buffer phase=precommit action=fallback content_type=text/html chunked=1 content_length_known=0 markdown_error_policy=pass while sending to client, client: 10.0.0.8, server: docs.example.com, request: "GET /blog/complex-post HTTP/1.1", upstream: "http://127.0.0.1:8080/blog/complex-post", host: "docs.example.com"
```

**Field breakdown:**

| Field | Value | Meaning |
|-------|-------|---------|
| `reason=STREAMING_FALLBACK_PREBUFFER` | Reason code | Streaming abandoned before committing headers |
| `engine=full_buffer` | Engine path | Fell back to full-buffer conversion |
| `phase=precommit` | Phase | Decision made before headers were sent to client |
| `action=fallback` | Outcome | Client will receive correct output via full-buffer |
| `markdown_error_policy=pass` | Error policy | On errors, pass original HTML through |

**Action**: Monitor the rate.  A few fallbacks are expected; if this exceeds
5% of candidates, investigate the content.

---

### NGINX Error Log: Streaming Failures (Error/Warn Level)

These entries indicate problems.  Post-commit failures are critical because
the client may receive truncated output.

#### Post-commit parse error (critical)

```text
2025/01/15 14:31:02 [warn] 1234#0: *8910 markdown decision: reason=STREAMING_FAIL_POSTCOMMIT engine=streaming phase=postcommit action=abort committed=1 fallback_available=0 content_type=text/html category=FAIL_CONVERSION while sending to client, client: 10.0.0.12, server: docs.example.com, request: "GET /reports/quarterly HTTP/1.1", upstream: "http://127.0.0.1:8080/reports/quarterly", host: "docs.example.com"
```

**Field breakdown:**

| Field | Value | Meaning |
|-------|-------|---------|
| `reason=STREAMING_FAIL_POSTCOMMIT` | Reason code | Error occurred after headers were sent |
| `phase=postcommit` | Phase | Past the point of no return |
| `action=abort` | Outcome | Connection closed; client got partial output |
| `committed=1` | Headers sent? | Yes — cannot transparently recover |
| `fallback_available=0` | Can fall back? | No — headers already on the wire |
| `category=FAIL_CONVERSION` | Failure class | HTML parsing failure |

**Action**: **Immediate** — disable streaming for this path:
```nginx
location /reports/ {
    markdown_streaming_engine off;
}
```
Then reload: `nginx -t && nginx -s reload`

#### Budget exceeded after commit (critical)

```text
2025/01/15 14:31:05 [warn] 1234#0: *8915 markdown decision: reason=STREAMING_BUDGET_EXCEEDED engine=streaming phase=postcommit action=abort committed=1 fallback_available=0 content_type=text/html category=FAIL_RESOURCE_LIMIT while sending to client, client: 10.0.0.20, server: docs.example.com, request: "GET /docs/full-export HTTP/1.1", upstream: "http://127.0.0.1:8080/docs/full-export", host: "docs.example.com"
```

**Field breakdown:**

| Field | Value | Meaning |
|-------|-------|---------|
| `reason=STREAMING_BUDGET_EXCEEDED` | Reason code | Memory budget exhausted during streaming |
| `category=FAIL_RESOURCE_LIMIT` | Failure class | Resource limit violation |
| `committed=1` | Headers sent? | Cannot recover |

**Action**: Disable streaming for this path, then raise
`markdown_parser_budget` or increase `markdown_stream_threshold` so this
response stays in full-buffer.

#### Pre-commit reject (clients receiving errors)

```text
2025/01/15 14:31:08 [warn] 1234#0: *8920 markdown decision: reason=STREAMING_PRECOMMIT_REJECT engine=rejected phase=precommit action=reject committed=0 fallback_available=0 content_type=text/html markdown_error_policy=fail_closed while sending to client, client: 10.0.0.15, server: docs.example.com, request: "GET /api/docs HTTP/1.1", upstream: "http://127.0.0.1:8080/api/docs", host: "docs.example.com"
```

**Field breakdown:**

| Field | Value | Meaning |
|-------|-------|---------|
| `reason=STREAMING_PRECOMMIT_REJECT` | Reason code | Pre-commit error with reject policy |
| `engine=rejected` | Engine path | Request rejected (error returned to client) |
| `markdown_error_policy=fail_closed` | Error policy | Fail-closed: errors produce HTTP error responses |
| `committed=0` | Headers sent? | No — but client still gets an error response |

**Action**: **Urgent** — switch to fail-open policy or disable streaming:
```nginx
markdown_error_policy pass;
# or:
markdown_streaming_engine off;
```

---

### JSON Metrics Response: Healthy Streaming Operation

This shows what the metrics endpoint returns when streaming is working
normally.  Query with:

```bash
curl -s -H 'Accept: application/json' http://localhost/markdown-metrics
```

```json
{
  "conversions_attempted": 38150,
  "conversions_succeeded": 38147,
  "conversions_failed": 0,
  "conversions_bypassed": 7080,
  "failopen_total": 3,
  "input_bytes": 1547832000,
  "output_bytes": 489210000,
  "streaming": {
    "requests_total": 11200,
    "fallback_total": 250,
    "succeeded_total": 11195,
    "failed_total": 0,
    "postcommit_error_total": 0,
    "precommit_failopen_total": 250,
    "precommit_reject_total": 0,
    "budget_exceeded_total": 0,
    "shadow_total": 0,
    "shadow_diff_total": 0,
    "last_ttfb_ms": 45,
    "last_peak_memory_bytes": 524288
  },
  "delivery_total": 38147,
  "decision_total": 45230
}
```

**Key indicators of health:**

| Metric | Value | Assessment |
|--------|-------|------------|
| `streaming.postcommit_error_total` | 0 | No post-commit failures |
| `streaming.fallback_total` / `streaming.requests_total` | 250 / 11200 = 2.2% | Below 5% threshold — healthy |
| `streaming.succeeded_total` / `streaming.requests_total` | 11195 / 11200 = 99.96% | Excellent success rate |
| `delivery_total` ≈ `conversions_total` | 38147 ≈ 38150 | Deliveries match decisions (no backpressure gap) |
| `failopen_total` | 3 | Near-zero fail-opens |

**Action**: None — system is healthy.

---

### JSON Metrics Response: Problematic Streaming (High Fallback Rate)

This shows what an unhealthy streaming deployment looks like.

```json
{
  "conversions_attempted": 38150,
  "conversions_succeeded": 38100,
  "conversions_failed": 50,
  "conversions_bypassed": 7080,
  "failopen_total": 47,
  "input_bytes": 1547832000,
  "output_bytes": 489210000,
  "streaming": {
    "requests_total": 8100,
    "fallback_total": 4350,
    "succeeded_total": 8050,
    "failed_total": 12,
    "postcommit_error_total": 12,
    "precommit_failopen_total": 4320,
    "precommit_reject_total": 30,
    "budget_exceeded_total": 75,
    "shadow_total": 0,
    "shadow_diff_total": 0,
    "last_ttfb_ms": 80,
    "last_peak_memory_bytes": 1048576
  },
  "delivery_total": 38100,
  "decision_total": 45230
}
```

**Problem indicators:**

| Metric | Value | Assessment |
|--------|-------|------------|
| `streaming.fallback_total` / `streaming.requests_total` | 4350 / 8100 = **53.7%** | Far above 5% threshold — streaming ineffective |
| `streaming.failed_total` | **12** | Post-commit failures occurring — clients see truncation |
| `streaming.postcommit_error_total` | **12** | Post-commit failures occurring |
| `streaming.fallback_precommit_reject` | **30** | 30 clients received error responses |
| `delivery_total` vs `conversions_total` | 38100 vs 38150 | 50 delivery gaps (possible backpressure) |

**Action**:

1. **Immediate**: Disable streaming (`markdown_streaming_engine off`) — 12
   post-commit failures and 30 rejections mean clients are affected.
2. **Investigate**: Check structured logs for the dominant `precommit_*`
   reason code causing the 34.9% fallback rate.
3. **Remediate**: Either raise budgets/thresholds for the affected content, or
   exclude problematic paths from streaming.

---

### Prometheus Format: Healthy Streaming Operation

Query with:

```bash
curl -s -H 'Accept: text/plain; version=0.0.4' http://localhost/markdown-metrics | \
  grep -E '^nginx_markdown_streaming_'
```

```text
# HELP nginx_markdown_streaming_engine_choice_total Engine selection decisions by engine.
# TYPE nginx_markdown_streaming_engine_choice_total counter
nginx_markdown_streaming_engine_choice_total{engine="streaming"} 11200
nginx_markdown_streaming_engine_choice_total{engine="full_buffer"} 1250
nginx_markdown_streaming_engine_choice_total{engine="passthrough"} 0
nginx_markdown_streaming_engine_choice_total{engine="not_eligible"} 0

# HELP nginx_markdown_streaming_fallback_total Pre-commit fallbacks by phase and action.
# TYPE nginx_markdown_streaming_fallback_total counter
nginx_markdown_streaming_fallback_total{phase="precommit",action="pass"} 250
nginx_markdown_streaming_fallback_total{phase="precommit",action="reject"} 0

# HELP nginx_markdown_streaming_failure_total Post-commit failures by phase and action.
# TYPE nginx_markdown_streaming_failure_total counter
nginx_markdown_streaming_failure_total{phase="postcommit",action="abort"} 0
nginx_markdown_streaming_failure_total{phase="postcommit",action="safe_finish"} 0

# HELP nginx_markdown_streaming_candidate_total Candidates evaluated for streaming.
# TYPE nginx_markdown_streaming_candidate_total counter
nginx_markdown_streaming_candidate_total 12450

# HELP nginx_markdown_true_streaming_selected_total Final streaming selections.
# TYPE nginx_markdown_true_streaming_selected_total counter
nginx_markdown_true_streaming_selected_total 11200

# HELP nginx_markdown_streaming_output_bytes_total Markdown bytes emitted via streaming.
# TYPE nginx_markdown_streaming_output_bytes_total counter
nginx_markdown_streaming_output_bytes_total 312500000

# HELP nginx_markdown_streaming_total Streaming conversion outcomes.
# TYPE nginx_markdown_streaming_total counter
nginx_markdown_streaming_total{result="success"} 11195
nginx_markdown_streaming_total{result="failed"} 0
nginx_markdown_streaming_total{result="fallback"} 250

# HELP nginx_markdown_streaming_ttfb_seconds Last streaming time-to-first-byte.
# TYPE nginx_markdown_streaming_ttfb_seconds gauge
nginx_markdown_streaming_ttfb_seconds 0.045

# HELP nginx_markdown_streaming_peak_memory_bytes Last streaming peak memory.
# TYPE nginx_markdown_streaming_peak_memory_bytes gauge
nginx_markdown_streaming_peak_memory_bytes 524288
```

**How to read this:**

| Line | Meaning |
|------|---------|
| `engine_choice_total{engine="streaming"} 11200` | 11,200 requests used true streaming |
| `fallback_total{...,action="pass"} 250` | 250 safe fallbacks (HTML passed through) |
| `fallback_total{...,action="reject"} 0` | No client-facing rejections |
| `failure_total{...,action="abort"} 0` | No post-commit aborts |
| `streaming_total{result="success"} 11195` | 11,195 successful completions |
| `streaming_ttfb_seconds 0.045` | Last TTFB was 45ms (healthy) |
| `streaming_peak_memory_bytes 524288` | Last peak memory was 512 KiB (well within budget) |

**Action**: None — all indicators healthy.  Fallback rate is
250 / 12450 = 2%, well below the 5% threshold.

---

### Diagnostics Endpoint: Recent Streaming Decisions

The diagnostics endpoint provides runtime state including current
configuration and recent decision history.  Query with:

```bash
curl -s http://localhost/nginx-markdown/diagnostics | python3 -m json.tool
```

Example output (streaming-relevant sections):

```json
{
  "version": "0.9.0",
  "uptime_seconds": 86420,
  "worker_pid": 1234,
  "streaming_config": {
    "engine": "auto",
    "error_policy": "pass",
    "threshold": 1048576,
    "precommit_buffer": 262144,
    "flush_min": 4096,
    "excluded_types": ["text/event-stream", "application/x-ndjson"],
    "threshold_explicit": false
  },
  "streaming_metrics": {
    "candidate_total": 12450,
    "succeeded_total": 11195,
    "failed_total": 0,
    "fallback_total": 250,
    "output_bytes_total": 312500000,
    "engine_choice_streaming": 11200,
    "engine_choice_full_buffer": 1250,
    "ttfb_last_seconds": 0.045,
    "peak_memory_last_bytes": 524288
  },
  "recent_decisions": [
    {
      "timestamp": "2025-01-15T14:30:25Z",
      "uri": "/api/reference",
      "reason": "eligible",
      "engine": "streaming",
      "phase": "header_filter",
      "content_length_known": false,
      "chunked": true
    },
    {
      "timestamp": "2025-01-15T14:30:26Z",
      "uri": "/api/reference",
      "reason": "STREAMING_CONVERT",
      "engine": "streaming",
      "phase": "postcommit",
      "action": "complete"
    },
    {
      "timestamp": "2025-01-15T14:30:27Z",
      "uri": "/blog/complex-post",
      "reason": "precommit_html_error",
      "engine": "full_buffer",
      "phase": "precommit",
      "action": "fallback"
    },
    {
      "timestamp": "2025-01-15T14:30:28Z",
      "uri": "/docs/overview",
      "reason": "content_length_known",
      "engine": "full_buffer",
      "phase": "header_filter",
      "content_length_known": true,
      "content_length": 45200
    }
  ]
}
```

**How to interpret:**

| Section | What It Tells You |
|---------|-------------------|
| `streaming_config` | Current runtime configuration (engine mode, thresholds, excluded types) |
| `streaming_config.threshold` | Responses must exceed this size (bytes) for streaming in `auto` mode |
| `streaming_config.precommit_buffer` | Pre-commit replay buffer size (bytes) for fail-open recovery |
| `streaming_config.flush_min` | Minimum output batch size (bytes) before flushing downstream |
| `streaming_config.threshold_explicit` | Whether `markdown_stream_threshold` was explicitly configured |
| `streaming_config.on_error` | What happens on errors: `pass` (serve HTML) or `reject` (return error) |
| `streaming_metrics` | Cumulative counters since last NGINX start |
| `streaming_metrics.ttfb_last_seconds` | Time-to-first-byte of the most recent streaming request |
| `streaming_metrics.peak_memory_last_bytes` | Peak memory of the most recent streaming conversion |
| `recent_decisions` | Last N streaming decisions with full context |
| `recent_decisions[].reason` | The reason code for why this path was chosen |

**Action**: Use this endpoint to confirm configuration is applied correctly
and to trace individual requests during debugging.  The `recent_decisions`
array is especially useful for understanding why a specific request did or
did not stream.

---

## Reason Code Quick Reference

| Code | Category | Operator Concern |
|------|----------|------------------|
| `eligible` | Selection | None — healthy |
| `content_length_known` | Selection | Normal for small known-size responses |
| `below_threshold` | Selection | Normal — full-buffer used |
| `config_disabled` | Selection | Expected when `off` is configured |
| `excluded_content_type` | Selection | Expected for excluded types |
| `precommit_html_error` | Fallback (safe) | Investigate HTML quality |
| `precommit_budget` | Fallback (safe) | Consider raising budget |
| `precommit_timeout` | Fallback (safe) | Consider raising timeout |
| `postcommit_parse_error` | Failure (critical) | Disable streaming; investigate |
| `postcommit_budget_exceeded` | Failure (critical) | Disable streaming; raise budget |
| `postcommit_io_error` | Failure (critical) | Disable streaming; check connectivity |

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.8.0 | 2026-06-16 | Kang | Initial streaming troubleshooting guide with 5 diagnostic scenarios |
| 0.8.0 | 2026-06-16 | Kang  | Added Log and Metrics Examples section with annotated log entries, JSON/Prometheus metrics examples (healthy and unhealthy), and diagnostics endpoint output |
| 0.8.0 | 2026-06-16 | Kang  | Added Scenario 6 (fallback-disabled escalation), decision tree, concrete commands |
| 0.8.3 | 2026-06-26 | Kang | No changes; version alignment with 0.8.3 release |
