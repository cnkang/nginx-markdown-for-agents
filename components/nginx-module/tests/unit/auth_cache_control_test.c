/*
 * Test: auth_cache_control
 * Description: authentication and cache control
 */

#include "test_common.h"

typedef struct {
    const char *authorization;
    const char *cookie_header;
} request_t;

static int
cookie_matches_pattern(const char *cookie_name, const char *pattern)
{
    size_t name_len;
    size_t pat_len;

    if (cookie_name == NULL || pattern == NULL || *cookie_name == '\0' || *pattern == '\0') {
        return 0;
    }

    name_len = strlen(cookie_name);
    pat_len = strlen(pattern);

    if (pattern[pat_len - 1] == '*') {
        size_t prefix_len = pat_len - 1;
        return name_len >= prefix_len && strncmp(cookie_name, pattern, prefix_len) == 0;
    }
    if (pattern[0] == '*') {
        size_t suffix_len = pat_len - 1;
        return name_len >= suffix_len &&
               strcmp(cookie_name + (name_len - suffix_len), pattern + 1) == 0;
    }
    return strcmp(cookie_name, pattern) == 0;
}

static int
has_auth_cookie(const char *cookie_header, const char **patterns, size_t pattern_count)
{
    char buf[512];
    char *cursor;

    if (cookie_header == NULL || *cookie_header == '\0') {
        return 0;
    }

    strncpy(buf, cookie_header, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';
    cursor = strtok(buf, ";");

    while (cursor != NULL) {
        char *eq = strchr(cursor, '=');
        size_t i;
        if (eq != NULL) {
            char *name = cursor;
            while (*name == ' ') name++;
            *eq = '\0';
            for (i = 0; i < pattern_count; i++) {
                if (cookie_matches_pattern(name, patterns[i])) {
                    return 1;
                }
            }
        }
        cursor = strtok(NULL, ";");
    }
    return 0;
}

static int
is_authenticated(const request_t *r, const char **patterns, size_t pattern_count)
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
    char *cursor;
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

    strncpy(scratch, cache_control, sizeof(scratch) - 1);
    scratch[sizeof(scratch) - 1] = '\0';

    rewritten[0] = '\0';
    wrote = 0;
    cursor = strtok(scratch, ",");
    while (cursor != NULL) {
        char *token = cursor;
        while (*token == ' ' || *token == '\t') token++;
        if (!STR_EQ(token, "public")) {
            if (wrote) {
                strncat(rewritten, ", ", sizeof(rewritten) - strlen(rewritten) - 1);
            }
            strncat(rewritten, token, sizeof(rewritten) - strlen(rewritten) - 1);
            wrote = 1;
        }
        cursor = strtok(NULL, ",");
    }

    if (wrote) {
        strncat(rewritten, ", private", sizeof(rewritten) - strlen(rewritten) - 1);
    } else {
        strncat(rewritten, "private", sizeof(rewritten) - strlen(rewritten) - 1);
    }

    return rewritten;
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
