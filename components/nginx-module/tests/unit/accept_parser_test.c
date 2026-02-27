/*
 * Test: accept_parser
 * Description: Accept header parsing
 */

#include "test_common.h"
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
        e = token + strlen(token);
        trim(&s, &e);
        *e = '\0';

        slash = strchr(s, '/');
        if (slash == NULL) {
            continue;
        }
        *slash = '\0';
        strncpy(ent->type, s, sizeof(ent->type) - 1);

        semi = strchr(slash + 1, ';');
        if (semi != NULL) {
            char *q = strstr(semi + 1, "q=");
            *semi = '\0';
            if (q != NULL) {
                ent->q = (float) atof(q + 2);
                if (ent->q < 0.0f) ent->q = 0.0f;
                if (ent->q > 1.0f) ent->q = 1.0f;
            }
        }
        strncpy(ent->subtype, slash + 1, sizeof(ent->subtype) - 1);

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
    int i;
    int n;
    int best = -1;
    int explicit_reject_markdown = 0;

    n = parse_accept(accept_header, entries, (int) ARRAY_SIZE(entries));
    if (n == 0) {
        return 0;
    }

    for (i = 0; i < n; i++) {
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

int
main(void)
{
    printf("\n========================================\n");
    printf("accept_parser Tests\n");
    printf("========================================\n");

    test_accept_core_cases();
    test_wildcard_behavior();
    test_malformed_entries();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
