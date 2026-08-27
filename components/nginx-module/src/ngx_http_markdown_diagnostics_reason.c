/*
 * NGINX Markdown Filter Module - Diagnostics Reason Mapping
 *
 * Keeps the bounded length-matching adapter in a small standalone translation
 * unit so it can be exercised without the HTTP diagnostics handler's
 * request-path dependencies. Canonical names come from the generated reason
 * registry header; this file owns no mapping data.
 */

#include <ngx_config.h>
#include <ngx_core.h>
#include <string.h>

#include "ngx_http_markdown_diagnostics.h"
#define MARKDOWN_REASON_META_DEFINE
#include "markdown_reason_meta.h"

/*
 * Convert a diagnostics reason string to its canonical
 * ReasonCode discriminant (reason_registry.toml is the source; the generated
 * Rust enum and C metadata provide its projections).
 *
 * The decision path carries pre-formatted reason strings; the ring stores a
 * compact numeric code. Unknown or noncanonical strings map to -1 so the
 * consumer can render them distinctly without guessing.
 */
ngx_int_t
ngx_http_markdown_diagnostics_reason_to_code(const u_char *reason,
    size_t reason_len)
{
    const markdown_reason_meta_t   *meta;
    size_t                          name_len;
    if (reason == NULL && reason_len != 0) {
        return -1;
    }

    for (size_t i = 0; i < MARKDOWN_REASON_META_COUNT; i++) {
        meta = &markdown_reason_meta[i];
        name_len = strlen(meta->key);

        if (reason_len == name_len
            && (reason_len == 0
                || strncmp((const char *) reason, meta->key,
                           reason_len) == 0))
        {
            return (ngx_int_t) i;
        }
    }

    return -1;
}
