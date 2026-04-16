/*
 * Test: accept_parser
 * Description: Accept header parsing
 */

#include "../include/test_common.h"
#include <ctype.h>

typedef struct {
    char type[32];
    char subtype[32];
    float q;
    int specificity;
    int order;
    int valid;
} accept_entry_t;

static int
str_case_eq(const char *a, const char *b)
{
    while (*a && *b) {
        if (tolower((unsigned char) *a) != tolower((unsigned char) *b)) {
            return 0;
        }
        a++;
        b++;
    }
    return *a == '\0' && *b == '\0';
}

static void
trim(char **start, char **end)
{
    while (*start < *end && (**start == ' ' || **start == '\t')) {
        (*start)++;
    }
    while (*end > *start && ((*(*end - 1) == ' ') || (*(*end - 1) == '\t'))) {
        (*end)--;
    }
}

static int
specificity_for(const char *type, const char *subtype)
{
    if (str_case_eq(type, "text") && str_case_eq(subtype, "markdown")) {
        return 3;
    }
    if (str_case_eq(type, "text") && str_case_eq(subtype, "*")) {
        return 2;
    }
    if (str_case_eq(type, "*") && str_case_eq(subtype, "*")) {
        return 1;
    }
    return 3;
}

static void
clamp_q_value(float *q_value)
{
    if (*q_value < 0.0f) {
        *q_value = 0.0f;
        return;
    }
    if (*q_value > 1.0f) {
        *q_value = 1.0f;
    }
}

static void
parse_q_param(accept_entry_t *entry, const char *params)
{
    const char *q;

    if (entry == NULL || params == NULL) {
        return;
    }

    q = strstr(params, "q=");
    if (q == NULL) {
        return;
    }

    entry->q = (float) atof(q + 2);
    clamp_q_value(&entry->q);
}

static int
copy_token(char *dst, size_t dst_size, const char *src)
{
    size_t len;

    if (dst == NULL || src == NULL || dst_size == 0) {
        return 0;
    }

    len = test_cstrnlen(src, dst_size);
    if (len >= dst_size) {
        return 0;
    }

    memcpy(dst, src, len + 1);
    return 1;
}

static int
parse_accept(const char *header, accept_entry_t *entries, int max_entries)
{
    const char *p = header;
    int n = 0;

    if (header == NULL || *header == '\0') {
        return 0;
    }

    while (*p && n < max_entries) {
        char token[128];
        char *slash;
        char *semi;
        char *s;
        char *e;
        size_t len;
        accept_entry_t *ent;

        while (*p == ' ' || *p == '\t' || *p == ',') {
            p++;
        }
        if (*p == '\0') {
            break;
        }

        len = 0;
        while (*p && *p != ',' && len + 1 < sizeof(token)) {
            token[len++] = *p++;
        }
        token[len] = '\0';

        ent = &entries[n];
        memset(ent, 0, sizeof(*ent));
        ent->q = 1.0f;
        ent->order = n;

        s = token;
        e = token + test_cstrnlen(token, sizeof(token));
        trim(&s, &e);
        *e = '\0';

        slash = strchr(s, '/');
        if (slash == NULL) {
            continue;
        }
        *slash = '\0';
        if (!copy_token(ent->type, sizeof(ent->type), s)) {
            continue;
        }

        semi = strchr(slash + 1, ';');
        if (semi != NULL) {
            *semi = '\0';
            parse_q_param(ent, semi + 1);
        }
        if (!copy_token(ent->subtype, sizeof(ent->subtype), slash + 1)) {
            continue;
        }

        ent->specificity = specificity_for(ent->type, ent->subtype);
        ent->valid = 1;
        n++;
    }

    return n;
}

static int
matches_markdown(const accept_entry_t *e, int on_wildcard)
{
    if (str_case_eq(e->type, "text") && str_case_eq(e->subtype, "markdown")) {
        return 1;
    }
    if (!on_wildcard) {
        return 0;
    }
    if (str_case_eq(e->type, "text") && str_case_eq(e->subtype, "*")) {
        return 1;
    }
    if (str_case_eq(e->type, "*") && str_case_eq(e->subtype, "*")) {
        return 1;
    }
    return 0;
}

static int
should_convert(const char *accept_header, int on_wildcard)
{
    accept_entry_t entries[32];
    int n;
    int best = -1;
    int explicit_reject_markdown = 0;

    n = parse_accept(accept_header, entries, (int) ARRAY_SIZE(entries));
    if (n == 0) {
        return 0;
    }

    for (int i = 0; i < n; i++) {
        if (str_case_eq(entries[i].type, "text") &&
            str_case_eq(entries[i].subtype, "markdown") &&
            entries[i].q == 0.0f)
        {
            explicit_reject_markdown = 1;
        }

        if (best == -1) {
            best = i;
            continue;
        }

        if (entries[i].q > entries[best].q) {
            best = i;
            continue;
        }
        if (entries[i].q == entries[best].q &&
            entries[i].specificity > entries[best].specificity)
        {
            best = i;
            continue;
        }
    }

    if (explicit_reject_markdown) {
        return 0;
    }

    if (best < 0 || entries[best].q <= 0.0f) {
        return 0;
    }

    return matches_markdown(&entries[best], on_wildcard);
}

static void
test_accept_core_cases(void)
{
    TEST_SUBSECTION("Core Accept header cases");
    TEST_ASSERT(should_convert("text/markdown", 0) == 1, "text/markdown should convert");
    TEST_ASSERT(should_convert("text/html", 0) == 0, "text/html should not convert");
    TEST_ASSERT(should_convert("text/markdown;q=0", 0) == 0, "q=0 should reject markdown");
    TEST_ASSERT(should_convert("text/markdown;q=0.9, text/html;q=0.9", 0) == 1, "equal q/order should prefer first");
    TEST_ASSERT(should_convert("text/html;q=0.9, text/markdown;q=0.9", 0) == 0, "equal q/order should preserve order");
    TEST_ASSERT(should_convert("text/html;q=0.9, text/markdown;q=0.8", 0) == 0, "higher q html should win");
    TEST_PASS("Core Accept cases passed");
}

static void
test_wildcard_behavior(void)
{
    TEST_SUBSECTION("Wildcard behavior");
    TEST_ASSERT(should_convert("*/*", 0) == 0, "*/* should not convert by default");
    TEST_ASSERT(should_convert("*/*", 1) == 1, "*/* should convert when wildcard enabled");
    TEST_ASSERT(should_convert("text/*;q=0.8, text/html;q=0.9", 1) == 0, "lower-q wildcard should lose");
    TEST_ASSERT(should_convert("text/*;q=0.9, text/html;q=0.8", 1) == 1, "higher-q wildcard should win");
    TEST_PASS("Wildcard behavior passed");
}

static void
test_malformed_entries(void)
{
    TEST_SUBSECTION("Malformed Accept entries");
    TEST_ASSERT(should_convert(",,, ,", 0) == 0, "empty/malformed header should not convert");
    TEST_ASSERT(should_convert("invalid-entry, text/markdown;q=1", 0) == 1, "malformed entries should not block valid ones");
    TEST_PASS("Malformed handling passed");
}

/* ── Q-value edge cases (Task 7.1, Req 9.4) ─────────────────────── */

static void
test_q_value_edge_cases(void)
{
    TEST_SUBSECTION("Q-value edge cases");

    /* q=1 (maximum valid) */
    TEST_ASSERT(should_convert("text/markdown;q=1", 0) == 1,
                "q=1 should convert");

    /* q=1.000 (maximum valid with decimals) */
    TEST_ASSERT(should_convert("text/markdown;q=1.000", 0) == 1,
                "q=1.000 should convert");

    /* q=0.000 (minimum valid, should reject) */
    TEST_ASSERT(should_convert("text/markdown;q=0.000", 0) == 0,
                "q=0.000 should reject markdown");

    /* q=0 (explicit rejection — already covered, verify completeness) */
    TEST_ASSERT(should_convert("text/markdown;q=0", 0) == 0,
                "q=0 should reject markdown (completeness)");

    /* q=0.5 (valid intermediate value) */
    TEST_ASSERT(should_convert("text/markdown;q=0.5", 0) == 1,
                "q=0.5 should convert");

    /*
     * Negative q-value: atof("-0.1") returns -0.1, clamped to 0.0 by
     * clamp_q_value().  Production ngx_atofp() rejects negative input
     * and defaults to 1.0; this test verifies the stub's clamping path.
     */
    TEST_ASSERT(should_convert("text/markdown;q=-0.1", 0) == 0,
                "negative q should clamp to 0.0 and reject");

    /*
     * Whitespace around q-values: the stub's strstr("q=") + atof()
     * tolerates leading whitespace after '=' (atof skips it) and
     * trailing whitespace (atof stops at non-numeric).  Space before
     * '=' prevents strstr from finding "q=", so q defaults to 1.0.
     */
    TEST_ASSERT(should_convert("text/markdown;q= 0.5", 0) == 1,
                "leading space after '=' in q-value accepted (atof skips)");
    TEST_ASSERT(should_convert("text/markdown;q=0.5 ", 0) == 1,
                "trailing space after q-value accepted (atof stops)");
    TEST_ASSERT(should_convert("text/markdown;q =0.5", 0) == 1,
                "space before '=' means strstr misses q=, defaults to 1.0");

    TEST_PASS("Q-value edge cases passed");
}

static void
test_q_value_malformed(void)
{
    TEST_SUBSECTION("Malformed q-values");

    /* NOTE: The test stub uses atof() which returns 0.0 for non-numeric
       input, causing q=abc and q= to reject. The production code uses
       ngx_atofp() which returns NGX_ERROR for non-numeric input, causing
       parse_q_value() to default to 1.0 (convert). These tests verify
       the stub's behavior; production behavior is validated by e2e tests. */

    /* q=abc (non-numeric) — atof returns 0.0, clamped to 0.0, should reject */
    TEST_ASSERT(should_convert("text/markdown;q=abc", 0) == 0,
                "q=abc should reject in stub (atof returns 0)");

    /* q= (empty value) — atof returns 0.0, clamped to 0.0, should reject */
    TEST_ASSERT(should_convert("text/markdown;q=", 0) == 0,
                "q= (empty) should reject in stub (atof returns 0)");

    /* q=2.0 (out of range) — clamped to 1.0, should convert */
    TEST_ASSERT(should_convert("text/markdown;q=2.0", 0) == 1,
                "q=2.0 should convert (clamped to 1.0)");

    /* Missing q parameter — defaults to 1.0 */
    TEST_ASSERT(should_convert("text/markdown", 0) == 1,
                "Missing q parameter defaults to 1.0");

    TEST_PASS("Malformed q-values passed");
}

/* Feature: improve-test-coverage, Property 5: Q-value parsing range invariant */

static void
test_q_value_range_invariant(void)
{
    accept_entry_t entries[8];
    int n;

    TEST_SUBSECTION("Property 5: Q-value parsing range invariant");

    /* Valid q-values in [0.0, 1.0] — verify parsed value matches */
    struct {
        const char *header;
        float expected_q;
    } cases[] = {
        { "text/markdown;q=0",     0.0f },
        { "text/markdown;q=0.5",   0.5f },
        { "text/markdown;q=1",     1.0f },
        { "text/markdown;q=1.0",   1.0f },
        { "text/markdown;q=0.001", 0.001f },
        { "text/markdown;q=0.999", 0.999f },
    };

    for (size_t i = 0; i < ARRAY_SIZE(cases); i++) {
        n = parse_accept(cases[i].header, entries, 8);
        TEST_ASSERT(n == 1, "Parsed one entry");
        TEST_ASSERT(entries[0].q >= 0.0f && entries[0].q <= 1.0f,
                    "Q-value clamped to [0.0, 1.0]");
        /* Verify parsed q matches expected value (within float tolerance) */
        TEST_ASSERT(entries[0].q >= cases[i].expected_q - 0.002f &&
                    entries[0].q <= cases[i].expected_q + 0.002f,
                    "Parsed q-value matches expected");
    }

    /* Out-of-range q=2.0 clamped to 1.0 */
    n = parse_accept("text/markdown;q=2.0", entries, 8);
    TEST_ASSERT(n == 1, "Parsed q=2.0 entry");
    TEST_ASSERT(entries[0].q <= 1.0f,
                "q=2.0 clamped to at most 1.0");

    /* Missing q defaults to 1.0 */
    n = parse_accept("text/markdown", entries, 8);
    TEST_ASSERT(n == 1, "Parsed entry without q");
    TEST_ASSERT(entries[0].q == 1.0f,
                "Missing q defaults to 1.0");

    TEST_PASS("Q-value range invariant verified");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("accept_parser Tests\n");
    printf("========================================\n");

    test_accept_core_cases();
    test_wildcard_behavior();
    test_malformed_entries();
    test_q_value_edge_cases();
    test_q_value_malformed();
    test_q_value_range_invariant();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
