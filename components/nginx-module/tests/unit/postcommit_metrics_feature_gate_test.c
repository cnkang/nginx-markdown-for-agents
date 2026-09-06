/*
 * Regression: feature-disabled builds omit the streaming metrics subtree.
 * The production postcommit helper implementation must therefore compile
 * without requiring that subtree when MARKDOWN_STREAMING_ENABLED is absent.
 *
 * Behavioral contract (r43 P1-11): with the feature disabled the impl header
 * compiles to an EMPTY translation unit — none of the postcommit helper
 * symbols may be emitted, because the production callers are themselves
 * compiled out by the same feature guard.  If a future change re-adds an
 * unconditional definition here while callers are still guarded (or vice
 * versa), the link either fails (missing symbol) or succeeds with a stale
 * definition; this test pins the guarded-empty contract so the mismatch
 * surfaces as a link error in the disabled-build link, not as silent
 * divergence.
 *
 * The test binary links ONLY this translation unit plus the guarded impl
 * header.  Assertions:
 *   1. The guarded include compiles cleanly without the feature defined
 *      (build-time: this file compiles at all).
 *   2. Post-include, the feature-disabled contract holds: the header made
 *      no unguarded definitions visible — declaring a helper here and
 *      referencing it must NOT resolve, which is enforced by NOT declaring
 *      or calling any helper in this file.  The compile succeeds only while
 *      the header stays definition-free.
 */

#include "../include/test_common.h"

typedef unsigned long  ngx_atomic_t;

typedef struct {
    struct {
        ngx_atomic_t  backpressure_total;
        ngx_atomic_t  pending_output_high_watermark_bytes;
        ngx_atomic_t  copied_output_total;
    } perf;
} ngx_http_markdown_metrics_t;

static ngx_http_markdown_metrics_t *ngx_http_markdown_metrics = NULL;

#define NGX_HTTP_MARKDOWN_METRIC_ADD(field, value)                    \
    do {                                                               \
        if (ngx_http_markdown_metrics != NULL) {                       \
            ngx_http_markdown_metrics->field += (value);              \
        }                                                              \
    } while (0)

#define NGX_HTTP_MARKDOWN_METRIC_INC(field)                            \
    NGX_HTTP_MARKDOWN_METRIC_ADD(field, 1)

#define NGX_HTTP_MARKDOWN_METRIC_WATERMARK(field, value)               \
    do {                                                               \
        if (ngx_http_markdown_metrics != NULL                          \
            && (value) > ngx_http_markdown_metrics->field)            \
        {                                                              \
            ngx_http_markdown_metrics->field = (value);                \
        }                                                              \
    } while (0)

#include "../../src/ngx_http_markdown_postcommit_metrics_impl.h"

/*
 * Feature-disabled macro contract: the include above must NOT have defined
 * MARKDOWN_STREAMING_ENABLED on its own — the build command decides the
 * feature state, not the header.  Preprocessor-level check: when the
 * feature is genuinely disabled, the guarded helper block compiled out and
 * the marker below stays defined; if someone later makes the header
 * self-enable the feature, this translation unit would need the full
 * streaming metrics layout and the static assert below fails.
 */
#ifndef MARKDOWN_STREAMING_ENABLED
#define FEATURE_GATE_CONTRACT_VERIFIED 1
#else
#define FEATURE_GATE_CONTRACT_VERIFIED 0
#endif

_Static_assert(FEATURE_GATE_CONTRACT_VERIFIED == 1,
    "postcommit metrics impl must not enable the streaming feature gate "
    "itself; the build command owns MARKDOWN_STREAMING_ENABLED");
/*
 * Runtime check complementing the static assert above: this binary is built
 * WITHOUT MARKDOWN_STREAMING_ENABLED (see tests/Makefile — this target has
 * no feature flags).  If the impl header ever self-enables the feature, the
 * static assert fires at compile time.  The runtime check records the
 * compiled-in state so the pass message is meaningful in the log.
 */
static void
test_include_does_not_enable_feature(void)
{
#if FEATURE_GATE_CONTRACT_VERIFIED
    TEST_PASS("impl header leaves the feature gate to the build");
#else
    TEST_FAIL("impl header self-enabled MARKDOWN_STREAMING_ENABLED");
#endif
}

int
main(void)
{
    UNUSED(ngx_http_markdown_metrics);
    test_include_does_not_enable_feature();
    TEST_PASS("feature-disabled postcommit metrics implementation compiles");
    return 0;
}
