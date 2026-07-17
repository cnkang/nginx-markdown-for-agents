#ifndef NGX_HTTP_MARKDOWN_METRICS_JSON_PERF_IMPL_H
#define NGX_HTTP_MARKDOWN_METRICS_JSON_PERF_IMPL_H

typedef struct {
    ngx_atomic_uint_t backpressure_total;
    ngx_atomic_uint_t backpressure_resume_total;
    ngx_atomic_uint_t pending_output_high_watermark_bytes;
    ngx_atomic_uint_t decompression_streaming_total;
    ngx_atomic_uint_t decompression_fullbuffer_total;
    ngx_atomic_uint_t decompression_budget_exceeded_total;
    ngx_atomic_uint_t zero_copy_output_total;
    ngx_atomic_uint_t copied_output_total;

    /* Inflight guard metrics (spec 52, per-worker) */
    struct {
        ngx_atomic_uint_t current;
        ngx_atomic_uint_t high_watermark;
        ngx_atomic_uint_t overload_total;
    } inflight;
} ngx_http_markdown_metrics_perf_snapshot_t;


static u_char *
ngx_http_markdown_metrics_write_json_perf(
    u_char *p,
    u_char *end,
    const ngx_http_markdown_metrics_perf_snapshot_t *perf)
{
    return ngx_slprintf(p, end,
        "  \"perf\": {\n"
        "    \"backpressure_total\": %uA,\n"
        "    \"backpressure_resume_total\": %uA,\n"
        "    \"pending_output_high_watermark_bytes\": %uA,\n"
        "    \"decompression_streaming_total\": %uA,\n"
        "    \"decompression_fullbuffer_total\": %uA,\n"
        "    \"decompression_budget_exceeded_total\": %uA,\n"
        "    \"zero_copy_output_total\": %uA,\n"
        "    \"copied_output_total\": %uA\n"
        "  }\n",
        perf->backpressure_total,
        perf->backpressure_resume_total,
        perf->pending_output_high_watermark_bytes,
        perf->decompression_streaming_total,
        perf->decompression_fullbuffer_total,
        perf->decompression_budget_exceeded_total,
        perf->zero_copy_output_total,
        perf->copied_output_total);
}

#endif /* NGX_HTTP_MARKDOWN_METRICS_JSON_PERF_IMPL_H */
