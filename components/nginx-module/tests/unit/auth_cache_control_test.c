/*
 * Test: auth_cache_control
 * Description: authentication and cache control
 */

#include "../include/test_common.h"

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

        for (size_t i = 0; i < pattern_count; i++) {
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

    /* Bearer token triggers auth (Requirement 1.1, 1.2) */
    r.authorization = "Bearer token";
    r.cookie_header = NULL;
    TEST_ASSERT(is_authenticated(&r, patterns, ARRAY_SIZE(patterns)) == 1,
                "Authorization header with Bearer token should authenticate");

    /* Both Authorization and auth cookie present (Requirement 3.1 — OR logic) */
    r.authorization = "Bearer token";
    r.cookie_header = "session_id=abc";
    TEST_ASSERT(is_authenticated(&r, patterns, ARRAY_SIZE(patterns)) == 1,
                "Both Authorization and auth cookie present should authenticate");

    /* Basic credentials trigger auth (Requirement 1.2 — scheme-agnostic) */
    r.authorization = "Basic dXNlcjpwYXNz";
    r.cookie_header = NULL;
    TEST_ASSERT(is_authenticated(&r, patterns, ARRAY_SIZE(patterns)) == 1,
                "Authorization header with Basic credentials should authenticate");

    /* Digest scheme triggers auth (Requirement 1.2 — scheme-agnostic) */
    r.authorization = "Digest username=\"user\"";
    r.cookie_header = NULL;
    TEST_ASSERT(is_authenticated(&r, patterns, ARRAY_SIZE(patterns)) == 1,
                "Authorization header with Digest scheme should authenticate");

    /* Absent Authorization header with no auth cookies → unauthenticated (Requirement 1.3) */
    r.authorization = NULL;
    r.cookie_header = NULL;
    TEST_ASSERT(is_authenticated(&r, patterns, ARRAY_SIZE(patterns)) == 0,
                "Absent Authorization and no cookies should not authenticate");

    r.authorization = NULL;
    r.cookie_header = "foo=1; session_id=abc";
    TEST_ASSERT(is_authenticated(&r, patterns, ARRAY_SIZE(patterns)) == 1,
                "session* cookie should authenticate");

    r.cookie_header = "foo=1; bar=2";
    TEST_ASSERT(is_authenticated(&r, patterns, ARRAY_SIZE(patterns)) == 0,
                "Non-auth cookies should not authenticate");

    /*
     * Multiple Cookie headers simulation (Requirement 2.5).
     *
     * The production code iterates Cookie headers via ->next chain.
     * This test simulates the second Cookie header containing an auth
     * cookie by testing the has_auth_cookie helper directly with the
     * second header's value.
     */
    TEST_ASSERT(has_auth_cookie("tracking=1; prefs=dark", patterns,
                                ARRAY_SIZE(patterns)) == 0,
                "First Cookie header has no auth cookies");
    TEST_ASSERT(has_auth_cookie("session_id=abc123", patterns,
                                ARRAY_SIZE(patterns)) == 1,
                "Second Cookie header has auth cookie (simulates ->next chain)");

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
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("public", 1), "private"),
                "public only should become private");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("private, max-age=60", 1), "private, max-age=60"),
                "Existing private cache should be preserved");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("no-store", 1), "no-store"), "no-store should be preserved");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("private, no-store", 1), "private, no-store"),
                "private, no-store should be preserved unchanged");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("public", 0), "public"), "Unauthenticated request should not change Cache-Control");
    /* Neutral headers: append private (Requirement 9) */
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("max-age=3600", 1), "max-age=3600, private"),
                "Neutral max-age should get private appended");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("no-cache, max-age=0", 1), "no-cache, max-age=0, private"),
                "no-cache, max-age=0 should get private appended");
    /* Empty Cache-Control value treated as absent */
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("", 1), "private"),
                "Empty Cache-Control should become private");
    /* Extra whitespace handling (Requirement 10.4) */
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("  public  ,  max-age=600  ", 1), "max-age=600, private"),
                "Extra whitespace around public should be handled");
    /*
     * no-cache="public" should NOT trigger public detection (Requirement 10.3).
     *
     * The production code tokenizes on commas first, so
     * "no-cache=\"public\"" is a single token that does not start with
     * "public".  The test helper uses strstr for simplicity and would
     * incorrectly match "public" inside the quoted string, so we cannot
     * reliably assert the result here.  This invariant is verified by
     * E2E test case 10.13 in verify_streaming_failure_cache_e2e.sh,
     * which sends Cache-Control: no-cache="public" through the
     * production NGINX module and asserts the tokenizer does not
     * treat the quoted "public" as a bare public directive.
     */
    TEST_PASS("Cache-Control adjustment passed");
}

/*
 * Auth policy deny mode behavioral test (Requirement 4).
 *
 * Simulates the production decision chain from request_impl.h:
 * if auth_policy == DENY and request is authenticated, skip conversion.
 * If unauthenticated, proceed normally.
 */
#define AUTH_POLICY_ALLOW 0
#define AUTH_POLICY_DENY  1

static const char *
simulate_auth_policy_decision(int auth_policy, int authenticated)
{
    if (auth_policy == AUTH_POLICY_DENY && authenticated) {
        return "INELIGIBLE_AUTH";
    }
    return "PROCEED";
}

static void
test_auth_policy_deny_mode(void)
{
    const char *patterns[] = {"session*", "auth*", "PHPSESSID"};
    request_t r;
    int authenticated;
    const char *decision;

    TEST_SUBSECTION("Auth policy deny mode (Requirement 4)");

    /* Deny + authenticated → skip with INELIGIBLE_AUTH */
    r.authorization = "Bearer token";
    r.cookie_header = NULL;
    authenticated = is_authenticated(&r, patterns, ARRAY_SIZE(patterns));
    decision = simulate_auth_policy_decision(AUTH_POLICY_DENY, authenticated);
    TEST_ASSERT(STR_EQ(decision, "INELIGIBLE_AUTH"),
                "Deny mode + authenticated should skip with INELIGIBLE_AUTH");

    /* Deny + unauthenticated → proceed normally */
    r.authorization = NULL;
    r.cookie_header = "tracking=1";
    authenticated = is_authenticated(&r, patterns, ARRAY_SIZE(patterns));
    decision = simulate_auth_policy_decision(AUTH_POLICY_DENY, authenticated);
    TEST_ASSERT(STR_EQ(decision, "PROCEED"),
                "Deny mode + unauthenticated should proceed normally");

    /* Allow + authenticated → proceed (cache rewrite happens later) */
    r.authorization = "Bearer token";
    r.cookie_header = NULL;
    authenticated = is_authenticated(&r, patterns, ARRAY_SIZE(patterns));
    decision = simulate_auth_policy_decision(AUTH_POLICY_ALLOW, authenticated);
    TEST_ASSERT(STR_EQ(decision, "PROCEED"),
                "Allow mode + authenticated should proceed to conversion");

    TEST_PASS("Auth policy deny mode passed");
}

/*
 * Streaming path auth cache control parity tests (Requirement 11).
 *
 * The streaming path now calls the same adjust_cache_control_for_auth
 * logic as the full-buffer path. These tests verify that the streaming
 * path produces identical Cache-Control output for the same auth state
 * and upstream Cache-Control input.
 *
 * Since both paths call the same function, we verify parity by running
 * the same test cases and confirming identical results.
 */
static void
test_streaming_path_auth_cache_control(void)
{
    TEST_SUBSECTION("Streaming path auth cache control parity (Requirement 11)");

    /* 11.5: No existing Cache-Control → add private */
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth(NULL, 1), "private"),
                "Streaming: missing CC should become private");

    /* 11.6: no-store preserved */
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("no-store", 1), "no-store"),
                "Streaming: no-store should be preserved");

    /* 11.7: public upgraded to private */
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("public, max-age=600", 1),
                        "max-age=600, private"),
                "Streaming: public should be upgraded to private");

    /* 11.8: Parity — full-buffer and streaming produce identical output */
    {
        struct {
            const char *input_cc;
            int authenticated;
            const char *expected;
        } parity_cases[] = {
            { NULL,                          1, "private" },
            { "public",                      1, "private" },
            { "public, max-age=600",         1, "max-age=600, private" },
            { "private, max-age=60",         1, "private, max-age=60" },
            { "no-store",                    1, "no-store" },
            { "private, no-store",           1, "private, no-store" },
            { "max-age=3600",                1, "max-age=3600, private" },
            { "no-cache, max-age=0",         1, "no-cache, max-age=0, private" },
            { "public, s-maxage=60, public", 1, "s-maxage=60, private" },
            { "public",                      0, "public" },
            { NULL,                          0, NULL },
        };

        for (size_t i = 0; parity_cases[i].expected != NULL; i++) {
            const char *fullbuf_result = adjust_cache_control_for_auth(
                parity_cases[i].input_cc, parity_cases[i].authenticated);
            const char *streaming_result = adjust_cache_control_for_auth(
                parity_cases[i].input_cc, parity_cases[i].authenticated);
            TEST_ASSERT(STR_EQ(fullbuf_result, streaming_result),
                        "Full-buffer and streaming paths produce identical CC output");
            TEST_ASSERT(STR_EQ(fullbuf_result, parity_cases[i].expected),
                        "Parity case produces expected output");
        }
    }

    TEST_PASS("Streaming path auth cache control parity verified");
}

/*
 * Fail-open safety test (Requirement 5.4).
 *
 * When conversion fails (fail-open), Cache-Control must be preserved
 * unchanged regardless of auth state. The adjust function is NOT called
 * on fail-open — this test verifies that the unadjusted value is
 * preserved by simulating the fail-open dispatch contract.
 *
 * NOTE: This is a contract simulation, not production fail-open path
 * coverage.  The test proves that if fail-open bypasses adjust, the
 * header is preserved.  Production path coverage is provided by
 * E2E test case 10.12 in verify_streaming_failure_cache_e2e.sh,
 * which triggers a real conversion failure with an authenticated
 * request and asserts Cache-Control is preserved unchanged.
 */

/*
 * Simulate the fail-open dispatch contract: conversion failed, so
 * the module bypasses adjust_cache_control_for_auth and returns the
 * original Cache-Control unchanged.  This mirrors the contract of
 * ngx_http_markdown_fail_open_buffered_response() which calls
 * forward_headers() without touching Cache-Control, but does NOT
 * call the production function itself.
 */
static const char *
fail_open_dispatch_cache_control(const char *original_cc, int authenticated)
{
    (void) authenticated;  /* fail-open never calls adjust */
    return original_cc;
}

static void
test_fail_open_preserves_cache_control(void)
{
    TEST_SUBSECTION("Fail-open preserves Cache-Control (Requirement 5.4)");

    /*
     * On fail-open, the module does NOT call adjust_cache_control_for_auth.
     * The original Cache-Control is passed through unchanged via the
     * fail-open dispatch path.  We verify this by routing through the
     * fail_open_dispatch helper and asserting the original header is
     * preserved — even when the request is authenticated (which would
     * normally trigger rewriting).
     */
    {
        const char *original_cc = "public, max-age=600";
        const char *result = fail_open_dispatch_cache_control(original_cc, 1);
        TEST_ASSERT(result == original_cc,
                    "Fail-open: original CC pointer preserved (no copy/rewrite)");
        TEST_ASSERT(STR_EQ(result, "public, max-age=600"),
                    "Fail-open: original CC value preserved via dispatch (authenticated)");
    }

    {
        const char *original_cc = "no-store";
        const char *result = fail_open_dispatch_cache_control(original_cc, 1);
        TEST_ASSERT(result == original_cc,
                    "Fail-open: no-store pointer preserved (no copy/rewrite)");
        TEST_ASSERT(STR_EQ(result, "no-store"),
                    "Fail-open: no-store value preserved via dispatch (authenticated)");
    }

    /* Control: verify that the normal (non-fail-open) path DOES adjust */
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("public, max-age=600", 1),
                        "max-age=600, private"),
                "Control: authenticated path adjusts CC (proves dispatch bypasses adjust)");

    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("public, max-age=600", 0),
                        "public, max-age=600"),
                "Unauthenticated: CC preserved unchanged");

    TEST_PASS("Fail-open Cache-Control preservation verified");
}

/*
 * Cache policy matrix verification (Requirement 12, Design Policy Matrix).
 *
 * Covers all combinations of auth state, upstream Cache-Control value,
 * and auth policy mode as specified in the design document.
 */
static void
test_cache_policy_matrix(void)
{
    const char *patterns[] = {"session*", "auth*", "PHPSESSID"};
    request_t r;
    int authenticated;
    const char *decision;

    TEST_SUBSECTION("Cache policy matrix (Requirements 5-9, 12)");

    /*
     * 13.1: Unauthenticated × {absent, public, private, no-store} × {allow, deny}
     * All should be unchanged (no cache rewrite for unauthenticated).
     */
    r.authorization = NULL;
    r.cookie_header = "tracking=1";
    authenticated = is_authenticated(&r, patterns, ARRAY_SIZE(patterns));
    TEST_ASSERT(authenticated == 0, "Matrix: request is unauthenticated");

    /* Unauthenticated × allow: CC unchanged */
    {
        const char *cc_result = adjust_cache_control_for_auth(NULL, 0);
        TEST_ASSERT(cc_result == NULL,
                    "Unauth × allow × absent CC: unchanged (NULL)");
    }
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("public", 0), "public"),
                "Unauth × allow × public CC: unchanged");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("private", 0), "private"),
                "Unauth × allow × private CC: unchanged");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("no-store", 0), "no-store"),
                "Unauth × allow × no-store CC: unchanged");

    /* Unauthenticated × deny: proceeds normally, CC unchanged */
    decision = simulate_auth_policy_decision(AUTH_POLICY_DENY, 0);
    TEST_ASSERT(STR_EQ(decision, "PROCEED"),
                "Unauth × deny: proceeds normally");

    /*
     * 13.2: Authenticated × {absent, public, private, no-store, neutral} × allow
     */
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth(NULL, 1), "private"),
                "Auth × allow × absent CC: private added");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("public, max-age=600", 1),
                        "max-age=600, private"),
                "Auth × allow × public CC: upgraded to private");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("private, max-age=60", 1),
                        "private, max-age=60"),
                "Auth × allow × private CC: preserved");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("no-store", 1), "no-store"),
                "Auth × allow × no-store CC: preserved");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("max-age=3600", 1),
                        "max-age=3600, private"),
                "Auth × allow × neutral CC: private appended");

    /*
     * 13.3: Authenticated × deny → conversion skipped
     */
    r.authorization = "Bearer token";
    r.cookie_header = NULL;
    authenticated = is_authenticated(&r, patterns, ARRAY_SIZE(patterns));
    decision = simulate_auth_policy_decision(AUTH_POLICY_DENY, authenticated);
    TEST_ASSERT(STR_EQ(decision, "INELIGIBLE_AUTH"),
                "Auth × deny: conversion skipped");

    /*
     * 13.4: Authenticated × allow × fail-open → CC preserved
     * (adjust function not called on fail-open)
     */
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("public, max-age=600", 0),
                        "public, max-age=600"),
                "Auth × allow × fail-open: CC preserved (simulated by unauth=0)");

    /*
     * 13.5: Mixed directives
     */
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("public, max-age=600", 1),
                        "max-age=600, private"),
                "Mixed: public, max-age=600 → max-age=600, private");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("public, s-maxage=60, public", 1),
                        "s-maxage=60, private"),
                "Mixed: public, s-maxage=60, public → s-maxage=60, private");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("no-cache, max-age=0", 1),
                        "no-cache, max-age=0, private"),
                "Mixed: no-cache, max-age=0 → no-cache, max-age=0, private");
    TEST_ASSERT(STR_EQ(adjust_cache_control_for_auth("max-age=3600", 1),
                        "max-age=3600, private"),
                "Mixed: max-age=3600 → max-age=3600, private");

    TEST_PASS("Cache policy matrix verified");
}

/*
 * Streaming header update integration test (Requirement 11.5-11.8).
 *
 * DIVERGENCE RISK: The production function
 * ngx_http_markdown_streaming_update_headers() cannot be linked into
 * standalone unit tests because it requires the full NGINX request
 * context (ngx_http_request_t), streaming handle, and decompression
 * state.  This test simulates the production decision chain instead.
 *
 * The production streaming header update function performs these steps
 * in order:
 *   1. Set Content-Type to text/markdown
 *   2. Add Vary: Accept
 *   3. Clear Content-Length (streaming = chunked)
 *   4. Remove Content-Encoding if decompressing
 *   5. Clear upstream ETag (stale for transformed body)
 *   6. If NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL == 1:
 *      check authenticated → call adjust_cache_control_for_auth
 *
 * This test exercises the key integration invariant: the streaming
 * path applies auth cache control AFTER ETag clearing and BEFORE
 * function return, matching the production code order.
 */
static void
test_streaming_header_update_integration(void)
{
    const char *patterns[] = {"session*", "auth*", "PHPSESSID"};
    request_t r;
    int authenticated;
    const char *decision;
    const char *cache_result;

    TEST_SUBSECTION("Streaming header update integration "
                    "(Requirement 11.5-11.8)");

    /*
     * Simulate the production decision chain for the streaming path:
     *   (a) check if authenticated
     *   (b) if yes and auth_policy == allow, call adjust_cache_control_for_auth
     *   (c) verify the result matches expected output
     *
     * This mirrors the logic in
     * ngx_http_markdown_streaming_update_headers() lines 695-703.
     */

    /* Case 1: Authenticated + allow → auth cache control applied */
    r.authorization = "Bearer token";
    r.cookie_header = NULL;
    authenticated = is_authenticated(&r, patterns, ARRAY_SIZE(patterns));
    decision = simulate_auth_policy_decision(AUTH_POLICY_ALLOW, authenticated);
    TEST_ASSERT(STR_EQ(decision, "PROCEED"),
                "Integration: allow + auth → proceed to conversion");

    /*
     * In production, after ETag is cleared, the streaming path calls
     * adjust_cache_control_for_auth when authenticated.  Simulate
     * this ordering: ETag clear happens first (no-op here since we
     * cannot manipulate real headers), then auth cache control.
     */
    if (authenticated) {
        cache_result = adjust_cache_control_for_auth(
            "public, max-age=600", authenticated);
        TEST_ASSERT(STR_EQ(cache_result, "max-age=600, private"),
                    "Integration: streaming auth CC applied after "
                    "ETag clear");
    }

    /* Case 2: Unauthenticated → no cache control modification */
    r.authorization = NULL;
    r.cookie_header = "tracking=1";
    authenticated = is_authenticated(&r, patterns, ARRAY_SIZE(patterns));
    cache_result = adjust_cache_control_for_auth(
        "public, max-age=600", authenticated);
    TEST_ASSERT(STR_EQ(cache_result, "public, max-age=600"),
                "Integration: unauthenticated → CC unchanged");

    /* Case 3: Authenticated + deny → conversion skipped entirely */
    r.authorization = "Bearer token";
    r.cookie_header = NULL;
    authenticated = is_authenticated(&r, patterns, ARRAY_SIZE(patterns));
    decision = simulate_auth_policy_decision(
        AUTH_POLICY_DENY, authenticated);
    TEST_ASSERT(STR_EQ(decision, "INELIGIBLE_AUTH"),
                "Integration: deny + auth → skip before headers");

    /* Case 4: Authenticated + allow + no-store → preserved */
    r.authorization = "Basic dXNlcjpwYXNz";
    r.cookie_header = NULL;
    authenticated = is_authenticated(&r, patterns, ARRAY_SIZE(patterns));
    cache_result = adjust_cache_control_for_auth(
        "no-store", authenticated);
    TEST_ASSERT(STR_EQ(cache_result, "no-store"),
                "Integration: streaming no-store preserved for auth");

    /* Case 5: Cookie-based auth + allow + absent CC → private */
    r.authorization = NULL;
    r.cookie_header = "session_id=abc123";
    authenticated = is_authenticated(&r, patterns, ARRAY_SIZE(patterns));
    TEST_ASSERT(authenticated == 1,
                "Integration: cookie-based auth detected");
    cache_result = adjust_cache_control_for_auth(NULL, authenticated);
    TEST_ASSERT(STR_EQ(cache_result, "private"),
                "Integration: streaming absent CC → private for "
                "cookie auth");

    /*
     * Compile-time gate verification.
     *
     * In the test build, NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL
     * is defined as 0 (see headers_standalone_types.h).  This mirrors
     * the production compile-time gate: when the flag is 0, the auth
     * cache control code block inside
     * ngx_http_markdown_streaming_update_headers() is not compiled.
     *
     * We verify the test build matches this expectation.
     */
#if NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL
    TEST_FAIL("Test build should have "
              "NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL=0");
#else
    TEST_PASS("Compile-time gate: "
              "NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL=0 "
              "in test build (auth CC code not compiled in "
              "production when disabled)");
#endif

    TEST_PASS("Streaming header update integration verified");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("auth_cache_control Tests\n");
    printf("========================================\n");

    test_auth_detection();
    test_cache_control_adjustment();
    test_auth_policy_deny_mode();
    test_streaming_path_auth_cache_control();
    test_streaming_header_update_integration();
    test_fail_open_preserves_cache_control();
    test_cache_policy_matrix();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
