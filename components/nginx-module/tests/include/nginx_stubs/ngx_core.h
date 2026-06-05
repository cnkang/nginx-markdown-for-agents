#ifndef _NGX_CORE_H_INCLUDED_
#define _NGX_CORE_H_INCLUDED_

#include "ngx_config.h"

typedef struct {
    size_t      len;
    u_char     *data;
} ngx_str_t;

typedef ngx_uint_t ngx_msec_t;
typedef int ngx_atomic_t;
typedef ngx_uint_t ngx_atomic_uint_t;
typedef ngx_int_t ngx_atomic_int_t;

typedef struct ngx_http_request_s ngx_http_request_t;
typedef struct ngx_module_s       ngx_module_t;
typedef struct ngx_command_s      ngx_command_t;
typedef struct ngx_conf_s         ngx_conf_t;
typedef struct ngx_log_s          ngx_log_t;
typedef struct ngx_pool_s         ngx_pool_t;
typedef struct ngx_shm_zone_s     ngx_shm_zone_t;
typedef struct ngx_chain_s        ngx_chain_t;
typedef struct ngx_array_s        ngx_array_t;
typedef struct ngx_buf_s          ngx_buf_t;

struct ngx_buf_s {
    u_char     *pos;
    u_char     *last;
    u_char     *start;
    u_char     *end;
    unsigned    temporary:1;
    unsigned    memory:1;
    unsigned    last_buf:1;
    unsigned    last_in_chain:1;
    unsigned    flush:1;
    unsigned    sync:1;
};
typedef struct ngx_http_complex_value_s ngx_http_complex_value_t;

typedef struct ngx_rbtree_node_s  ngx_rbtree_node_t;
typedef struct ngx_rbtree_s       ngx_rbtree_t;

struct ngx_rbtree_node_s {
    ngx_uint_t     key;
    ngx_rbtree_node_t  *left;
    ngx_rbtree_node_t  *right;
    ngx_rbtree_node_t  *parent;
    u_char              color;
    u_char              data;
};

struct ngx_rbtree_s {
    ngx_rbtree_node_t  *root;
    ngx_rbtree_node_t   sentinel;
    void               (*insert)(ngx_rbtree_node_t *root,
                                 ngx_rbtree_node_t *node,
                                 ngx_rbtree_node_t *sentinel);
};

typedef struct ngx_http_markdown_otel_span_s {
    ngx_msec_t    start_ms;
    ngx_msec_t    end_ms;
    int64_t       start_epoch_nano;
    int64_t       end_epoch_nano;
    ngx_uint_t    attr_count;
    ngx_uint_t    exported;
    u_char        trace_id[33];
    u_char        span_id[17];
    u_char        parent_span_id[17];
    ngx_uint_t    trace_flags;
    ngx_uint_t    has_parent;
} ngx_http_markdown_otel_span_t;

#define ngx_string(str)     { sizeof(str) - 1, (u_char *) str }

/* NGINX return codes — guarded so per-test overrides still work */
#ifndef NGX_OK
#define NGX_OK          0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR      -1
#endif
#ifndef NGX_AGAIN
#define NGX_AGAIN      -2
#endif
#ifndef NGX_DONE
#define NGX_DONE       -4
#endif
#ifndef NGX_DECLINED
#define NGX_DECLINED   -5
#endif

#define NGX_LOG_ERR         1
#define NGX_LOG_WARN        2

static ngx_inline void
ngx_test_log_ignore(const char *fmt, ...)
{
    (void) fmt;
}

#define ngx_log_error(level, log, err, fmt, ...)                                     \
    do {                                                                              \
        (void) (level);                                                               \
        (void) (log);                                                                 \
        (void) (err);                                                                 \
        if (0) {                                                                      \
            ngx_test_log_ignore((fmt), ##__VA_ARGS__);                               \
        }                                                                             \
    } while (0)

#define ngx_tolower(c)  (u_char) (((c) >= 'A' && (c) <= 'Z') ? ((c) | 0x20) : (c))
#define ngx_toupper(c)  (u_char) (((c) >= 'a' && (c) <= 'z') ? ((c) & ~0x20) : (c))
#define ngx_memcmp(s1, s2, n)  memcmp(s1, s2, n)
#define ngx_random()           rand()
#define ngx_rbt_red(node)      ((node)->color = 1)
#define ngx_rbt_black(node)    ((node)->color = 0)
#define ngx_rbtree_init(tree, s, i)  \
    do {                             \
        (tree)->root = (s);          \
        (tree)->sentinel = *(s);     \
        (tree)->insert = (i);        \
    } while (0)

#endif
