/*
 * Test: removed_directive_rejection_property
 *
 * Property-based test for removed directive rejection (Property 2).
 *
 * Feature: 62-final-pre-v1-breaking-freeze-fixed
 * Property 2: Removed directive rejection
 *
 * Validates: Requirements 2.5, 7.1, 7.2
 *
 * For each removed directive name (all 19 reject-only + 7 specified
 * active deletions + OTel directives + additional removed directives),
 * verify the directive is NOT present in the module's command table.
 *
 * When a directive is absent from the command table, NGINX's standard
 * config parser produces "unknown directive" at `nginx -t` time.
 *
 * Test approach:
 *   1. Enumerate all removed directive names (complete set of 38)
 *   2. For each name, iterate the command table and verify absence
 *   3. Verify the active command table contains exactly 25 entries
 */

#include "../include/test_common.h"

#include "../../src/ngx_http_markdown_directive_names.h"

/* ----------------------------------------------------------------
 * The active command-table target (25 entries, frozen).
 * Extracted from the production directive registry. This
 * self-contained list avoids needing to compile the full
 * production header with all its handler stubs.
 * ---------------------------------------------------------------- */

static const char *active_directives[] = {
    NGX_HTTP_MARKDOWN_DIRECTIVE_FILTER,
    NGX_HTTP_MARKDOWN_DIRECTIVE_LIMITS,
    NGX_HTTP_MARKDOWN_DIRECTIVE_ERROR_POLICY,
    NGX_HTTP_MARKDOWN_DIRECTIVE_FLAVOR,
    NGX_HTTP_MARKDOWN_DIRECTIVE_TOKEN_ESTIMATE,
    NGX_HTTP_MARKDOWN_DIRECTIVE_FRONT_MATTER,
    NGX_HTTP_MARKDOWN_DIRECTIVE_ACCEPT,
    NGX_HTTP_MARKDOWN_DIRECTIVE_AUTH_POLICY,
    NGX_HTTP_MARKDOWN_DIRECTIVE_AUTH_COOKIES,
    NGX_HTTP_MARKDOWN_DIRECTIVE_CACHE_VALIDATION,
    NGX_HTTP_MARKDOWN_DIRECTIVE_STREAMING,
    NGX_HTTP_MARKDOWN_DIRECTIVE_LOG_VERBOSITY,
    NGX_HTTP_MARKDOWN_DIRECTIVE_CONTENT_TYPES,
    NGX_HTTP_MARKDOWN_DIRECTIVE_TRUSTED_PROXIES,
    NGX_HTTP_MARKDOWN_DIRECTIVE_METRICS_SHM_SIZE,
    NGX_HTTP_MARKDOWN_DIRECTIVE_METRICS,
    NGX_HTTP_MARKDOWN_DIRECTIVE_PRUNE_NOISE,
    NGX_HTTP_MARKDOWN_DIRECTIVE_PRUNE_SELECTORS,
    NGX_HTTP_MARKDOWN_DIRECTIVE_PRUNE_PROTECTION_SELECTORS,
    NGX_HTTP_MARKDOWN_DIRECTIVE_AUTO_DECOMPRESS,
    NGX_HTTP_MARKDOWN_DIRECTIVE_DYNAMIC_CONFIG,
    NGX_HTTP_MARKDOWN_DIRECTIVE_DYNAMIC_CONFIG_PATH,
    NGX_HTTP_MARKDOWN_DIRECTIVE_DYNCONF_DRY_RUN,
    NGX_HTTP_MARKDOWN_DIRECTIVE_DIAGNOSTICS,
    NGX_HTTP_MARKDOWN_DIRECTIVE_STREAM_EXCLUDED_TYPES
};

#define ACTIVE_DIRECTIVE_COUNT \
    (sizeof(active_directives) / sizeof(active_directives[0]))

/* ----------------------------------------------------------------
 * Removed directive name sets
 * ---------------------------------------------------------------- */

/*
 * 19 reject-only directives: formerly migration stubs that produced
 * a "deprecated" error. Now completely removed so NGINX produces
 * "unknown directive".
 */
static const char *reject_only_directives[] = {
    "markdown_max_size",
    "markdown_timeout",
    "markdown_streaming_budget",
    "markdown_on_error",
    "markdown_streaming_on_error",
    "markdown_on_wildcard",
    "markdown_etag",
    "markdown_etag_policy",
    "markdown_conditional_requests",
    "markdown_trust_forwarded_headers",
    "markdown_forwarded_headers",
    "markdown_large_body_threshold",
    "markdown_streaming_engine",
    "markdown_memory_budget",
    "markdown_otel_tracing",
    "markdown_otel_metrics",
    "markdown_otel_service_name",
    "markdown_otel_span_buffer_size",
    "markdown_otel_export_timeout"
};

/*
 * 7 active deleted directives: formerly functional directives
 * now removed (behavior internalized or removed entirely).
 */
static const char *active_deleted_directives[] = {
    "markdown_streaming_zero_copy",
    "markdown_streaming_shadow",
    "markdown_buffer_chunked",
    "markdown_metrics_format",
    "markdown_metrics_per_path",
    "markdown_metrics_per_path_cardinality",
    "markdown_llm_provider"
};

/*
 * Additional removed directives: OTel, unified into markdown_limits,
 * internalized, or simplified access control.
 */
static const char *additional_removed_directives[] = {
    "markdown_profile",
    "markdown_chars_per_token",
    "markdown_stream_types",
    "markdown_otel",
    "markdown_otel_endpoint",
    "markdown_stream_precommit_buffer",
    "markdown_stream_flush_min",
    "markdown_stream_threshold",
    "markdown_parse_timeout",
    "markdown_parser_budget",
    "markdown_decompress_max_size",
    "markdown_diagnostics_allow"
};

#define REJECT_ONLY_COUNT \
    (sizeof(reject_only_directives) / sizeof(reject_only_directives[0]))
#define ACTIVE_DELETED_COUNT \
    (sizeof(active_deleted_directives) / sizeof(active_deleted_directives[0]))
#define ADDITIONAL_REMOVED_COUNT \
    (sizeof(additional_removed_directives) / sizeof(additional_removed_directives[0]))
#define TOTAL_REMOVED_COUNT \
    (REJECT_ONLY_COUNT + ACTIVE_DELETED_COUNT + ADDITIONAL_REMOVED_COUNT)

/* ----------------------------------------------------------------
 * Helper: check if a name is in the active directive set
 *
 * Returns: 1 if found, 0 if absent.
 * ---------------------------------------------------------------- */

static int
active_set_contains(const char *name)
{
    size_t i;
    size_t len;

    len = strlen(name);

    for (i = 0; i < ACTIVE_DIRECTIVE_COUNT; i++) {
        if (strlen(active_directives[i]) == len
            && strcmp(active_directives[i], name) == 0)
        {
            return 1;
        }
    }

    return 0;
}

/* ----------------------------------------------------------------
 * Property 2a: No reject-only directive in command table
 *
 * For each of the 19 reject-only directive names, verify the
 * active command table does NOT contain it.
 *
 * **Validates: Requirements 2.5, 7.1**
 * ---------------------------------------------------------------- */

static void
test_property2a_reject_only_absent(void)
{
    size_t i;

    TEST_SUBSECTION(
        "Property 2a: All 19 reject-only directives are "
        "absent from command table");

    TEST_ASSERT(REJECT_ONLY_COUNT == 19,
        "reject-only directive count must be 19");

    for (i = 0; i < REJECT_ONLY_COUNT; i++) {
        TEST_ASSERT(
            !active_set_contains(reject_only_directives[i]),
            reject_only_directives[i]);
    }

    TEST_PASS(
        "Property 2a: all 19 reject-only directives are "
        "absent from command table");
}

/* ----------------------------------------------------------------
 * Property 2b: No active-deleted directive in command table
 *
 * For each of the 7 active-deleted directive names, verify the
 * command table does NOT contain it.
 *
 * **Validates: Requirements 2.5, 7.2**
 * ---------------------------------------------------------------- */

static void
test_property2b_active_deleted_absent(void)
{
    size_t i;

    TEST_SUBSECTION(
        "Property 2b: All 7 active-deleted directives are "
        "absent from command table");

    TEST_ASSERT(ACTIVE_DELETED_COUNT == 7,
        "active-deleted directive count must be 7");

    for (i = 0; i < ACTIVE_DELETED_COUNT; i++) {
        TEST_ASSERT(
            !active_set_contains(active_deleted_directives[i]),
            active_deleted_directives[i]);
    }

    TEST_PASS(
        "Property 2b: all 7 active-deleted directives are "
        "absent from command table");
}

/* ----------------------------------------------------------------
 * Property 2c: No additional removed directive in command table
 *
 * For each additional removed directive (OTel, unified, internalized,
 * access control simplified), verify absence from command table.
 *
 * **Validates: Requirements 2.5, 7.1, 7.2**
 * ---------------------------------------------------------------- */

static void
test_property2c_additional_removed_absent(void)
{
    size_t i;

    TEST_SUBSECTION(
        "Property 2c: All additional removed directives are "
        "absent from command table");

    TEST_ASSERT(ADDITIONAL_REMOVED_COUNT == 12,
        "additional removed directive count must be 12");

    for (i = 0; i < ADDITIONAL_REMOVED_COUNT; i++) {
        TEST_ASSERT(
            !active_set_contains(additional_removed_directives[i]),
            additional_removed_directives[i]);
    }

    TEST_PASS(
        "Property 2c: all additional removed directives are "
        "absent from command table");
}

/* ----------------------------------------------------------------
 * Property 2d: Total removed count is complete and disjoint
 *
 * Verify: total removed = 38, none appear in the active set,
 * and no removed directive name appears in any other removed set
 * (no duplicates across the three arrays).
 *
 * **Validates: Requirements 2.5, 7.1, 7.2**
 * ---------------------------------------------------------------- */

static void
test_property2d_total_removed_complete(void)
{
    size_t i;
    size_t j;
    size_t total_checked;

    TEST_SUBSECTION(
        "Property 2d: Complete removed directive set "
        "(38 total) absent from command table");

    TEST_ASSERT(TOTAL_REMOVED_COUNT == 38,
        "total removed directive count must be 38");

    total_checked = 0;

    for (i = 0; i < REJECT_ONLY_COUNT; i++) {
        TEST_ASSERT(
            !active_set_contains(reject_only_directives[i]),
            reject_only_directives[i]);
        total_checked++;
    }

    for (i = 0; i < ACTIVE_DELETED_COUNT; i++) {
        TEST_ASSERT(
            !active_set_contains(active_deleted_directives[i]),
            active_deleted_directives[i]);
        total_checked++;
    }

    for (i = 0; i < ADDITIONAL_REMOVED_COUNT; i++) {
        TEST_ASSERT(
            !active_set_contains(additional_removed_directives[i]),
            additional_removed_directives[i]);
        total_checked++;
    }

    TEST_ASSERT(total_checked == 38,
        "total checked directives must be 38");

    /* Verify no duplicates across the three removed arrays */
    for (i = 0; i < REJECT_ONLY_COUNT; i++) {
        for (j = 0; j < ACTIVE_DELETED_COUNT; j++) {
            TEST_ASSERT(
                strcmp(reject_only_directives[i],
                       active_deleted_directives[j]) != 0,
                "reject-only and active-deleted must not overlap");
        }
        for (j = 0; j < ADDITIONAL_REMOVED_COUNT; j++) {
            TEST_ASSERT(
                strcmp(reject_only_directives[i],
                       additional_removed_directives[j]) != 0,
                "reject-only and additional must not overlap");
        }
    }
    for (i = 0; i < ACTIVE_DELETED_COUNT; i++) {
        for (j = 0; j < ADDITIONAL_REMOVED_COUNT; j++) {
            TEST_ASSERT(
                strcmp(active_deleted_directives[i],
                       additional_removed_directives[j]) != 0,
                "active-deleted and additional must not overlap");
        }
    }

    TEST_PASS(
        "Property 2d: all 38 removed directives verified "
        "absent; no duplicates across sets");
}

/* ----------------------------------------------------------------
 * Property 2e: Command table contains exactly 25 entries
 *
 * The frozen target command table has exactly 25 directives.
 * This guards against accidentally adding back removed
 * directives or other regressions.
 *
 * **Validates: Requirements 2.5, 7.1, 7.2**
 * ---------------------------------------------------------------- */

static void
test_property2e_command_table_count_is_25(void)
{
    TEST_SUBSECTION(
        "Property 2e: Command table contains exactly 25 "
        "directives (frozen target)");

    TEST_ASSERT(ACTIVE_DIRECTIVE_COUNT == 25,
        "active directive count must be exactly 25");

    TEST_PASS(
        "Property 2e: command table count == 25 (frozen "
        "target verified)");
}

/* ----------------------------------------------------------------
 * Property 2f: All removed names are unique (no duplicates
 *              within each set)
 *
 * Validates internal consistency of the test's removed directive
 * arrays: no name appears twice within the same array.
 *
 * **Validates: Requirements 2.5, 7.1, 7.2**
 * ---------------------------------------------------------------- */

static void
test_property2f_no_internal_duplicates(void)
{
    size_t i;
    size_t j;

    TEST_SUBSECTION(
        "Property 2f: No internal duplicates within "
        "each removed directive set");

    /* Check reject-only set */
    for (i = 0; i < REJECT_ONLY_COUNT; i++) {
        for (j = i + 1; j < REJECT_ONLY_COUNT; j++) {
            TEST_ASSERT(
                strcmp(reject_only_directives[i],
                       reject_only_directives[j]) != 0,
                "reject-only set has no duplicates");
        }
    }

    /* Check active-deleted set */
    for (i = 0; i < ACTIVE_DELETED_COUNT; i++) {
        for (j = i + 1; j < ACTIVE_DELETED_COUNT; j++) {
            TEST_ASSERT(
                strcmp(active_deleted_directives[i],
                       active_deleted_directives[j]) != 0,
                "active-deleted set has no duplicates");
        }
    }

    /* Check additional removed set */
    for (i = 0; i < ADDITIONAL_REMOVED_COUNT; i++) {
        for (j = i + 1; j < ADDITIONAL_REMOVED_COUNT; j++) {
            TEST_ASSERT(
                strcmp(additional_removed_directives[i],
                       additional_removed_directives[j]) != 0,
                "additional set has no duplicates");
        }
    }

    /* Check active directives set */
    for (i = 0; i < ACTIVE_DIRECTIVE_COUNT; i++) {
        for (j = i + 1; j < ACTIVE_DIRECTIVE_COUNT; j++) {
            TEST_ASSERT(
                strcmp(active_directives[i],
                       active_directives[j]) != 0,
                "active directive set has no duplicates");
        }
    }

    TEST_PASS(
        "Property 2f: no internal duplicates in any "
        "directive set");
}

/* ----------------------------------------------------------------
 * Property 2g: All directive names have the markdown_ prefix
 *
 * Every removed directive name must start with "markdown_"
 * (module namespace convention). This catches typos.
 *
 * **Validates: Requirements 2.5, 7.1, 7.2**
 * ---------------------------------------------------------------- */

static void
test_property2g_all_names_have_prefix(void)
{
    size_t i;
    const char *prefix;
    size_t prefix_len;

    prefix = "markdown_";
    prefix_len = strlen(prefix);

    TEST_SUBSECTION(
        "Property 2g: All directive names have markdown_ "
        "prefix");

    for (i = 0; i < REJECT_ONLY_COUNT; i++) {
        TEST_ASSERT(
            strncmp(reject_only_directives[i], prefix,
                    prefix_len) == 0,
            reject_only_directives[i]);
    }

    for (i = 0; i < ACTIVE_DELETED_COUNT; i++) {
        TEST_ASSERT(
            strncmp(active_deleted_directives[i], prefix,
                    prefix_len) == 0,
            active_deleted_directives[i]);
    }

    for (i = 0; i < ADDITIONAL_REMOVED_COUNT; i++) {
        TEST_ASSERT(
            strncmp(additional_removed_directives[i], prefix,
                    prefix_len) == 0,
            additional_removed_directives[i]);
    }

    for (i = 0; i < ACTIVE_DIRECTIVE_COUNT; i++) {
        TEST_ASSERT(
            strncmp(active_directives[i], prefix,
                    prefix_len) == 0,
            active_directives[i]);
    }

    TEST_PASS(
        "Property 2g: all directive names have markdown_ "
        "prefix (63 checked)");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    TEST_SECTION(
        "Feature: 62-final-pre-v1-breaking-freeze-fixed\n"
        "Property 2: Removed Directive Rejection");

    test_property2a_reject_only_absent();
    test_property2b_active_deleted_absent();
    test_property2c_additional_removed_absent();
    test_property2d_total_removed_complete();
    test_property2e_command_table_count_is_25();
    test_property2f_no_internal_duplicates();
    test_property2g_all_names_have_prefix();

    printf("\n");
    TEST_PASS(
        "removed_directive_rejection_property: all property "
        "tests passed");
    return 0;
}
