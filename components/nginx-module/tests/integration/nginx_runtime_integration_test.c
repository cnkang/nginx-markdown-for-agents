/*
 * Test: integration
 * Description: integration testing
 */

#include "test_common.h"
#include <ctype.h>
#include <strings.h>

typedef struct {
    const char *method;
    const char *accept;
    int status;
    const char *content_type;
    const char *body;
    int has_range;
} request_response_t;

typedef struct {
    int status;
    const char *content_type;
    char body[2048];
    int converted;
} output_t;

static int
contains_markdown_accept(const char *accept)
{
    return accept != NULL && strstr(accept, "text/markdown") != NULL;
}

static int
eligible_for_conversion(const request_response_t *rr)
{
    if (!(STR_EQ(rr->method, "GET") || STR_EQ(rr->method, "HEAD"))) return 0;
    if (rr->status != 200) return 0;
    if (rr->has_range) return 0;
    if (rr->content_type == NULL || strncasecmp(rr->content_type, "text/html", 9) != 0) return 0;
    return 1;
}

static void
convert_html_to_markdown(const char *html, char *out, size_t out_len)
{
    if (html == NULL) {
        out[0] = '\0';
        return;
    }
    snprintf(out, out_len, "# Converted\n\n%s", html);
}

static output_t
process_request(const request_response_t *rr)
{
    output_t out;
    memset(&out, 0, sizeof(out));
    out.status = rr->status;
    out.content_type = rr->content_type;
    snprintf(out.body, sizeof(out.body), "%s", rr->body == NULL ? "" : rr->body);

    if (!contains_markdown_accept(rr->accept)) {
        return out;
    }
    if (!eligible_for_conversion(rr)) {
        return out;
    }

    out.status = 200;
    out.content_type = "text/markdown; charset=utf-8";
    out.converted = 1;

    if (STR_EQ(rr->method, "HEAD")) {
        out.body[0] = '\0';
    } else {
        convert_html_to_markdown(rr->body, out.body, sizeof(out.body));
    }

    return out;
}

static request_response_t
base_case(void)
{
    request_response_t rr;
    rr.method = "GET";
    rr.accept = "text/markdown";
    rr.status = 200;
    rr.content_type = "text/html; charset=utf-8";
    rr.body = "<h1>Hello</h1>";
    rr.has_range = 0;
    return rr;
}

static void
test_basic_conversion_flow(void)
{
    request_response_t rr = base_case();
    output_t out;

    TEST_SUBSECTION("Basic conversion with Accept: text/markdown");
    out = process_request(&rr);
    TEST_ASSERT(out.converted == 1, "Should convert eligible markdown request");
    TEST_ASSERT(STR_EQ(out.content_type, "text/markdown; charset=utf-8"), "Content-Type should be markdown");
    TEST_ASSERT(strstr(out.body, "Converted") != NULL, "Body should be converted markdown");
    TEST_PASS("Basic conversion flow works");
}

static void
test_passthrough_when_not_markdown_accept(void)
{
    request_response_t rr = base_case();
    output_t out;

    TEST_SUBSECTION("Passthrough with non-markdown Accept");
    rr.accept = "text/html";
    out = process_request(&rr);
    TEST_ASSERT(out.converted == 0, "Should not convert without markdown accept");
    TEST_ASSERT(STR_EQ(out.content_type, rr.content_type), "Content-Type should remain original");
    TEST_ASSERT(STR_EQ(out.body, rr.body), "Body should remain original");
    TEST_PASS("Accept-based passthrough works");
}

static void
test_head_request_no_body(void)
{
    request_response_t rr = base_case();
    output_t out;

    TEST_SUBSECTION("HEAD request conversion without body");
    rr.method = "HEAD";
    out = process_request(&rr);
    TEST_ASSERT(out.converted == 1, "HEAD should still go through conversion pipeline");
    TEST_ASSERT(STR_EQ(out.content_type, "text/markdown; charset=utf-8"), "HEAD content-type should be markdown");
    TEST_ASSERT(strlen(out.body) == 0, "HEAD response body must be empty");
    TEST_PASS("HEAD semantics work");
}

static void
test_range_bypass(void)
{
    request_response_t rr = base_case();
    output_t out;

    TEST_SUBSECTION("Range request bypass");
    rr.has_range = 1;
    rr.status = 206;
    out = process_request(&rr);
    TEST_ASSERT(out.converted == 0, "Range requests should bypass conversion");
    TEST_ASSERT(out.status == 206, "Status should remain 206");
    TEST_PASS("Range bypass works");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("integration Tests\n");
    printf("========================================\n");

    test_basic_conversion_flow();
    test_passthrough_when_not_markdown_accept();
    test_head_request_no_body();
    test_range_bypass();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
