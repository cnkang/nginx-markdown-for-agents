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

#define ngx_string(str)     { sizeof(str) - 1, (u_char *) str }
#define NGX_LOG_ERR         1
#define NGX_LOG_WARN        2
#define ngx_log_error(level, log, err, fmt, ...) (void)(log)

#endif
