/*
 * Test: auth_cookie_pattern
 * Description: Cookie pattern matching for authentication detection
 *
 * Validates: Requirements 9.3
 *
 * Tests prefix wildcard, suffix wildcard, exact matching, empty inputs,
 * non-matching patterns, and edge cases for the cookie pattern matching
 * function used in auth detection.
 */

#include "../include/test_common.h"

/*
 * Standalone reimplementation of ngx_http_markdown_cookie_matches_pattern
 * following the same logic as the production code in
 * ngx_http_markdown_auth.c.
 *
 * DIVERGENCE RISK: The production function operates on ngx_str_t (NGINX
 * length-delimited strings) and cannot be linked into standalone unit
 * tests.  This reimplementation uses NUL-terminated C strings but must
 * mirror the production matching semantics exactly.  If the production
 * logic in ngx_http_markdown_auth.c changes, update this function in
 * the same change set and re-run this test.
 *
 * Supports three matching modes:
 * 1. Exact match: "session" matches "session" only
 * 2. Prefix match with wildcard: "session*" matches "session", "session_id", etc.
 * 3. Suffix match with wildcard: "*_logged_in" matches "wordpress_logged_in", etc.
 *
 * Patterns with '*' in the middle (e.g. "sess*id") or at both ends
 * (e.g. "*session*") are NOT treated as substring wildcards.  The
 * last-char-is-'*' check fires first, so "*session*" enters the
 * prefix-wildcard branch with prefix "*session" (which will only
 * match cookie names starting with "*session").  "sess*id" has no
 * leading or trailing '*', so it falls through to exact match.
 */
static int
cookie_matches_pattern(const char *cookie_name, const char *pattern)
{
    size_t cookie_len;
    size_t pattern_len;

    if (cookie_name == NULL || pattern == NULL ||
        cookie_name[0] == '\0' || pattern[0] == '\0')
    {
        return 0;
    }

    cookie_len = strlen(cookie_name);
    pattern_len = strlen(pattern);

    /* Prefix wildcard: pattern ends with '*' */
    if (pattern[pattern_len - 1] == '*') {
        size_t prefix_len = pattern_len - 1;
        if (cookie_len < prefix_len) {
            return 0;
        }
        return strncmp(cookie_name, pattern, prefix_len) == 0;
    }

    /* Suffix wildcard: pattern starts with '*' */
    if (pattern[0] == '*') {
        size_t suffix_len = pattern_len - 1;
        if (cookie_len < suffix_len) {
            return 0;
        }
        return strncmp(cookie_name + (cookie_len - suffix_len),
                        pattern + 1, suffix_len) == 0;
    }

    /* Exact match */
    if (cookie_len != pattern_len) {
        return 0;
    }

    return strncmp(cookie_name, pattern, pattern_len) == 0;
}

/* ── Prefix wildcard matching tests ──────────────────────────────── */

static void
test_prefix_wildcard_matching(void)
{
    TEST_SUBSECTION("Prefix wildcard matching");

    /* "session*" matches "session_id" */
    TEST_ASSERT(cookie_matches_pattern("session_id", "session*") == 1,
                "session* matches session_id");
    TEST_ASSERT(cookie_matches_pattern("session", "session*") == 1,
                "session* matches session (exact prefix)");
    TEST_ASSERT(cookie_matches_pattern("sessionToken", "session*") == 1,
                "session* matches sessionToken");

    /* Non-matching prefix */
    TEST_ASSERT(cookie_matches_pattern("auth_token", "session*") == 0,
                "session* does not match auth_token");
    TEST_ASSERT(cookie_matches_pattern("sess", "session*") == 0,
                "session* does not match sess (too short)");

    /* "auth*" matches */
    TEST_ASSERT(cookie_matches_pattern("auth", "auth*") == 1,
                "auth* matches auth");
    TEST_ASSERT(cookie_matches_pattern("auth_cookie", "auth*") == 1,
                "auth* matches auth_cookie");

    TEST_PASS("Prefix wildcard matching correct");
}

/* Feature: improve-test-coverage, Property 3: Cookie prefix pattern matching correctness */

static void
test_prefix_wildcard_property(void)
{
    struct {
        const char *cookie;
        const char *pattern;
        int expected;
    } cases[] = {
        { "session_id",  "session*", 1 },
        { "session",     "session*", 1 },
        { "sess",        "session*", 0 },
        { "SESSION_ID",  "session*", 0 },  /* case-sensitive */
        { "s",           "s*",       1 },
        { "abc",         "a*",       1 },
        { "abc",         "b*",       0 },
    };

    TEST_SUBSECTION("Property 3: Cookie prefix pattern matching correctness");
    for (size_t i = 0; i < ARRAY_SIZE(cases); i++) {
        int result = cookie_matches_pattern(cases[i].cookie, cases[i].pattern);
        TEST_ASSERT(result == cases[i].expected,
                    "Prefix pattern match correctness");
    }

    /* Empty cookie name or empty pattern returns false */
    TEST_ASSERT(cookie_matches_pattern("", "session*") == 0,
                "Empty cookie name returns false");
    /*
     * Bare "*" hits the prefix-wildcard branch (last char is '*'),
     * not the suffix branch.  For this pattern, prefix_len = 0,
     * so strncmp compares 0 bytes and returns 0 (match).  Therefore
     * bare "*" matches any non-empty cookie name.  This mirrors the
     * production code where the last-char check fires before the
     * first-char check.
     */
    TEST_ASSERT(cookie_matches_pattern("session", "*") == 1,
                "Bare * pattern matches any non-empty cookie name");
    TEST_ASSERT(cookie_matches_pattern("a", "*") == 1,
                "Bare * pattern matches single-char cookie name");

    TEST_PASS("Prefix pattern matching property verified");
}

/* ── Suffix wildcard matching tests ──────────────────────────────── */

static void
test_suffix_wildcard_matching(void)
{
    TEST_SUBSECTION("Suffix wildcard matching");

    /* "*_logged_in" matches "wordpress_logged_in" */
    TEST_ASSERT(cookie_matches_pattern("wordpress_logged_in", "*_logged_in") == 1,
                "*_logged_in matches wordpress_logged_in");
    TEST_ASSERT(cookie_matches_pattern("site_logged_in", "*_logged_in") == 1,
                "*_logged_in matches site_logged_in");
    TEST_ASSERT(cookie_matches_pattern("_logged_in", "*_logged_in") == 1,
                "*_logged_in matches _logged_in (exact suffix)");

    /* Non-matching suffix */
    TEST_ASSERT(cookie_matches_pattern("wordpress_auth", "*_logged_in") == 0,
                "*_logged_in does not match wordpress_auth");
    TEST_ASSERT(cookie_matches_pattern("logged_in", "*_logged_in") == 0,
                "*_logged_in does not match logged_in (missing underscore)");

    TEST_PASS("Suffix wildcard matching correct");
}

/* Feature: improve-test-coverage, Property 4: Cookie suffix pattern matching correctness */

static void
test_suffix_wildcard_property(void)
{
    struct {
        const char *cookie;
        const char *pattern;
        int expected;
    } cases[] = {
        { "wordpress_logged_in", "*_logged_in", 1 },
        { "_logged_in",         "*_logged_in", 1 },
        { "logged_in",          "*_logged_in", 0 },
        { "x_y",                "*_y",         1 },
        { "y",                  "*_y",         0 },
        { "abc",                "*c",          1 },
        { "abc",                "*b",          0 },
    };

    TEST_SUBSECTION("Property 4: Cookie suffix pattern matching correctness");
    for (size_t i = 0; i < ARRAY_SIZE(cases); i++) {
        int result = cookie_matches_pattern(cases[i].cookie, cases[i].pattern);
        TEST_ASSERT(result == cases[i].expected,
                    "Suffix pattern match correctness");
    }

    /* Empty cookie name or empty pattern returns false */
    TEST_ASSERT(cookie_matches_pattern("", "*_logged_in") == 0,
                "Empty cookie name returns false for suffix pattern");

    TEST_PASS("Suffix pattern matching property verified");
}

/* ── Exact matching tests ────────────────────────────────────────── */

static void
test_exact_matching(void)
{
    TEST_SUBSECTION("Exact matching (no wildcards)");

    TEST_ASSERT(cookie_matches_pattern("PHPSESSID", "PHPSESSID") == 1,
                "PHPSESSID exact match");
    TEST_ASSERT(cookie_matches_pattern("PHPSESSID", "phpsessid") == 0,
                "Exact match is case-sensitive");
    TEST_ASSERT(cookie_matches_pattern("session", "session") == 1,
                "session exact match");
    TEST_ASSERT(cookie_matches_pattern("session_id", "session") == 0,
                "session does not match session_id (different length)");

    TEST_PASS("Exact matching correct");
}

/* ── Empty inputs and edge cases ─────────────────────────────────── */

static void
test_empty_inputs_and_edge_cases(void)
{
    TEST_SUBSECTION("Empty inputs and edge cases");

    /* NULL inputs */
    TEST_ASSERT(cookie_matches_pattern(NULL, "session*") == 0,
                "NULL cookie name returns false");
    TEST_ASSERT(cookie_matches_pattern("session", NULL) == 0,
                "NULL pattern returns false");
    TEST_ASSERT(cookie_matches_pattern(NULL, NULL) == 0,
                "Both NULL returns false");

    /* Empty strings */
    TEST_ASSERT(cookie_matches_pattern("", "session*") == 0,
                "Empty cookie name returns false");
    TEST_ASSERT(cookie_matches_pattern("session", "") == 0,
                "Empty pattern returns false");
    TEST_ASSERT(cookie_matches_pattern("", "") == 0,
                "Both empty returns false");

    /* Single-char patterns */
    TEST_ASSERT(cookie_matches_pattern("a", "a*") == 1,
                "Single-char prefix pattern matches");
    TEST_ASSERT(cookie_matches_pattern("a", "*a") == 1,
                "Single-char suffix pattern matches");
    TEST_ASSERT(cookie_matches_pattern("a", "a") == 1,
                "Single-char exact match");

    /* Single-char cookie names */
    TEST_ASSERT(cookie_matches_pattern("x", "y*") == 0,
                "Single-char cookie doesn't match different prefix");
    TEST_ASSERT(cookie_matches_pattern("x", "*y") == 0,
                "Single-char cookie doesn't match different suffix");

    TEST_PASS("Empty inputs and edge cases correct");
}

/* ── Non-matching patterns ───────────────────────────────────────── */

/*
 * Patterns with '*' in the middle or at both ends are not treated as
 * general substring wildcards.  Document current behavior so future
 * changes to the matching logic don't silently alter semantics.
 */
static void
test_middle_and_double_wildcard(void)
{
    TEST_SUBSECTION("Middle and double wildcard behavior");

    /*
     * "*session*" — last char is '*', so the prefix-wildcard branch
     * fires with prefix "*session".  Only cookie names literally
     * starting with "*session" would match.
     */
    TEST_ASSERT(cookie_matches_pattern("sessionid", "*session*") == 0,
                "*session* does not substring-match sessionid");
    TEST_ASSERT(cookie_matches_pattern("my_session_cookie", "*session*") == 0,
                "*session* does not substring-match my_session_cookie");

    /* "sess*id" — no leading/trailing '*', falls to exact match */
    TEST_ASSERT(cookie_matches_pattern("sessionid", "sess*id") == 0,
                "sess*id does not wildcard-match sessionid");
    TEST_ASSERT(cookie_matches_pattern("sess*id", "sess*id") == 1,
                "sess*id exact-matches itself (literal '*' in name)");

    /* Double wildcard "**" — last char is '*', prefix is "*" (literal
     * asterisk).  strncmp compares the first char of the cookie against
     * '*', so only cookie names starting with '*' would match. */
    TEST_ASSERT(cookie_matches_pattern("anything", "**") == 0,
                "** does not match arbitrary cookies (prefix is literal '*')");
    TEST_ASSERT(cookie_matches_pattern("*foo", "**") == 1,
                "** matches cookie names starting with literal '*'");

    TEST_PASS("Middle and double wildcard behavior correct");
}

static void
test_non_matching_patterns(void)
{
    TEST_SUBSECTION("Non-matching patterns");

    TEST_ASSERT(cookie_matches_pattern("tracking", "session*") == 0,
                "tracking doesn't match session*");
    TEST_ASSERT(cookie_matches_pattern("analytics", "*_logged_in") == 0,
                "analytics doesn't match *_logged_in");
    TEST_ASSERT(cookie_matches_pattern("csrf_token", "PHPSESSID") == 0,
                "csrf_token doesn't match PHPSESSID");

    TEST_PASS("Non-matching patterns correct");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("auth_cookie_pattern Tests\n");
    printf("========================================\n");

    test_prefix_wildcard_matching();
    test_prefix_wildcard_property();
    test_suffix_wildcard_matching();
    test_suffix_wildcard_property();
    test_exact_matching();
    test_empty_inputs_and_edge_cases();
    test_middle_and_double_wildcard();
    test_non_matching_patterns();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
