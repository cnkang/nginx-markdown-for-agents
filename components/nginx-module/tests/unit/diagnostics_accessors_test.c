/*
 * Test: diagnostics_accessors
 *
 * Validates the diagnostics accessor functions that bridge the
 * diagnostics compilation unit with module-internal state:
 *   - collect_metrics (reads SHM metrics zone)
 *
 * Coverage targets:
 *   ngx_http_markdown_diagnostics_accessors_impl.h
 */

#include "../include/test_common.h"
#include <ngx_config.h>
#include <ngx_core.h>

struct ngx_log_s {
    int dummy;
};

typedef struct ngx_http_markdown_conf_s ngx_http_markdown_conf_t;
struct ngx_http_markdown_conf_s {
    int dummy;
};

#define NGX_OK         0
#define NGX_ERROR     -1

#define ngx_memzero(buf, n) memset(buf, 0, n)
#define ngx_memcpy(dst, src, n) memcpy((dst), (src), (n))
#define ngx_min(a, b) (((a) < (b)) ? (a) : (b))
#define ngx_strlen(s) strlen((const char *) (s))

/* ── Metrics struct (mirrors production SHM layout) ───────────── */

typedef struct {
    ngx_atomic_t  conversions_succeeded;
    struct {
        ngx_atomic_t  delivery_count;
        ngx_atomic_t  failopen_count;
    } results;
    ngx_atomic_t  requests_entered;
#ifdef MARKDOWN_STREAMING_ENABLED
    struct {
        ngx_atomic_t  requests_total;
        ngx_atomic_t  fallback_total;
        ngx_atomic_t  succeeded_total;
        ngx_atomic_t  failed_total;
        ngx_atomic_t  postcommit_error_total;
        ngx_atomic_t  precommit_failopen_total;
        ngx_atomic_t  precommit_reject_total;
        ngx_atomic_t  budget_exceeded_total;
        ngx_atomic_t  last_ttfb_ms;
        ngx_atomic_t  last_peak_memory_bytes;
        ngx_atomic_t  streaming_fallback_precommit_pass;
        ngx_atomic_t  streaming_fallback_precommit_reject;
        ngx_atomic_t  streaming_failure_postcommit_abort;
        ngx_atomic_t  streaming_failure_postcommit_safe_finish;
        struct {
            ngx_atomic_t  streaming;
            ngx_atomic_t  full_buffer;
            ngx_atomic_t  passthrough;
            ngx_atomic_t  not_eligible;
        } engine_choice;
        struct {
            ngx_atomic_t  candidate_total;
            ngx_atomic_t  true_streaming_selected_total;
            ngx_atomic_t  output_bytes_total;
            ngx_atomic_t  excluded_content_type_total;
        } selection;
    } streaming;
#endif
    struct {
        ngx_atomic_t  backpressure_total;
        ngx_atomic_t  backpressure_resume_total;
        ngx_atomic_t  pending_output_high_watermark_bytes;
        ngx_atomic_t  decompression_streaming_total;
        ngx_atomic_t  decompression_fullbuffer_total;
        ngx_atomic_t  decompression_budget_exceeded_total;
        ngx_atomic_t  copied_output_total;
    } perf;
} ngx_http_markdown_metrics_t;

/* Global metrics pointer (mirrors production) */
static ngx_http_markdown_metrics_t  g_metrics_data;
static ngx_http_markdown_metrics_t *ngx_http_markdown_metrics = NULL;

/* ── Inflight overload stub ────────────────────────────────────── */

static ngx_atomic_int_t g_inflight_overload_total;
static ngx_atomic_uint_t g_pending_output_requests;

static ngx_inline ngx_atomic_int_t
ngx_http_markdown_inflight_current(void)
{
    return 0;
}

static ngx_inline ngx_atomic_int_t
ngx_http_markdown_inflight_overload_total(void)
{
    return g_inflight_overload_total;
}

ngx_atomic_uint_t
ngx_http_markdown_pending_output_current(void)
{
    return g_pending_output_requests;
}

/* ── Production function headers and implementation ───────────── */

#include "ngx_http_markdown_diagnostics_accessors_impl.h"

ngx_uint_t
ngx_http_markdown_diagnostics_recording_state(void)
{
    return NGX_HTTP_MARKDOWN_DIAG_RECORDING_ACTIVE;
}

/* ── Tests ─────────────────────────────────────────────────────── */

static void
test_collect_metrics_null_output(void)
{
    TEST_SUBSECTION("collect_metrics with NULL output");

    /* Should not crash */
    ngx_http_markdown_diagnostics_collect_metrics(NULL);

    TEST_PASS("NULL output is no-op");
}

static void
test_collect_metrics_null_zone(void)
{
    ngx_http_markdown_diag_metrics_t out;

    TEST_SUBSECTION("collect_metrics with NULL metrics zone");

    ngx_http_markdown_metrics = NULL;
    g_pending_output_requests = 7;
    memset(&out, 0xFF, sizeof(out));

    ngx_http_markdown_diagnostics_collect_metrics(&out);

    TEST_ASSERT(out.conversions_total == 0, "conversions should be 0");
    TEST_ASSERT(out.delivery_total == 0, "delivery should be 0");
    TEST_ASSERT(out.requests_total == 0, "requests should be 0");
    TEST_ASSERT(out.failopen_total == 0, "failopen should be 0");
    TEST_ASSERT(out.pending_output == 7,
                "pending_output should survive a NULL metrics zone");
    TEST_ASSERT(out.diagnostics_recording_state
                == NGX_HTTP_MARKDOWN_DIAG_RECORDING_ACTIVE,
                "recording state should be collected without a metrics zone");

    TEST_PASS("NULL zone zeroes all fields");
}

static void
test_collect_metrics_with_data(void)
{
    ngx_http_markdown_diag_metrics_t out;

    TEST_SUBSECTION("collect_metrics with populated zone");

    memset(&g_metrics_data, 0, sizeof(g_metrics_data));
    g_metrics_data.conversions_succeeded = 42;
    g_metrics_data.results.delivery_count = 100;
    g_metrics_data.requests_entered = 200;
    g_metrics_data.results.failopen_count = 3;
    g_metrics_data.perf.copied_output_total = 2;
    ngx_http_markdown_metrics = &g_metrics_data;
    g_pending_output_requests = 3;

    ngx_http_markdown_diagnostics_collect_metrics(&out);

    TEST_ASSERT(out.conversions_total == 42, "conversions should be 42");
    TEST_ASSERT(out.delivery_total == 100, "delivery should be 100");
    TEST_ASSERT(out.requests_total == 200, "requests should be 200");
    TEST_ASSERT(out.failopen_total == 3, "failopen should be 3");
    TEST_ASSERT(out.copied_output_total == 2,
                "copied_output should be 2");
    TEST_ASSERT(out.pending_output == 3,
                "pending_output should count requests with pending chains");
    TEST_ASSERT(out.diagnostics_recording_state
                == NGX_HTTP_MARKDOWN_DIAG_RECORDING_ACTIVE,
                "recording state should be collected with metrics data");

    TEST_PASS("Metrics collected correctly");
}

#ifdef MARKDOWN_STREAMING_ENABLED
static void
test_collect_metrics_streaming(void)
{
    ngx_http_markdown_diag_metrics_t out;

    TEST_SUBSECTION("collect_metrics with streaming counters");

    memset(&g_metrics_data, 0, sizeof(g_metrics_data));
    g_metrics_data.conversions_succeeded = 10;
    g_metrics_data.results.delivery_count = 50;
    g_metrics_data.requests_entered = 100;
    g_metrics_data.results.failopen_count = 1;
    g_metrics_data.streaming.requests_total = 30;
    g_metrics_data.streaming.precommit_failopen_total = 4;
    g_metrics_data.streaming.succeeded_total = 25;
    g_metrics_data.streaming.failed_total = 5;
    g_metrics_data.streaming.fallback_total = 2;
    g_metrics_data.streaming.selection.candidate_total = 35;
    g_metrics_data.streaming.selection.output_bytes_total = 1024000;
    g_metrics_data.streaming.engine_choice.streaming = 20;
    g_metrics_data.streaming.engine_choice.full_buffer = 10;
    ngx_http_markdown_metrics = &g_metrics_data;

    ngx_http_markdown_diagnostics_collect_metrics(&out);

    TEST_ASSERT(out.conversions_total == 10, "conversions should be 10");
    TEST_ASSERT(out.streaming_requests_total == 30,
                "streaming_requests should be 30");
    TEST_ASSERT(out.precommit_failopen_total == 4,
                "precommit_failopen should be 4");
    TEST_ASSERT(out.streaming_succeeded_total == 25,
                "streaming_succeeded should be 25");
    TEST_ASSERT(out.streaming_failed_total == 5,
                "streaming_failed should be 5");
    TEST_ASSERT(out.streaming_fallback_total == 2,
                "streaming_fallback should be 2");
    TEST_ASSERT(out.streaming_candidate_total == 35,
                "streaming_candidate should be 35");
    TEST_ASSERT(out.streaming_output_bytes_total == 1024000,
                "streaming_output_bytes should be 1024000");
    TEST_ASSERT(out.engine_choice_streaming == 20,
                "engine_choice_streaming should be 20");
    TEST_ASSERT(out.engine_choice_full_buffer == 10,
                "engine_choice_full_buffer should be 10");

    TEST_PASS("Streaming metrics collected correctly");
}
#endif

/* SHA-256 is implemented by hand in the accessors header (no libcrypto
 * dependency); pin it against NIST vectors so a regression in the
 * transform/padding is caught by the C unit suite rather than only by
 * the Python-hashlib golden checks. */
static void
test_sha256_nist_vectors(void)
{
    static const u_char abc[] = "abc";
    static const u_char empty[] = "";
    static const u_char long_input[] =
        "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    u_char  out[65];

    TEST_SUBSECTION("SHA-256 NIST vectors");

    /* sha256_hex writes exactly 64 hex chars without a terminator (the
     * production caller prefixes "sha256:" itself); zero the buffer so
     * strlen-based checks are deterministic. */
    ngx_memzero(out, sizeof(out));

    /* NIST FIPS 180-4: SHA256("abc") */
    TEST_ASSERT(ngx_http_markdown_sha256_hex(abc, 3, out) == NGX_OK,
                "sha256_hex handles a short input");
    TEST_ASSERT(ngx_strlen(out) == 64, "sha256_hex emits 64 hex chars");
    TEST_ASSERT(ngx_memcmp(out,
        (u_char *) "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        64) == 0, "SHA-256(abc) matches NIST vector");

    /* Empty-string vector. */
    TEST_ASSERT(ngx_http_markdown_sha256_hex(empty, 0, out) == NGX_OK,
                "sha256_hex handles an empty input");
    TEST_ASSERT(ngx_memcmp(out,
        (u_char *) "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        64) == 0, "SHA-256(empty) matches NIST vector");

    /* 448-bit input crosses the padding block boundary. */
    TEST_ASSERT(ngx_http_markdown_sha256_hex(long_input,
                    sizeof(long_input) - 1, out) == NGX_OK,
                "sha256_hex handles a padding-boundary input");
    TEST_ASSERT(ngx_memcmp(out,
        (u_char *) "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
        64) == 0, "SHA-256(padding boundary) matches NIST vector");

    TEST_PASS("SHA-256 NIST vectors match");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("diagnostics_accessors Tests\n");
    printf("========================================\n");

    test_collect_metrics_null_output();
    test_collect_metrics_null_zone();
    test_collect_metrics_with_data();
#ifdef MARKDOWN_STREAMING_ENABLED
    test_collect_metrics_streaming();
#endif
    test_sha256_nist_vectors();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
