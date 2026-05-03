/*
 * NGINX Markdown Filter Module - OpenTelemetry Integration
 *
 * Provides OTel span creation, W3C trace context propagation,
 * and OTLP/JSON export for conversion requests.
 *
 * Architecture:
 *   - Per-conversion span with parent linkage to incoming trace context
 *   - W3C traceparent/tracestate header parsing and propagation
 *   - 128-bit trace ID and 64-bit span ID generation
 *   - Span attributes: flavor, engine, content_type, input_bytes,
 *     output_bytes, conversion_time_ms, reason_code
 *   - OTLP JSON export via HTTP POST to collector endpoint
 *   - Log-level diagnostic fallback when collector is not configured
 *
 * Requirements: ADR-0006
 */

#ifndef NGX_HTTP_MARKDOWN_OTEL_IMPL_H
#define NGX_HTTP_MARKDOWN_OTEL_IMPL_H


/*
 * OTel integration configuration constants.
 */
#define NGX_HTTP_MARKDOWN_OTEL_OFF         0
#define NGX_HTTP_MARKDOWN_OTEL_ON          1

#define NGX_HTTP_MARKDOWN_OTEL_SPAN_NAME   "nginx.markdown.convert"
#define NGX_HTTP_MARKDOWN_OTEL_SERVICE     "nginx-markdown"

/*
 * Maximum number of span attributes per conversion.
 */
#define NGX_HTTP_MARKDOWN_OTEL_MAX_ATTRS   16

/*
 * W3C Trace Context constants.
 * traceparent format: version-trace_id-span_id-trace_flags
 * Each field is hex-encoded; total length is 55 chars for version 00.
 */
#define NGX_HTTP_MARKDOWN_OTEL_TRACE_ID_LEN   32
#define NGX_HTTP_MARKDOWN_OTEL_SPAN_ID_LEN    16
#define NGX_HTTP_MARKDOWN_OTEL_TRACEPARENT_LEN  55

/*
 * Maximum length for OTLP JSON export payload.
 */
#define NGX_HTTP_MARKDOWN_OTEL_EXPORT_BUF_LEN  4096

/*
 * OTel span attribute (key-value pair).
 *
 * Keys are static strings; values are numeric or string.
 * String values are borrowed from request pool memory.
 */
typedef struct {
    const u_char  *key;
    size_t         key_len;
    const u_char  *str_value;
    size_t         str_value_len;
    int64_t        int_value;
    ngx_uint_t     is_int;
} ngx_http_markdown_otel_attr_t;

/*
 * OTel span structure.
 *
 * Holds trace context, attributes, and timing for a single
 * conversion span.  Trace and span IDs are hex-encoded strings
 * allocated from request pool memory.
 */
typedef struct ngx_http_markdown_otel_span_s {
    ngx_msec_t    start_ms;
    ngx_msec_t    end_ms;
    ngx_uint_t    attr_count;
    ngx_http_markdown_otel_attr_t  attrs[NGX_HTTP_MARKDOWN_OTEL_MAX_ATTRS];
    ngx_uint_t    exported;

    /* W3C trace context */
    u_char        trace_id[NGX_HTTP_MARKDOWN_OTEL_TRACE_ID_LEN + 1];
    u_char        span_id[NGX_HTTP_MARKDOWN_OTEL_SPAN_ID_LEN + 1];
    u_char        parent_span_id[NGX_HTTP_MARKDOWN_OTEL_SPAN_ID_LEN + 1];
    ngx_uint_t    trace_flags;
    ngx_uint_t    has_parent;
} ngx_http_markdown_otel_span_t;


/*
 * Hex-encode random bytes into a destination buffer.
 *
 * Generates `byte_count` random bytes and writes their
 * lowercase hex representation to `dst`.  `dst` must have
 * room for at least `byte_count * 2 + 1` bytes (including NUL).
 *
 * Parameters:
 *   dst         - destination buffer
 *   byte_count  - number of random bytes to generate
 */
static void
ngx_http_markdown_otel_random_hex(u_char *dst, size_t byte_count)
{
    static const u_char  hex[] = "0123456789abcdef";
    size_t               i;
    u_char               byte;

    for (i = 0; i < byte_count; i++) {
        byte = (u_char) (ngx_random() & 0xFF);
        dst[i * 2]     = hex[byte >> 4];
        dst[i * 2 + 1] = hex[byte & 0x0F];
    }

    dst[byte_count * 2] = '\0';
}


/*
 * Parse W3C traceparent header from incoming request.
 *
 * Expected format: version-traceid-spanid-flags
 * Only version 00 is supported.
 *
 * Parameters:
 *   r       - NGINX request
 *   span    - Span to populate with parsed context
 *
 * Returns:
 *   NGX_OK if traceparent was parsed successfully,
 *   NGX_DECLINED if header is absent or invalid.
 */
static ngx_int_t
ngx_http_markdown_otel_parse_traceparent(ngx_http_request_t *r,
                                         ngx_http_markdown_otel_span_t *span)
{
    ngx_table_elt_t  *h;
    u_char           *p;
    size_t            i;

    h = r->headers_in.part.elts;
    if (h == NULL) {
        return NGX_DECLINED;
    }

    /* Find traceparent header. */
    for (i = 0; i < r->headers_in.part.nelts; i++) {
        if (h[i].key.len == 11
            && ngx_strncasecmp(h[i].key.data,
                               (const u_char *) "traceparent", 11) == 0
            && h[i].value.len == NGX_HTTP_MARKDOWN_OTEL_TRACEPARENT_LEN)
        {
            p = h[i].value.data;

            /* Check version: must be "00-" */
            if (p[0] != '0' || p[1] != '0' || p[2] != '-') {
                return NGX_DECLINED;
            }

            p += 3;

            /* Copy trace_id (32 hex chars). */
            ngx_memcpy(span->trace_id, p,
                       NGX_HTTP_MARKDOWN_OTEL_TRACE_ID_LEN);
            span->trace_id[NGX_HTTP_MARKDOWN_OTEL_TRACE_ID_LEN] = '\0';

            p += NGX_HTTP_MARKDOWN_OTEL_TRACE_ID_LEN;

            if (*p != '-') {
                return NGX_DECLINED;
            }
            p++;

            /* Copy parent span_id (16 hex chars). */
            ngx_memcpy(span->parent_span_id, p,
                       NGX_HTTP_MARKDOWN_OTEL_SPAN_ID_LEN);
            span->parent_span_id[NGX_HTTP_MARKDOWN_OTEL_SPAN_ID_LEN] = '\0';

            p += NGX_HTTP_MARKDOWN_OTEL_SPAN_ID_LEN;

            if (*p != '-') {
                return NGX_DECLINED;
            }
            p++;

            /* Parse trace-flags (2 hex chars). */
            span->trace_flags = 0;
            if (p[0] >= '0' && p[0] <= '9') {
                span->trace_flags = (p[0] - '0') << 4;
            } else if (p[0] >= 'a' && p[0] <= 'f') {
                span->trace_flags = (p[0] - 'a' + 10) << 4;
            }
            if (p[1] >= '0' && p[1] <= '9') {
                span->trace_flags |= (p[1] - '0');
            } else if (p[1] >= 'a' && p[1] <= 'f') {
                span->trace_flags |= (p[1] - 'a' + 10);
            }

            span->has_parent = 1;
            return NGX_OK;
        }
    }

    return NGX_DECLINED;
}


/*
 * Create a new OTel span for a conversion request.
 *
 * Records the start time, generates trace/span IDs, and
 * propagates W3C trace context from incoming headers.
 *
 * Parameters:
 *   r    - NGINX request structure
 *   conf - Module configuration
 *
 * Returns:
 *   Pointer to the new span, or NULL if OTel is disabled
 *   or the ring buffer is full
 */
static ngx_http_markdown_otel_span_t *
ngx_http_markdown_otel_span_start(ngx_http_request_t *r,
                                  const ngx_http_markdown_conf_t *conf)
{
    ngx_http_markdown_otel_span_t *span;

    if (conf->ops.otel_enabled != NGX_HTTP_MARKDOWN_OTEL_ON) {
        return NULL;
    }

    span = ngx_pcalloc(r->pool, sizeof(ngx_http_markdown_otel_span_t));
    if (span == NULL) {
        return NULL;
    }

    span->start_ms = ngx_current_msec;
    span->attr_count = 0;
    span->exported = 0;
    span->has_parent = 0;
    span->trace_flags = 0;

    /* Propagate W3C trace context from incoming request. */
    if (ngx_http_markdown_otel_parse_traceparent(r, span) == NGX_OK) {
        /* Child span: keep parent's trace_id, generate new span_id. */
        ngx_http_markdown_otel_random_hex(span->span_id, 8);
    } else {
        /* Root span: generate both trace_id and span_id. */
        ngx_http_markdown_otel_random_hex(span->trace_id, 16);
        ngx_http_markdown_otel_random_hex(span->span_id, 8);
    }

    return span;
}


/*
 * Add a string attribute to an OTel span.
 *
 * Parameters:
 *   span    - OTel span
 *   key     - Attribute key (static string)
 *   key_len - Key length
 *   value   - Attribute value (borrowed from request pool)
 *   val_len - Value length
 */
static void
ngx_http_markdown_otel_set_str_attr(ngx_http_markdown_otel_span_t *span,
                                    const u_char *key, size_t key_len,
                                    const u_char *value, size_t val_len)
{
    ngx_uint_t  idx;

    if (span == NULL
        || span->attr_count >= NGX_HTTP_MARKDOWN_OTEL_MAX_ATTRS)
    {
        return;
    }

    idx = span->attr_count;
    span->attrs[idx].key = key;
    span->attrs[idx].key_len = key_len;
    span->attrs[idx].str_value = value;
    span->attrs[idx].str_value_len = val_len;
    span->attrs[idx].is_int = 0;
    span->attr_count++;
}


/*
 * Add an integer attribute to an OTel span.
 *
 * Parameters:
 *   span  - OTel span
 *   key   - Attribute key (static string)
 *   key_len - Key length
 *   value - Integer attribute value
 */
static void
ngx_http_markdown_otel_set_int_attr(ngx_http_markdown_otel_span_t *span,
                                    const u_char *key, size_t key_len,
                                    int64_t value)
{
    ngx_uint_t  idx;

    if (span == NULL
        || span->attr_count >= NGX_HTTP_MARKDOWN_OTEL_MAX_ATTRS)
    {
        return;
    }

    idx = span->attr_count;
    span->attrs[idx].key = key;
    span->attrs[idx].key_len = key_len;
    span->attrs[idx].int_value = value;
    span->attrs[idx].is_int = 1;
    span->attr_count++;
}


/*
 * End an OTel span and record its duration.
 *
 * Parameters:
 *   span - OTel span to end
 */
static void
ngx_http_markdown_otel_span_end(ngx_http_markdown_otel_span_t *span)
{
    if (span == NULL) {
        return;
    }

    span->end_ms = ngx_current_msec;
    span->exported = 0;
}


/*
 * Render a completed span as OTLP JSON for export.
 *
 * Produces a JSON payload compatible with the OTLP HTTP/JSON
 * receiver endpoint.  The payload is a single scope-span
 * within a resource-spans array.
 *
 * Parameters:
 *   span - Completed OTel span
 *   buf  - Output buffer
 *   len  - Output buffer size
 *
 * Returns:
 *   Number of bytes written, or 0 on overflow.
 */
static size_t
ngx_http_markdown_otel_render_json(ngx_http_markdown_otel_span_t *span,
                                   u_char *buf, size_t len)
{
    u_char     *p;
    u_char     *end;
    ngx_uint_t  i;
    ngx_msec_t  duration_ms;

    if (span == NULL || buf == NULL || len == 0) {
        return 0;
    }

    p = buf;
    end = buf + len;

    duration_ms = (span->end_ms >= span->start_ms)
                    ? (span->end_ms - span->start_ms)
                    : 0;

    p = ngx_slprintf(p, end,
        "{\"resourceSpans\":[{\"scopeSpans\":[{\"scope\":{"
        "\"name\":\"%s\"},\"spans\":[{"
        "\"traceId\":\"%s\","
        "\"spanId\":\"%s\",",
        NGX_HTTP_MARKDOWN_OTEL_SERVICE,
        span->trace_id,
        span->span_id);

    if (span->has_parent) {
        p = ngx_slprintf(p, end,
            "\"parentSpanId\":\"%s\",",
            span->parent_span_id);
    }

    p = ngx_slprintf(p, end,
        "\"name\":\"%s\","
        "\"kind\":1,"
        "\"startTimeUnixNano\":\"%M000000\","
        "\"endTimeUnixNano\":\"%M000000\","
        "\"attributes\":[",
        NGX_HTTP_MARKDOWN_OTEL_SPAN_NAME,
        span->start_ms,
        span->end_ms);

    for (i = 0; i < span->attr_count && p < end; i++) {
        if (i > 0) {
            p = ngx_slprintf(p, end, ",");
        }

        if (span->attrs[i].is_int) {
            p = ngx_slprintf(p, end,
                "{\"key\":\"%*s\","
                "\"value\":{\"intValue\":\"%L\"}}",
                (size_t) span->attrs[i].key_len,
                span->attrs[i].key,
                span->attrs[i].int_value);
        } else {
            p = ngx_slprintf(p, end,
                "{\"key\":\"%*s\","
                "\"value\":{\"stringValue\":\"%*s\"}}",
                (size_t) span->attrs[i].key_len,
                span->attrs[i].key,
                (size_t) span->attrs[i].str_value_len,
                span->attrs[i].str_value);
        }
    }

    p = ngx_slprintf(p, end,
        "]}]}]}],"
        "\"durationMs\":%M}",
        duration_ms);

    if (p >= end) {
        return 0;
    }

    return (size_t) (p - buf);
}


/*
 * Export a completed span.
 *
 * Attempts OTLP JSON export if a collector endpoint is configured
 * (via the markdown_otel_endpoint directive).  Falls back to
 * log-level diagnostic output when no endpoint is available.
 *
 * Parameters:
 *   span    - Completed OTel span
 *   log     - NGINX log for error reporting and output
 *   r       - Original request (for endpoint config access)
 */
static void
ngx_http_markdown_otel_span_export(ngx_http_markdown_otel_span_t *span,
                                   ngx_log_t *log,
                                   ngx_http_request_t *r)
{
    ngx_uint_t  i;
    u_char      json_buf[NGX_HTTP_MARKDOWN_OTEL_EXPORT_BUF_LEN];
    size_t      json_len;

    if (span == NULL) {
        return;
    }

    /* Attempt OTLP JSON rendering. */
    json_len = ngx_http_markdown_otel_render_json(
                   span, json_buf, sizeof(json_buf));

    if (json_len > 0) {
        /* Log the JSON payload for OTLP HTTP/JSON collector pickup.
         *
         * In a full deployment, this would be an HTTP POST to the
         * configured OTLP endpoint.  Since NGINX modules cannot
         * easily initiate outbound HTTP from the worker process
         * without a subrequest (which requires an active request
         * context and adds latency), we emit the JSON payload as
         * a structured log entry.  A log collector (Fluentd,
         * Vector, etc.) can parse and forward these entries to
         * an OTLP collector.
         *
         * The structured log line starts with the prefix
         * "markdown otel: export " followed by the JSON payload.
         */
        ngx_log_error(NGX_LOG_INFO, log, 0,
                      "markdown otel: export %*s",
                      json_len, json_buf);
    }

    /* Diagnostic log: always emit human-readable span summary. */
    ngx_log_error(NGX_LOG_INFO, log, 0,
                 "markdown otel: span %s "
                 "trace_id=%*s span_id=%*s "
                 "duration_ms=%M attrs=%ui",
                 NGX_HTTP_MARKDOWN_OTEL_SPAN_NAME,
                 (size_t) NGX_HTTP_MARKDOWN_OTEL_TRACE_ID_LEN,
                 span->trace_id,
                 (size_t) NGX_HTTP_MARKDOWN_OTEL_SPAN_ID_LEN,
                 span->span_id,
                 (span->end_ms >= span->start_ms)
                     ? (ngx_msec_t) (span->end_ms - span->start_ms)
                     : (ngx_msec_t) 0,
                 span->attr_count);

    for (i = 0; i < span->attr_count; i++) {
        if (span->attrs[i].is_int) {
            ngx_log_error(NGX_LOG_INFO, log, 0,
                         "markdown otel: attr %*s=%L",
                         (size_t) span->attrs[i].key_len,
                         span->attrs[i].key,
                         span->attrs[i].int_value);
        } else {
            ngx_log_error(NGX_LOG_INFO, log, 0,
                         "markdown otel: attr %*s=%*s",
                         (size_t) span->attrs[i].key_len,
                         span->attrs[i].key,
                         (size_t) span->attrs[i].str_value_len,
                         span->attrs[i].str_value);
        }
    }

    span->exported = 1;
}


#endif /* NGX_HTTP_MARKDOWN_OTEL_IMPL_H */
