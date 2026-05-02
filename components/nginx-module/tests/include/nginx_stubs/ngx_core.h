#ifndef _NGX_CORE_H_INCLUDED_
#define _NGX_CORE_H_INCLUDED_

#include "ngx_config.h"

typedef struct {
    size_t      len;
    u_char     *data;
} ngx_str_t;

typedef ngx_uint_t ngx_msec_t;
typedef int ngx_atomic_t;

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

typedef struct {
    ngx_msec_t    start_ms;
    ngx_msec_t    end_ms;
    ngx_uint_t    attr_count;
    ngx_uint_t    exported;
} ngx_http_markdown_otel_span_t;

#define ngx_string(str)     { sizeof(str) - 1, (u_char *) str }
#define NGX_LOG_ERR         1
#define NGX_LOG_WARN        2
#define ngx_log_error(level, log, err, fmt, ...) (void)(log)

#define ngx_memcmp(s1, s2, n)  memcmp(s1, s2, n)
#define ngx_rbt_red(node)      ((node)->color = 1)
#define ngx_rbt_black(node)    ((node)->color = 0)
#define ngx_rbtree_init(tree, s, i)  \
    do {                             \
        (tree)->root = (s);          \
        (tree)->sentinel = *(s);     \
        (tree)->insert = (i);        \
    } while (0)

#endif
