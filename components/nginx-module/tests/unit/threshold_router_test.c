/*
 * Test: threshold_router
 * Description: threshold router path selection, config parsing, and metrics
 *
 * Validates: Requirements 16.3, 16.4, 16.6
 *
 * Property 6: Threshold router path selection correctness
 *   - threshold=off (0): always full-buffer
 *   - CL < threshold: full-buffer
 *   - CL >= threshold: incremental
 *
 * Property 7: Special request path semantics preserved
 *   - HEAD requests always full-buffer regardless of threshold
 *   - 304 responses always full-buffer regardless of threshold
 *   - fail-open replay always full-buffer
 */

#include "test_common.h"

/* --- Processing path constants (mirror module header) --- */

#define PATH_FULLBUFFER   0
#define PATH_INCREMENTAL  1

/* --- HTTP method / status constants --- */

#define METHOD_GET   1
#define METHOD_HEAD  2

#define STATUS_OK            200
#define STATUS_NOT_MODIFIED  304

/* --- Lightweight config / context / metrics stubs --- */

typedef struct {
    size_t  large_body_threshold;   /* 0 = off */
} conf_t;

typedef struct {
    int         method;
    int         status;
    long        content_length;     /* -1 = unknown */
} request_t;

typedef struct {
    int  processing_path;
} ctx_t;

typedef struct {
    unsigned long  fullbuffer_path_hits;
    unsigned long  incremental_path_hits;
} metrics_t;

/* --- Threshold router logic (mirrors ngx_http_markdown_header_filter) --- */

/*
 * Select processing path based on threshold, request method,
 * response status, and Content-Length.
 *
 * HEAD requests and 304 responses always use the full-buffer
 * path.  When threshold is off (== 0) all requests also use
 * the full-buffer path.
 *
 * When Content-Length is known and >= threshold, select the
 * incremental path.  When Content-Length is absent (-1), the
 * decision is deferred (stays full-buffer here; body filter
 * may upgrade later).
 */
static void
select_processing_path(const conf_t *conf, const request_t *r,
    ctx_t *ctx, metrics_t *m)
{
    ctx->processing_path = PATH_FULLBUFFER;

    if (conf->large_body_threshold > 0
        && r->method != METHOD_HEAD
        && r->status != STATUS_NOT_MODIFIED
        && r->content_length >= 0
        && (size_t) r->content_length
            >= conf->large_body_threshold)
    {
        ctx->processing_path = PATH_INCREMENTAL;
    }

    if (ctx->processing_path == PATH_INCREMENTAL) {
        m->incremental_path_hits++;
    } else {
        m->fullbuffer_path_hits++;
    }
}

/*
 * Deferred path upgrade in body filter: when Content-Length
 * was absent in the header phase, the body filter checks the
 * buffered size against the threshold.
 */
static void
deferred_path_upgrade(const conf_t *conf, const request_t *r,
    ctx_t *ctx, size_t buffered_size, metrics_t *m)
{
    if (conf->large_body_threshold > 0
        && ctx->processing_path == PATH_FULLBUFFER
        && r->method != METHOD_HEAD
        && r->status != STATUS_NOT_MODIFIED
        && buffered_size >= conf->large_body_threshold)
    {
        ctx->processing_path = PATH_INCREMENTAL;
        /* Correct counters: undo header-phase fullbuffer hit
         * (guard against underflow) */
        if (m->fullbuffer_path_hits > 0) {
            m->fullbuffer_path_hits--;
        }
        m->incremental_path_hits++;
    }
}

/* --- Config parsing stub (mirrors directive handler) --- */

#define CONF_UNSET_SIZE  ((size_t) -1)
#define PARSE_ERROR      ((size_t) -2)

/*
 * Parse "off" or a byte-size string into a threshold value.
 * Returns 0 for "off", the parsed size for valid sizes,
 * or PARSE_ERROR for invalid input.
 */
static size_t
parse_threshold(const char *value)
{
    size_t       result = 0;
    const char  *p;

    if (value == NULL) {
        return PARSE_ERROR;
    }

    if (STR_EQ(value, "off") || STR_EQ(value, "OFF")
        || STR_EQ(value, "Off"))
    {
        return 0;
    }

    /* Simple numeric parser with optional k/m suffix */
    p = value;
    while (*p >= '0' && *p <= '9') {
        result = result * 10 + (size_t)(*p - '0');
        p++;
    }

    if (p == value) {
        return PARSE_ERROR;   /* no digits */
    }

    if (*p == 'k' || *p == 'K') {
        result *= 1024;
        p++;
    } else if (*p == 'm' || *p == 'M') {
        result *= 1024 * 1024;
        p++;
    }

    if (*p != '\0') {
        return PARSE_ERROR;   /* trailing garbage */
    }

    return result;
}

/* --- Config merge stub (mirrors merge_conf) --- */

static void
merge_threshold(size_t *child, size_t parent)
{
    if (*child == CONF_UNSET_SIZE) {
        *child = (parent == CONF_UNSET_SIZE) ? 0 : parent;
    }
}

/* --- Helper constructors --- */

static conf_t
make_conf(size_t threshold)
{
    conf_t c;
    c.large_body_threshold = threshold;
    return c;
}

static request_t
make_request(int method, int status, long content_length)
{
    request_t r;
    r.method = method;
    r.status = status;
    r.content_length = content_length;
    return r;
}

static ctx_t
fresh_ctx(void)
{
    ctx_t ctx;
    ctx.processing_path = PATH_FULLBUFFER;
    return ctx;
}

static metrics_t
fresh_metrics(void)
{
    metrics_t m;
    m.fullbuffer_path_hits = 0;
    m.incremental_path_hits = 0;
    return m;
}

/* ================================================================
 * Property 6: Threshold router path selection correctness
 * Validates: Requirements 16.3, 16.4
 * ================================================================ */

static void
test_threshold_off_always_fullbuffer(void)
{
    conf_t     c = make_conf(0);
    request_t  r = make_request(METHOD_GET, STATUS_OK, 999999);
    ctx_t      ctx = fresh_ctx();
    metrics_t  m = fresh_metrics();

    TEST_SUBSECTION("threshold=off: always full-buffer");

    select_processing_path(&c, &r, &ctx, &m);
    TEST_ASSERT(ctx.processing_path == PATH_FULLBUFFER,
        "threshold=off should select full-buffer even for large CL");
    TEST_ASSERT(m.fullbuffer_path_hits == 1,
        "fullbuffer counter should increment");
    TEST_ASSERT(m.incremental_path_hits == 0,
        "incremental counter should stay zero");
    TEST_PASS("threshold=off always selects full-buffer");
}

static void
test_cl_below_threshold_fullbuffer(void)
{
    conf_t     c = make_conf(1024);
    request_t  r = make_request(METHOD_GET, STATUS_OK, 512);
    ctx_t      ctx = fresh_ctx();
    metrics_t  m = fresh_metrics();

    TEST_SUBSECTION("CL < threshold: full-buffer");

    select_processing_path(&c, &r, &ctx, &m);
    TEST_ASSERT(ctx.processing_path == PATH_FULLBUFFER,
        "CL below threshold should select full-buffer");
    TEST_ASSERT(m.fullbuffer_path_hits == 1,
        "fullbuffer counter should increment");
    TEST_ASSERT(m.incremental_path_hits == 0,
        "incremental counter should stay zero");
    TEST_PASS("CL < threshold selects full-buffer");
}

static void
test_cl_equal_threshold_incremental(void)
{
    conf_t     c = make_conf(1024);
    request_t  r = make_request(METHOD_GET, STATUS_OK, 1024);
    ctx_t      ctx = fresh_ctx();
    metrics_t  m = fresh_metrics();

    TEST_SUBSECTION("CL == threshold: incremental");

    select_processing_path(&c, &r, &ctx, &m);
    TEST_ASSERT(ctx.processing_path == PATH_INCREMENTAL,
        "CL equal to threshold should select incremental");
    TEST_ASSERT(m.incremental_path_hits == 1,
        "incremental counter should increment");
    TEST_ASSERT(m.fullbuffer_path_hits == 0,
        "fullbuffer counter should stay zero");
    TEST_PASS("CL == threshold selects incremental");
}

static void
test_cl_above_threshold_incremental(void)
{
    conf_t     c = make_conf(1024);
    request_t  r = make_request(METHOD_GET, STATUS_OK, 2048);
    ctx_t      ctx = fresh_ctx();
    metrics_t  m = fresh_metrics();

    TEST_SUBSECTION("CL > threshold: incremental");

    select_processing_path(&c, &r, &ctx, &m);
    TEST_ASSERT(ctx.processing_path == PATH_INCREMENTAL,
        "CL above threshold should select incremental");
    TEST_ASSERT(m.incremental_path_hits == 1,
        "incremental counter should increment");
    TEST_PASS("CL > threshold selects incremental");
}

static void
test_unknown_cl_defers_to_fullbuffer(void)
{
    conf_t     c = make_conf(1024);
    request_t  r = make_request(METHOD_GET, STATUS_OK, -1);
    ctx_t      ctx = fresh_ctx();
    metrics_t  m = fresh_metrics();

    TEST_SUBSECTION("Unknown CL: deferred (full-buffer initially)");

    select_processing_path(&c, &r, &ctx, &m);
    TEST_ASSERT(ctx.processing_path == PATH_FULLBUFFER,
        "Unknown CL should defer to full-buffer in header phase");
    TEST_ASSERT(m.fullbuffer_path_hits == 1,
        "fullbuffer counter should increment for deferred case");
    TEST_PASS("Unknown CL defers to full-buffer");
}

static void
test_deferred_upgrade_when_buffered_exceeds(void)
{
    conf_t     c = make_conf(1024);
    request_t  r = make_request(METHOD_GET, STATUS_OK, -1);
    ctx_t      ctx = fresh_ctx();
    metrics_t  m = fresh_metrics();

    TEST_SUBSECTION("Deferred upgrade: buffered >= threshold");

    /* Header phase: unknown CL → full-buffer */
    select_processing_path(&c, &r, &ctx, &m);
    TEST_ASSERT(ctx.processing_path == PATH_FULLBUFFER,
        "Header phase should select full-buffer");
    TEST_ASSERT(m.fullbuffer_path_hits == 1,
        "fullbuffer should be 1 after header phase");

    /* Body filter: buffered size exceeds threshold → upgrade */
    deferred_path_upgrade(&c, &r, &ctx, 2048, &m);
    TEST_ASSERT(ctx.processing_path == PATH_INCREMENTAL,
        "Deferred upgrade should switch to incremental");
    TEST_ASSERT(m.fullbuffer_path_hits == 0,
        "fullbuffer counter should be corrected to 0");
    TEST_ASSERT(m.incremental_path_hits == 1,
        "incremental counter should be 1 after upgrade");
    TEST_PASS("Deferred upgrade works correctly");
}

static void
test_deferred_no_upgrade_when_below(void)
{
    conf_t     c = make_conf(1024);
    request_t  r = make_request(METHOD_GET, STATUS_OK, -1);
    ctx_t      ctx = fresh_ctx();
    metrics_t  m = fresh_metrics();

    TEST_SUBSECTION("Deferred: no upgrade when buffered < threshold");

    select_processing_path(&c, &r, &ctx, &m);
    deferred_path_upgrade(&c, &r, &ctx, 512, &m);
    TEST_ASSERT(ctx.processing_path == PATH_FULLBUFFER,
        "Should remain full-buffer when buffered < threshold");
    TEST_ASSERT(m.fullbuffer_path_hits == 1,
        "fullbuffer counter should stay at 1");
    TEST_ASSERT(m.incremental_path_hits == 0,
        "incremental counter should stay at 0");
    TEST_PASS("No spurious deferred upgrade");
}

/* ================================================================
 * Property 7: Special request path semantics preserved
 * Validates: Requirement 16.6
 * ================================================================ */

static void
test_head_always_fullbuffer(void)
{
    conf_t     c = make_conf(512);
    request_t  r = make_request(METHOD_HEAD, STATUS_OK, 999999);
    ctx_t      ctx = fresh_ctx();
    metrics_t  m = fresh_metrics();

    TEST_SUBSECTION("HEAD request: always full-buffer");

    select_processing_path(&c, &r, &ctx, &m);
    TEST_ASSERT(ctx.processing_path == PATH_FULLBUFFER,
        "HEAD should always select full-buffer");
    TEST_ASSERT(m.fullbuffer_path_hits == 1,
        "fullbuffer counter should increment for HEAD");
    TEST_ASSERT(m.incremental_path_hits == 0,
        "incremental counter should stay zero for HEAD");
    TEST_PASS("HEAD always uses full-buffer");
}

static void
test_head_deferred_no_upgrade(void)
{
    conf_t     c = make_conf(512);
    request_t  r = make_request(METHOD_HEAD, STATUS_OK, -1);
    ctx_t      ctx = fresh_ctx();
    metrics_t  m = fresh_metrics();

    TEST_SUBSECTION("HEAD request: deferred upgrade blocked");

    select_processing_path(&c, &r, &ctx, &m);
    deferred_path_upgrade(&c, &r, &ctx, 99999, &m);
    TEST_ASSERT(ctx.processing_path == PATH_FULLBUFFER,
        "HEAD should block deferred upgrade");
    TEST_PASS("HEAD blocks deferred upgrade");
}

static void
test_304_always_fullbuffer(void)
{
    conf_t     c = make_conf(512);
    request_t  r = make_request(METHOD_GET, STATUS_NOT_MODIFIED,
                                999999);
    ctx_t      ctx = fresh_ctx();
    metrics_t  m = fresh_metrics();

    TEST_SUBSECTION("304 response: always full-buffer");

    select_processing_path(&c, &r, &ctx, &m);
    TEST_ASSERT(ctx.processing_path == PATH_FULLBUFFER,
        "304 should always select full-buffer");
    TEST_ASSERT(m.fullbuffer_path_hits == 1,
        "fullbuffer counter should increment for 304");
    TEST_ASSERT(m.incremental_path_hits == 0,
        "incremental counter should stay zero for 304");
    TEST_PASS("304 always uses full-buffer");
}

static void
test_304_deferred_no_upgrade(void)
{
    conf_t     c = make_conf(512);
    request_t  r = make_request(METHOD_GET, STATUS_NOT_MODIFIED,
                                -1);
    ctx_t      ctx = fresh_ctx();
    metrics_t  m = fresh_metrics();

    TEST_SUBSECTION("304 response: deferred upgrade blocked");

    select_processing_path(&c, &r, &ctx, &m);
    deferred_path_upgrade(&c, &r, &ctx, 99999, &m);
    TEST_ASSERT(ctx.processing_path == PATH_FULLBUFFER,
        "304 should block deferred upgrade");
    TEST_PASS("304 blocks deferred upgrade");
}

/* ================================================================
 * Config parsing: markdown_large_body_threshold
 * ================================================================ */

static void
test_parse_off(void)
{
    TEST_SUBSECTION("Config parse: 'off' → 0");

    TEST_ASSERT(parse_threshold("off") == 0,
        "'off' should parse to 0");
    TEST_ASSERT(parse_threshold("OFF") == 0,
        "'OFF' should parse to 0");
    TEST_ASSERT(parse_threshold("Off") == 0,
        "'Off' should parse to 0");
    TEST_PASS("'off' variants parse correctly");
}

static void
test_parse_byte_sizes(void)
{
    TEST_SUBSECTION("Config parse: byte sizes");

    TEST_ASSERT(parse_threshold("0") == 0,
        "'0' should parse to 0 (treated as off)");
    TEST_ASSERT(parse_threshold("1024") == 1024,
        "'1024' should parse to 1024");
    TEST_ASSERT(parse_threshold("512k") == 512 * 1024,
        "'512k' should parse to 524288");
    TEST_ASSERT(parse_threshold("512K") == 512 * 1024,
        "'512K' should parse to 524288");
    TEST_ASSERT(parse_threshold("1m") == 1024 * 1024,
        "'1m' should parse to 1048576");
    TEST_ASSERT(parse_threshold("1M") == 1024 * 1024,
        "'1M' should parse to 1048576");
    TEST_ASSERT(parse_threshold("5m") == 5 * 1024 * 1024,
        "'5m' should parse to 5242880");
    TEST_PASS("Byte size parsing works");
}

static void
test_parse_invalid(void)
{
    TEST_SUBSECTION("Config parse: invalid values");

    TEST_ASSERT(parse_threshold("abc") == PARSE_ERROR,
        "'abc' should be rejected");
    TEST_ASSERT(parse_threshold("") == PARSE_ERROR,
        "empty string should be rejected");
    TEST_ASSERT(parse_threshold(NULL) == PARSE_ERROR,
        "NULL should be rejected");
    TEST_ASSERT(parse_threshold("512g") == PARSE_ERROR,
        "'512g' (invalid suffix) should be rejected");
    TEST_ASSERT(parse_threshold("12k3") == PARSE_ERROR,
        "'12k3' (trailing garbage) should be rejected");
    TEST_PASS("Invalid values rejected correctly");
}

/* ================================================================
 * Config merge / inheritance
 * ================================================================ */

static void
test_merge_inherit_from_parent(void)
{
    size_t child = CONF_UNSET_SIZE;
    size_t parent = 512 * 1024;

    TEST_SUBSECTION("Config merge: child inherits parent");

    merge_threshold(&child, parent);
    TEST_ASSERT(child == 512 * 1024,
        "Unset child should inherit parent threshold");
    TEST_PASS("Inheritance works");
}

static void
test_merge_child_overrides(void)
{
    size_t child = 1024 * 1024;
    size_t parent = 512 * 1024;

    TEST_SUBSECTION("Config merge: child overrides parent");

    merge_threshold(&child, parent);
    TEST_ASSERT(child == 1024 * 1024,
        "Set child should keep its own threshold");
    TEST_PASS("Override works");
}

static void
test_merge_both_unset_defaults_off(void)
{
    size_t child = CONF_UNSET_SIZE;
    size_t parent = CONF_UNSET_SIZE;

    TEST_SUBSECTION("Config merge: both unset defaults to off (0)");

    merge_threshold(&child, parent);
    TEST_ASSERT(child == 0,
        "Both unset should default to 0 (off)");
    TEST_PASS("Default off when both unset");
}

/* ================================================================
 * Metrics counter tests
 * ================================================================ */

static void
test_metrics_fullbuffer_increments(void)
{
    conf_t     c = make_conf(0);
    request_t  r = make_request(METHOD_GET, STATUS_OK, 1024);
    ctx_t      ctx;
    metrics_t  m = fresh_metrics();

    TEST_SUBSECTION("Metrics: fullbuffer counter increments");

    for (int i = 0; i < 5; i++) {
        ctx = fresh_ctx();
        select_processing_path(&c, &r, &ctx, &m);
    }

    TEST_ASSERT(m.fullbuffer_path_hits == 5,
        "fullbuffer counter should be 5 after 5 requests");
    TEST_ASSERT(m.incremental_path_hits == 0,
        "incremental counter should remain 0");
    TEST_PASS("Fullbuffer counter accumulates correctly");
}

static void
test_metrics_incremental_increments(void)
{
    conf_t     c = make_conf(512);
    request_t  r = make_request(METHOD_GET, STATUS_OK, 1024);
    ctx_t      ctx;
    metrics_t  m = fresh_metrics();

    TEST_SUBSECTION("Metrics: incremental counter increments");

    for (int i = 0; i < 3; i++) {
        ctx = fresh_ctx();
        select_processing_path(&c, &r, &ctx, &m);
    }

    TEST_ASSERT(m.incremental_path_hits == 3,
        "incremental counter should be 3 after 3 requests");
    TEST_ASSERT(m.fullbuffer_path_hits == 0,
        "fullbuffer counter should remain 0");
    TEST_PASS("Incremental counter accumulates correctly");
}

static void
test_metrics_mixed_paths(void)
{
    conf_t     c = make_conf(1024);
    request_t  r_small = make_request(METHOD_GET, STATUS_OK, 512);
    request_t  r_large = make_request(METHOD_GET, STATUS_OK, 2048);
    request_t  r_head  = make_request(METHOD_HEAD, STATUS_OK, 2048);
    ctx_t      ctx;
    metrics_t  m = fresh_metrics();

    TEST_SUBSECTION("Metrics: mixed path counters");

    /* 2 small (full-buffer) + 2 large (incremental) + 1 HEAD (full-buffer) */
    ctx = fresh_ctx();
    select_processing_path(&c, &r_small, &ctx, &m);
    ctx = fresh_ctx();
    select_processing_path(&c, &r_small, &ctx, &m);
    ctx = fresh_ctx();
    select_processing_path(&c, &r_large, &ctx, &m);
    ctx = fresh_ctx();
    select_processing_path(&c, &r_large, &ctx, &m);
    ctx = fresh_ctx();
    select_processing_path(&c, &r_head, &ctx, &m);

    TEST_ASSERT(m.fullbuffer_path_hits == 3,
        "fullbuffer should be 3 (2 small + 1 HEAD)");
    TEST_ASSERT(m.incremental_path_hits == 2,
        "incremental should be 2 (2 large)");
    TEST_PASS("Mixed path counters are correct");
}

/* ================================================================
 * main
 * ================================================================ */

int
main(void)
{
    printf("\n========================================\n");
    printf("threshold_router Tests\n");
    printf("========================================\n");

    TEST_SECTION("Property 6: Threshold router path selection");
    test_threshold_off_always_fullbuffer();
    test_cl_below_threshold_fullbuffer();
    test_cl_equal_threshold_incremental();
    test_cl_above_threshold_incremental();
    test_unknown_cl_defers_to_fullbuffer();
    test_deferred_upgrade_when_buffered_exceeds();
    test_deferred_no_upgrade_when_below();

    TEST_SECTION("Property 7: Special request path semantics");
    test_head_always_fullbuffer();
    test_head_deferred_no_upgrade();
    test_304_always_fullbuffer();
    test_304_deferred_no_upgrade();

    TEST_SECTION("Config parsing: markdown_large_body_threshold");
    test_parse_off();
    test_parse_byte_sizes();
    test_parse_invalid();

    TEST_SECTION("Config merge / inheritance");
    test_merge_inherit_from_parent();
    test_merge_child_overrides();
    test_merge_both_unset_defaults_off();

    TEST_SECTION("Metrics: path hit counters");
    test_metrics_fullbuffer_increments();
    test_metrics_incremental_increments();
    test_metrics_mixed_paths();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
