/*
 * Test: conditional_requests
 * Description: conditional request handling
 */

#include "test_common.h"

#define MODE_FULL_SUPPORT 0
#define MODE_IF_MODIFIED_SINCE_ONLY 1
#define MODE_DISABLED 2

#define RC_MATCH 304
#define RC_DECLINED -5
#define RC_ERROR -1

static size_t
parse_if_none_match(const char *header, char tokens[][128], size_t max_tokens)
{
    const char *p = header;
    size_t n = 0;

    if (header == NULL || *header == '\0') {
        return 0;
    }

    while (*p && n < max_tokens) {
        char *out = tokens[n];
        size_t len = 0;

        while (*p == ' ' || *p == '\t' || *p == ',') p++;
        if (*p == '\0') break;

        if (*p == '"') {
            p++;
            while (*p && *p != '"' && len + 1 < 128) out[len++] = *p++;
            if (*p != '"') {
                return 0; /* malformed header: ignore conditional */
            }
            p++;
        } else {
            while (*p && *p != ',' && *p != ' ' && *p != '\t' && len + 1 < 128) out[len++] = *p++;
        }
        out[len] = '\0';
        n++;
        while (*p && *p != ',') p++;
        if (*p == ',') p++;
    }

    return n;
}

static void
normalize_etag_token(const char *input, char *out, size_t out_len)
{
    const char *start = input;
    size_t len;

    if (out_len == 0) {
        return;
    }

    if (input == NULL) {
        out[0] = '\0';
        return;
    }

    len = strlen(input);
    if (len >= 2 && (start[0] == 'W' || start[0] == 'w') && start[1] == '/') {
        start += 2;
        len -= 2;
    }

    if (len >= 2 && start[0] == '"' && start[len - 1] == '"') {
        size_t copy_len = len - 2;
        if (copy_len >= out_len) copy_len = out_len - 1;
        memcpy(out, start + 1, copy_len);
        out[copy_len] = '\0';
        return;
    }

    if (len >= out_len) {
        len = out_len - 1;
    }
    memcpy(out, start, len);
    out[len] = '\0';
}

static void
normalize_generated_etag(const char *generated, char *out, size_t out_len)
{
    normalize_etag_token(generated, out, out_len);
}

static void
normalize_client_token(const char *token, char *out, size_t out_len)
{
    normalize_etag_token(token, out, out_len);
}

static int
is_wildcard_token(const char *token)
{
    return token != NULL && STR_EQ(token, "*");
}

static int
token_matches_generated(const char *token, const char *generated_raw, const char *generated_norm)
{
    char token_norm[128];

    if (token == NULL || generated_raw == NULL || generated_norm == NULL) {
        return 0;
    }

    if (STR_EQ(token, generated_raw)) {
        return 1;
    }

    normalize_client_token(token, token_norm, sizeof(token_norm));
    return STR_EQ(token_norm, generated_norm);
}

static int
etag_matches(const char *if_none_match, const char *generated_etag)
{
    char tokens[16][128];
    size_t count;
    size_t i;
    char normalized[128];

    normalize_generated_etag(generated_etag, normalized, sizeof(normalized));
    count = parse_if_none_match(if_none_match, tokens, ARRAY_SIZE(tokens));
    if (count == 0) {
        return RC_DECLINED;
    }

    for (i = 0; i < count; i++) {
        if (is_wildcard_token(tokens[i])) {
            return RC_MATCH;
        }
        if (token_matches_generated(tokens[i], generated_etag, normalized)) {
            return RC_MATCH;
        }
    }
    return RC_DECLINED;
}

static int
handle_if_none_match(int mode, const char *if_none_match, const char *generated_etag)
{
    if (mode == MODE_DISABLED || mode == MODE_IF_MODIFIED_SINCE_ONLY) {
        return RC_DECLINED;
    }
    return etag_matches(if_none_match, generated_etag);
}

static void
test_mode_handling(void)
{
    TEST_SUBSECTION("Conditional mode handling");
    TEST_ASSERT(handle_if_none_match(MODE_DISABLED, "\"abc\"", "\"abc\"") == RC_DECLINED, "disabled mode should skip");
    TEST_ASSERT(handle_if_none_match(MODE_IF_MODIFIED_SINCE_ONLY, "\"abc\"", "\"abc\"") == RC_DECLINED,
                "if_modified_since_only mode should skip");
    TEST_PASS("Mode handling passed");
}

static void
test_etag_matching(void)
{
    TEST_SUBSECTION("ETag matching behavior");
    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "\"abc\"", "\"abc\"") == RC_MATCH, "Exact quoted match should return 304");
    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "\"abc\"", "abc") == RC_MATCH, "Quoted client vs unquoted generated should match");
    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "W/\"abc\"", "\"abc\"") == RC_MATCH,
                "Weak client ETag should match strong generated ETag");
    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "*", "\"anything\"") == RC_MATCH, "Wildcard should match");
    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "\"abc\", \"def\"", "\"def\"") == RC_MATCH, "Any token match should return 304");
    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "\"abc\"", "\"xyz\"") == RC_DECLINED, "Non-match should decline");
    TEST_PASS("ETag matching passed");
}

static void
test_malformed_header(void)
{
    TEST_SUBSECTION("Malformed If-None-Match header");
    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "\"abc", "\"abc\"") == RC_DECLINED,
                "Malformed header should be ignored");
    TEST_PASS("Malformed header handling passed");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("conditional_requests Tests\n");
    printf("========================================\n");

    test_mode_handling();
    test_etag_matching();
    test_malformed_header();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
