/*
 * Test: eligibility
 * Description: request eligibility checking
 */

#include "test_common.h"
#include <ctype.h>

#define METHOD_GET 1
#define METHOD_HEAD 2
#define METHOD_POST 3

typedef enum {
    ELIGIBLE = 0,
    INELIGIBLE_METHOD,
    INELIGIBLE_STATUS,
    INELIGIBLE_CONTENT_TYPE,
    INELIGIBLE_SIZE,
    INELIGIBLE_STREAMING,
    INELIGIBLE_RANGE,
    INELIGIBLE_CONFIG
} eligibility_t;

typedef struct {
    int enabled;
    size_t max_size;
    const char *stream_types[8];
    size_t stream_type_count;
} conf_t;

typedef struct {
    int method;
    int status;
    const char *content_type;
    long content_length;
    int has_range_header;
} request_t;

static int
strncasecmp_ascii(const char *a, const char *b, size_t n)
{
    size_t i;
    for (i = 0; i < n; i++) {
        unsigned char ca = (unsigned char) a[i];
        unsigned char cb = (unsigned char) b[i];
        if (tolower(ca) != tolower(cb)) {
            return (int) tolower(ca) - (int) tolower(cb);
        }
        if (ca == '\0' || cb == '\0') {
            break;
        }
    }
    return 0;
}

static int
starts_with_ci(const char *s, const char *prefix)
{
    size_t plen = strlen(prefix);
    return strlen(s) >= plen && strncasecmp_ascii(s, prefix, plen) == 0;
}

static int
is_html_content_type(const char *content_type)
{
    if (content_type == NULL || content_type[0] == '\0') {
        return 0;
    }
    if (!starts_with_ci(content_type, "text/html")) {
        return 0;
    }
    return content_type[9] == '\0' || content_type[9] == ';' || content_type[9] == ' ';
}

static int
is_streaming_type(const char *content_type, const conf_t *conf)
{
    size_t i;
    if (content_type == NULL) {
        return 0;
    }

    if (starts_with_ci(content_type, "text/event-stream")) {
        return 1;
    }

    for (i = 0; i < conf->stream_type_count; i++) {
        if (starts_with_ci(content_type, conf->stream_types[i])) {
            return 1;
        }
    }
    return 0;
}

static eligibility_t
check_eligibility(const request_t *r, const conf_t *conf)
{
    if (!conf->enabled) {
        return INELIGIBLE_CONFIG;
    }
    if (!(r->method == METHOD_GET || r->method == METHOD_HEAD)) {
        return INELIGIBLE_METHOD;
    }
    if (r->status != 200) {
        return INELIGIBLE_STATUS;
    }
    if (r->has_range_header) {
        return INELIGIBLE_RANGE;
    }
    if (is_streaming_type(r->content_type, conf)) {
        return INELIGIBLE_STREAMING;
    }
    if (!is_html_content_type(r->content_type)) {
        return INELIGIBLE_CONTENT_TYPE;
    }
    if (r->content_length >= 0 && (size_t) r->content_length > conf->max_size) {
        return INELIGIBLE_SIZE;
    }
    return ELIGIBLE;
}

static request_t
base_request(void)
{
    request_t r;
    r.method = METHOD_GET;
    r.status = 200;
    r.content_type = "text/html; charset=utf-8";
    r.content_length = 1024;
    r.has_range_header = 0;
    return r;
}

static conf_t
base_conf(void)
{
    conf_t c;
    memset(&c, 0, sizeof(c));
    c.enabled = 1;
    c.max_size = 10 * 1024 * 1024;
    return c;
}

static void
test_positive_and_boundaries(void)
{
    conf_t c = base_conf();
    request_t r = base_request();

    TEST_SUBSECTION("Positive path and boundary size checks");

    TEST_ASSERT(check_eligibility(&r, &c) == ELIGIBLE, "Baseline request should be eligible");
    r.content_length = (long) c.max_size;
    TEST_ASSERT(check_eligibility(&r, &c) == ELIGIBLE, "content_length == max_size should be eligible");
    r.content_length = -1;
    TEST_ASSERT(check_eligibility(&r, &c) == ELIGIBLE, "Unknown content length should be eligible");
    TEST_PASS("Positive and boundary checks passed");
}

static void
test_ineligible_reasons(void)
{
    conf_t c = base_conf();
    request_t r = base_request();

    TEST_SUBSECTION("Ineligible reason checks");

    c.enabled = 0;
    TEST_ASSERT(check_eligibility(&r, &c) == INELIGIBLE_CONFIG, "Disabled config should be ineligible");
    c.enabled = 1;

    r.method = METHOD_POST;
    TEST_ASSERT(check_eligibility(&r, &c) == INELIGIBLE_METHOD, "POST should be ineligible");
    r.method = METHOD_GET;

    r.status = 206;
    TEST_ASSERT(check_eligibility(&r, &c) == INELIGIBLE_STATUS, "206 should be ineligible");
    r.status = 200;

    r.has_range_header = 1;
    TEST_ASSERT(check_eligibility(&r, &c) == INELIGIBLE_RANGE, "Range request should be ineligible");
    r.has_range_header = 0;

    r.content_type = "application/json";
    TEST_ASSERT(check_eligibility(&r, &c) == INELIGIBLE_CONTENT_TYPE, "Non-HTML content should be ineligible");
    r.content_type = "text/event-stream";
    TEST_ASSERT(check_eligibility(&r, &c) == INELIGIBLE_STREAMING, "SSE should be ineligible");

    r.content_type = "text/html";
    r.content_length = (long) c.max_size + 1;
    TEST_ASSERT(check_eligibility(&r, &c) == INELIGIBLE_SIZE, "Oversized response should be ineligible");
    TEST_PASS("All ineligible checks passed");
}

static void
test_custom_stream_type_exclusion(void)
{
    conf_t c = base_conf();
    request_t r = base_request();

    TEST_SUBSECTION("Configured stream_types exclusion");

    c.stream_types[0] = "application/x-ndjson";
    c.stream_type_count = 1;

    r.content_type = "application/x-ndjson; charset=utf-8";
    TEST_ASSERT(check_eligibility(&r, &c) == INELIGIBLE_STREAMING, "Configured stream type should be ineligible");
    TEST_PASS("Custom stream exclusion works");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("eligibility Tests\n");
    printf("========================================\n");

    test_positive_and_boundaries();
    test_ineligible_reasons();
    test_custom_stream_type_exclusion();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
