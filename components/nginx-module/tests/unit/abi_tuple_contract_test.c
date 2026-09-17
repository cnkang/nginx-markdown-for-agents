/*
 * Test: abi_tuple_contract
 *
 * C-side authority test for the Rust/C ABI 4-tuple handshake.
 *
 * The Rust-side property suite (`tests/property_abi_handshake.rs`, properties
 * 13a-13e) can only assert a LOCAL reimplementation of the comparison: it
 * builds `x == CONSTANT` expressions from the exported constants and checks
 * them in Rust, so it proves the constants agree but never exercises the
 * comparison the C module actually links.  The authoritative comparison is
 * `ngx_http_markdown_ffi_abi_tuple_matches()` in
 * src/ngx_http_markdown_ffi_layout_check.h, which is header-inline C compiled
 * into this module's translation units.  This test drives that function
 * directly.
 *
 * Contract under test (startup rejection, design 14(b), Requirements
 * LTS-R023): the module evaluates this gate in `preconfiguration()` before it
 * trusts a linked Rust archive, so a stale or foreign archive must be
 * REJECTED and can never silently load.  A mismatch in ANY one of the four
 * legs must fail the handshake, and only an all-four-legs match may pass.
 *
 * Self-contained: includes the layout header by relative path (the same
 * pattern as conversion_impl_base_url_test.c) so the wildcard-derived
 * `unit-<name>` target needs no per-target Makefile flag beyond the shared
 * include of ../../rust-converter/include.
 */

#include <stdio.h>
#include <string.h>

typedef int ngx_int_t;

#include "../../src/ngx_http_markdown_ffi_layout_check.h"

static int g_failures;

#define TEST_ASSERT(condition, message)                                       \
    do {                                                                      \
        if (!(condition)) {                                                   \
            fprintf(stderr, "\n\xe2\x9c\x97 ASSERTION FAILED: %s\n", message); \
            fprintf(stderr, "  at %s:%d\n", __FILE__, __LINE__);              \
            fprintf(stderr, "  condition: %s\n\n", #condition);               \
            g_failures++;                                                     \
        }                                                                     \
    } while (0)

#define TEST_SUBSECTION(name) printf("Test: %s\n", name)
#define TEST_PASS(name)       printf("  \xe2\x9c\x93 %s\n", name)


/*
 * The gate opens ONLY for a complete match of all four legs.
 */
static void
test_full_match_is_the_only_accept(void)
{
    TEST_SUBSECTION("full 4-tuple match is the only accepted input");

    TEST_ASSERT(ngx_http_markdown_ffi_abi_tuple_matches(
                    MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH,
                    MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT),
                "the current 4-tuple must be accepted");

    TEST_PASS("the current 4-tuple is accepted");
}


/*
 * Each leg is independently load-bearing: a mismatch in exactly one leg must
 * fail the handshake.  Written as four explicit cases so a regression that
 * drops a term from the conjunction (for example `return abi == ...;` after a
 * bad merge) cannot pass by way of a coincidental multi-leg match.
 */
static void
test_each_leg_alone_rejects(void)
{
    TEST_SUBSECTION("each leg independently rejects a stale archive");

    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
                    MARKDOWN_ABI_VERSION + 1, MARKDOWN_HEADER_HASH,
                    MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT),
                "numeric ABI mismatch alone must reject");

    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
                    MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH + 1,
                    MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT),
                "header-hash mismatch alone must reject");

    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
                    MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH,
                    MARKDOWN_SYMBOL_SET_HASH + 1, MARKDOWN_LAYOUT_FINGERPRINT),
                "symbol-set-hash mismatch alone must reject");

    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
                    MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH,
                    MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT + 1),
                "layout-fingerprint mismatch alone must reject");

    TEST_PASS("each of the four legs is independently load-bearing");
}


/*
 * The concrete "old binary" case: the immediately prior incompatible ABI (2)
 * reporting the CURRENT header/symbol/layout fingerprints must not pass.
 * This is the realistic stale-archive scenario — a library built from the
 * previous release where only the numeric version moved.
 */
static void
test_prior_abi_with_current_fingerprints_rejects(void)
{
    const uint32_t  prior_abi = MARKDOWN_ABI_VERSION - 1;

    TEST_SUBSECTION("prior ABI with current fingerprints is rejected");

    TEST_ASSERT(MARKDOWN_ABI_VERSION == 3,
                "this release must be ABI version 3 (prior incompatible ABI = 2)");
    TEST_ASSERT(prior_abi == 2,
                "the prior incompatible ABI under test must be 2");

    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
                    prior_abi, MARKDOWN_HEADER_HASH,
                    MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT),
                "old ABI 2 with current fingerprints must be rejected at startup");

    TEST_PASS("the old-binary case cannot silently load");
}


/*
 * Multiple simultaneous mismatches must still reject (guards against a
 * partial-fix where one leg was restored and another left stale), and the
 * all-zeros "no handshake at all" input must reject as well — a NULL-ish
 * report from a foreign archive must never be read as a match.
 */
static void
test_compound_and_degenerate_rejections(void)
{
    TEST_SUBSECTION("compound and degenerate inputs are rejected");

    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
                    MARKDOWN_ABI_VERSION + 1, MARKDOWN_HEADER_HASH + 1,
                    MARKDOWN_SYMBOL_SET_HASH + 1,
                    MARKDOWN_LAYOUT_FINGERPRINT + 1),
                "all four legs wrong must reject");

    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
                    MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH + 1,
                    MARKDOWN_SYMBOL_SET_HASH + 1,
                    MARKDOWN_LAYOUT_FINGERPRINT),
                "two wrong legs must reject");

    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(0, 0, 0, 0),
                "an all-zero report from a foreign archive must reject");

    TEST_PASS("compound and degenerate inputs are rejected");
}


/*
 * The numeric-only helper must agree with the tuple helper on the ABI leg:
 * a value the tuple gate rejects for its numeric leg is also rejected by
 * ngx_http_markdown_ffi_abi_matches(), and the reverse.  A divergence means
 * one of the two gates would admit an archive the other refuses.
 */
static void
test_numeric_helper_agrees_with_tuple_leg(void)
{
    TEST_SUBSECTION("numeric helper agrees with the tuple's ABI leg");

    TEST_ASSERT(ngx_http_markdown_ffi_abi_matches(MARKDOWN_ABI_VERSION) == 1,
                "numeric helper accepts the current ABI version");
    TEST_ASSERT(ngx_http_markdown_ffi_abi_matches(MARKDOWN_ABI_VERSION - 1) == 0,
                "numeric helper rejects the prior ABI version");

    /* Agreement, evaluated against the tuple gate with the other three legs
     * held at their matching values. */
    TEST_ASSERT(ngx_http_markdown_ffi_abi_matches(MARKDOWN_ABI_VERSION)
                == ngx_http_markdown_ffi_abi_tuple_matches(
                       MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH,
                       MARKDOWN_SYMBOL_SET_HASH,
                       MARKDOWN_LAYOUT_FINGERPRINT),
                "numeric and tuple gates must agree on the ABI leg (accept case)");
    TEST_ASSERT(ngx_http_markdown_ffi_abi_matches(MARKDOWN_ABI_VERSION - 1)
                == ngx_http_markdown_ffi_abi_tuple_matches(
                       MARKDOWN_ABI_VERSION - 1, MARKDOWN_HEADER_HASH,
                       MARKDOWN_SYMBOL_SET_HASH,
                       MARKDOWN_LAYOUT_FINGERPRINT),
                "numeric and tuple gates must agree on the ABI leg (reject case)");

    TEST_PASS("numeric and tuple gates agree on the ABI leg");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("abi_tuple_contract Tests\n");
    printf("========================================\n");

    test_full_match_is_the_only_accept();
    test_each_leg_alone_rejects();
    test_prior_abi_with_current_fingerprints_rejects();
    test_compound_and_degenerate_rejections();
    test_numeric_helper_agrees_with_tuple_leg();

    if (g_failures != 0) {
        fprintf(stderr, "\n%d assertion(s) failed\n", g_failures);
        return 1;
    }

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
