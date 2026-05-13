/*
 * Test: eligibility
 * Description: request eligibility checking
 *
 * Validates the request eligibility decision logic: method (GET/HEAD only),
 * status (200 only, 206 treated as range), content-type (text/html only,
 * excludes streaming types), size limits, and configuration enable/disable.
 */

#include "test_common.h"
#include <ctype.h>

/*
 * HTTP method constants for the test harness.
 */
#define METHOD_GET 1
#define METHOD_HEAD 2
#define METHOD_POST 3

/*
 * Eligibility result enumeration.
 */
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

/*
 * Test configuration struct.
 */
typedef struct {
    int enabled;
    size_t max_size;
    const char *stream_types[8];
    size_t stream_type_count;
} conf_t;

/*
 * Test request struct.
 */
typedef struct {
    int method;
    int status;
    const char *content_type;
    long content_length;
    int has_range_header;
} request_t;

/*
 * Case-insensitive string comparison (ASCII only).
 *
 * Parameters:
 *   a - first string
 *   b - second string
 *   n - maximum bytes to compare
 *
 * Returns:
 *   0 if equal (case-insensitive), difference of first mismatch otherwise.
 */
static int
strncasecmp_ascii(const char *a, const char *b, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        unsigned char ca = (unsigned char) a[i];
        unsigned char cb = (unsigned char) b[i];
        if (tolower(ca) != tolower(cb)) {
            return tolower(ca) - tolower(cb);
        }
        if (ca == '\0' || cb == '\0') {
            break;
        }
    }
    return 0;
}

/*
 * Check if string starts with a prefix (case-insensitive).
 *
 * Parameters:
 *   s      - string to check
 *   prefix - prefix to match
 *
 * Returns:
 *   1 if s starts with prefix (case-insensitive), 0 otherwise.
 */
static int
starts_with_ci(const char *s, const char *prefix)
{
    size_t plen;

    if (s == NULL || prefix == NULL) {
        return 0;
    }

    plen = 0;
    while (prefix[plen] != '\0') {
        if (s[plen] == '\0') {
            return 0;
        }
        plen++;
    }

    return strncasecmp_ascii(s, prefix, plen) == 0;
}

/*
 * Check if content type is text/html (exact or with parameters).
 *
 * Parameters:
 *   content_type - Content-Type header value
 *
 * Returns:
 *   1 if content type starts with "text/html" followed by NUL, ';', or ' '.
 */
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

/*
 * Check if content type is a streaming type (SSE or configured stream types).
 *
 * Parameters:
 *   content_type - Content-Type header value
 *   conf         - configuration with stream_types list
 *
 * Returns:
 *   1 if content type matches text/event-stream or any configured stream type.
 */
static int
is_streaming_type(const char *content_type, const conf_t *conf)
{
    if (content_type == NULL) {
        return 0;
    }

    if (starts_with_ci(content_type, "text/event-stream")) {
        return 1;
    }

    for (size_t i = 0; i < conf->stream_type_count; i++) {
        if (starts_with_ci(content_type, conf->stream_types[i])) {
            return 1;
        }
    }
    return 0;
}

/*
 * Check request eligibility for Markdown conversion.
 *
 * Evaluates: config enabled, HTTP method, status code, range headers,
 * streaming content type, HTML content type, and content size.
 *
 * Parameters:
 *   r    - request to check
 *   conf - configuration with eligibility parameters
 *
 * Returns:
 *   ELIGIBLE if all checks pass, or the first INELIGIBLE_* reason code.
 */
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
        if (r->status == 206) {
            return INELIGIBLE_RANGE;
        }
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

/*
 * Create a baseline eligible request.
 *
 * Returns:
 *   A request_t that passes all eligibility checks.
 */
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

/*
 * Create a baseline configuration with defaults.
 *
 * Returns:
 *   A conf_t with enabled=1 and max_size=10MB.
 */
static conf_t
base_conf(void)
{
    conf_t c;
    memset(&c, 0, sizeof(c));
    c.enabled = 1;
    c.max_size = 10 * 1024 * 1024;
    return c;
}

/*
 * Verify positive eligibility path and boundary size checks.
 * Tests baseline eligible request, content_length == max_size (eligible),
 * and unknown content length (-1, eligible).
 *
 * Expected: all three cases return ELIGIBLE.
 */
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

/*
 * Verify all ineligibility reasons: disabled config, POST method,
 * 206 status, range header, non-HTML content, streaming content,
 * and oversized response.
 *
 * Expected: each condition returns the correct INELIGIBLE_* reason.
 */
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
    TEST_ASSERT(check_eligibility(&r, &c) == INELIGIBLE_RANGE, "206 should be ineligible (range)");
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

/*
 * Verify configured stream_types exclusion: a custom stream type
 * (application/x-ndjson) in the config causes matching content types
 * to be ineligible.
 *
 * Expected: configured stream type returns INELIGIBLE_STREAMING.
 */
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
