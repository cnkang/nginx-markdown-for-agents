/*
 * Test: metrics_v1_renderer
 *
 * Validates the single Prometheus text exposition renderer used by the
 * metrics endpoint.  The removed JSON, plain-text, and per-path renderers
 * deliberately have no test surface in the current release.
 */

#include "../include/test_common.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

typedef unsigned char u_char;
typedef unsigned long ngx_atomic_uint_t;
typedef uintptr_t ngx_uint_t;

static u_char *
ngx_slprintf(u_char *buf, u_char *last, const char *fmt, ...)
{
    va_list      args;
    int          written;
    size_t       remaining;
    char         local_fmt[4096];
    size_t       format_index;
    size_t       output_index;

    if (buf == NULL || last == NULL || buf >= last || fmt == NULL) {
        return buf;
    }

    remaining = (size_t) (last - buf);
    format_index = 0;
    output_index = 0;
    while (fmt[format_index] != '\0'
           && output_index < sizeof(local_fmt) - 4)
    {
        if (fmt[format_index] == '%') {
            local_fmt[output_index++] = fmt[format_index++];
            while (fmt[format_index] >= '0'
                   && fmt[format_index] <= '9'
                   && output_index < sizeof(local_fmt) - 4)
            {
                local_fmt[output_index++] = fmt[format_index++];
            }
            if (fmt[format_index] == 'u'
                && fmt[format_index + 1] == 'A')
            {
                local_fmt[output_index++] = 'l';
                local_fmt[output_index++] = 'u';
                format_index += 2;
            } else {
                local_fmt[output_index++] = fmt[format_index++];
            }
        } else {
            local_fmt[output_index++] = fmt[format_index++];
        }
    }
    local_fmt[output_index] = '\0';

    va_start(args, fmt);
    written = vsnprintf((char *) buf, remaining, local_fmt, args);
    va_end(args);

    if (written < 0 || (size_t) written >= remaining) {
        return last;
    }

    return buf + written;
}

#include "../../src/ngx_http_markdown_metrics_v1_renderer.h"

static int
contains(const char *haystack, const char *needle)
{
    return strstr(haystack, needle) != NULL;
}

static void
test_renderer_emits_frozen_families(void)
{
    u_char                                  buffer[32768];
    u_char                                 *end;
    ngx_http_markdown_metrics_v1_snapshot_t  snapshot;

    TEST_SUBSECTION("frozen Prometheus families");

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.requests.converted = 3;
    snapshot.requests.failed_open = 4;
    snapshot.attempts.full_buffer = 5;
    snapshot.attempts.streaming = 6;
    snapshot.deliveries.full_buffer = 7;
    snapshot.deliveries.streaming = 8;
    snapshot.duration_full_buffer.buckets[0] = 9;
    snapshot.duration_full_buffer.count = 9;
    snapshot.input_bytes = 10;
    snapshot.output_bytes = 11;
    snapshot.streaming_peak_memory_bytes = 13;
    snapshot.streaming_events.resume_failure = 14;
    snapshot.decompression.gzip_failure_format = 15;
    snapshot.dynconf_reloads.failure_file_error = 16;
    snapshot.build_info.version = (const u_char *) "0.9.2";
    snapshot.build_info.nginx_version_text = (const u_char *) "1.26.3";
    snapshot.build_info.features = (const u_char *) "streaming";

    end = ngx_http_markdown_metrics_v1_render(
        buffer, buffer + sizeof(buffer), &snapshot);
    TEST_ASSERT(end != NULL && end < buffer + sizeof(buffer),
                "renderer should fit the bounded response buffer");
    *end = '\0';

    TEST_ASSERT(contains((char *) buffer,
                         "# TYPE nginx_markdown_requests_total counter"),
                "renderer must emit the requests family");
    TEST_ASSERT(contains((char *) buffer,
                         "# TYPE nginx_markdown_conversion_duration_seconds histogram"),
                "renderer must emit the duration family");
    TEST_ASSERT(contains((char *) buffer,
                         "{engine=\"full_buffer\",le=\"0.001\"} 9"),
                "renderer must emit finite histogram buckets");
    TEST_ASSERT(contains((char *) buffer,
                         "{transition=\"resume_failure\",reason=\"streaming_mid_flight_error\"} 14"),
                "renderer must emit bounded streaming labels");
    TEST_ASSERT(contains((char *) buffer,
                         "# TYPE nginx_markdown_build_info gauge"),
                "renderer must emit build information");
    TEST_ASSERT(!contains((char *) buffer,
                          "nginx_markdown_inflight_requests"),
                "renderer must not export the worker-local inflight gauge");

    TEST_PASS("frozen Prometheus families are emitted");
}

static void
test_histogram_reconciles_count_with_bucket_total(void)
{
    u_char                                  buffer[32768];
    u_char                                 *end;
    ngx_http_markdown_metrics_v1_snapshot_t  snapshot;

    TEST_SUBSECTION("histogram count reconciliation");

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.duration_full_buffer.buckets[0] = 5;
    snapshot.duration_full_buffer.count = 2;

    end = ngx_http_markdown_metrics_v1_render(
        buffer, buffer + sizeof(buffer), &snapshot);
    TEST_ASSERT(end != NULL && end < buffer + sizeof(buffer),
                "renderer should emit a valid histogram");
    *end = '\0';
    TEST_ASSERT(contains((char *) buffer,
                         "{engine=\"full_buffer\",le=\"+Inf\"} 5"),
                "+Inf must not be below the finite bucket total");
    TEST_ASSERT(contains((char *) buffer,
                         "nginx_markdown_conversion_duration_seconds_count{engine=\"full_buffer\"} 5"),
                "count must match the reconciled +Inf value");

    TEST_PASS("histogram count stays monotonic");
}

static void
test_renderer_fails_on_truncation(void)
{
    u_char                                  buffer[64];
    ngx_http_markdown_metrics_v1_snapshot_t  snapshot;

    TEST_SUBSECTION("bounded output failure");

    memset(&snapshot, 0, sizeof(snapshot));
    TEST_ASSERT(ngx_http_markdown_metrics_v1_render(
                    buffer, buffer + sizeof(buffer), &snapshot) == NULL,
                "renderer must fail closed when the buffer is exhausted");

    TEST_PASS("truncation fails closed");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("metrics_v1_renderer Tests\n");
    printf("========================================\n");

    test_renderer_emits_frozen_families();
    test_histogram_reconciles_count_with_bucket_total();
    test_renderer_fails_on_truncation();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
