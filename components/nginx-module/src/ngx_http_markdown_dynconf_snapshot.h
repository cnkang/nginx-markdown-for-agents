/*
 * NGINX Markdown Filter Module - Dynconf Snapshot Introspection
 *
 * Provides a mechanism to query the current dynconf active_snapshot,
 * returning all configuration item names and their current values
 * serialized as JSON key-value pairs.
 *
 * Used by the diagnostics endpoint (E01.3) to expose the current
 * configuration state for operational introspection.
 *
 * Requirement: REQ-0700-OPERABILITY-003
 * Risk Pack: dynamic-config-hot-reload
 * Rules: 34, 35
 */

#ifndef _NGX_HTTP_MARKDOWN_DYNCONF_SNAPSHOT_H_INCLUDED_
#define _NGX_HTTP_MARKDOWN_DYNCONF_SNAPSHOT_H_INCLUDED_

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

#include "ngx_http_markdown_filter_module.h"


/*
 * Maximum buffer size for the config snapshot JSON output.
 * Sized to accommodate all configuration items with generous
 * padding for key names, values, and JSON formatting.
 */
#define NGX_HTTP_MARKDOWN_SNAPSHOT_BUF_SIZE  4096


/*
 * Serialize the current location configuration to JSON format.
 *
 * Takes the current location config and serializes all dynconf-relevant
 * configuration fields into a JSON object.  The output is suitable for
 * embedding in the diagnostics endpoint response.
 *
 * Handles all field types:
 *   - Flags: serialized as "on" or "off"
 *   - Strings: serialized as quoted string values
 *   - Sizes: serialized as numeric byte values (string representation)
 *   - Numbers: serialized as numeric values (string representation)
 *   - Enums: serialized as human-readable string values
 *   - Milliseconds: serialized as numeric ms values (string representation)
 *
 * The returned buffer is allocated from the provided pool and contains
 * a JSON object fragment (without outer braces) suitable for embedding
 * in a larger JSON document.
 *
 * Parameters:
 *   pool - Memory pool for buffer allocation (request or cycle pool)
 *   conf - Current location configuration to serialize
 *   out_buf  - [out] Pointer to the start of the JSON output
 *   out_len  - [out] Length of the JSON output in bytes
 *
 * Returns:
 *   NGX_OK on success, NGX_ERROR on allocation failure
 */
ngx_int_t ngx_http_markdown_dynconf_snapshot_to_json(
    ngx_pool_t *pool,
    const ngx_http_markdown_conf_t *conf,
    u_char **out_buf,
    size_t *out_len);


#endif /* _NGX_HTTP_MARKDOWN_DYNCONF_SNAPSHOT_H_INCLUDED_ */
