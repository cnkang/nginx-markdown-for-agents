/*
 * NGINX Markdown Filter Module - Response Buffer Implementation
 *
 * This file implements the response buffer for accumulating upstream
 * response bodies before conversion. The buffer enforces size limits
 * to prevent resource exhaustion.
 *
 * Requirements: FR-10.1 (Resource Protection)
 */

#include "ngx_http_markdown_filter_module.h"

#define NGX_HTTP_MARKDOWN_BUFFER_INITIAL_CAPACITY (64 * 1024)

static void ngx_http_markdown_buffer_cleanup(void *data);
static ngx_int_t ngx_http_markdown_buffer_ensure_capacity(ngx_http_markdown_buffer_t *buf,
    size_t required);

/*
 * Initialize response buffer
 *
 * Initializes the buffer structure and registers request-pool cleanup.
 * The backing store is allocated lazily on first append (and grows on demand)
 * while enforcing the configured markdown_max_size limit.
 *
 * Parameters:
 *   buf      - Buffer structure to initialize
 *   max_size - Maximum buffer size in bytes (capacity limit)
 *   pool     - NGINX memory pool for allocation
 *
 * Returns:
 *   NGX_OK    - Buffer initialized successfully
 *   NGX_ERROR - Allocation failed or invalid parameters
 *
 * Memory Management:
 *   - Backing store uses ngx_alloc()/ngx_free() for resizable storage
 *   - A request-pool cleanup handler releases the backing store automatically
 *   - Buffer lifetime remains tied to request lifetime
 */
ngx_int_t
ngx_http_markdown_buffer_init(ngx_http_markdown_buffer_t *buf,
                               size_t max_size,
                               ngx_pool_t *pool)
{
    ngx_pool_cleanup_t *cln;

    /* Validate parameters */
    if (buf == NULL || pool == NULL) {
        return NGX_ERROR;
    }

    /* Validate max_size is reasonable (must be > 0) */
    if (max_size == 0) {
        return NGX_ERROR;
    }

    /*
     * Register request-pool cleanup and allocate lazily on first append.
     *
     * Allocating `max_size` eagerly becomes prohibitively expensive when
     * operators intentionally raise the limit to support very large bodies
     * (for example 100MB/1GB validation scenarios).
     */
    cln = ngx_pool_cleanup_add(pool, 0);
    if (cln == NULL) {
        return NGX_ERROR;
    }
    cln->handler = ngx_http_markdown_buffer_cleanup;
    cln->data = buf;

    /* Initialize buffer state */
    buf->data = NULL;
    buf->size = 0;
    buf->capacity = 0;
    buf->max_size = max_size;
    buf->pool = pool;

    return NGX_OK;
}

/*
 * Append data to response buffer
 *
 * Appends data to the buffer while enforcing the size limit. If appending
 * the data would exceed the configured size limit, the operation fails and
 * NGX_ERROR is returned.
 *
 * Parameters:
 *   buf  - Buffer to append to
 *   data - Data to append
 *   len  - Length of data in bytes
 *
 * Returns:
 *   NGX_OK    - Data appended successfully
 *   NGX_ERROR - Would exceed capacity, invalid parameters, or NULL data
 *
 * Size Limit Enforcement:
 *   - Checks if (buf->size + len) > buf->max_size before appending
 *   - Returns NGX_ERROR if limit would be exceeded
 *   - This enforces FR-10.1 (resource protection)
 *
 * Thread Safety:
 *   - Not thread-safe (NGINX single-threaded per request)
 *   - Each request has its own buffer instance
 */
ngx_int_t
ngx_http_markdown_buffer_append(ngx_http_markdown_buffer_t *buf,
                                 u_char *data,
                                 size_t len)
{
    /* Validate parameters */
    if (buf == NULL) {
        return NGX_ERROR;
    }

    /* Handle zero-length append (success, no-op) */
    if (len == 0) {
        return NGX_OK;
    }

    if (data == NULL) {
        return NGX_ERROR;
    }

    /* Check if appending would exceed configured size limit with overflow safety. */
    if (buf->size > buf->max_size || len > (buf->max_size - buf->size)) {
        /* Size limit exceeded - return error to trigger failure strategy */
        return NGX_ERROR;
    }

    if (ngx_http_markdown_buffer_ensure_capacity(buf, buf->size + len) != NGX_OK) {
        return NGX_ERROR;
    }

    /* Append data to buffer */
    ngx_memcpy(buf->data + buf->size, data, len);
    buf->size += len;

    return NGX_OK;
}

ngx_int_t
ngx_http_markdown_buffer_reserve(ngx_http_markdown_buffer_t *buf, size_t capacity_hint)
{
    if (buf == NULL) {
        return NGX_ERROR;
    }

    if (capacity_hint == 0) {
        return NGX_OK;
    }

    if (capacity_hint > buf->max_size) {
        capacity_hint = buf->max_size;
    }

    return ngx_http_markdown_buffer_ensure_capacity(buf, capacity_hint);
}

static ngx_int_t
ngx_http_markdown_buffer_ensure_capacity(ngx_http_markdown_buffer_t *buf, size_t required)
{
    u_char *new_data;
    size_t  new_capacity;

    if (buf == NULL) {
        return NGX_ERROR;
    }

    if (required <= buf->capacity) {
        return NGX_OK;
    }

    if (required > buf->max_size) {
        return NGX_ERROR;
    }

    if (buf->capacity == 0) {
        new_capacity = NGX_HTTP_MARKDOWN_BUFFER_INITIAL_CAPACITY;
        if (new_capacity > buf->max_size) {
            new_capacity = buf->max_size;
        }
        if (new_capacity < required) {
            new_capacity = required;
        }
    } else {
        new_capacity = buf->capacity;
        while (new_capacity < required) {
            if (new_capacity >= buf->max_size) {
                new_capacity = buf->max_size;
                break;
            }

            if (new_capacity > buf->max_size / 2) {
                new_capacity = buf->max_size;
            } else {
                new_capacity *= 2;
            }
        }
    }

    if (new_capacity < required) {
        return NGX_ERROR;
    }

    /*
     * Use ngx_alloc/ngx_free instead of pool allocations for the backing store.
     * This prevents pool growth from retaining superseded buffers during
     * repeated expansions on very large responses.
     */
    new_data = ngx_alloc(new_capacity, buf->pool != NULL ? buf->pool->log : NULL);
    if (new_data == NULL) {
        return NGX_ERROR;
    }

    if (buf->data != NULL && buf->size > 0) {
        ngx_memcpy(new_data, buf->data, buf->size);
        ngx_free(buf->data);
    }

    buf->data = new_data;
    buf->capacity = new_capacity;

    return NGX_OK;
}

static void
ngx_http_markdown_buffer_cleanup(void *data)
{
    ngx_http_markdown_buffer_t *buf;

    buf = data;
    if (buf == NULL) {
        return;
    }

    if (buf->data != NULL) {
        ngx_free(buf->data);
        buf->data = NULL;
    }

    buf->size = 0;
    buf->capacity = 0;
}
