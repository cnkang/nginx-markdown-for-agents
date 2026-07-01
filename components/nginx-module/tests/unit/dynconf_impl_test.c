/*
 * Test: dynconf_impl
 */

#include "../include/test_common.h"

#include <ctype.h>
#include <stdarg.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>

#define MARKDOWN_STREAMING_ENABLED 1

#include "../../src/ngx_http_markdown_filter_module.h"

#ifndef NGX_OK
#define NGX_OK       0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR   -1
#endif
#ifndef NGX_DECLINED
#define NGX_DECLINED -2
#endif
#ifndef NGX_DONE
#define NGX_DONE    -4
#endif
#ifndef NGX_AGAIN
#define NGX_AGAIN   -5
#endif

#ifndef NGX_LOG_ERR
#define NGX_LOG_ERR    1
#endif
#ifndef NGX_LOG_WARN
#define NGX_LOG_WARN   2
#endif
#ifndef NGX_LOG_INFO
#define NGX_LOG_INFO   3
#endif
#ifndef NGX_LOG_DEBUG
#define NGX_LOG_DEBUG  4
#endif

typedef intptr_t ngx_err_t;

typedef struct ngx_cycle_s     ngx_cycle_t;
typedef struct ngx_connection_s ngx_connection_t;

struct ngx_module_s {
    int dummy;
};

struct ngx_pool_s {
    int dummy;
};

struct ngx_log_s {
    int dummy;
};

struct ngx_cycle_s {
    ngx_pool_t *pool;
    ngx_log_t  *log;
};

struct ngx_connection_s {
    ngx_log_t *log;
};

struct ngx_http_request_s {
    ngx_connection_t *connection;
};

struct ngx_http_complex_value_s {
    ngx_str_t  value;
    ngx_int_t  eval_rc;
};

ngx_module_t ngx_http_markdown_filter_module;
ngx_str_t ngx_http_markdown_metrics_shm_name = ngx_string("");
ngx_shm_zone_t *ngx_http_markdown_metrics_shm_zone = NULL;

#define ngx_memzero(p, n)   memset((p), 0, (n))
#define ngx_memcpy(dst, src, n) memcpy((dst), (src), (n))
#define ngx_memmove(dst, src, n) memmove((dst), (src), (n))

static ngx_int_t
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        u_char c1 = (u_char) tolower((unsigned char) s1[i]);
        u_char c2 = (u_char) tolower((unsigned char) s2[i]);
        if (c1 != c2) {
            return (ngx_int_t) c1 - (ngx_int_t) c2;
        }
    }
    return 0;
}

#undef ngx_log_error
static void
test_dynconf_log_ignore(const char *fmt, ...)
{
    UNUSED(fmt);
}

#define ngx_log_error(level, log, err, fmt, ...)                                     \
    do {                                                                              \
        UNUSED(level);                                                                \
        UNUSED(log);                                                                  \
        UNUSED(err);                                                                  \
        if (0) {                                                                      \
            test_dynconf_log_ignore((fmt), ##__VA_ARGS__);                           \
        }                                                                             \
    } while (0)

#define NGX_MAX_PATH 1024

typedef time_t ngx_mtime_t;

#define ngx_file_info_t       struct stat
#define ngx_file_info(name, fi) stat((const char *)(name), (fi))
#define ngx_file_mtime(fi)    ((fi)->st_mtime)
#define NGX_FILE_ERROR        (-1)

typedef int ngx_fd_t;
#define NGX_INVALID_FILE     (-1)

#define NGX_FILE_RDONLY      0
#define NGX_FILE_OPEN        0

static ngx_fd_t
ngx_open_file(u_char *name, int mode, int create, int access)
{
    UNUSED(mode);
    UNUSED(create);
    UNUSED(access);
    return open((const char *) name, O_RDONLY);
}

#define ngx_close_file(fd) close(fd)

static ssize_t
ngx_read_fd(ngx_fd_t fd, void *buf, size_t size)
{
    return read(fd, buf, size);
}

static void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return calloc(1, size);
}

static void *
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return malloc(size);
}

static ssize_t
ngx_parse_size(ngx_str_t *line)
{
    ssize_t     num;
    u_char     *last;

    num = 0;
    last = line->data + line->len;

    for (u_char *p = line->data; p < last; p++) {
        if (*p >= '0' && *p <= '9') {
            num = num * 10 + (*p - '0');
        } else {
            switch (*p) {
            case 'k': case 'K': return num * 1024;
            case 'm': case 'M': return num * 1024 * 1024;
            case 'g': case 'G': return num * 1024 * 1024 * 1024;
            default: return NGX_ERROR;
            }
        }
    }
    return num;
}

typedef struct ngx_event_s ngx_event_t;

struct ngx_event_s {
    void        (*handler)(ngx_event_t *ev);
    void         *data;
    ngx_log_t   *log;
    unsigned      timer_set;
};

static void
ngx_add_timer(ngx_event_t *ev, ngx_msec_t timer)
{
    UNUSED(ev);
    UNUSED(timer);
}

static void
ngx_del_timer(ngx_event_t *ev)
{
    UNUSED(ev);
}

#include "../../src/ngx_http_markdown_dynconf_impl.h"

#ifndef NGX_MAX_SIZE_T_VALUE
#define NGX_MAX_SIZE_T_VALUE  ((size_t) -1)
#endif

static ngx_pool_t  g_pool;
static ngx_log_t   g_log;
static ngx_cycle_t g_cycle = { &g_pool, &g_log };

static void
set_ngx_str(ngx_str_t *dst, const char *src)
{
    dst->data = (u_char *) src;
    dst->len = strlen(src);
}

static void
test_effective_conf_helpers_smoke(void)
{
    ngx_http_markdown_conf_t            conf;
    ngx_http_markdown_dynconf_snapshot_t snap;
    ngx_http_markdown_effective_conf_t   eff;

    TEST_SUBSECTION("effective_conf helpers smoke");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&snap, sizeof(snap));
    ngx_memzero(&eff, sizeof(eff));

    conf.enabled = 0;
    conf.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
    conf.advanced.prune_noise = 0;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_WARN;
    conf.advanced.memory_budget = 8 * 1024 * 1024;
#ifdef MARKDOWN_STREAMING_ENABLED
    conf.stream.budget = 4 * 1024 * 1024;
#endif

    snap.valid = 1;
    snap.enabled = 1;
    snap.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_COMPLEX;
    snap.prune_noise = 1;
    snap.log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;
    snap.memory_budget = 16 * 1024 * 1024;
#ifdef MARKDOWN_STREAMING_ENABLED
    snap.streaming_budget = 12 * 1024 * 1024;
#endif

    ngx_http_markdown_build_effective_conf(&eff, &snap, &conf);
    TEST_ASSERT(ngx_http_markdown_effective_enabled(&eff, &conf) == 1,
                "enabled should come from snapshot");
    TEST_ASSERT(ngx_http_markdown_effective_enabled_source(&eff, &conf)
                    == NGX_HTTP_MARKDOWN_ENABLED_COMPLEX,
                "enabled_source should come from snapshot");
    TEST_ASSERT(ngx_http_markdown_effective_prune_noise(&eff, &conf) == 1,
                "prune_noise should come from snapshot");
    TEST_ASSERT(ngx_http_markdown_effective_log_verbosity(&eff, &conf)
                    == NGX_HTTP_MARKDOWN_LOG_DEBUG,
                "log_verbosity should come from snapshot");
    TEST_ASSERT(ngx_http_markdown_effective_memory_budget(&eff, &conf)
                    == 16 * 1024 * 1024,
                "memory_budget should come from snapshot");
#ifdef MARKDOWN_STREAMING_ENABLED
    TEST_ASSERT(ngx_http_markdown_effective_streaming_budget(&eff, &conf)
                    == 12 * 1024 * 1024,
                "streaming_budget should come from snapshot");
#endif

    ngx_http_markdown_build_effective_conf(&eff, NULL, &conf);
    TEST_ASSERT(ngx_http_markdown_effective_enabled(&eff, &conf) == 0,
                "enabled should fall back to conf");
    TEST_ASSERT(ngx_http_markdown_effective_enabled_source(&eff, &conf)
                    == NGX_HTTP_MARKDOWN_ENABLED_STATIC,
                "enabled_source should fall back to conf");
    TEST_ASSERT(ngx_http_markdown_effective_prune_noise(&eff, &conf) == 0,
                "prune_noise should fall back to conf");
    TEST_ASSERT(ngx_http_markdown_effective_log_verbosity(&eff, &conf)
                    == NGX_HTTP_MARKDOWN_LOG_WARN,
                "log_verbosity should fall back to conf");
    TEST_ASSERT(ngx_http_markdown_effective_memory_budget(&eff, &conf)
                    == 8 * 1024 * 1024,
                "memory_budget should fall back to conf");
#ifdef MARKDOWN_STREAMING_ENABLED
    TEST_ASSERT(ngx_http_markdown_effective_streaming_budget(&eff, &conf)
                    == 4 * 1024 * 1024,
                "streaming_budget should fall back to conf");
#endif

    TEST_PASS("effective_conf helper symbols exercised");
}

static void
test_parse_line_blank(void)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   rc;

    u_char line[] = "";
    rc = ngx_http_markdown_dynconf_parse_line(line, 0, &key, &value, &value_len);
    TEST_ASSERT(rc == NGX_DECLINED, "blank line returns DECLINED");
    TEST_PASS("parse_line: blank line");
}

static void
test_parse_line_comment(void)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   rc;

    u_char line[] = "# this is a comment";
    rc = ngx_http_markdown_dynconf_parse_line(line, sizeof(line) - 1,
                                              &key, &value, &value_len);
    TEST_ASSERT(rc == NGX_DECLINED, "comment returns DECLINED");
    TEST_PASS("parse_line: comment");
}

static void
test_parse_line_whitespace_only(void)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   rc;

    u_char line[] = "   \t  ";
    rc = ngx_http_markdown_dynconf_parse_line(line, sizeof(line) - 1,
                                              &key, &value, &value_len);
    TEST_ASSERT(rc == NGX_DECLINED, "whitespace-only returns DECLINED");
    TEST_PASS("parse_line: whitespace only");
}

static void
test_parse_line_filter_on(void)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   rc;

    u_char line[] = "markdown_filter=on";
    rc = ngx_http_markdown_dynconf_parse_line(line, sizeof(line) - 1,
                                              &key, &value, &value_len);
    TEST_ASSERT(rc == NGX_OK, "markdown_filter=on parses OK");
    TEST_ASSERT(key == NGX_HTTP_MARKDOWN_DYNCONF_KEY_FILTER,
                "key is FILTER");
    TEST_ASSERT(value_len == 2, "value length is 2");
    TEST_ASSERT(ngx_strncasecmp(value, (u_char *) "on", 2) == 0,
                "value is 'on'");
    TEST_PASS("parse_line: markdown_filter=on");
}

static void
test_parse_line_filter_off(void)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   rc;

    u_char line[] = "markdown_filter=off";
    rc = ngx_http_markdown_dynconf_parse_line(line, sizeof(line) - 1,
                                              &key, &value, &value_len);
    TEST_ASSERT(rc == NGX_OK, "markdown_filter=off parses OK");
    TEST_ASSERT(key == NGX_HTTP_MARKDOWN_DYNCONF_KEY_FILTER, "key is FILTER");
    TEST_PASS("parse_line: markdown_filter=off");
}

static void
test_parse_line_prune_noise(void)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   rc;

    u_char line[] = "prune_noise=on";
    rc = ngx_http_markdown_dynconf_parse_line(line, sizeof(line) - 1,
                                              &key, &value, &value_len);
    TEST_ASSERT(rc == NGX_OK, "prune_noise=on parses OK");
    TEST_ASSERT(key == NGX_HTTP_MARKDOWN_DYNCONF_KEY_PRUNE_NOISE,
                "key is PRUNE_NOISE");
    TEST_PASS("parse_line: prune_noise=on");
}

static void
test_parse_line_log_verbosity(void)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   rc;

    u_char line[] = "log_verbosity=debug";
    rc = ngx_http_markdown_dynconf_parse_line(line, sizeof(line) - 1,
                                              &key, &value, &value_len);
    TEST_ASSERT(rc == NGX_OK, "log_verbosity=debug parses OK");
    TEST_ASSERT(key == NGX_HTTP_MARKDOWN_DYNCONF_KEY_LOG_VERBOSITY,
                "key is LOG_VERBOSITY");
    TEST_PASS("parse_line: log_verbosity=debug");
}

static void
test_parse_line_streaming_budget(void)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   rc;

    u_char line[] = "streaming_budget=4m";
    rc = ngx_http_markdown_dynconf_parse_line(line, sizeof(line) - 1,
                                              &key, &value, &value_len);
    TEST_ASSERT(rc == NGX_OK, "streaming_budget=4m parses OK");
    TEST_ASSERT(key == NGX_HTTP_MARKDOWN_DYNCONF_KEY_STREAMING_BUDGET,
                "key is STREAMING_BUDGET");
    TEST_PASS("parse_line: streaming_budget=4m");
}

static void
test_parse_line_memory_budget(void)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   rc;

    u_char line[] = "memory_budget=128k";
    rc = ngx_http_markdown_dynconf_parse_line(line, sizeof(line) - 1,
                                              &key, &value, &value_len);
    TEST_ASSERT(rc == NGX_OK, "memory_budget=128k parses OK");
    TEST_ASSERT(key == NGX_HTTP_MARKDOWN_DYNCONF_KEY_MEMORY_BUDGET,
                "key is MEMORY_BUDGET");
    TEST_PASS("parse_line: memory_budget=128k");
}

static void
test_parse_line_schema_version(void)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   rc;

    u_char line[] = "schema_version=0.9";
    rc = ngx_http_markdown_dynconf_parse_line(line, sizeof(line) - 1,
                                              &key, &value, &value_len);
    TEST_ASSERT(rc == NGX_OK, "schema_version=0.9 parses OK");
    TEST_ASSERT(key == NGX_HTTP_MARKDOWN_DYNCONF_KEY_SCHEMA_VERSION,
                "key is SCHEMA_VERSION");
    TEST_ASSERT(value_len == 3, "value length is 3");
    TEST_PASS("parse_line: schema_version=0.9");
}

static void
test_parse_line_unknown_key(void)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   rc;

    u_char line[] = "unknown_key=value";
    rc = ngx_http_markdown_dynconf_parse_line(line, sizeof(line) - 1,
                                              &key, &value, &value_len);
    TEST_ASSERT(rc == NGX_ERROR, "unknown key returns ERROR (atomic reload rejection)");
    TEST_PASS("parse_line: unknown key");
}

static void
test_parse_line_no_equals(void)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   rc;

    u_char line[] = "justakeynovalue";
    rc = ngx_http_markdown_dynconf_parse_line(line, sizeof(line) - 1,
                                              &key, &value, &value_len);
    TEST_ASSERT(rc == NGX_ERROR, "no '=' returns ERROR");
    TEST_PASS("parse_line: no equals sign");
}

static void
test_parse_line_empty_value(void)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   rc;

    u_char line[] = "markdown_filter=";
    rc = ngx_http_markdown_dynconf_parse_line(line, sizeof(line) - 1,
                                              &key, &value, &value_len);
    TEST_ASSERT(rc == NGX_ERROR, "empty value returns ERROR");
    TEST_PASS("parse_line: empty value");
}

static void
test_parse_line_whitespace_around_value(void)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   rc;

    u_char line[] = "markdown_filter = on ";
    rc = ngx_http_markdown_dynconf_parse_line(line, sizeof(line) - 1,
                                              &key, &value, &value_len);
    TEST_ASSERT(rc == NGX_OK, "whitespace around = and value parses OK");
    TEST_ASSERT(key == NGX_HTTP_MARKDOWN_DYNCONF_KEY_FILTER, "key is FILTER");
    TEST_ASSERT(value_len == 2, "trailing whitespace trimmed");
    TEST_PASS("parse_line: whitespace around value");
}

static void
test_parse_line_leading_whitespace(void)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   rc;

    u_char line[] = "  prune_noise=off";
    rc = ngx_http_markdown_dynconf_parse_line(line, sizeof(line) - 1,
                                              &key, &value, &value_len);
    TEST_ASSERT(rc == NGX_OK, "leading whitespace parsed OK");
    TEST_ASSERT(key == NGX_HTTP_MARKDOWN_DYNCONF_KEY_PRUNE_NOISE,
                "key is PRUNE_NOISE");
    TEST_PASS("parse_line: leading whitespace");
}

static void
test_apply_filter_on(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "on";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_FILTER,
                                         val, 2, &g_log);
    TEST_ASSERT(rc == NGX_OK, "apply filter=on returns OK");
    TEST_ASSERT(snapshot.enabled == 1, "enabled set to 1");
    TEST_PASS("apply: markdown_filter=on");
}

static void
test_apply_filter_off(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.enabled = 1;
    snapshot.valid = 1;

    u_char val[] = "off";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_FILTER,
                                         val, 3, &g_log);
    TEST_ASSERT(rc == NGX_OK, "apply filter=off returns OK");
    TEST_ASSERT(snapshot.enabled == 0, "enabled set to 0");
    TEST_PASS("apply: markdown_filter=off");
}

static void
test_apply_filter_invalid(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "maybe";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_FILTER,
                                         val, 5, &g_log);
    TEST_ASSERT(rc == NGX_ERROR, "apply filter=maybe returns ERROR");
    TEST_PASS("apply: markdown_filter=invalid");
}

static void
test_apply_prune_noise_on(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "on";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_PRUNE_NOISE,
                                         val, 2, &g_log);
    TEST_ASSERT(rc == NGX_OK, "apply prune_noise=on returns OK");
    TEST_ASSERT(snapshot.prune_noise == 1, "prune_noise set to 1");
    TEST_PASS("apply: prune_noise=on");
}

static void
test_apply_prune_noise_off(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.prune_noise = 1;
    snapshot.valid = 1;

    u_char val[] = "off";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_PRUNE_NOISE,
                                         val, 3, &g_log);
    TEST_ASSERT(rc == NGX_OK, "apply prune_noise=off returns OK");
    TEST_ASSERT(snapshot.prune_noise == 0, "prune_noise set to 0");
    TEST_PASS("apply: prune_noise=off");
}

static void
test_apply_prune_noise_invalid(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "yes";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_PRUNE_NOISE,
                                         val, 3, &g_log);
    TEST_ASSERT(rc == NGX_ERROR, "apply prune_noise=yes returns ERROR");
    TEST_PASS("apply: prune_noise=invalid");
}

static void
test_apply_log_verbosity_error(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "error";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_LOG_VERBOSITY,
                                         val, 5, &g_log);
    TEST_ASSERT(rc == NGX_OK, "apply log_verbosity=error returns OK");
    TEST_ASSERT(snapshot.log_verbosity == NGX_HTTP_MARKDOWN_LOG_ERROR,
                "set to NGX_HTTP_MARKDOWN_LOG_ERROR");
    TEST_PASS("apply: log_verbosity=error");
}

static void
test_apply_log_verbosity_warn(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "warn";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_LOG_VERBOSITY,
                                         val, 4, &g_log);
    TEST_ASSERT(rc == NGX_OK, "apply log_verbosity=warn returns OK");
    TEST_ASSERT(snapshot.log_verbosity == NGX_HTTP_MARKDOWN_LOG_WARN,
                "set to NGX_HTTP_MARKDOWN_LOG_WARN");
    TEST_PASS("apply: log_verbosity=warn");
}

static void
test_apply_log_verbosity_info(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "info";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_LOG_VERBOSITY,
                                         val, 4, &g_log);
    TEST_ASSERT(rc == NGX_OK, "apply log_verbosity=info returns OK");
    TEST_ASSERT(snapshot.log_verbosity == NGX_HTTP_MARKDOWN_LOG_INFO,
                "set to NGX_HTTP_MARKDOWN_LOG_INFO");
    TEST_PASS("apply: log_verbosity=info");
}

static void
test_apply_log_verbosity_debug(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "debug";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_LOG_VERBOSITY,
                                         val, 5, &g_log);
    TEST_ASSERT(rc == NGX_OK, "apply log_verbosity=debug returns OK");
    TEST_ASSERT(snapshot.log_verbosity == NGX_HTTP_MARKDOWN_LOG_DEBUG,
                "set to NGX_HTTP_MARKDOWN_LOG_DEBUG");
    TEST_PASS("apply: log_verbosity=debug");
}

static void
test_apply_log_verbosity_invalid(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "trace";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_LOG_VERBOSITY,
                                         val, 5, &g_log);
    TEST_ASSERT(rc == NGX_ERROR, "apply log_verbosity=trace returns ERROR");
    TEST_PASS("apply: log_verbosity=invalid");
}

static void
test_apply_filter_on_overrides_complex(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.enabled = 0;
    snapshot.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_COMPLEX;
    snapshot.enabled_complex = (ngx_http_complex_value_t *) 1;
    snapshot.valid = 1;

    u_char val[] = "on";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_FILTER,
                                         val, 2, &g_log);
    TEST_ASSERT(rc == NGX_OK, "apply filter=on with prior complex returns OK");
    TEST_ASSERT(snapshot.enabled == 1, "enabled set to 1");
    TEST_ASSERT(snapshot.enabled_source == NGX_HTTP_MARKDOWN_ENABLED_STATIC,
                "enabled_source overridden to STATIC");
    TEST_ASSERT(snapshot.enabled_complex == NULL,
                "enabled_complex cleared to NULL");
    TEST_PASS("apply: markdown_filter=on overrides complex value");
}

static void
test_apply_filter_off_overrides_complex(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.enabled = 1;
    snapshot.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_COMPLEX;
    snapshot.enabled_complex = (ngx_http_complex_value_t *) 1;
    snapshot.valid = 1;

    u_char val[] = "off";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_FILTER,
                                         val, 3, &g_log);
    TEST_ASSERT(rc == NGX_OK, "apply filter=off with prior complex returns OK");
    TEST_ASSERT(snapshot.enabled == 0, "enabled set to 0");
    TEST_ASSERT(snapshot.enabled_source == NGX_HTTP_MARKDOWN_ENABLED_STATIC,
                "enabled_source overridden to STATIC");
    TEST_ASSERT(snapshot.enabled_complex == NULL,
                "enabled_complex cleared to NULL");
    TEST_PASS("apply: markdown_filter=off overrides complex value");
}

static void
test_apply_streaming_budget(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "4m";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_STREAMING_BUDGET,
                                         val, 2, &g_log);
    TEST_ASSERT(rc == NGX_OK, "apply streaming_budget=4m returns OK");
    TEST_ASSERT(snapshot.streaming_budget == 4 * 1024 * 1024,
                "streaming_budget set to 4MiB");
    TEST_PASS("apply: streaming_budget=4m");
}

static void
test_apply_streaming_budget_invalid(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "abc";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_STREAMING_BUDGET,
                                         val, 3, &g_log);
    TEST_ASSERT(rc == NGX_ERROR, "apply streaming_budget=abc returns ERROR");
    TEST_PASS("apply: streaming_budget=invalid");
}

static void
test_apply_memory_budget(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "128k";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_MEMORY_BUDGET,
                                         val, 4, &g_log);
    TEST_ASSERT(rc == NGX_OK, "apply memory_budget=128k returns OK");
    TEST_ASSERT(snapshot.memory_budget == 128 * 1024,
                "memory_budget set to 128KiB");
    TEST_PASS("apply: memory_budget=128k");
}

static void
test_apply_memory_budget_invalid(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "xyz";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_MEMORY_BUDGET,
                                         val, 3, &g_log);
    TEST_ASSERT(rc == NGX_ERROR, "apply memory_budget=xyz returns ERROR");
    TEST_PASS("apply: memory_budget=invalid");
}

static void
test_apply_default_key(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "1";
    rc = ngx_http_markdown_dynconf_apply(&snapshot, 99,
                                         val, 1, &g_log);
    TEST_ASSERT(rc == NGX_ERROR, "apply unknown key returns ERROR (atomic rejection)");
    TEST_PASS("apply: unknown key -> ERROR");
}

static void
test_check_null_watcher(void)
{
    ngx_int_t rc;

    rc = ngx_http_markdown_dynconf_check(NULL, &g_log);
    TEST_ASSERT(rc == 0, "check NULL watcher returns 0");
    TEST_PASS("check: NULL watcher");
}

static void
test_check_inactive_watcher(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_int_t                            rc;

    memset(&watcher, 0, sizeof(watcher));
    watcher.active = 0;

    rc = ngx_http_markdown_dynconf_check(&watcher, &g_log);
    TEST_ASSERT(rc == 0, "check inactive watcher returns 0");
    TEST_PASS("check: inactive watcher");
}

static void
test_check_file_changed(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_int_t                            rc;
    const char                          *tmpfile;
    ngx_file_info_t                      fi;

    tmpfile = "/tmp/dynconf_test_check_changed.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file");
        fprintf(f, "markdown_filter=on\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    watcher.active = 1;
    set_ngx_str(&watcher.path, tmpfile);

    if (ngx_file_info((const u_char *) tmpfile, &fi) == NGX_FILE_ERROR) {
        TEST_FAIL("stat temp file failed");
    }
    watcher.last_mtime = ngx_file_mtime(&fi) - 1;

    rc = ngx_http_markdown_dynconf_check(&watcher, &g_log);
    TEST_ASSERT(rc == 1, "check detects mtime change");

    rc = ngx_http_markdown_dynconf_check(&watcher, &g_log);
    TEST_ASSERT(rc == 0, "check returns 0 on second call (same mtime)");

    unlink(tmpfile);
    TEST_PASS("check: file changed detection");
}

static void
test_check_path_too_long(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_int_t                            rc;

    memset(&watcher, 0, sizeof(watcher));
    watcher.active = 1;
    watcher.path.len = NGX_MAX_PATH + 1;
    watcher.path.data = (u_char *) "/tmp/placeholder";

    rc = ngx_http_markdown_dynconf_check(&watcher, &g_log);
    TEST_ASSERT(rc == 0, "check path too long returns 0");
    TEST_PASS("check: path too long");
}

static void
test_check_stat_fails(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_int_t                            rc;

    memset(&watcher, 0, sizeof(watcher));
    watcher.active = 1;
    set_ngx_str(&watcher.path, "/tmp/nonexistent_dynconf_file_12345.conf");

    rc = ngx_http_markdown_dynconf_check(&watcher, &g_log);
    TEST_ASSERT(rc == 0, "check stat failure returns 0");
    TEST_PASS("check: stat fails");
}

static void
test_start_null_watcher(void)
{
    ngx_int_t  rc;
    ngx_str_t  path;
    ngx_http_markdown_conf_t  conf;

    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&path, "/tmp/some.conf");
    rc = ngx_http_markdown_dynconf_start(NULL, &g_cycle, &path, &conf, &g_log);
    TEST_ASSERT(rc == NGX_OK, "start NULL watcher returns NGX_OK");
    TEST_PASS("start: NULL watcher");
}

static void
test_start_null_path(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_int_t                            rc;
    ngx_http_markdown_conf_t             conf;

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    rc = ngx_http_markdown_dynconf_start(&watcher, &g_cycle, NULL, &conf, &g_log);
    TEST_ASSERT(rc == NGX_OK, "start NULL path returns NGX_OK");
    TEST_PASS("start: NULL path");
}

static void
test_start_empty_path(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_str_t                            path;
    ngx_int_t                            rc;
    ngx_http_markdown_conf_t             conf;

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    path.data = (u_char *) "";
    path.len = 0;
    rc = ngx_http_markdown_dynconf_start(&watcher, &g_cycle, &path, &conf, &g_log);
    TEST_ASSERT(rc == NGX_OK, "start empty path returns NGX_OK");
    TEST_PASS("start: empty path");
}

static void
test_start_path_too_long(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_str_t                            path;
    ngx_int_t                            rc;
    u_char                               longpath[NGX_MAX_PATH + 2];
    ngx_http_markdown_conf_t             conf;

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    memset(longpath, 'a', sizeof(longpath));
    longpath[sizeof(longpath) - 1] = '\0';
    path.data = longpath;
    path.len = NGX_MAX_PATH + 1;

    rc = ngx_http_markdown_dynconf_start(&watcher, &g_cycle, &path, &conf, &g_log);
    TEST_ASSERT(rc == NGX_ERROR, "start path too long returns ERROR");
    TEST_PASS("start: path too long");
}

static void
test_start_success(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_str_t                            path;
    ngx_int_t                            rc;
    const char                          *tmpfile;
    ngx_http_markdown_conf_t             conf;

    tmpfile = "/tmp/dynconf_test_start.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file for start");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "markdown_filter=on\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    conf.enabled = 1;
    set_ngx_str(&path, tmpfile);

    rc = ngx_http_markdown_dynconf_start(&watcher, &g_cycle, &path, &conf, &g_log);
    TEST_ASSERT(rc == NGX_OK, "start returns NGX_OK");
    TEST_ASSERT(watcher.active == 1, "watcher is active");
    TEST_ASSERT(watcher.timer != NULL, "timer allocated");
    TEST_ASSERT(watcher.path.len == strlen(tmpfile), "path copied");
    TEST_ASSERT(watcher.last_mtime > 0, "mtime recorded");
    TEST_ASSERT(watcher.active_snapshot.valid == 1, "active snapshot valid");
    TEST_ASSERT(watcher.active_snapshot.enabled == 1, "active snapshot has enabled=1");

    free(watcher.path.data);
    free(watcher.timer);
    unlink(tmpfile);
    TEST_PASS("start: success");
}

static void
test_start_stat_fails(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_str_t                            path;
    ngx_int_t                            rc;
    const char                          *tmpfile;
    ngx_http_markdown_conf_t             conf;

    tmpfile = "/tmp/dynconf_test_start_nofile.conf";
    unlink(tmpfile);

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&path, tmpfile);

    rc = ngx_http_markdown_dynconf_start(&watcher, &g_cycle, &path, &conf, &g_log);
    TEST_ASSERT(rc == NGX_OK, "start with nonexistent file still returns OK");
    TEST_ASSERT(watcher.active == 1, "watcher still becomes active");
    TEST_ASSERT(watcher.last_mtime == 0, "mtime set to 0 on stat failure");

    free(watcher.path.data);
    free(watcher.timer);
    TEST_PASS("start: stat failure (file not yet created)");
}


/*
 * Verify that dynconf_start applies an existing valid file immediately
 * at startup, so runtime overrides persist across NGINX restart/reload.
 */
static void
test_start_applies_existing_file_on_startup(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_str_t                            path;
    ngx_int_t                            rc;
    const char                          *tmpfile;
    ngx_http_markdown_conf_t             conf;

    TEST_SUBSECTION("start applies existing valid dynconf file on startup");

    tmpfile = "/tmp/dynconf_test_start_apply.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file with runtime overrides");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "prune_noise=off\n");
        fprintf(f, "log_verbosity=debug\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    conf.enabled = 1;
    conf.advanced.prune_noise = 1;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_ERROR;
    set_ngx_str(&path, tmpfile);

    rc = ngx_http_markdown_dynconf_start(&watcher, &g_cycle, &path, &conf, &g_log);
    TEST_ASSERT(rc == NGX_OK, "start returns NGX_OK");
    TEST_ASSERT(watcher.active == 1, "watcher is active");

    /* The initial reload should have applied the file contents,
     * overriding the static conf values. */
    TEST_ASSERT(watcher.active_snapshot.prune_noise == 0,
                "active snapshot prune_noise overridden to 0 by startup reload");
    TEST_ASSERT(watcher.active_snapshot.log_verbosity
                    == NGX_HTTP_MARKDOWN_LOG_DEBUG,
                "active snapshot log_verbosity overridden to DEBUG by startup reload");
    TEST_ASSERT(watcher.applied_mtime == watcher.last_mtime,
                "applied_mtime equals last_mtime after successful startup reload");
    TEST_ASSERT(watcher.version == 1,
                "version incremented after startup reload");

    /* live conf should also reflect the applied values */
    TEST_ASSERT(conf.advanced.prune_noise == 0,
                "live conf prune_noise overridden by startup reload");
    TEST_ASSERT(conf.policy.log_verbosity == NGX_HTTP_MARKDOWN_LOG_DEBUG,
                "live conf log_verbosity overridden by startup reload");

    free(watcher.path.data);
    free(watcher.timer);
    unlink(tmpfile);
    TEST_PASS("start applies existing valid dynconf file on startup");
}


/*
 * Verify that when the existing dynconf file is invalid at startup,
 * the watcher starts with static conf baseline and applied_mtime=0
 * so the timer will retry on the next cycle.
 */
static void
test_start_invalid_file_leaves_applied_mtime_zero(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_str_t                            path;
    ngx_int_t                            rc;
    const char                          *tmpfile;
    ngx_http_markdown_conf_t             conf;

    TEST_SUBSECTION("start with invalid existing file leaves applied_mtime=0 for retry");

    tmpfile = "/tmp/dynconf_test_start_invalid.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file with invalid content");
        fprintf(f, "prune_noise=yes\n");  /* invalid value */
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    conf.enabled = 1;
    conf.advanced.prune_noise = 1;
    set_ngx_str(&path, tmpfile);

    rc = ngx_http_markdown_dynconf_start(&watcher, &g_cycle, &path, &conf, &g_log);
    TEST_ASSERT(rc == NGX_OK, "start returns NGX_OK even with invalid file");
    TEST_ASSERT(watcher.active == 1, "watcher is active");

    /* The initial reload should have failed; static conf preserved. */
    TEST_ASSERT(watcher.active_snapshot.prune_noise == 1,
                "active snapshot prune_noise unchanged (1) after failed startup reload");
    TEST_ASSERT(watcher.applied_mtime == 0,
                "applied_mtime is 0 after failed startup reload (triggers timer retry)");
    TEST_ASSERT(watcher.last_mtime > 0,
                "last_mtime recorded from stat (file exists)");
    TEST_ASSERT(watcher.last_mtime != watcher.applied_mtime,
                "last_mtime != applied_mtime triggers retry on next timer cycle");

    /* live conf unchanged */
    TEST_ASSERT(conf.advanced.prune_noise == 1,
                "live conf prune_noise unchanged after failed startup reload");

    free(watcher.path.data);
    free(watcher.timer);
    unlink(tmpfile);
    TEST_PASS("start with invalid existing file leaves applied_mtime=0 for retry");
}

static void
test_stop_null_watcher(void)
{
    ngx_http_markdown_dynconf_stop(NULL, &g_log);
    TEST_PASS("stop: NULL watcher (no crash)");
}

static void
test_stop_inactive_watcher(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;

    memset(&watcher, 0, sizeof(watcher));
    watcher.active = 0;

    ngx_http_markdown_dynconf_stop(&watcher, &g_log);
    TEST_PASS("stop: inactive watcher (no-op)");
}

static void
test_stop_active_watcher(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_event_t                          timer;

    memset(&watcher, 0, sizeof(watcher));
    memset(&timer, 0, sizeof(timer));
    timer.timer_set = 1;
    watcher.timer = &timer;
    watcher.active = 1;
    set_ngx_str(&watcher.path, "/tmp/dynconf_stop_test.conf");

    ngx_http_markdown_dynconf_stop(&watcher, &g_log);
    TEST_ASSERT(watcher.active == 0, "watcher marked inactive");
    TEST_PASS("stop: active watcher");
}

static void
test_stop_no_timer(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;

    memset(&watcher, 0, sizeof(watcher));
    watcher.active = 1;
    watcher.timer = NULL;
    set_ngx_str(&watcher.path, "/tmp/dynconf_stop_notimer.conf");

    ngx_http_markdown_dynconf_stop(&watcher, &g_log);
    TEST_ASSERT(watcher.active == 0, "watcher marked inactive");
    TEST_PASS("stop: active watcher with no timer");
}

static void
test_timer_handler_null_data(void)
{
    ngx_event_t  ev;

    memset(&ev, 0, sizeof(ev));
    ev.data = NULL;
    ev.log = &g_log;

    ngx_http_markdown_dynconf_timer_handler(&ev);
    TEST_PASS("timer_handler: NULL data (no crash)");
}

static void
test_timer_handler_inactive_watcher(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_event_t                          ev;

    memset(&watcher, 0, sizeof(watcher));
    watcher.active = 0;

    memset(&ev, 0, sizeof(ev));
    ev.data = &watcher;
    ev.log = &g_log;

    ngx_http_markdown_dynconf_timer_handler(&ev);
    TEST_PASS("timer_handler: inactive watcher");
}

static void
test_timer_handler_rearm(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_event_t                          timer;
    ngx_event_t                          ev;

    memset(&watcher, 0, sizeof(watcher));
    memset(&timer, 0, sizeof(timer));
    timer.timer_set = 0;
    watcher.active = 1;
    watcher.timer = &timer;
    set_ngx_str(&watcher.path, "/tmp/nonexistent_dynconf_rearm.conf");

    memset(&ev, 0, sizeof(ev));
    ev.data = &watcher;
    ev.log = &g_log;
    ev.handler = ngx_http_markdown_dynconf_timer_handler;

    ngx_http_markdown_dynconf_timer_handler(&ev);
    TEST_PASS("timer_handler: rearm timer (no crash)");
}

static void
test_timer_handler_change_detected(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_event_t                          timer;
    ngx_event_t                          ev;
    const char                          *tmpfile;
    ngx_file_info_t                      fi;

    tmpfile = "/tmp/dynconf_test_timer_change.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file for timer handler");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "markdown_filter=on\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    memset(&timer, 0, sizeof(timer));
    timer.timer_set = 0;
    watcher.active = 1;
    watcher.timer = &timer;
    watcher.conf = &conf;
    set_ngx_str(&watcher.path, tmpfile);

    if (ngx_file_info((const u_char *) tmpfile, &fi) != NGX_FILE_ERROR) {
        watcher.last_mtime = ngx_file_mtime(&fi) - 1;
    }

    memset(&ev, 0, sizeof(ev));
    ev.data = &watcher;
    ev.log = &g_log;
    ev.handler = ngx_http_markdown_dynconf_timer_handler;

    ngx_http_markdown_dynconf_timer_handler(&ev);

    TEST_ASSERT(watcher.version > 0,
                "timer_handler: version incremented after reload");
    TEST_ASSERT(watcher.active_snapshot.enabled == 1,
                "timer_handler: active_snapshot reflects new config");

    unlink(tmpfile);
    TEST_PASS("timer_handler: change detected, two-phase reload in timer");
}

static void
test_reload_null_args(void)
{
    ngx_http_markdown_conf_t              conf;
    ngx_int_t                             rc;

    memset(&conf, 0, sizeof(conf));

    rc = ngx_http_markdown_dynconf_reload(NULL, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR,
                "reload NULL watcher returns IO_ERROR");

    {
        ngx_http_markdown_dynconf_watcher_t  watcher;
        memset(&watcher, 0, sizeof(watcher));
        rc = ngx_http_markdown_dynconf_reload(&watcher, NULL, &g_log);
        TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR,
                    "reload NULL conf returns IO_ERROR");
    }

    TEST_PASS("reload: NULL arguments");
}

static void
test_reload_file_not_found(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, "/tmp/nonexistent_dynconf_reload.conf");

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR,
                "reload nonexistent file returns IO_ERROR");
    TEST_PASS("reload: file not found");
}

static void
test_reload_valid_file(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    tmpfile = "/tmp/dynconf_test_reload.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file for reload");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "markdown_filter=on\nprune_noise=off\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "reload valid file returns APPLIED");
    TEST_ASSERT(conf.enabled == 1, "enabled applied");
    TEST_ASSERT(conf.advanced.prune_noise == 0, "prune_noise applied");
    TEST_ASSERT(watcher.version == 1, "version incremented");

    unlink(tmpfile);
    TEST_PASS("reload: valid file with multiple keys");
}

static void
test_reload_empty_file(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    tmpfile = "/tmp/dynconf_test_reload_empty.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create empty temp file");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE,
                "reload empty file returns INVALID_FILE (missing schema_version)");

    unlink(tmpfile);
    TEST_PASS("reload: empty file rejected (missing schema_version)");
}

static void
test_reload_comments_and_blanks(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    tmpfile = "/tmp/dynconf_test_reload_comments.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file with comments");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "# This is a comment\n\n  \nlog_verbosity=warn\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "reload file with comments returns APPLIED");
    TEST_ASSERT(conf.policy.log_verbosity == NGX_HTTP_MARKDOWN_LOG_WARN,
                "log_verbosity applied");

    unlink(tmpfile);
    TEST_PASS("reload: comments and blank lines");
}

static void
test_reload_no_newline_at_eof(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    tmpfile = "/tmp/dynconf_test_reload_no_nl.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file without trailing NL");
        fprintf(f, "schema_version=0.9\nmemory_budget=64k");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "reload file without trailing NL returns APPLIED");
    TEST_ASSERT(conf.advanced.memory_budget == 64 * 1024, "memory_budget applied");

    unlink(tmpfile);
    TEST_PASS("reload: no newline at EOF");
}

static void
test_reload_path_too_long(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;
    u_char                               longpath[NGX_MAX_PATH + 2];

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));

    memset(longpath, 'a', sizeof(longpath));
    longpath[sizeof(longpath) - 1] = '\0';
    watcher.path.data = longpath;
    watcher.path.len = NGX_MAX_PATH + 1;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR,
                "reload path too long returns IO_ERROR");
    TEST_PASS("reload: path too long");
}

static void
test_reload_all_keys(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    tmpfile = "/tmp/dynconf_test_reload_all.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file with all keys");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "markdown_filter=off\n");
        fprintf(f, "prune_noise=on\n");
        fprintf(f, "log_verbosity=error\n");
        fprintf(f, "streaming_budget=8m\n");
        fprintf(f, "memory_budget=256k\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "reload all keys returns APPLIED");
    TEST_ASSERT(conf.enabled == 0, "enabled=off applied");
    TEST_ASSERT(conf.advanced.prune_noise == 1, "prune_noise=on applied");
    TEST_ASSERT(conf.policy.log_verbosity == NGX_HTTP_MARKDOWN_LOG_ERROR,
                "log_verbosity=error applied");
    TEST_ASSERT(conf.stream.budget == 8 * 1024 * 1024,
                "streaming_budget=8m applied");
    TEST_ASSERT(conf.advanced.memory_budget == 256 * 1024,
                "memory_budget=256k applied");

    unlink(tmpfile);
    TEST_PASS("reload: all keys in one file");
}

static void
test_reload_filter_overrides_complex(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    tmpfile = "/tmp/dynconf_test_filter_override_complex.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file for filter override");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "markdown_filter=off\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    conf.enabled = 1;
    conf.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_COMPLEX;
    conf.enabled_complex = (ngx_http_complex_value_t *) 1;
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;
    watcher.active_snapshot.enabled = 1;
    watcher.active_snapshot.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_COMPLEX;
    watcher.active_snapshot.enabled_complex = (ngx_http_complex_value_t *) 1;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "reload file with filter=off returns APPLIED");
    TEST_ASSERT(conf.enabled == 0, "enabled=off applied");
    TEST_ASSERT(conf.enabled_source == NGX_HTTP_MARKDOWN_ENABLED_STATIC,
                "enabled_source overridden to STATIC after reload");
    TEST_ASSERT(conf.enabled_complex == NULL,
                "enabled_complex cleared after reload");

    unlink(tmpfile);
    TEST_PASS("reload: markdown_filter=off overrides complex value");
}

static void
test_reload_verbosity_module_enum(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    tmpfile = "/tmp/dynconf_test_verbosity_module_enum.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file for verbosity enum test");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "log_verbosity=debug\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "reload verbosity=debug returns APPLIED");
    TEST_ASSERT(conf.policy.log_verbosity == NGX_HTTP_MARKDOWN_LOG_DEBUG,
                "log_verbosity is module enum DEBUG (3), not NGX_LOG_DEBUG (4)");

    unlink(tmpfile);
    TEST_PASS("reload: log_verbosity maps to module enum, not NGX_LOG_*");
}

static void
test_reload_invalid_line_rejects_all(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;
    ngx_uint_t                           orig_version;

    tmpfile = "/tmp/dynconf_test_invalid_line.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file with invalid line");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "markdown_filter=on\n");
        fprintf(f, "prune_noise=yes\n");
        fprintf(f, "log_verbosity=warn\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    conf.advanced.prune_noise = 0;
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;
    watcher.active_snapshot.prune_noise = 0;
    watcher.version = 0;
    orig_version = watcher.version;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE,
                "reload with invalid line returns INVALID_FILE");
    TEST_ASSERT(watcher.version == orig_version,
                "version NOT incremented on invalid file");
    TEST_ASSERT(conf.advanced.prune_noise == 0,
                "conf unchanged after invalid file (staged commit)");

    unlink(tmpfile);
    TEST_PASS("reload: invalid line rejects entire file (staged commit)");
}


/*
 * schema_version validation tests (spec 45/53, task 2.8).
 */

static void
test_reload_missing_schema_version_rejected(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    TEST_SUBSECTION("schema_version: missing → INVALID_FILE");

    tmpfile = "/tmp/dynconf_test_no_schema_version.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create file without schema_version");
        fprintf(f, "markdown_filter=on\n");
        fprintf(f, "prune_noise=off\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE,
                "missing schema_version → INVALID_FILE");
    TEST_ASSERT(watcher.version == 0,
                "version not incremented");

    unlink(tmpfile);
    TEST_PASS("schema_version: missing → rejected");
}

static void
test_reload_unknown_schema_version_rejected(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    TEST_SUBSECTION("schema_version: unknown version → INVALID_FILE");

    tmpfile = "/tmp/dynconf_test_bad_schema_version.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create file with unknown schema_version");
        fprintf(f, "schema_version=1.0\n");
        fprintf(f, "markdown_filter=on\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE,
                "unknown schema_version=1.0 → INVALID_FILE");

    unlink(tmpfile);
    TEST_PASS("schema_version: unknown version → rejected");
}

static void
test_reload_schema_version_08_rejected(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    TEST_SUBSECTION("schema_version: old version 0.8 → INVALID_FILE");

    tmpfile = "/tmp/dynconf_test_old_schema_version.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create file with old schema_version");
        fprintf(f, "schema_version=0.8\n");
        fprintf(f, "markdown_filter=on\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE,
                "schema_version=0.8 → INVALID_FILE");

    unlink(tmpfile);
    TEST_PASS("schema_version: old version 0.8 → rejected");
}

static void
test_reload_schema_version_valid(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    TEST_SUBSECTION("schema_version: valid 0.9 → APPLIED");

    tmpfile = "/tmp/dynconf_test_valid_schema_version.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create file with valid schema_version");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "markdown_filter=off\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "schema_version=0.9 → APPLIED");
    TEST_ASSERT(conf.enabled == 0, "markdown_filter=off applied");

    unlink(tmpfile);
    TEST_PASS("schema_version: valid 0.9 → accepted");
}

static void
test_reload_schema_version_only(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    TEST_SUBSECTION("schema_version: only schema_version line → APPLIED");

    tmpfile = "/tmp/dynconf_test_schema_version_only.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create file with only schema_version");
        fprintf(f, "schema_version=0.9\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    /* schema_version counts as an applied key, so result is APPLIED
     * even though no runtime fields are modified. */
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "schema_version=0.9 only → APPLIED");

    unlink(tmpfile);
    TEST_PASS("schema_version: only schema_version line → accepted");
}


static void
test_snapshot_from_conf_and_apply(void)
{
    ngx_http_markdown_conf_t             conf;
    ngx_http_markdown_dynconf_snapshot_t  snapshot;

    memset(&conf, 0, sizeof(conf));
    conf.enabled = 1;
    conf.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
    conf.advanced.prune_noise = 1;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_WARN;
    conf.stream.budget = 4 * 1024 * 1024;
    conf.advanced.memory_budget = 128 * 1024;

    ngx_http_markdown_dynconf_snapshot_from_conf(&snapshot, &conf);
    TEST_ASSERT(snapshot.valid == 1, "snapshot marked valid");
    TEST_ASSERT(snapshot.enabled == 1, "snapshot enabled=1");
    TEST_ASSERT(snapshot.prune_noise == 1, "snapshot prune_noise=1");
    TEST_ASSERT(snapshot.log_verbosity == NGX_HTTP_MARKDOWN_LOG_WARN,
                "snapshot log_verbosity=WARN");
    TEST_ASSERT(snapshot.streaming_budget == 4 * 1024 * 1024,
                "snapshot streaming_budget=4MiB");
    TEST_ASSERT(snapshot.memory_budget == 128 * 1024,
                "snapshot memory_budget=128KiB");

    memset(&conf, 0, sizeof(conf));
    ngx_http_markdown_dynconf_apply_snapshot(&conf, &snapshot);
    TEST_ASSERT(conf.enabled == 1, "apply snapshot: enabled=1");
    TEST_ASSERT(conf.advanced.prune_noise == 1, "apply snapshot: prune_noise=1");
    TEST_ASSERT(conf.policy.log_verbosity == NGX_HTTP_MARKDOWN_LOG_WARN,
                "apply snapshot: log_verbosity=WARN");
    TEST_ASSERT(conf.stream.budget == 4 * 1024 * 1024,
                "apply snapshot: streaming_budget=4MiB");
    TEST_ASSERT(conf.advanced.memory_budget == 128 * 1024,
                "apply snapshot: memory_budget=128KiB");

    TEST_PASS("snapshot: from_conf and apply_snapshot round-trip");
}


static void
test_parse_size_safe_valid_values(void)
{
    size_t     result;
    ngx_int_t  rc;

    TEST_SUBSECTION("parse_size_safe: valid size values");

    u_char val_128k[] = "128k";
    rc = ngx_http_markdown_dynconf_parse_size_safe(
            val_128k, 4, "memory_budget",
            NGX_MAX_SIZE_T_VALUE, &g_log, &result);
    TEST_ASSERT(rc == NGX_OK, "128k parses OK");
    TEST_ASSERT(result == 128 * 1024, "128k == 131072");

    u_char val_4m[] = "4m";
    rc = ngx_http_markdown_dynconf_parse_size_safe(
            val_4m, 2, "streaming_budget",
            NGX_MAX_SIZE_T_VALUE, &g_log, &result);
    TEST_ASSERT(rc == NGX_OK, "4m parses OK");
    TEST_ASSERT(result == 4 * 1024 * 1024, "4m == 4194304");

    u_char val_1g[] = "1g";
    rc = ngx_http_markdown_dynconf_parse_size_safe(
            val_1g, 2, "memory_budget",
            NGX_MAX_SIZE_T_VALUE, &g_log, &result);
    TEST_ASSERT(rc == NGX_OK, "1g parses OK");
    TEST_ASSERT(result == 1UL * 1024 * 1024 * 1024, "1g == 1073741824");

    u_char val_plain[] = "4096";
    rc = ngx_http_markdown_dynconf_parse_size_safe(
            val_plain, 4, "memory_budget",
            NGX_MAX_SIZE_T_VALUE, &g_log, &result);
    TEST_ASSERT(rc == NGX_OK, "4096 parses OK");
    TEST_ASSERT(result == 4096, "plain 4096");

    TEST_PASS("parse_size_safe: valid size values");
}


static void
test_parse_size_safe_invalid_values(void)
{
    size_t     result;
    ngx_int_t  rc;

    TEST_SUBSECTION("parse_size_safe: invalid values rejected");

    u_char val_abc[] = "abc";
    rc = ngx_http_markdown_dynconf_parse_size_safe(
            val_abc, 3, "memory_budget",
            NGX_MAX_SIZE_T_VALUE, &g_log, &result);
    TEST_ASSERT(rc == NGX_ERROR, "abc rejected (parse error)");

    u_char val_badunit[] = "100x";
    rc = ngx_http_markdown_dynconf_parse_size_safe(
            val_badunit, 4, "memory_budget",
            NGX_MAX_SIZE_T_VALUE, &g_log, &result);
    TEST_ASSERT(rc == NGX_ERROR, "unknown unit 'x' rejected");

    u_char val_zero[] = "0";
    rc = ngx_http_markdown_dynconf_parse_size_safe(
            val_zero, 1, "streaming_budget",
            NGX_MAX_SIZE_T_VALUE, &g_log, &result);
    TEST_ASSERT(rc == NGX_OK, "0 is valid (zero budget)");
    TEST_ASSERT(result == 0, "zero budget result is 0");

    TEST_PASS("parse_size_safe: invalid values rejected");
}


static void
test_parse_size_safe_upper_bound_enforcement(void)
{
    size_t     result;
    ngx_int_t  rc;

    TEST_SUBSECTION("parse_size_safe: upper bound enforcement");

    u_char val_4m[] = "4m";
    rc = ngx_http_markdown_dynconf_parse_size_safe(
            val_4m, 2, "memory_budget",
            (size_t) 2 * 1024 * 1024, &g_log, &result);
    TEST_ASSERT(rc == NGX_ERROR,
                "4m rejected when max is 2m (exceeds bound)");

    rc = ngx_http_markdown_dynconf_parse_size_safe(
            val_4m, 2, "memory_budget",
            (size_t) 4 * 1024 * 1024, &g_log, &result);
    TEST_ASSERT(rc == NGX_OK,
                "4m accepted when max is exactly 4m");
    TEST_ASSERT(result == 4 * 1024 * 1024,
                "result equals 4m at boundary");

    rc = ngx_http_markdown_dynconf_parse_size_safe(
            val_4m, 2, "memory_budget",
            (size_t) 8 * 1024 * 1024, &g_log, &result);
    TEST_ASSERT(rc == NGX_OK,
                "4m accepted when max is 8m");

    TEST_PASS("parse_size_safe: upper bound enforcement");
}


static void
test_apply_memory_budget_does_not_mutate_on_error(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;
    size_t                               original_budget;

    TEST_SUBSECTION("apply: memory_budget not mutated on error");

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;
    snapshot.memory_budget = 999;
    original_budget = snapshot.memory_budget;

    u_char bad_val[] = "notasize";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_MEMORY_BUDGET,
                                         bad_val, 8, &g_log);
    TEST_ASSERT(rc == NGX_ERROR, "invalid memory_budget returns ERROR");
    TEST_ASSERT(snapshot.memory_budget == original_budget,
                "memory_budget unchanged after error");

    TEST_PASS("apply: memory_budget not mutated on error");
}


static void
test_apply_streaming_budget_does_not_mutate_on_error(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;
    size_t                               original_budget;

    TEST_SUBSECTION("apply: streaming_budget not mutated on error");

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;
    snapshot.streaming_budget = 777;
    original_budget = snapshot.streaming_budget;

    u_char bad_val[] = "xyz";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_STREAMING_BUDGET,
                                         bad_val, 3, &g_log);
    TEST_ASSERT(rc == NGX_ERROR, "invalid streaming_budget returns ERROR");
    TEST_ASSERT(snapshot.streaming_budget == original_budget,
                "streaming_budget unchanged after error");

    TEST_PASS("apply: streaming_budget not mutated on error");
}


static void
test_apply_memory_budget_large_valid(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    TEST_SUBSECTION("apply: memory_budget with large valid value");

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "1g";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_MEMORY_BUDGET,
                                         val, 2, &g_log);
    TEST_ASSERT(rc == NGX_OK, "memory_budget=1g returns OK");
    TEST_ASSERT(snapshot.memory_budget == 1UL * 1024 * 1024 * 1024,
                "memory_budget set to 1GiB");

    TEST_PASS("apply: memory_budget with large valid value");
}


static void
test_apply_streaming_budget_large_valid(void)
{
    ngx_http_markdown_dynconf_snapshot_t  snapshot;
    ngx_int_t                            rc;

    TEST_SUBSECTION("apply: streaming_budget with large valid value");

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.valid = 1;

    u_char val[] = "1g";
    rc = ngx_http_markdown_dynconf_apply(&snapshot,
                                         NGX_HTTP_MARKDOWN_DYNCONF_KEY_STREAMING_BUDGET,
                                         val, 2, &g_log);
    TEST_ASSERT(rc == NGX_OK, "streaming_budget=1g returns OK");
    TEST_ASSERT(snapshot.streaming_budget == 1UL * 1024 * 1024 * 1024,
                "streaming_budget set to 1GiB");

    TEST_PASS("apply: streaming_budget with large valid value");
}

static void
test_dynconf_start_watcher_already_active(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;
    const char                          *path_a = "/tmp/dynconf_start_a.conf";
    const char                          *path_b = "/tmp/dynconf_start_b.conf";
    u_char                               path_buf_a[256];
    size_t                               len_a;

    {
        FILE *f = fopen(path_a, "w");
        TEST_ASSERT(f != NULL, "create path_a");
        fclose(f);
    }
    {
        FILE *f = fopen(path_b, "w");
        TEST_ASSERT(f != NULL, "create path_b");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));

    len_a = strlen(path_a);
    ngx_memcpy(path_buf_a, path_a, len_a);
    path_buf_a[len_a] = '\0';
    watcher.path.data = path_buf_a;
    watcher.path.len = len_a;
    watcher.active = 1;

    set_ngx_str(&conf.advanced.dynconf_path, path_b);

    rc = ngx_http_markdown_dynconf_start(&watcher, NULL,
                                          (const ngx_str_t *) &conf.advanced.dynconf_path,
                                          &conf, &g_log);
    TEST_ASSERT(rc == NGX_ERROR,
                "start with already-active watcher returns NGX_ERROR (duplicate rejected)");
    TEST_ASSERT(watcher.active == 1,
                "watcher still active after duplicate start");
    TEST_ASSERT(watcher.path.len == len_a
                && ngx_memcmp(watcher.path.data, path_buf_a, len_a) == 0,
                "watcher path unchanged after duplicate start");

    watcher.active = 0;

    unlink(path_a);
    unlink(path_b);
    TEST_PASS("dynconf_start: watcher already active guard");
}

static void
test_reload_line_too_long(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;
    ngx_uint_t                           orig_version;

    tmpfile = "/tmp/dynconf_test_long_line.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create long-line dynconf file");
        fprintf(f, "markdown_filter=on\n");
        for (size_t j = 0; j < NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE + 100; j++) {
            fputc('x', f);
        }
        fputc('\n', f);
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    conf.enabled = 1;
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active = 1;
    watcher.version = 7;
    orig_version = watcher.version;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE,
                "reload with too-long line returns INVALID_FILE");
    TEST_ASSERT(watcher.version == orig_version,
                "version unchanged after too-long line");
    TEST_ASSERT(conf.enabled == 1,
                "conf fields unchanged after too-long line");

    unlink(tmpfile);
    TEST_PASS("reload: line too long returns INVALID_FILE");
}

static void
test_reload_lkg_preserved_on_success(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    tmpfile = "/tmp/dynconf_test_lkg_preserve.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file for LKG test");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "markdown_filter=off\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile);

    /* Set up initial active snapshot: enabled=1, prune_noise=1 */
    watcher.active_snapshot.valid = 1;
    watcher.active_snapshot.enabled = 1;
    watcher.active_snapshot.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
    watcher.active_snapshot.prune_noise = 1;
    watcher.active_snapshot.log_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
    watcher.active_snapshot.memory_budget = 64 * 1024;
    watcher.lkg_valid = 0;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "reload returns APPLIED");

    /* LKG should now hold the previous active snapshot values */
    TEST_ASSERT(watcher.lkg_valid == 1,
                "lkg_valid set to 1 after successful reload");
    TEST_ASSERT(watcher.last_known_good.valid == 1,
                "LKG snapshot marked valid");
    TEST_ASSERT(watcher.last_known_good.enabled == 1,
                "LKG preserved previous enabled=1");
    TEST_ASSERT(watcher.last_known_good.prune_noise == 1,
                "LKG preserved previous prune_noise=1");
    TEST_ASSERT(watcher.last_known_good.log_verbosity
                == NGX_HTTP_MARKDOWN_LOG_INFO,
                "LKG preserved previous log_verbosity=INFO");
    TEST_ASSERT(watcher.last_known_good.memory_budget == 64 * 1024,
                "LKG preserved previous memory_budget=64k");

    /* Active snapshot should have the new value */
    TEST_ASSERT(watcher.active_snapshot.enabled == 0,
                "active_snapshot updated to enabled=0");

    unlink(tmpfile);
    TEST_PASS("reload: LKG preserved on successful reload");
}

static void
test_reload_lkg_not_updated_on_failure(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    tmpfile = "/tmp/dynconf_test_lkg_no_update.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file for LKG failure test");
        fprintf(f, "unknown_key=bad\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile);

    /* Set up initial active snapshot */
    watcher.active_snapshot.valid = 1;
    watcher.active_snapshot.enabled = 1;
    watcher.active_snapshot.prune_noise = 1;
    watcher.lkg_valid = 0;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE,
                "reload with unknown key returns INVALID_FILE");

    /* LKG should NOT be updated on failure */
    TEST_ASSERT(watcher.lkg_valid == 0,
                "lkg_valid remains 0 after failed reload");

    /* Active snapshot should be unchanged */
    TEST_ASSERT(watcher.active_snapshot.enabled == 1,
                "active_snapshot unchanged after failed reload");
    TEST_ASSERT(watcher.active_snapshot.prune_noise == 1,
                "active_snapshot prune_noise unchanged after failed reload");

    unlink(tmpfile);
    TEST_PASS("reload: LKG not updated on validation failure");
}

static void
test_reload_lkg_successive_reloads(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    tmpfile = "/tmp/dynconf_test_lkg_successive.conf";

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile);

    /* Initial state: enabled=1, prune_noise=0 */
    watcher.active_snapshot.valid = 1;
    watcher.active_snapshot.enabled = 1;
    watcher.active_snapshot.prune_noise = 0;
    watcher.active_snapshot.log_verbosity = NGX_HTTP_MARKDOWN_LOG_ERROR;
    watcher.lkg_valid = 0;

    /* First reload: set prune_noise=on */
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create file for first reload");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "prune_noise=on\n");
        fclose(f);
    }

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "first reload APPLIED");
    TEST_ASSERT(watcher.lkg_valid == 1,
                "lkg_valid=1 after first reload");
    TEST_ASSERT(watcher.last_known_good.prune_noise == 0,
                "LKG has prune_noise=0 (previous state)");
    TEST_ASSERT(watcher.active_snapshot.prune_noise == 1,
                "active has prune_noise=1 (new state)");

    /* Second reload: set log_verbosity=debug */
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create file for second reload");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "log_verbosity=debug\n");
        fclose(f);
    }

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "second reload APPLIED");

    /* LKG should now hold the state from AFTER first reload
     * (prune_noise=1, log_verbosity=ERROR from first active) */
    TEST_ASSERT(watcher.lkg_valid == 1,
                "lkg_valid still 1 after second reload");
    TEST_ASSERT(watcher.last_known_good.prune_noise == 1,
                "LKG has prune_noise=1 (state after first reload)");
    TEST_ASSERT(watcher.last_known_good.log_verbosity
                == NGX_HTTP_MARKDOWN_LOG_ERROR,
                "LKG has log_verbosity=ERROR (from first active)");
    TEST_ASSERT(watcher.active_snapshot.log_verbosity
                == NGX_HTTP_MARKDOWN_LOG_DEBUG,
                "active has log_verbosity=DEBUG (new state)");

    unlink(tmpfile);
    TEST_PASS("reload: successive reloads update LKG correctly");
}

static void
test_reload_lkg_preserved_after_failed_reload(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    tmpfile = "/tmp/dynconf_test_lkg_after_fail.conf";

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile);

    /* Initial state */
    watcher.active_snapshot.valid = 1;
    watcher.active_snapshot.enabled = 1;
    watcher.active_snapshot.prune_noise = 0;
    watcher.lkg_valid = 0;

    /* First reload: success → LKG populated */
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create file for success reload");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "prune_noise=on\n");
        fclose(f);
    }

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "first reload APPLIED");
    TEST_ASSERT(watcher.lkg_valid == 1, "lkg_valid=1");
    TEST_ASSERT(watcher.last_known_good.prune_noise == 0,
                "LKG has original prune_noise=0");

    /* Second reload: failure → LKG must NOT change */
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create file for failed reload");
        fprintf(f, "invalid_key=bad\n");
        fclose(f);
    }

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE,
                "second reload INVALID_FILE");

    /* LKG should still hold the state from before first reload */
    TEST_ASSERT(watcher.lkg_valid == 1,
                "lkg_valid still 1 after failed reload");
    TEST_ASSERT(watcher.last_known_good.prune_noise == 0,
                "LKG prune_noise unchanged after failed reload");

    /* Active should still be the state from first reload */
    TEST_ASSERT(watcher.active_snapshot.prune_noise == 1,
                "active prune_noise unchanged after failed reload");

    unlink(tmpfile);
    TEST_PASS("reload: LKG preserved unchanged after failed reload");
}


/*
 * Rollback tests (E04.2)
 */

static void
test_rollback_null_watcher(void)
{
    ngx_int_t  rc;

    rc = ngx_http_markdown_dynconf_rollback(NULL, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_APPLY_ERR,
                "rollback NULL watcher returns APPLY_ERR");
    TEST_PASS("rollback: NULL watcher");
}

static void
test_rollback_null_conf(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_int_t                            rc;

    memset(&watcher, 0, sizeof(watcher));
    watcher.conf = NULL;

    rc = ngx_http_markdown_dynconf_rollback(&watcher, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_APPLY_ERR,
                "rollback NULL conf returns APPLY_ERR");
    TEST_PASS("rollback: NULL conf pointer");
}

static void
test_rollback_no_lkg(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    watcher.conf = &conf;
    watcher.lkg_valid = 0;

    rc = ngx_http_markdown_dynconf_rollback(&watcher, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_NO_LKG,
                "rollback with no LKG returns NO_LKG");
    TEST_PASS("rollback: no LKG available");
}

static void
test_rollback_success(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    watcher.conf = &conf;

    /* Set up active snapshot (version N+1) */
    watcher.active_snapshot.valid = 1;
    watcher.active_snapshot.enabled = 0;
    watcher.active_snapshot.prune_noise = 1;
    watcher.active_snapshot.log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;

    /* Set up LKG (version N) */
    watcher.last_known_good.valid = 1;
    watcher.last_known_good.enabled = 1;
    watcher.last_known_good.prune_noise = 0;
    watcher.last_known_good.log_verbosity = NGX_HTTP_MARKDOWN_LOG_ERROR;
    watcher.lkg_valid = 1;
    watcher.version = 5;

    rc = ngx_http_markdown_dynconf_rollback(&watcher, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_OK,
                "rollback returns OK");

    /* Active snapshot should now match LKG */
    TEST_ASSERT(watcher.active_snapshot.enabled == 1,
                "active enabled restored from LKG");
    TEST_ASSERT(watcher.active_snapshot.prune_noise == 0,
                "active prune_noise restored from LKG");
    TEST_ASSERT(watcher.active_snapshot.log_verbosity
                == NGX_HTTP_MARKDOWN_LOG_ERROR,
                "active log_verbosity restored from LKG");

    /* Version should be incremented */
    TEST_ASSERT(watcher.version == 6,
                "version incremented after rollback");

    /* LKG should remain valid (idempotent rollback) */
    TEST_ASSERT(watcher.lkg_valid == 1,
                "lkg_valid remains 1 after rollback");

    /* Live conf should reflect the restored snapshot */
    TEST_ASSERT(conf.enabled == 1,
                "live conf enabled updated from rollback");
    TEST_ASSERT(conf.advanced.prune_noise == 0,
                "live conf prune_noise updated from rollback");
    TEST_ASSERT(conf.policy.log_verbosity
                == NGX_HTTP_MARKDOWN_LOG_ERROR,
                "live conf log_verbosity updated from rollback");

    TEST_PASS("rollback: success restores LKG to active and conf");
}

static void
test_rollback_idempotent(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    watcher.conf = &conf;

    /* Set up LKG */
    watcher.last_known_good.valid = 1;
    watcher.last_known_good.enabled = 1;
    watcher.last_known_good.prune_noise = 1;
    watcher.last_known_good.log_verbosity = NGX_HTTP_MARKDOWN_LOG_WARN;
    watcher.lkg_valid = 1;

    /* Active differs from LKG */
    watcher.active_snapshot.valid = 1;
    watcher.active_snapshot.enabled = 0;
    watcher.active_snapshot.prune_noise = 0;
    watcher.active_snapshot.log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;
    watcher.version = 10;

    /* First rollback */
    rc = ngx_http_markdown_dynconf_rollback(&watcher, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_OK,
                "first rollback OK");
    TEST_ASSERT(watcher.version == 11, "version=11 after first rollback");

    /* Second rollback (idempotent) */
    rc = ngx_http_markdown_dynconf_rollback(&watcher, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_OK,
                "second rollback OK (idempotent)");
    TEST_ASSERT(watcher.version == 12, "version=12 after second rollback");

    /* Active should still match LKG */
    TEST_ASSERT(watcher.active_snapshot.enabled == 1,
                "active enabled still matches LKG");
    TEST_ASSERT(watcher.active_snapshot.prune_noise == 1,
                "active prune_noise still matches LKG");

    TEST_PASS("rollback: repeated rollback is idempotent");
}

static void
test_rollback_null_log(void)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    watcher.conf = &conf;
    watcher.lkg_valid = 1;
    watcher.last_known_good.valid = 1;
    watcher.last_known_good.enabled = 1;
    watcher.active_snapshot.valid = 1;
    watcher.active_snapshot.enabled = 0;
    watcher.version = 3;

    /* NULL log should not crash */
    rc = ngx_http_markdown_dynconf_rollback(&watcher, NULL);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_OK,
                "rollback with NULL log still succeeds");
    TEST_ASSERT(watcher.active_snapshot.enabled == 1,
                "active restored even with NULL log");
    TEST_ASSERT(watcher.version == 4, "version incremented");

    TEST_PASS("rollback: NULL log does not crash");
}


/*
 * Dry-run tests (E02.4)
 */

static void
test_dry_run_valid_file_returns_ok(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;
    ngx_uint_t                           orig_version;

    TEST_SUBSECTION("dry-run: valid file returns DRY_RUN_OK");

    tmpfile = "/tmp/dynconf_test_dryrun_valid.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file for dry-run valid test");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "markdown_filter=on\nprune_noise=off\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    conf.advanced.dynconf_dry_run = 1;
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;
    watcher.active_snapshot.enabled = 0;
    watcher.active_snapshot.prune_noise = 1;
    watcher.version = 3;
    orig_version = watcher.version;
    watcher.lkg_valid = 0;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK,
                "reload returns DRY_RUN_OK for valid file in dry-run mode");

    /* active_snapshot must NOT be modified */
    TEST_ASSERT(watcher.active_snapshot.enabled == 0,
                "active_snapshot.enabled unchanged (still 0)");
    TEST_ASSERT(watcher.active_snapshot.prune_noise == 1,
                "active_snapshot.prune_noise unchanged (still 1)");

    /* last_known_good must NOT be updated */
    TEST_ASSERT(watcher.lkg_valid == 0,
                "lkg_valid remains 0 (not updated in dry-run)");

    /* version must NOT be incremented */
    TEST_ASSERT(watcher.version == orig_version,
                "version not incremented in dry-run mode");

    /* live conf must NOT be modified */
    TEST_ASSERT(conf.enabled == 0,
                "live conf.enabled unchanged");
    TEST_ASSERT(conf.advanced.prune_noise == 0,
                "live conf.prune_noise unchanged");

    unlink(tmpfile);
    TEST_PASS("dry-run: valid file returns DRY_RUN_OK without side effects");
}

static void
test_dry_run_invalid_file_returns_fail(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;
    ngx_uint_t                           orig_version;

    TEST_SUBSECTION("dry-run: invalid file returns DRY_RUN_FAIL");

    tmpfile = "/tmp/dynconf_test_dryrun_invalid.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file for dry-run invalid test");
        fprintf(f, "unknown_key=bad\n");
        fprintf(f, "another_bad_key=value\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    conf.advanced.dynconf_dry_run = 1;
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;
    watcher.active_snapshot.enabled = 1;
    watcher.active_snapshot.prune_noise = 1;
    watcher.version = 5;
    orig_version = watcher.version;
    watcher.lkg_valid = 0;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL,
                "reload returns DRY_RUN_FAIL for invalid file in dry-run mode");

    /* active_snapshot must NOT be modified */
    TEST_ASSERT(watcher.active_snapshot.enabled == 1,
                "active_snapshot.enabled unchanged (still 1)");
    TEST_ASSERT(watcher.active_snapshot.prune_noise == 1,
                "active_snapshot.prune_noise unchanged (still 1)");

    /* last_known_good must NOT be updated */
    TEST_ASSERT(watcher.lkg_valid == 0,
                "lkg_valid remains 0 (not updated in dry-run)");

    /* version must NOT be incremented */
    TEST_ASSERT(watcher.version == orig_version,
                "version not incremented on dry-run failure");

    /* validation result should have errors */
    TEST_ASSERT(watcher.last_validation.total_errors > 0,
                "last_validation.total_errors > 0");
    TEST_ASSERT(watcher.last_validation.count > 0,
                "last_validation.count > 0");

    unlink(tmpfile);
    TEST_PASS("dry-run: invalid file returns DRY_RUN_FAIL with errors recorded");
}

static void
test_dry_run_off_applies_normally(void)
{
    const char                          *tmpfile;
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_int_t                            rc;

    TEST_SUBSECTION("dry-run off: normal mode applies changes");

    tmpfile = "/tmp/dynconf_test_dryrun_off.conf";
    {
        FILE *f = fopen(tmpfile, "w");
        TEST_ASSERT(f != NULL, "create temp file for dry-run off test");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "markdown_filter=on\nprune_noise=off\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    conf.advanced.dynconf_dry_run = 0;
    set_ngx_str(&watcher.path, tmpfile);
    watcher.active_snapshot.valid = 1;
    watcher.active_snapshot.enabled = 0;
    watcher.active_snapshot.prune_noise = 1;
    watcher.version = 0;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "reload returns APPLIED (not DRY_RUN_OK) when dry-run is off");

    /* active_snapshot IS modified in normal mode */
    TEST_ASSERT(watcher.active_snapshot.enabled == 1,
                "active_snapshot.enabled updated to 1");
    TEST_ASSERT(watcher.active_snapshot.prune_noise == 0,
                "active_snapshot.prune_noise updated to 0");

    /* version IS incremented */
    TEST_ASSERT(watcher.version == 1,
                "version incremented in normal mode");

    unlink(tmpfile);
    TEST_PASS("dry-run off: normal mode applies changes as expected");
}


static void
test_reload_concurrent_request_snapshot_consistency(void)
{
    const char                              *tmpfile_v1;
    const char                              *tmpfile_v2;
    ngx_http_markdown_dynconf_watcher_t      watcher;
    ngx_http_markdown_conf_t                 conf;
    ngx_http_markdown_dynconf_snapshot_t     bound_snapshot;
    ngx_http_markdown_effective_conf_t       eff;
    ngx_int_t                                rc;

    TEST_SUBSECTION("reload vs request: snapshot consistency (Rule 34)");

    /*
     * Step 1: Set up initial dynconf file (v1) and reload to establish
     * the active_snapshot with known values.
     */
    tmpfile_v1 = "/tmp/dynconf_test_concurrent_v1.conf";
    {
        FILE *f = fopen(tmpfile_v1, "w");
        TEST_ASSERT(f != NULL, "create v1 config file");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "markdown_filter=on\n");
        fprintf(f, "prune_noise=off\n");
        fprintf(f, "log_verbosity=warn\n");
        fprintf(f, "memory_budget=128k\n");
        fclose(f);
    }

    memset(&watcher, 0, sizeof(watcher));
    memset(&conf, 0, sizeof(conf));
    set_ngx_str(&watcher.path, tmpfile_v1);
    watcher.active_snapshot.valid = 1;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "v1 reload applied successfully");
    TEST_ASSERT(watcher.active_snapshot.enabled == 1,
                "v1: active_snapshot.enabled == 1");
    TEST_ASSERT(watcher.active_snapshot.prune_noise == 0,
                "v1: active_snapshot.prune_noise == 0");
    TEST_ASSERT(watcher.active_snapshot.log_verbosity
                    == NGX_HTTP_MARKDOWN_LOG_WARN,
                "v1: active_snapshot.log_verbosity == WARN");
    TEST_ASSERT(watcher.active_snapshot.memory_budget == 128 * 1024,
                "v1: active_snapshot.memory_budget == 128KiB");

    /*
     * Step 2: Simulate a request binding its effective_conf.
     * In production, header_filter copies active_snapshot into
     * ctx->dynconf_snapshot (bind-once).  We simulate this by
     * copying the snapshot struct (memcpy).
     */
    memcpy(&bound_snapshot, &watcher.active_snapshot,
           sizeof(ngx_http_markdown_dynconf_snapshot_t));

    /* Build effective_conf from the bound snapshot */
    memset(&eff, 0, sizeof(eff));
    ngx_http_markdown_build_effective_conf(&eff, &bound_snapshot, &conf);

    TEST_ASSERT(eff.enabled == 1,
                "bound eff.enabled == 1 (from v1 snapshot)");
    TEST_ASSERT(eff.prune_noise == 0,
                "bound eff.prune_noise == 0 (from v1 snapshot)");
    TEST_ASSERT(eff.log_verbosity == NGX_HTTP_MARKDOWN_LOG_WARN,
                "bound eff.log_verbosity == WARN (from v1 snapshot)");
    TEST_ASSERT(eff.memory_budget == 128 * 1024,
                "bound eff.memory_budget == 128KiB (from v1 snapshot)");

    /*
     * Step 3: Simulate a concurrent reload that changes the global
     * active_snapshot to new values (v2).
     */
    tmpfile_v2 = "/tmp/dynconf_test_concurrent_v2.conf";
    {
        FILE *f = fopen(tmpfile_v2, "w");
        TEST_ASSERT(f != NULL, "create v2 config file");
        fprintf(f, "schema_version=0.9\n");
        fprintf(f, "markdown_filter=off\n");
        fprintf(f, "prune_noise=on\n");
        fprintf(f, "log_verbosity=debug\n");
        fprintf(f, "memory_budget=256k\n");
        fclose(f);
    }

    set_ngx_str(&watcher.path, tmpfile_v2);
    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &g_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "v2 reload applied successfully");

    /*
     * Step 4: Verify the global active_snapshot now has v2 values.
     */
    TEST_ASSERT(watcher.active_snapshot.enabled == 0,
                "v2: active_snapshot.enabled == 0 (new value)");
    TEST_ASSERT(watcher.active_snapshot.prune_noise == 1,
                "v2: active_snapshot.prune_noise == 1 (new value)");
    TEST_ASSERT(watcher.active_snapshot.log_verbosity
                    == NGX_HTTP_MARKDOWN_LOG_DEBUG,
                "v2: active_snapshot.log_verbosity == DEBUG (new value)");
    TEST_ASSERT(watcher.active_snapshot.memory_budget == 256 * 1024,
                "v2: active_snapshot.memory_budget == 256KiB (new value)");

    /*
     * Step 5: Verify the request's bound snapshot still has v1 values.
     * This demonstrates the bind-once semantic: the request's copy is
     * independent of the global state.
     */
    TEST_ASSERT(bound_snapshot.enabled == 1,
                "bound snapshot still has v1 enabled == 1");
    TEST_ASSERT(bound_snapshot.prune_noise == 0,
                "bound snapshot still has v1 prune_noise == 0");
    TEST_ASSERT(bound_snapshot.log_verbosity == NGX_HTTP_MARKDOWN_LOG_WARN,
                "bound snapshot still has v1 log_verbosity == WARN");
    TEST_ASSERT(bound_snapshot.memory_budget == 128 * 1024,
                "bound snapshot still has v1 memory_budget == 128KiB");

    /*
     * Step 6: Verify the effective_conf built from the bound snapshot
     * still returns v1 values (not affected by the reload).
     */
    TEST_ASSERT(eff.enabled == 1,
                "eff.enabled still 1 after reload (bound to v1)");
    TEST_ASSERT(eff.prune_noise == 0,
                "eff.prune_noise still 0 after reload (bound to v1)");
    TEST_ASSERT(eff.log_verbosity == NGX_HTTP_MARKDOWN_LOG_WARN,
                "eff.log_verbosity still WARN after reload (bound to v1)");
    TEST_ASSERT(eff.memory_budget == 128 * 1024,
                "eff.memory_budget still 128KiB after reload (bound to v1)");

    /*
     * Step 7: A new request binding AFTER the reload would see v2.
     * Verify this to confirm the global state did change.
     */
    {
        ngx_http_markdown_dynconf_snapshot_t  new_bound;
        ngx_http_markdown_effective_conf_t    new_eff;

        memcpy(&new_bound, &watcher.active_snapshot,
               sizeof(ngx_http_markdown_dynconf_snapshot_t));
        memset(&new_eff, 0, sizeof(new_eff));
        ngx_http_markdown_build_effective_conf(&new_eff, &new_bound, &conf);

        TEST_ASSERT(new_eff.enabled == 0,
                    "new request sees v2 enabled == 0");
        TEST_ASSERT(new_eff.prune_noise == 1,
                    "new request sees v2 prune_noise == 1");
        TEST_ASSERT(new_eff.log_verbosity == NGX_HTTP_MARKDOWN_LOG_DEBUG,
                    "new request sees v2 log_verbosity == DEBUG");
        TEST_ASSERT(new_eff.memory_budget == 256 * 1024,
                    "new request sees v2 memory_budget == 256KiB");
    }

    unlink(tmpfile_v1);
    unlink(tmpfile_v2);
    TEST_PASS("reload vs request: bound snapshot independent of global state");
}


int
main(void)
{
    TEST_SECTION("dynconf_impl: effective-conf helper smoke");
    test_effective_conf_helpers_smoke();

    TEST_SECTION("dynconf_impl: parse_line tests");

    test_parse_line_blank();
    test_parse_line_comment();
    test_parse_line_whitespace_only();
    test_parse_line_filter_on();
    test_parse_line_filter_off();
    test_parse_line_prune_noise();
    test_parse_line_log_verbosity();
    test_parse_line_streaming_budget();
    test_parse_line_memory_budget();
    test_parse_line_schema_version();
    test_parse_line_unknown_key();
    test_parse_line_no_equals();
    test_parse_line_empty_value();
    test_parse_line_whitespace_around_value();
    test_parse_line_leading_whitespace();

    TEST_SECTION("dynconf_impl: apply tests");

    test_apply_filter_on();
    test_apply_filter_off();
    test_apply_filter_invalid();
    test_apply_prune_noise_on();
    test_apply_prune_noise_off();
    test_apply_prune_noise_invalid();
    test_apply_log_verbosity_error();
    test_apply_log_verbosity_warn();
    test_apply_log_verbosity_info();
    test_apply_log_verbosity_debug();
    test_apply_log_verbosity_invalid();
    test_apply_filter_on_overrides_complex();
    test_apply_filter_off_overrides_complex();
    test_apply_streaming_budget();
    test_apply_streaming_budget_invalid();
    test_apply_memory_budget();
    test_apply_memory_budget_invalid();
    test_apply_default_key();

    TEST_SECTION("dynconf_impl: check tests");

    test_check_null_watcher();
    test_check_inactive_watcher();
    test_check_file_changed();
    test_check_path_too_long();
    test_check_stat_fails();

    TEST_SECTION("dynconf_impl: start tests");

    test_start_null_watcher();
    test_start_null_path();
    test_start_empty_path();
    test_start_path_too_long();
    test_start_success();
    test_start_stat_fails();
    test_start_applies_existing_file_on_startup();
    test_start_invalid_file_leaves_applied_mtime_zero();

    TEST_SECTION("dynconf_impl: stop tests");

    test_stop_null_watcher();
    test_stop_inactive_watcher();
    test_stop_active_watcher();
    test_stop_no_timer();

    TEST_SECTION("dynconf_impl: timer_handler tests");

    test_timer_handler_null_data();
    test_timer_handler_inactive_watcher();
    test_timer_handler_rearm();
    test_timer_handler_change_detected();

    TEST_SECTION("dynconf_impl: reload tests");

    test_reload_null_args();
    test_reload_file_not_found();
    test_reload_valid_file();
    test_reload_empty_file();
    test_reload_comments_and_blanks();
    test_reload_no_newline_at_eof();
    test_reload_path_too_long();
    test_reload_all_keys();
    test_reload_filter_overrides_complex();
    test_reload_verbosity_module_enum();
    test_reload_invalid_line_rejects_all();

    TEST_SECTION("dynconf_impl: schema_version validation (spec 45/53)");

    test_reload_missing_schema_version_rejected();
    test_reload_unknown_schema_version_rejected();
    test_reload_schema_version_08_rejected();
    test_reload_schema_version_valid();
    test_reload_schema_version_only();

    TEST_SECTION("dynconf_impl: additional reload/start tests");

    test_dynconf_start_watcher_already_active();
    test_reload_line_too_long();

    TEST_SECTION("dynconf_impl: snapshot tests");

    test_snapshot_from_conf_and_apply();

    TEST_SECTION("dynconf_impl: parse_size_safe overflow/consistency tests");

    test_parse_size_safe_valid_values();
    test_parse_size_safe_invalid_values();
    test_parse_size_safe_upper_bound_enforcement();
    test_apply_memory_budget_does_not_mutate_on_error();
    test_apply_streaming_budget_does_not_mutate_on_error();
    test_apply_memory_budget_large_valid();
    test_apply_streaming_budget_large_valid();

    TEST_SECTION("dynconf_impl: last-known-good (LKG) tests");

    test_reload_lkg_preserved_on_success();
    test_reload_lkg_not_updated_on_failure();
    test_reload_lkg_successive_reloads();
    test_reload_lkg_preserved_after_failed_reload();

    TEST_SECTION("dynconf_impl: rollback tests (E04.2)");

    test_rollback_null_watcher();
    test_rollback_null_conf();
    test_rollback_no_lkg();
    test_rollback_success();
    test_rollback_idempotent();
    test_rollback_null_log();

    TEST_SECTION("dynconf_impl: dry-run tests (E02.4)");

    test_dry_run_valid_file_returns_ok();
    test_dry_run_invalid_file_returns_fail();
    test_dry_run_off_applies_normally();

    TEST_SECTION("dynconf_impl: reload-request concurrency (E03.4)");

    test_reload_concurrent_request_snapshot_consistency();

    printf("\nAll dynconf_impl tests passed!\n");
    return 0;
}
