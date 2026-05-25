/*
 * Test: diagnostics_output
 *
 * Validates the diagnostics endpoint JSON output format.
 * The endpoint must produce valid JSON containing all 4 required sections:
 *   - config_snapshot (current configuration values)
 *   - recent_decisions (ring buffer of recent decision entries)
 *   - metrics_snapshot (current metric counters)
 *   - dynconf_state (dynamic configuration state)
 *
 * Models the diagnostics output builder as a state machine:
 *   INIT -> BUILD_CONFIG -> BUILD_DECISIONS -> BUILD_METRICS
 *        -> BUILD_DYNCONF -> DONE
 *
 * Also validates ring buffer serialization:
 *   - Empty ring buffer produces []
 *   - Ring buffer with entries produces [{...}, {...}] format
 *
 * Corresponds to task E01.6.
 *
 * Rules: 9 (metric names match emitted keys).
 */

#include "../include/test_common.h"


enum {
    NGX_OK    =  0,
    NGX_ERROR = -1
};

/* Simulated config snapshot */
typedef struct {
    int          markdown_enabled;
    size_t       max_size;
    size_t       decompression_budget;
    unsigned int parse_timeout_ms;
    size_t       parser_budget;
    int          diagnostics_enabled;
} config_snapshot_t;

/* Simulated decision entry */
typedef struct {
    const char   *reason_code;
    const char   *accept_result;
    const char   *conversion_status;
    unsigned int  timestamp;
} decision_entry_t;

/* Simulated metrics snapshot */
typedef struct {
    unsigned int conversions_total;
    unsigned int delivery_total;
    unsigned int requests_total;
    unsigned int failopen_total;
} metrics_snapshot_t;

/* Simulated dynconf state */
typedef struct {
    int          dynconf_enabled;
    unsigned int applied_mtime;
    const char   *last_known_good_status;
    int          pending_reload;
} dynconf_state_t;

/* Ring buffer for recent decisions */
typedef struct {
    decision_entry_t *entries;
    int               capacity;
    int               count;
    int               head;
} decision_ring_t;

/* Output buffer context */
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
    int n;

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


/*
 * Build diagnostics JSON output.
 *
 * Returns NGX_OK on success, NGX_ERROR on buffer overflow.
 */
static int
build_diagnostics_json(output_ctx_t *out,
    const config_snapshot_t *config,
    const decision_entry_t *decisions, int decision_count,
    const metrics_snapshot_t *metrics,
    const dynconf_state_t *dynconf)
{
    int i;

    output_append(out, "{\n");

    /* config_snapshot section */
    output_append(out, "  \"config_snapshot\": {\n");
    output_append(out, "    \"markdown_enabled\": %s,\n",
        config->markdown_enabled ? "true" : "false");
    output_append(out, "    \"max_size\": %zu,\n", config->max_size);
    output_append(out, "    \"decompression_budget\": %zu,\n",
        config->decompression_budget);
    output_append(out, "    \"parse_timeout_ms\": %u,\n",
        config->parse_timeout_ms);
    output_append(out, "    \"parser_budget\": %zu,\n",
        config->parser_budget);
    output_append(out, "    \"diagnostics_enabled\": %s\n",
        config->diagnostics_enabled ? "true" : "false");
    output_append(out, "  },\n");

    /* recent_decisions section (ring buffer serialization) */
    output_append(out, "  \"recent_decisions\": [");
    if (decision_count > 0) {
        output_append(out, "\n");
        for (i = 0; i < decision_count; i++) {
            output_append(out, "    {\n");
            output_append(out, "      \"reason_code\": \"%s\",\n",
                decisions[i].reason_code);
            output_append(out, "      \"accept_result\": \"%s\",\n",
                decisions[i].accept_result);
            output_append(out, "      \"conversion_status\": \"%s\",\n",
                decisions[i].conversion_status);
            output_append(out, "      \"timestamp\": %u\n",
                decisions[i].timestamp);
            output_append(out, "    }%s\n",
                (i < decision_count - 1) ? "," : "");
        }
        output_append(out, "  ],\n");
    } else {
        output_append(out, "],\n");
    }

    /* metrics_snapshot section */
    output_append(out, "  \"metrics_snapshot\": {\n");
    output_append(out, "    \"conversions_total\": %u,\n",
        metrics->conversions_total);
    output_append(out, "    \"delivery_total\": %u,\n",
        metrics->delivery_total);
    output_append(out, "    \"requests_total\": %u,\n",
        metrics->requests_total);
    output_append(out, "    \"failopen_total\": %u\n",
        metrics->failopen_total);
    output_append(out, "  },\n");

    /* dynconf_state section */
    output_append(out, "  \"dynconf_state\": {\n");
    output_append(out, "    \"dynconf_enabled\": %s,\n",
        dynconf->dynconf_enabled ? "true" : "false");
    output_append(out, "    \"applied_mtime\": %u,\n",
        dynconf->applied_mtime);
    output_append(out, "    \"last_known_good_status\": \"%s\",\n",
        dynconf->last_known_good_status != NULL
            ? dynconf->last_known_good_status : "none");
    output_append(out, "    \"pending_reload\": %s\n",
        dynconf->pending_reload ? "true" : "false");
    output_append(out, "  }\n");

    output_append(out, "}\n");

    if (out->overflow) {
        return NGX_ERROR;
    }
    return NGX_OK;
}


/*
 * Serialize ring buffer to JSON array format.
 *
 * Empty ring buffer produces "[]".
 * Non-empty ring buffer produces "[{...}, {...}]".
 *
 * Returns NGX_OK on success, NGX_ERROR on overflow.
 */
static int
serialize_ring_buffer(output_ctx_t *out, const decision_ring_t *ring)
{
    int i;
    int idx;

    output_append(out, "[");

    if (ring == NULL || ring->count == 0) {
        output_append(out, "]");
        if (out->overflow) {
            return NGX_ERROR;
        }
        return NGX_OK;
    }

    for (i = 0; i < ring->count; i++) {
        idx = (ring->head + i) % ring->capacity;
        output_append(out, "{\"reason_code\":\"%s\"}",
            ring->entries[idx].reason_code);
        if (i < ring->count - 1) {
            output_append(out, ", ");
        }
    }

    output_append(out, "]");

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


/* ── Test: basic JSON structure ───────────────────────────────── */

static void
test_basic_json_structure(void)
{
    char buf[4096];
    output_ctx_t out;
    config_snapshot_t config;
    metrics_snapshot_t metrics;
    dynconf_state_t dynconf;
    int rc;

    TEST_SUBSECTION("Basic JSON structure with all 4 sections");

    memset(&config, 0, sizeof(config));
    config.markdown_enabled = 1;
    config.max_size = 1048576;
    config.decompression_budget = 10485760;
    config.parse_timeout_ms = 30000;
    config.parser_budget = 67108864;
    config.diagnostics_enabled = 1;

    memset(&metrics, 0, sizeof(metrics));
    metrics.conversions_total = 100;
    metrics.delivery_total = 95;
    metrics.requests_total = 200;
    metrics.failopen_total = 5;

    memset(&dynconf, 0, sizeof(dynconf));
    dynconf.dynconf_enabled = 1;
    dynconf.applied_mtime = 1700000000;
    dynconf.last_known_good_status = "active";
    dynconf.pending_reload = 0;

    output_init(&out, buf, sizeof(buf));
    rc = build_diagnostics_json(&out, &config, NULL, 0, &metrics, &dynconf);

    TEST_ASSERT(rc == NGX_OK, "JSON build succeeds");
    TEST_ASSERT(json_contains(buf, "\"config_snapshot\""),
        "contains config_snapshot section");
    TEST_ASSERT(json_contains(buf, "\"recent_decisions\""),
        "contains recent_decisions section");
    TEST_ASSERT(json_contains(buf, "\"metrics_snapshot\""),
        "contains metrics_snapshot section");
    TEST_ASSERT(json_contains(buf, "\"dynconf_state\""),
        "contains dynconf_state section");

    TEST_PASS("basic JSON structure with all 4 required sections");
}


/* ── Test: config values rendered correctly ───────────────────── */

static void
test_config_values(void)
{
    char buf[4096];
    output_ctx_t out;
    config_snapshot_t config;
    metrics_snapshot_t metrics;
    dynconf_state_t dynconf;
    int rc;

    TEST_SUBSECTION("Config values rendered correctly");

    memset(&config, 0, sizeof(config));
    config.markdown_enabled = 1;
    config.max_size = 2097152;
    config.decompression_budget = 5242880;
    config.parse_timeout_ms = 15000;
    config.parser_budget = 33554432;
    config.diagnostics_enabled = 0;

    memset(&metrics, 0, sizeof(metrics));
    memset(&dynconf, 0, sizeof(dynconf));
    dynconf.last_known_good_status = "none";

    output_init(&out, buf, sizeof(buf));
    rc = build_diagnostics_json(&out, &config, NULL, 0, &metrics, &dynconf);

    TEST_ASSERT(rc == NGX_OK, "build succeeds");
    TEST_ASSERT(json_contains(buf, "\"markdown_enabled\": true"),
        "markdown_enabled is true");
    TEST_ASSERT(json_contains(buf, "\"max_size\": 2097152"),
        "max_size is 2097152");
    TEST_ASSERT(json_contains(buf, "\"decompression_budget\": 5242880"),
        "decompression_budget is 5242880");
    TEST_ASSERT(json_contains(buf, "\"parse_timeout_ms\": 15000"),
        "parse_timeout_ms is 15000");
    TEST_ASSERT(json_contains(buf, "\"diagnostics_enabled\": false"),
        "diagnostics_enabled is false");

    TEST_PASS("config values rendered correctly");
}


/* ── Test: decisions array rendered ───────────────────────────── */

static void
test_decisions_array(void)
{
    char buf[4096];
    output_ctx_t out;
    config_snapshot_t config;
    metrics_snapshot_t metrics;
    dynconf_state_t dynconf;
    decision_entry_t decisions[2];
    int rc;

    TEST_SUBSECTION("Decisions array rendered");

    memset(&config, 0, sizeof(config));
    config.markdown_enabled = 1;
    config.diagnostics_enabled = 1;

    memset(&metrics, 0, sizeof(metrics));
    memset(&dynconf, 0, sizeof(dynconf));
    dynconf.last_known_good_status = "none";

    decisions[0].reason_code = "CONVERT";
    decisions[0].accept_result = "text/markdown";
    decisions[0].conversion_status = "success";
    decisions[0].timestamp = 1700000000;

    decisions[1].reason_code = "SKIP_ACCEPT";
    decisions[1].accept_result = "text/html";
    decisions[1].conversion_status = "skipped";
    decisions[1].timestamp = 1700000001;

    output_init(&out, buf, sizeof(buf));
    rc = build_diagnostics_json(&out, &config, decisions, 2, &metrics,
        &dynconf);

    TEST_ASSERT(rc == NGX_OK, "build succeeds");
    TEST_ASSERT(json_contains(buf, "\"reason_code\": \"CONVERT\""),
        "first decision has CONVERT reason");
    TEST_ASSERT(json_contains(buf, "\"reason_code\": \"SKIP_ACCEPT\""),
        "second decision has SKIP_ACCEPT reason");
    TEST_ASSERT(json_contains(buf, "\"accept_result\": \"text/markdown\""),
        "accept_result present");
    TEST_ASSERT(json_contains(buf, "\"conversion_status\": \"success\""),
        "conversion_status present");
    TEST_ASSERT(json_contains(buf, "\"timestamp\": 1700000000"),
        "timestamp present");

    TEST_PASS("decisions array rendered correctly");
}


/* ── Test: metrics values rendered ────────────────────────────── */

static void
test_metrics_values(void)
{
    char buf[4096];
    output_ctx_t out;
    config_snapshot_t config;
    metrics_snapshot_t metrics;
    dynconf_state_t dynconf;
    int rc;

    TEST_SUBSECTION("Metrics values rendered");

    memset(&config, 0, sizeof(config));
    config.markdown_enabled = 1;

    memset(&metrics, 0, sizeof(metrics));
    metrics.conversions_total = 500;
    metrics.delivery_total = 480;
    metrics.requests_total = 1000;
    metrics.failopen_total = 20;

    memset(&dynconf, 0, sizeof(dynconf));
    dynconf.last_known_good_status = "none";

    output_init(&out, buf, sizeof(buf));
    rc = build_diagnostics_json(&out, &config, NULL, 0, &metrics, &dynconf);

    TEST_ASSERT(rc == NGX_OK, "build succeeds");
    TEST_ASSERT(json_contains(buf, "\"conversions_total\": 500"),
        "conversions_total is 500");
    TEST_ASSERT(json_contains(buf, "\"delivery_total\": 480"),
        "delivery_total is 480");
    TEST_ASSERT(json_contains(buf, "\"requests_total\": 1000"),
        "requests_total is 1000");
    TEST_ASSERT(json_contains(buf, "\"failopen_total\": 20"),
        "failopen_total is 20");

    TEST_PASS("metrics values rendered correctly");
}


/* ── Test: buffer overflow detection ──────────────────────────── */

static void
test_buffer_overflow(void)
{
    char buf[32];  /* intentionally tiny */
    output_ctx_t out;
    config_snapshot_t config;
    metrics_snapshot_t metrics;
    dynconf_state_t dynconf;
    int rc;

    TEST_SUBSECTION("Buffer overflow detection");

    memset(&config, 0, sizeof(config));
    memset(&metrics, 0, sizeof(metrics));
    memset(&dynconf, 0, sizeof(dynconf));
    dynconf.last_known_good_status = "none";

    output_init(&out, buf, sizeof(buf));
    rc = build_diagnostics_json(&out, &config, NULL, 0, &metrics, &dynconf);

    TEST_ASSERT(rc == NGX_ERROR, "overflow detected");
    TEST_ASSERT(out.overflow == 1, "overflow flag set");

    TEST_PASS("buffer overflow detected correctly");
}


/* ── Test: empty decisions array (ring buffer empty) ──────────── */

static void
test_empty_decisions(void)
{
    char buf[4096];
    output_ctx_t out;
    config_snapshot_t config;
    metrics_snapshot_t metrics;
    dynconf_state_t dynconf;
    int rc;

    TEST_SUBSECTION("Empty decisions array (empty ring buffer)");

    memset(&config, 0, sizeof(config));
    config.markdown_enabled = 1;
    memset(&metrics, 0, sizeof(metrics));
    memset(&dynconf, 0, sizeof(dynconf));
    dynconf.last_known_good_status = "none";

    output_init(&out, buf, sizeof(buf));
    rc = build_diagnostics_json(&out, &config, NULL, 0, &metrics, &dynconf);

    TEST_ASSERT(rc == NGX_OK, "build succeeds with empty decisions");
    TEST_ASSERT(json_contains(buf, "\"recent_decisions\": []"),
        "empty decisions produces []");

    TEST_PASS("empty decisions array handled");
}


/* ── Test: ring buffer serialization empty ────────────────────── */

static void
test_ring_buffer_empty(void)
{
    char buf[256];
    output_ctx_t out;
    decision_ring_t ring;
    int rc;

    TEST_SUBSECTION("Ring buffer serialization: empty produces []");

    memset(&ring, 0, sizeof(ring));
    ring.entries = NULL;
    ring.capacity = 16;
    ring.count = 0;
    ring.head = 0;

    output_init(&out, buf, sizeof(buf));
    rc = serialize_ring_buffer(&out, &ring);

    TEST_ASSERT(rc == NGX_OK, "serialize succeeds");
    TEST_ASSERT(strcmp(buf, "[]") == 0, "empty ring produces []");

    TEST_PASS("ring buffer empty serialization");
}


/* ── Test: ring buffer serialization with entries ─────────────── */

static void
test_ring_buffer_with_entries(void)
{
    char buf[512];
    output_ctx_t out;
    decision_ring_t ring;
    decision_entry_t entries[4];
    int rc;

    TEST_SUBSECTION("Ring buffer serialization: entries produce [{...}]");

    entries[0].reason_code = "CONVERT";
    entries[0].accept_result = "text/markdown";
    entries[0].conversion_status = "success";
    entries[0].timestamp = 100;

    entries[1].reason_code = "SKIP_ACCEPT";
    entries[1].accept_result = "text/html";
    entries[1].conversion_status = "skipped";
    entries[1].timestamp = 101;

    ring.entries = entries;
    ring.capacity = 4;
    ring.count = 2;
    ring.head = 0;

    output_init(&out, buf, sizeof(buf));
    rc = serialize_ring_buffer(&out, &ring);

    TEST_ASSERT(rc == NGX_OK, "serialize succeeds");
    TEST_ASSERT(buf[0] == '[', "starts with [");
    TEST_ASSERT(buf[strlen(buf) - 1] == ']', "ends with ]");
    TEST_ASSERT(json_contains(buf, "{\"reason_code\":\"CONVERT\"}"),
        "first entry present");
    TEST_ASSERT(json_contains(buf, "{\"reason_code\":\"SKIP_ACCEPT\"}"),
        "second entry present");
    TEST_ASSERT(json_contains(buf, ", "),
        "entries separated by comma");

    TEST_PASS("ring buffer with entries serialization");
}


/* ── Test: ring buffer NULL produces [] ───────────────────────── */

static void
test_ring_buffer_null(void)
{
    char buf[64];
    output_ctx_t out;
    int rc;

    TEST_SUBSECTION("Ring buffer NULL produces []");

    output_init(&out, buf, sizeof(buf));
    rc = serialize_ring_buffer(&out, NULL);

    TEST_ASSERT(rc == NGX_OK, "serialize NULL ring succeeds");
    TEST_ASSERT(strcmp(buf, "[]") == 0, "NULL ring produces []");

    TEST_PASS("ring buffer NULL serialization");
}


/* ── Test: dynconf_state section rendered ─────────────────────── */

static void
test_dynconf_state(void)
{
    char buf[4096];
    output_ctx_t out;
    config_snapshot_t config;
    metrics_snapshot_t metrics;
    dynconf_state_t dynconf;
    int rc;

    TEST_SUBSECTION("dynconf_state section rendered");

    memset(&config, 0, sizeof(config));
    config.markdown_enabled = 1;
    memset(&metrics, 0, sizeof(metrics));

    dynconf.dynconf_enabled = 1;
    dynconf.applied_mtime = 1700001234;
    dynconf.last_known_good_status = "active";
    dynconf.pending_reload = 1;

    output_init(&out, buf, sizeof(buf));
    rc = build_diagnostics_json(&out, &config, NULL, 0, &metrics, &dynconf);

    TEST_ASSERT(rc == NGX_OK, "build succeeds");
    TEST_ASSERT(json_contains(buf, "\"dynconf_state\""),
        "dynconf_state section present");
    TEST_ASSERT(json_contains(buf, "\"dynconf_enabled\": true"),
        "dynconf_enabled is true");
    TEST_ASSERT(json_contains(buf, "\"applied_mtime\": 1700001234"),
        "applied_mtime rendered");
    TEST_ASSERT(json_contains(buf, "\"last_known_good_status\": \"active\""),
        "last_known_good_status rendered");
    TEST_ASSERT(json_contains(buf, "\"pending_reload\": true"),
        "pending_reload rendered");

    TEST_PASS("dynconf_state section rendered correctly");
}


int
main(void)
{
    TEST_SECTION("diagnostics_output");

    test_basic_json_structure();
    test_config_values();
    test_decisions_array();
    test_metrics_values();
    test_buffer_overflow();
    test_empty_decisions();
    test_ring_buffer_empty();
    test_ring_buffer_with_entries();
    test_ring_buffer_null();
    test_dynconf_state();

    TEST_PASS("diagnostics_output: all tests passed");
    return 0;
}
