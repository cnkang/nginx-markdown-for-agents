/*
 * Test: metrics_json_perf_renderer
 *
 * Exercises the production JSON perf renderer helper used by
 * ngx_http_markdown_metrics_write_json().
 */

#include "../include/test_common.h"

typedef unsigned char u_char;
typedef unsigned long ngx_atomic_uint_t;

static u_char *
ngx_slprintf(u_char *buf, u_char *last, const char *fmt, ...)
{
    va_list      args;
    int          n;
    size_t       remaining;
    char         local_fmt[2048];
    size_t       fi;
    size_t       oi;

    if (buf >= last) {
        return buf;
    }

    remaining = (size_t) (last - buf);
    fi = 0;
    oi = 0;

    while (fmt[fi] != '\0' && oi < sizeof(local_fmt) - 4) {
        if (fmt[fi] == '%') {
            local_fmt[oi++] = fmt[fi++];
            while (fmt[fi] >= '0' && fmt[fi] <= '9') {
                local_fmt[oi++] = fmt[fi++];
            }
            if (fmt[fi] == 'u' && fmt[fi + 1] == 'A') {
                local_fmt[oi++] = 'l';
                local_fmt[oi++] = 'u';
                fi += 2;
            } else {
                local_fmt[oi++] = fmt[fi++];
            }
        } else {
            local_fmt[oi++] = fmt[fi++];
        }
    }
    local_fmt[oi] = '\0';

    va_start(args, fmt);
    n = vsnprintf((char *) buf, remaining, local_fmt, args);
    va_end(args);

    if (n < 0) {
        return buf;
    }

    if ((size_t) n >= remaining) {
        return last;
    }

    return buf + n;
}

#include "../../src/ngx_http_markdown_metrics_json_perf_impl.h"

static int
contains(const char *haystack, const char *needle)
{
    return strstr(haystack, needle) != NULL;
}

static void
test_json_perf_renderer_emits_all_fields(void)
{
    u_char                                     buf[1024];
    u_char                                    *p;
    ngx_http_markdown_metrics_perf_snapshot_t  perf;

    TEST_SUBSECTION("JSON perf renderer emits all perf fields");

    memset(&perf, 0, sizeof(perf));
    perf.backpressure_total = 7;
    perf.backpressure_resume_total = 3;
    perf.pending_output_high_watermark_bytes = 65536;
    perf.decompression_streaming_total = 12;
    perf.decompression_fullbuffer_total = 8;
    perf.decompression_budget_exceeded_total = 2;
    perf.zero_copy_output_total = 50;
    perf.copied_output_total = 30;

    p = ngx_http_markdown_metrics_write_json_perf(
        buf, buf + sizeof(buf), &perf);
    TEST_ASSERT(p > buf, "renderer should produce output");
    TEST_ASSERT(p < buf + sizeof(buf), "renderer should fit in buffer");
    *p = '\0';

    TEST_ASSERT(contains((char *) buf, "\"perf\": {"),
        "JSON has perf object");
    TEST_ASSERT(contains((char *) buf, "\"backpressure_total\": 7"),
        "JSON has backpressure_total");
    TEST_ASSERT(contains((char *) buf, "\"backpressure_resume_total\": 3"),
        "JSON has backpressure_resume_total");
    TEST_ASSERT(contains((char *) buf,
        "\"pending_output_high_watermark_bytes\": 65536"),
        "JSON has pending_output_high_watermark_bytes");
    TEST_ASSERT(contains((char *) buf,
        "\"decompression_streaming_total\": 12"),
        "JSON has decompression_streaming_total");
    TEST_ASSERT(contains((char *) buf,
        "\"decompression_fullbuffer_total\": 8"),
        "JSON has decompression_fullbuffer_total");
    TEST_ASSERT(contains((char *) buf,
        "\"decompression_budget_exceeded_total\": 2"),
        "JSON has decompression_budget_exceeded_total");
    TEST_ASSERT(contains((char *) buf, "\"zero_copy_output_total\": 50"),
        "JSON has zero_copy_output_total");
    TEST_ASSERT(contains((char *) buf, "\"copied_output_total\": 30"),
        "JSON has copied_output_total");

    TEST_PASS("JSON perf renderer emits all perf fields");
}

int
main(void)
{
    printf("\nmetrics_json_perf_renderer\n\n");
    test_json_perf_renderer_emits_all_fields();
    TEST_PASS("metrics_json_perf_renderer: all tests passed");
    return 0;
}
