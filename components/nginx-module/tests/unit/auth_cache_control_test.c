/*
 * Test: auth_cache_control
 * Description: authentication and cache control
 */

#include "test_common.h"

#define TEST_COOKIE_NAME_MAX 256
#define TEST_COOKIE_PATTERN_MAX 256

typedef struct {
    const char *authorization;
    const char *cookie_header;
} request_t;

static int
cookie_matches_pattern(const char *cookie_name, const char *pattern)
{
    size_t name_len;
    size_t pat_len;

    if (cookie_name == NULL || pattern == NULL
        || *cookie_name == '\0' || *pattern == '\0')
    {
        return 0;
    }

    name_len = test_cstrnlen(cookie_name, TEST_COOKIE_NAME_MAX);
    pat_len = test_cstrnlen(pattern, TEST_COOKIE_PATTERN_MAX);
    if (name_len == TEST_COOKIE_NAME_MAX
        || pat_len == TEST_COOKIE_PATTERN_MAX)
    {
        return 0;
    }

    if (pattern[pat_len - 1] == '*') {
        size_t prefix_len = pat_len - 1;
        return name_len >= prefix_len
               && strncmp(cookie_name, pattern, prefix_len) == 0;
    }
    if (pattern[0] == '*') {
        size_t suffix_len = pat_len - 1;
        return name_len >= suffix_len &&
               strcmp(cookie_name + (name_len - suffix_len), pattern + 1) == 0;
    }
    return strcmp(cookie_name, pattern) == 0;
}

static int
append_with_bound(char *dst, size_t dst_size, const char *src)
{
    size_t dst_len;
    size_t src_len;

    if (dst == NULL || src == NULL || dst_size == 0) {
        return 0;
    }

    dst_len = test_cstrnlen(dst, dst_size);
    src_len = test_cstrnlen(src, dst_size);
    if (dst_len >= dst_size || src_len > dst_size - dst_len - 1) {
        return 0;
    }

    memcpy(dst + dst_len, src, src_len + 1);
    return 1;
}

static int
append_cache_control_directive(char *rewritten,
                               size_t rewritten_size,
                               const char *token,
                               int *wrote)
{
    if (STR_EQ(token, "public")) {
        return 1;
    }

    if (*wrote != 0 && !append_with_bound(rewritten, rewritten_size, ", ")) {
        return 0;
    }

    if (!append_with_bound(rewritten, rewritten_size, token)) {
        return 0;
    }

    *wrote = 1;
    return 1;
}

static const char *
finalize_private_cache_control(char *rewritten, size_t rewritten_size,
    int wrote)
{
    if (wrote != 0) {
        if (!append_with_bound(rewritten, rewritten_size, ", private")) {
            return "private";
        }
    } else {
        if (!append_with_bound(rewritten, rewritten_size, "private")) {
            return "private";
        }
    }

    return rewritten;
}

static char *
next_delimited_token(char **cursor, char delimiter)
{
    char *start;
    char *sep;
    char *end;

    if (cursor == NULL || *cursor == NULL) {
        return NULL;
    }

    start = *cursor;
    while (*start == delimiter || *start == ' ' || *start == '\t') {
        start++;
    }
    if (*start == '\0') {
        *cursor = NULL;
        return NULL;
    }

    sep = strchr(start, delimiter);
    if (sep != NULL) {
        *sep = '\0';
        *cursor = sep + 1;
    } else {
        *cursor = NULL;
    }

    end = start;
    while (*end != '\0') {
        end++;
    }
    while (end > start && (end[-1] == ' ' || end[-1] == '\t')) {
        end--;
        *end = '\0';
    }

    return start;
}

static int
has_auth_cookie(const char *cookie_header, const char **patterns,
    size_t pattern_count)
{
    char buf[512];
    const char *cursor;
    char *cookie_cursor;

    if (cookie_header == NULL || *cookie_header == '\0') {
        return 0;
    }

    snprintf(buf, sizeof(buf), "%s", cookie_header);
    cookie_cursor = buf;
    cursor = next_delimited_token(&cookie_cursor, ';');

    while (cursor != NULL) {
        const char *eq;
        const char *name;
        size_t name_len;
        char name_buf[128];

        size_t i;

        eq = strchr(cursor, '=');
        if (eq == NULL) {
            cursor = next_delimited_token(&cookie_cursor, ';');
            continue;
        }

        name = cursor;
        while (*name == ' ') {
            name++;
        }
        name_len = (size_t) (eq - name);
        while (name_len > 0
               && (name[name_len - 1] == ' ' || name[name_len - 1] == '\t'))
        {
            name_len--;
        }
        if (name_len == 0 || name_len >= sizeof(name_buf)) {
            cursor = next_delimited_token(&cookie_cursor, ';');
            continue;
        }

        memcpy(name_buf, name, name_len);
        name_buf[name_len] = '\0';

        for (i = 0; i < pattern_count; i++) {
            if (cookie_matches_pattern(name_buf, patterns[i])) {
                return 1;
            }
        }

        cursor = next_delimited_token(&cookie_cursor, ';');
    }
    return 0;
}

static int
is_authenticated(const request_t *r, const char **patterns,
    size_t pattern_count)
{
    if (r->authorization != NULL && *r->authorization != '\0') {
        return 1;
    }
    return has_auth_cookie(r->cookie_header, patterns, pattern_count);
}

static const char *
adjust_cache_control_for_auth(const char *cache_control, int authenticated)
{
    static char rewritten[512];
    char scratch[512];
    const char *cursor;
    char *directive_cursor;
    int wrote;

    if (!authenticated) {
        return cache_control;
    }
    if (cache_control == NULL || *cache_control == '\0') {
        return "private";
    }
    if (strstr(cache_control, "no-store") != NULL) {
        return cache_control;
    }
    if (strstr(cache_control, "private") != NULL) {
        return cache_control;
    }

    snprintf(scratch, sizeof(scratch), "%s", cache_control);

    rewritten[0] = '\0';
    wrote = 0;
    directive_cursor = scratch;
    cursor = next_delimited_token(&directive_cursor, ',');
    while (cursor != NULL) {
        const char *token = cursor;
        while (*token == ' ' || *token == '\t') {
            token++;
        }

        if (!append_cache_control_directive(rewritten,
                                            sizeof(rewritten),
                                            token,
                                            &wrote))
        {
            return "private";
        }

        cursor = next_delimited_token(&directive_cursor, ',');
    }

    return finalize_private_cache_control(rewritten, sizeof(rewritten), wrote);
}

static void
test_auth_detection(void)
{
    const char *patterns[] = {"session*", "auth*", "PHPSESSID"};
    request_t r;

    TEST_SUBSECTION("Authentication detection");

    r.authorization = "Bearer token";
    r.cookie_header = NULL;
    TEST_ASSERT(is_authenticated(&r, patterns, ARRAY_SIZE(patterns)) == 1, "Authorization header should authenticate");

    r.authorization = NULL;
    r.cookie_header = "foo=1; session_id=abc";
    TEST_ASSERT(is_authenticated(&r, patterns, ARRAY_SIZE(patterns)) == 1, "session* cookie should authenticate");

    r.cookie_header = "foo=1; bar=2";
    TEST_ASSERT(is_authenticated(&r, patterns, ARRAY_SIZE(patterns)) == 0, "Non-auth cookies should not authenticate");
    TEST_PASS("Authentication detection passed");
}

static void
test_cache_control_adjustment(void)
{
    TEST_SUBSECTION("Cache-Control adjustment for authenticated content");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth(NULL, 1), "private"), "Missing Cache-Control should become private");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("public, max-age=600", 1), "max-age=600, private"),
                "Public cache should be upgraded to private while preserving directives");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("public, s-maxage=60, public", 1), "s-maxage=60, private"),
                "Multiple public directives should be removed safely");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("private, max-age=60", 1), "private, max-age=60"),
                "Existing private cache should be preserved");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("no-store", 1), "no-store"), "no-store should be preserved");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("public", 0), "public"), "Unauthenticated request should not change Cache-Control");
    TEST_PASS("Cache-Control adjustment passed");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("auth_cache_control Tests\n");
    printf("========================================\n");

    test_auth_detection();
    test_cache_control_adjustment();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
