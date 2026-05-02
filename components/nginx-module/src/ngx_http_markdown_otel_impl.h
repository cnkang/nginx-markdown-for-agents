/*
 * NGINX Markdown Filter Module - OpenTelemetry Integration
 *
 * Provides OTel span creation and attribute injection for conversion
 * requests.  Designed for the OTLP HTTP exporter (protobuf over HTTP).
 *
 * Architecture:
 *   - Per-conversion span with parent linkage to incoming trace context
 *   - Span attributes: flavor, engine, content_type, input_bytes,
 *     output_bytes, conversion_time_ms, reason_code
 *   - Ring buffer for in-flight spans (avoid per-request heap alloc)
 *   - Batch export on span completion or buffer pressure
 *
 * Status: Span creation, attribute recording, and diagnostic
 * export are functional.  Spans are wired into the full-buffer
 * and streaming conversion paths.  OTLP HTTP protobuf export
 * to a collector endpoint and trace context propagation are
 * deferred to a follow-up change set.
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
 * Holds attributes and timing for a single conversion span.
 * Spans are allocated from a per-worker ring buffer.
 */
typedef struct {
    ngx_msec_t    start_ms;
    ngx_msec_t    end_ms;
    ngx_uint_t    attr_count;
    ngx_http_markdown_otel_attr_t  attrs[NGX_HTTP_MARKDOWN_OTEL_MAX_ATTRS];
    ngx_uint_t    exported;
} ngx_http_markdown_otel_span_t;

/*
 * Create a new OTel span for a conversion request.
 *
 * Records the start time and initializes the span with
 * standard attributes from the request and configuration.
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
 * Export a completed span for diagnostic consumption.
 *
 * Logs span attributes at NGX_LOG_INFO level so operators can
 * observe conversion telemetry when markdown_log_verbosity >= info.
 * Full OTLP HTTP protobuf export to a collector endpoint is
 * deferred to a follow-up change set.
 *
 * Parameters:
 *   span - Completed OTel span
 *   log  - NGINX log for error reporting and attribute output
 */
static void
ngx_http_markdown_otel_span_export(ngx_http_markdown_otel_span_t *span,
                                   ngx_log_t *log)
{
    ngx_uint_t  i;

    if (span == NULL) {
        return;
    }

    ngx_log_error(NGX_LOG_INFO, log, 0,
                 "markdown otel: span %s "
                 "duration_ms=%M attrs=%ui",
                 NGX_HTTP_MARKDOWN_OTEL_SPAN_NAME,
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
