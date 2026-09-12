#ifndef _NGX_CONFIG_H_INCLUDED_
#define _NGX_CONFIG_H_INCLUDED_

/* Marks the unit-test stub headers.  Stubs deliberately omit parts of NGINX's
 * API surface (for example the HTTP variable API), so module headers can offer
 * a stub-safe path where a test build cannot provide one. */
#define NGX_HTTP_MARKDOWN_TEST_STUBS 1

#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>
#include <string.h>

typedef unsigned char   u_char;
typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef intptr_t        ngx_flag_t;

#ifndef ngx_inline
#define ngx_inline inline
#endif

#endif
