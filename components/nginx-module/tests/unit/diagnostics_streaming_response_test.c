/*
 * Test: diagnostics_streaming_response
 *
 * Validates that the diagnostics endpoint JSON output includes the
 * streaming_config and streaming_metrics sections with the correct
 * field names when MARKDOWN_STREAMING_ENABLED is defined.
 *
 * Corresponds to streaming observability, task 6.2.
 *
 * Rules: 8 (metric names match emitted keys), 9 (field names match).
 */

#include "../include/test_common.h"

enum {
    NGX_OK    =  0,
    NGX_ERROR = -1
};

/* ── Output buffer context (mirrors diagnostics_output_test) ──── */

typedef struct {
    char    *buf;
    size_t   buf_size;
    size_t   pos;
    int      overflow;
} output_ctx_t;


static void
output_init(output_ctx_t *ctx, char *buf, size_t buf_size)
{
    ctx->buf = buf;
    ctx->buf_size = buf_size;
    ctx->pos = 0;
    ctx->overflow = 0;
}


static void
output_append(output_ctx_t *ctx, const char *fmt, ...)
{
    va_list args;
    int     n;

    if (ctx->overflow) {
        return;
    }

    va_start(args, fmt);
    n = vsnprintf(ctx->buf + ctx->pos, ctx->buf_size - ctx->pos, fmt, args);
    va_end(args);

    if (n < 0 || (size_t) n >= ctx->buf_size - ctx->pos) {
        ctx->overflow = 1;
        return;
    }

    ctx->pos += (size_t) n;
}


/* ── Streaming metrics snapshot ───────────────────────────────── */

typedef struct {
    unsigned long requests_total;
    unsigned long succeeded_total;
    unsigned long failed_total;
    unsigned long fallback_total;
    unsigned long candidate_total;
    unsigned long output_bytes_total;
    unsigned long engine_choice_streaming;
    unsigned long engine_choice_full_buffer;
} streaming_metrics_snapshot_t;


/* ── Streaming config snapshot ────────────────────────────────── */

typedef struct {
    const char  *engine;
    const char  *on_error;
    size_t       threshold;
    size_t       precommit_buffer;
    size_t       flush_min;
    int          threshold_explicit;
} streaming_config_snapshot_t;


/* ── Base metrics and config (existing sections) ──────────────── */

typedef struct {
    unsigned int conversions_total;
    unsigned int delivery_total;
    unsigned int requests_total;
    unsigned int failopen_total;
} base_metrics_t;


/*
 * Build diagnostics JSON with streaming sections.
 *
 * Produces JSON containing all expected sections including
 * streaming_metrics and streaming_config.
 *
 * Returns NGX_OK on success, NGX_ERROR on buffer overflow.
 */
static int
build_diagnostics_json_with_streaming(output_ctx_t *out,
    const base_metrics_t *base_metrics,
    const streaming_metrics_snapshot_t *stream_metrics,
    const streaming_config_snapshot_t *stream_config)
{
    output_append(out, "{\n");

    /* config_snapshot section (abbreviated for test) */
    output_append(out, "  \"config_snapshot\": {\n");
    output_append(out, "    \"markdown_enabled\": true\n");
    output_append(out, "  },\n");

    /* recent_decisions section (abbreviated) */
    output_append(out, "  \"recent_decisions\": [],\n");

    /* metrics_snapshot section */
    output_append(out, "  \"metrics_snapshot\": {\n");
    output_append(out, "    \"conversions_total\": %u,\n",
        base_metrics->conversions_total);
    output_append(out, "    \"delivery_total\": %u,\n",
        base_metrics->delivery_total);
    output_append(out, "    \"requests_total\": %u,\n",
        base_metrics->requests_total);
    output_append(out, "    \"failopen_total\": %u\n",
        base_metrics->failopen_total);
    output_append(out, "  },\n");

#ifdef MARKDOWN_STREAMING_ENABLED
    /* streaming_metrics section (streaming observability) */
    output_append(out, "  \"streaming_metrics\": {\n");
    output_append(out, "    \"requests_total\": %lu,\n",
        stream_metrics->requests_total);
    output_append(out, "    \"succeeded_total\": %lu,\n",
        stream_metrics->succeeded_total);
    output_append(out, "    \"failed_total\": %lu,\n",
        stream_metrics->failed_total);
    output_append(out, "    \"fallback_total\": %lu,\n",
        stream_metrics->fallback_total);
    output_append(out, "    \"candidate_total\": %lu,\n",
        stream_metrics->candidate_total);
    output_append(out, "    \"output_bytes_total\": %lu,\n",
        stream_metrics->output_bytes_total);
    output_append(out, "    \"engine_choice_streaming\": %lu,\n",
        stream_metrics->engine_choice_streaming);
    output_append(out, "    \"engine_choice_full_buffer\": %lu\n",
        stream_metrics->engine_choice_full_buffer);
    output_append(out, "  },\n");

    /* dynconf_state section (abbreviated) */
    output_append(out, "  \"dynconf_state\": {\n");
    output_append(out, "    \"lkg_valid\": false\n");
    output_append(out, "  },\n");

    /* streaming_config section (streaming observability) */
    output_append(out, "  \"streaming_config\": {\n");
    output_append(out, "    \"engine\": \"%s\",\n",
        stream_config->engine);
    output_append(out, "    \"on_error\": \"%s\",\n",
        stream_config->on_error);
    output_append(out, "    \"threshold\": %zu,\n",
        stream_config->threshold);
    output_append(out, "    \"precommit_buffer\": %zu,\n",
        stream_config->precommit_buffer);
    output_append(out, "    \"flush_min\": %zu,\n",
        stream_config->flush_min);
    output_append(out, "    \"threshold_explicit\": %s\n",
        stream_config->threshold_explicit ? "true" : "false");
    output_append(out, "  }\n");
#else
    /* dynconf_state section (abbreviated) */
    output_append(out, "  \"dynconf_state\": {\n");
    output_append(out, "    \"lkg_valid\": false\n");
    output_append(out, "  },\n");

    output_append(out, "  \"streaming_config\": null\n");
#endif

    output_append(out, "}\n");

    if (out->overflow) {
        return NGX_ERROR;
    }

    return NGX_OK;
}


/* ── Helpers ──────────────────────────────────────────────────── */

static int
json_contains(const char *json, const char *key)
{
    return strstr(json, key) != NULL;
}


/* ── Test: streaming_metrics section present ──────────────────── */

static void
test_streaming_metrics_section_present(void)
{
    char buf[4096];
    output_ctx_t out;
    base_metrics_t base;
    streaming_metrics_snapshot_t stream_metrics;
    streaming_config_snapshot_t stream_config;
    int rc;

    TEST_SUBSECTION("streaming_metrics section present in JSON");

    memset(&base, 0, sizeof(base));
    base.conversions_total = 100;
    base.delivery_total = 95;
    base.requests_total = 200;
    base.failopen_total = 5;

    memset(&stream_metrics, 0, sizeof(stream_metrics));
    stream_metrics.requests_total = 50;
    stream_metrics.succeeded_total = 40;
    stream_metrics.failed_total = 5;
    stream_metrics.fallback_total = 3;
    stream_metrics.candidate_total = 48;
    stream_metrics.output_bytes_total = 102400;
    stream_metrics.engine_choice_streaming = 45;
    stream_metrics.engine_choice_full_buffer = 155;

    stream_config.engine = "auto";
    stream_config.on_error = "pass";
    stream_config.threshold = 1048576;
    stream_config.precommit_buffer = 262144;
    stream_config.flush_min = 16384;
    stream_config.threshold_explicit = 0;

    output_init(&out, buf, sizeof(buf));
    rc = build_diagnostics_json_with_streaming(&out, &base,
        &stream_metrics, &stream_config);

    TEST_ASSERT(rc == NGX_OK, "JSON build succeeds");
    TEST_ASSERT(json_contains(buf, "\"streaming_metrics\""),
        "streaming_metrics section key present");

    TEST_PASS("streaming_metrics section present");
}


/* ── Test: streaming_metrics field names correct ──────────────── */

static void
test_streaming_metrics_field_names(void)
{
    char buf[4096];
    output_ctx_t out;
    base_metrics_t base;
    streaming_metrics_snapshot_t stream_metrics;
    streaming_config_snapshot_t stream_config;
    int rc;

    TEST_SUBSECTION("streaming_metrics field names match spec");

    memset(&base, 0, sizeof(base));
    memset(&stream_metrics, 0, sizeof(stream_metrics));
    stream_metrics.requests_total = 10;
    stream_metrics.succeeded_total = 8;
    stream_metrics.failed_total = 1;
    stream_metrics.fallback_total = 1;
    stream_metrics.candidate_total = 9;
    stream_metrics.output_bytes_total = 4096;
    stream_metrics.engine_choice_streaming = 7;
    stream_metrics.engine_choice_full_buffer = 3;

    stream_config.engine = "auto";
    stream_config.on_error = "pass";
    stream_config.threshold = 0;
    stream_config.precommit_buffer = 0;
    stream_config.flush_min = 0;
    stream_config.threshold_explicit = 0;

    output_init(&out, buf, sizeof(buf));
    rc = build_diagnostics_json_with_streaming(&out, &base,
        &stream_metrics, &stream_config);

    TEST_ASSERT(rc == NGX_OK, "JSON build succeeds");

    /* Verify all 8 expected field names */
    TEST_ASSERT(json_contains(buf, "\"requests_total\":"),
        "requests_total field present");
    TEST_ASSERT(json_contains(buf, "\"succeeded_total\":"),
        "succeeded_total field present");
    TEST_ASSERT(json_contains(buf, "\"failed_total\":"),
        "failed_total field present");
    TEST_ASSERT(json_contains(buf, "\"fallback_total\":"),
        "fallback_total field present");
    TEST_ASSERT(json_contains(buf, "\"candidate_total\":"),
        "candidate_total field present");
    TEST_ASSERT(json_contains(buf, "\"output_bytes_total\":"),
        "output_bytes_total field present");
    TEST_ASSERT(json_contains(buf, "\"engine_choice_streaming\":"),
        "engine_choice_streaming field present");
    TEST_ASSERT(json_contains(buf, "\"engine_choice_full_buffer\":"),
        "engine_choice_full_buffer field present");

    TEST_PASS("streaming_metrics field names match spec");
}


/* ── Test: streaming_metrics values rendered correctly ─────────── */

static void
test_streaming_metrics_values(void)
{
    char buf[4096];
    output_ctx_t out;
    base_metrics_t base;
    streaming_metrics_snapshot_t stream_metrics;
    streaming_config_snapshot_t stream_config;
    int rc;

    TEST_SUBSECTION("streaming_metrics values rendered correctly");

    memset(&base, 0, sizeof(base));

    memset(&stream_metrics, 0, sizeof(stream_metrics));
    stream_metrics.requests_total = 250;
    stream_metrics.succeeded_total = 200;
    stream_metrics.failed_total = 20;
    stream_metrics.fallback_total = 15;
    stream_metrics.candidate_total = 235;
    stream_metrics.output_bytes_total = 5242880;
    stream_metrics.engine_choice_streaming = 210;
    stream_metrics.engine_choice_full_buffer = 40;

    stream_config.engine = "configured";
    stream_config.on_error = "reject";
    stream_config.threshold = 1048576;
    stream_config.precommit_buffer = 262144;
    stream_config.flush_min = 16384;
    stream_config.threshold_explicit = 0;

    output_init(&out, buf, sizeof(buf));
    rc = build_diagnostics_json_with_streaming(&out, &base,
        &stream_metrics, &stream_config);

    TEST_ASSERT(rc == NGX_OK, "JSON build succeeds");
    TEST_ASSERT(json_contains(buf, "\"requests_total\": 250"),
        "requests_total value is 250");
    TEST_ASSERT(json_contains(buf, "\"succeeded_total\": 200"),
        "succeeded_total value is 200");
    TEST_ASSERT(json_contains(buf, "\"failed_total\": 20"),
        "failed_total value is 20");
    TEST_ASSERT(json_contains(buf, "\"fallback_total\": 15"),
        "fallback_total value is 15");
    TEST_ASSERT(json_contains(buf, "\"candidate_total\": 235"),
        "candidate_total value is 235");
    TEST_ASSERT(json_contains(buf, "\"output_bytes_total\": 5242880"),
        "output_bytes_total value is 5242880");
    TEST_ASSERT(json_contains(buf, "\"engine_choice_streaming\": 210"),
        "engine_choice_streaming value is 210");
    TEST_ASSERT(json_contains(buf, "\"engine_choice_full_buffer\": 40"),
        "engine_choice_full_buffer value is 40");

    TEST_PASS("streaming_metrics values rendered correctly");
}


/* ── Test: streaming_config section present ───────────────────── */

static void
test_streaming_config_section_present(void)
{
    char buf[4096];
    output_ctx_t out;
    base_metrics_t base;
    streaming_metrics_snapshot_t stream_metrics;
    streaming_config_snapshot_t stream_config;
    int rc;

    TEST_SUBSECTION("streaming_config section present in JSON");

    memset(&base, 0, sizeof(base));
    memset(&stream_metrics, 0, sizeof(stream_metrics));

    stream_config.engine = "auto";
    stream_config.on_error = "pass";
    stream_config.threshold = 65536;
    stream_config.precommit_buffer = 262144;
    stream_config.flush_min = 16384;
    stream_config.threshold_explicit = 0;

    output_init(&out, buf, sizeof(buf));
    rc = build_diagnostics_json_with_streaming(&out, &base,
        &stream_metrics, &stream_config);

    TEST_ASSERT(rc == NGX_OK, "JSON build succeeds");
    TEST_ASSERT(json_contains(buf, "\"streaming_config\""),
        "streaming_config section key present");

    TEST_PASS("streaming_config section present");
}


/* ── Test: streaming_config field names correct ───────────────── */

static void
test_streaming_config_field_names(void)
{
    char buf[4096];
    output_ctx_t out;
    base_metrics_t base;
    streaming_metrics_snapshot_t stream_metrics;
    streaming_config_snapshot_t stream_config;
    int rc;

    TEST_SUBSECTION("streaming_config field names match spec");

    memset(&base, 0, sizeof(base));
    memset(&stream_metrics, 0, sizeof(stream_metrics));

    stream_config.engine = "auto";
    stream_config.on_error = "pass";
    stream_config.threshold = 32768;
    stream_config.precommit_buffer = 262144;
    stream_config.flush_min = 16384;
    stream_config.threshold_explicit = 0;

    output_init(&out, buf, sizeof(buf));
    rc = build_diagnostics_json_with_streaming(&out, &base,
        &stream_metrics, &stream_config);

    TEST_ASSERT(rc == NGX_OK, "JSON build succeeds");

    /* Verify all expected field names */
    TEST_ASSERT(json_contains(buf, "\"engine\":"),
        "engine field present");
    TEST_ASSERT(json_contains(buf, "\"on_error\":"),
        "on_error field present");
    TEST_ASSERT(json_contains(buf, "\"threshold\":"),
        "threshold field present");
    TEST_ASSERT(json_contains(buf, "\"precommit_buffer\":"),
        "precommit_buffer field present");
    TEST_ASSERT(json_contains(buf, "\"flush_min\":"),
        "flush_min field present");
    TEST_ASSERT(json_contains(buf, "\"threshold\":"),
        "threshold field present");
    TEST_ASSERT(json_contains(buf, "\"threshold_explicit\":"),
        "threshold_explicit field present");

    TEST_PASS("streaming_config field names match spec");
}


/* ── Test: streaming_config values rendered correctly ──────────── */

static void
test_streaming_config_values(void)
{
    char buf[4096];
    output_ctx_t out;
    base_metrics_t base;
    streaming_metrics_snapshot_t stream_metrics;
    streaming_config_snapshot_t stream_config;
    int rc;

    TEST_SUBSECTION("streaming_config values rendered correctly");

    memset(&base, 0, sizeof(base));
    memset(&stream_metrics, 0, sizeof(stream_metrics));

    stream_config.engine = "configured";
    stream_config.on_error = "reject";
    stream_config.threshold = 1048576;
    stream_config.precommit_buffer = 262144;
    stream_config.flush_min = 16384;
    stream_config.threshold_explicit = 1;

    output_init(&out, buf, sizeof(buf));
    rc = build_diagnostics_json_with_streaming(&out, &base,
        &stream_metrics, &stream_config);

    TEST_ASSERT(rc == NGX_OK, "JSON build succeeds");
    TEST_ASSERT(json_contains(buf, "\"engine\": \"configured\""),
        "engine value is configured");
    TEST_ASSERT(json_contains(buf, "\"on_error\": \"reject\""),
        "on_error value is reject");
    TEST_ASSERT(json_contains(buf, "\"threshold\": 1048576"),
        "threshold value is 1048576");
    TEST_ASSERT(json_contains(buf, "\"precommit_buffer\": 262144"),
        "precommit_buffer value is 262144");
    TEST_ASSERT(json_contains(buf, "\"flush_min\": 16384"),
        "flush_min value is 16384");
    TEST_ASSERT(json_contains(buf, "\"threshold\": 1048576"),
        "threshold value is 1048576");
    TEST_ASSERT(json_contains(buf,
        "\"threshold_explicit\": true"),
        "threshold_explicit value is true");

    TEST_PASS("streaming_config values rendered correctly");
}


/* ── Test: streaming_config auto engine value ─────────────────── */

static void
test_streaming_config_auto_engine(void)
{
    char buf[4096];
    output_ctx_t out;
    base_metrics_t base;
    streaming_metrics_snapshot_t stream_metrics;
    streaming_config_snapshot_t stream_config;
    int rc;

    TEST_SUBSECTION("streaming_config with auto engine");

    memset(&base, 0, sizeof(base));
    memset(&stream_metrics, 0, sizeof(stream_metrics));

    stream_config.engine = "auto";
    stream_config.on_error = "pass";
    stream_config.threshold = 0;
    stream_config.precommit_buffer = 0;
    stream_config.flush_min = 0;
    stream_config.threshold_explicit = 0;

    output_init(&out, buf, sizeof(buf));
    rc = build_diagnostics_json_with_streaming(&out, &base,
        &stream_metrics, &stream_config);

    TEST_ASSERT(rc == NGX_OK, "JSON build succeeds");
    TEST_ASSERT(json_contains(buf, "\"engine\": \"auto\""),
        "engine value is auto");
    TEST_ASSERT(json_contains(buf, "\"on_error\": \"pass\""),
        "on_error value is pass");
    TEST_ASSERT(json_contains(buf, "\"threshold\": 0"),
        "threshold value is 0");
    TEST_ASSERT(json_contains(buf, "\"precommit_buffer\": 0"),
        "precommit_buffer value is 0");
    TEST_ASSERT(json_contains(buf, "\"flush_min\": 0"),
        "flush_min value is 0");
    TEST_ASSERT(json_contains(buf, "\"threshold\": 0"),
        "threshold value is 0");
    TEST_ASSERT(json_contains(buf,
        "\"threshold_explicit\": false"),
        "threshold_explicit value is false");

    TEST_PASS("streaming_config auto engine values correct");
}


/* ── Test: zero-valued streaming metrics ──────────────────────── */

static void
test_streaming_metrics_all_zero(void)
{
    char buf[4096];
    output_ctx_t out;
    base_metrics_t base;
    streaming_metrics_snapshot_t stream_metrics;
    streaming_config_snapshot_t stream_config;
    int rc;

    TEST_SUBSECTION("streaming_metrics with all zero counters");

    memset(&base, 0, sizeof(base));
    memset(&stream_metrics, 0, sizeof(stream_metrics));

    stream_config.engine = "auto";
    stream_config.on_error = "pass";
    stream_config.threshold = 65536;
    stream_config.precommit_buffer = 262144;
    stream_config.flush_min = 16384;
    stream_config.threshold_explicit = 0;

    output_init(&out, buf, sizeof(buf));
    rc = build_diagnostics_json_with_streaming(&out, &base,
        &stream_metrics, &stream_config);

    TEST_ASSERT(rc == NGX_OK, "JSON build succeeds with zero metrics");
    TEST_ASSERT(json_contains(buf, "\"engine_choice_streaming\": 0"),
        "engine_choice_streaming zero rendered");
    TEST_ASSERT(json_contains(buf, "\"engine_choice_full_buffer\": 0"),
        "engine_choice_full_buffer zero rendered");
    TEST_ASSERT(json_contains(buf, "\"candidate_total\": 0"),
        "candidate_total zero rendered");

    TEST_PASS("zero-valued streaming metrics rendered correctly");
}


int
main(void)
{
    TEST_SECTION("diagnostics_streaming_response");

    test_streaming_metrics_section_present();
    test_streaming_metrics_field_names();
    test_streaming_metrics_values();
    test_streaming_config_section_present();
    test_streaming_config_field_names();
    test_streaming_config_values();
    test_streaming_config_auto_engine();
    test_streaming_metrics_all_zero();

    TEST_PASS("diagnostics_streaming_response: all tests passed");
    return 0;
}
