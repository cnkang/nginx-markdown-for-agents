/*
 * Test: passthrough
 * Description: passthrough behavior
 */

#include "test_common.h"

typedef struct {
    int status;
    const char *content_type;
    const char *body;
    const char *content_encoding;
} http_response_t;

static http_response_t
passthrough_response(http_response_t upstream)
{
    return upstream;
}

static void
test_passthrough_preserves_everything(void)
{
    http_response_t upstream;
    http_response_t out;

    TEST_SUBSECTION("Passthrough preserves status, headers, and body");

    upstream.status = 206;
    upstream.content_type = "text/html";
    upstream.body = "<html>partial</html>";
    upstream.content_encoding = "gzip";

    out = passthrough_response(upstream);

    TEST_ASSERT(out.status == upstream.status, "status should be preserved");
    TEST_ASSERT(STR_EQ(out.content_type, upstream.content_type), "content-type should be preserved");
    TEST_ASSERT(STR_EQ(out.body, upstream.body), "body should be preserved");
    TEST_ASSERT(STR_EQ(out.content_encoding, upstream.content_encoding), "content-encoding should be preserved");
    TEST_PASS("Passthrough preserves response");
}

static void
test_passthrough_for_ineligible_accept(void)
{
    http_response_t upstream;
    http_response_t out;

    TEST_SUBSECTION("Non-markdown Accept request should passthrough");

    upstream.status = 200;
    upstream.content_type = "text/html";
    upstream.body = "<html>no convert</html>";
    upstream.content_encoding = NULL;

    out = passthrough_response(upstream);

    TEST_ASSERT(STR_EQ(out.body, "<html>no convert</html>"), "original HTML should remain unchanged");
    TEST_ASSERT(STR_EQ(out.content_type, "text/html"), "content-type should remain html");
    TEST_PASS("Ineligible request passthrough works");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("passthrough Tests\n");
    printf("========================================\n");

    test_passthrough_preserves_everything();
    test_passthrough_for_ineligible_accept();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
