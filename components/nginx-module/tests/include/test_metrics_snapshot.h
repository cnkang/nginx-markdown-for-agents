/*
 * Shared metrics snapshot type for standalone unit tests.
 *
 * Mirrors ngx_http_markdown_metrics_snapshot_t using unsigned long
 * in place of ngx_atomic_t so tests compile without NGINX headers.
 *
 * Included by:
 *   - metrics_snapshot_new_fields_test.c
 *   - prometheus_format_test.c
 */

#ifndef TEST_METRICS_SNAPSHOT_H
#define TEST_METRICS_SNAPSHOT_H

typedef struct {
    unsigned long conversions_attempted;
    unsigned long conversions_succeeded;
    unsigned long conversions_failed;
    unsigned long conversions_bypassed;
    unsigned long failures_conversion;
    unsigned long failures_resource_limit;
    unsigned long failures_system;
    unsigned long conversion_time_sum_ms;
    unsigned long input_bytes;
    unsigned long output_bytes;
    struct {
        unsigned long le_10ms;
        unsigned long le_100ms;
        unsigned long le_1000ms;
        unsigned long gt_1000ms;
    } conversion_latency;
    struct {
        unsigned long attempted;
        unsigned long succeeded;
        unsigned long failed;
        unsigned long gzip;
        unsigned long deflate;
        unsigned long brotli;
    } decompressions;
    unsigned long fullbuffer_path_hits;
    unsigned long incremental_path_hits;
    unsigned long requests_entered;
    struct {
        unsigned long config;
        unsigned long method;
        unsigned long status;
        unsigned long content_type;
        unsigned long size;
        unsigned long streaming;
        unsigned long auth;
        unsigned long range;
        unsigned long accept;
    } skips;
    unsigned long failopen_count;
    unsigned long estimated_token_savings;
} test_metrics_snapshot_t;

#endif /* TEST_METRICS_SNAPSHOT_H */
