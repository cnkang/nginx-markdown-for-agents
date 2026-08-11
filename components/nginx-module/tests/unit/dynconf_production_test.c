/*
 * Test: dynconf_production
 *
 * Exercises the production Rust-backed dynconf wrapper.  The legacy line
 * parser test remains useful for compatibility coverage, but this target
 * deliberately compiles without NGX_HTTP_MARKDOWN_DYNCONF_LEGACY_TEST so
 * file and FFI failure accounting cannot regress unnoticed.
 */

#if defined(__linux__) && !defined(_DEFAULT_SOURCE)
#define _DEFAULT_SOURCE
#endif

#include "../include/test_common.h"

#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <sys/stat.h>
#include <unistd.h>

#define MARKDOWN_STREAMING_ENABLED 1

#include "../../src/ngx_http_markdown_filter_module.h"

#ifndef NGX_OK
#define NGX_OK 0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR (-1)
#endif
#ifndef NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED
#define NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED 0
#endif
#ifndef NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE
#define NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE 2
#endif
#ifndef NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR
#define NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR 3
#endif
#ifndef NGX_LOG_INFO
#define NGX_LOG_INFO 3
#endif
#ifndef NGX_LOG_WARN
#define NGX_LOG_WARN 2
#endif
#ifndef NGX_HTTP_TOO_MANY_REQUESTS
#define NGX_HTTP_TOO_MANY_REQUESTS 429
#endif
#ifndef NGX_HTTP_SERVICE_UNAVAILABLE
#define NGX_HTTP_SERVICE_UNAVAILABLE 503
#endif

typedef struct ngx_cycle_s ngx_cycle_t;
typedef int ngx_fd_t;
typedef time_t ngx_mtime_t;

typedef struct ngx_event_s ngx_event_t;

struct ngx_event_s {
    void      (*handler)(ngx_event_t *ev);
    void       *data;
    ngx_log_t  *log;
    unsigned    timer_set;
};

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

ngx_module_t ngx_http_markdown_filter_module;
ngx_str_t ngx_http_markdown_metrics_shm_name = ngx_string("");
ngx_shm_zone_t *ngx_http_markdown_metrics_shm_zone = NULL;

#define ngx_memzero(p, n) memset((p), 0, (n))
#define ngx_memcpy(dst, src, n) memcpy((dst), (src), (n))
#define ngx_strlen(s) strlen((const char *) (s))
#define ngx_close_file(fd) close(fd)

#define NGX_MAX_PATH 1024
#define NGX_FILE_ERROR (-1)
#define NGX_INVALID_FILE (-1)
#define NGX_FILE_RDONLY O_RDONLY
#define NGX_FILE_OPEN 0
#define ngx_file_info_t struct stat
#define ngx_file_info(name, info) stat((const char *) (name), (info))
#define ngx_file_mtime(info) ((info)->st_mtime)

static ngx_uint_t g_reload_counts[256];
static int g_open_fail;
static int g_fd_info_fail;
static int g_read_fail;
static int g_alloc_fail;
static off_t g_forced_size = -1;

static void
test_log_ignore(const char *fmt, ...)
{
    UNUSED(fmt);
}

#undef ngx_log_error
#define ngx_log_error(level, log, err, fmt, ...)             \
    do {                                                     \
        UNUSED(level);                                       \
        UNUSED(log);                                         \
        UNUSED(err);                                         \
        if (0) {                                             \
            test_log_ignore((fmt), ##__VA_ARGS__);           \
        }                                                    \
    } while (0)

static ngx_fd_t
ngx_open_file(u_char *name, int mode, int create, int access)
{
    UNUSED(mode);
    UNUSED(create);
    UNUSED(access);
    if (g_open_fail) {
        return NGX_INVALID_FILE;
    }
    return open((const char *) name, O_RDONLY);
}

static ngx_int_t
test_fd_info(ngx_fd_t fd, struct stat *info)
{
    if (g_fd_info_fail || fstat(fd, info) != 0) {
        return NGX_FILE_ERROR;
    }
    if (g_forced_size >= 0) {
        info->st_size = g_forced_size;
    }
    return 0;
}

#define ngx_fd_info(fd, info) test_fd_info((fd), (info))

static void
ngx_add_timer(ngx_event_t *event, ngx_msec_t timer)
{
    UNUSED(event);
    UNUSED(timer);
}

static void
ngx_del_timer(ngx_event_t *event)
{
    UNUSED(event);
}

#define ngx_time() ((time_t) 1700000000)

static ssize_t
ngx_read_fd(ngx_fd_t fd, void *buf, size_t size)
{
    if (g_read_fail) {
        return -1;
    }
    return read(fd, buf, size);
}

static void *
ngx_alloc(size_t size, ngx_log_t *log)
{
    UNUSED(log);
    if (g_alloc_fail) {
        return NULL;
    }
    return malloc(size);
}

static void
ngx_free(void *ptr)
{
    free(ptr);
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

void
ngx_http_markdown_record_dynconf_reload(ngx_uint_t error_code)
{
    if (error_code < sizeof(g_reload_counts) / sizeof(g_reload_counts[0])) {
        g_reload_counts[error_code]++;
    }
}

#include "../../src/markdown_converter.h"
#include "../../src/ngx_http_markdown_dynconf_impl.h"

void
markdown_dynconf_result_init(FFIDynconfResult *result)
{
    memset(result, 0, sizeof(*result));
    result->error_code = DYNCONF_ERR_INTERNAL;
    result->filter = DYNCONF_NOT_SET_U8;
    result->prune_noise = DYNCONF_NOT_SET_U8;
    result->log_verbosity = DYNCONF_NOT_SET_U8;
    result->error_policy = DYNCONF_NOT_SET_U8;
    result->streaming_buffer = DYNCONF_NOT_SET_U64;
}

void
markdown_dynconf_parse(const uint8_t *data, uintptr_t data_len,
    FFIDynconfResult *result)
{
    static const uint8_t digest[] =
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    static const uint8_t invalid_message[] = "invalid JSON";

    if (data == NULL || data_len == 0
        || (data_len >= sizeof("invalid") - 1
            && memcmp(data, "invalid", sizeof("invalid") - 1) == 0))
    {
        result->error_code = DYNCONF_ERR_INVALID_JSON;
        result->error_message = invalid_message;
        result->error_message_len = sizeof(invalid_message) - 1;
        return;
    }

    result->error_code = DYNCONF_OK;
    result->source_digest = digest;
    result->source_digest_len = 64;
    result->active_digest = digest;
    result->active_digest_len = 64;
    result->filter = DYNCONF_FILTER_ON;
}

void
markdown_dynconf_result_free(FFIDynconfResult *result)
{
    if (result == NULL) {
        return;
    }
    result->error_message = NULL;
    result->error_message_len = 0;
    result->source_digest = NULL;
    result->source_digest_len = 0;
    result->active_digest = NULL;
    result->active_digest_len = 0;
}

static void
reset_state(void)
{
    memset(g_reload_counts, 0, sizeof(g_reload_counts));
    g_open_fail = 0;
    g_fd_info_fail = 0;
    g_read_fail = 0;
    g_alloc_fail = 0;
    g_forced_size = -1;
}

static void
write_file(const char *path, const char *contents)
{
    int fd;
    size_t length;

    fd = open(path, O_WRONLY | O_TRUNC);
    TEST_ASSERT(fd >= 0, "fixture file should open for writing");
    length = strlen(contents);
    TEST_ASSERT(write(fd, contents, length) == (ssize_t) length,
                "fixture file should be written completely");
    close(fd);
}

static void
init_watcher(ngx_http_markdown_dynconf_watcher_t *watcher,
    ngx_http_markdown_conf_t *conf, const char *path)
{
    memset(watcher, 0, sizeof(*watcher));
    memset(conf, 0, sizeof(*conf));
    watcher->path.data = (u_char *) path;
    watcher->path.len = strlen(path);
    watcher->conf = conf;
}

static void
test_failure_paths_are_exact_once(const char *path)
{
    ngx_http_markdown_dynconf_watcher_t watcher;
    ngx_http_markdown_conf_t conf;
    ngx_log_t log;
    ngx_int_t rc;

    TEST_SUBSECTION("production dynconf failures are observable once");

    init_watcher(&watcher, &conf, path);
    g_open_fail = 1;
    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR,
                "open failure should return IO error");
    TEST_ASSERT(g_reload_counts[NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO] == 1,
                "open failure should record one IO error");
    TEST_ASSERT(watcher.diagnostic_state.last_error_len > 0,
                "open failure should set last_error");

    reset_state();
    init_watcher(&watcher, &conf, path);
    g_fd_info_fail = 1;
    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR,
                "fd_info failure should return IO error");
    TEST_ASSERT(g_reload_counts[NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO] == 1,
                "fd_info failure should record one IO error");

    reset_state();
    init_watcher(&watcher, &conf, path);
    g_alloc_fail = 1;
    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR,
                "allocation failure should return IO error");
    TEST_ASSERT(g_reload_counts[NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO] == 1,
                "allocation failure should record one IO error");

    reset_state();
    init_watcher(&watcher, &conf, path);
    g_read_fail = 1;
    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR,
                "read failure should return IO error");
    TEST_ASSERT(g_reload_counts[NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO] == 1,
                "read failure should record one IO error");

    reset_state();
    init_watcher(&watcher, &conf, path);
    g_forced_size = NGX_HTTP_MARKDOWN_DYNCONF_MAX_FILE_SIZE + 1;
    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE,
                "oversized file should return invalid-file status");
    TEST_ASSERT(g_reload_counts[DYNCONF_ERR_TOO_LARGE] == 1,
                "oversized file should record one size error");
    TEST_ASSERT(watcher.diagnostic_state.last_error_len > 0,
                "oversized file should set last_error");

    TEST_PASS("production dynconf failure paths are exact-once");
}

static void
test_ffi_paths(const char *path)
{
    ngx_http_markdown_dynconf_watcher_t watcher;
    ngx_http_markdown_conf_t conf;
    ngx_log_t log;
    ngx_int_t rc;

    TEST_SUBSECTION("production dynconf FFI paths");

    write_file(path, "invalid");
    init_watcher(&watcher, &conf, path);
    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE,
                "invalid FFI result should reject the candidate");
    TEST_ASSERT(g_reload_counts[DYNCONF_ERR_INVALID_JSON] == 1,
                "invalid FFI result should record one parse error");
    TEST_ASSERT(watcher.diagnostic_state.last_error_len > 0,
                "invalid FFI result should set last_error");
    TEST_ASSERT(watcher.legacy_format_warning_logged == 1,
                "legacy input should emit the migration warning once");

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE,
                "repeated legacy input should remain invalid");
    TEST_ASSERT(watcher.legacy_format_warning_logged == 1,
                "legacy migration warning must remain one-time per watcher");

    reset_state();
    write_file(path, "{\"schema_version\":1}");
    init_watcher(&watcher, &conf, path);
    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "valid FFI result should apply the candidate");
    TEST_ASSERT(g_reload_counts[DYNCONF_OK] == 1,
                "successful reload should record one success");
    TEST_ASSERT(watcher.diagnostic_state.last_error_len == 0,
                "successful reload should clear last_error");
    TEST_ASSERT(watcher.active_snapshot.valid == 1,
                "successful reload should publish the snapshot");

    TEST_PASS("production dynconf FFI paths are covered");
}

int
main(void)
{
    char path[] = "/tmp/nginx-markdown-dynconf-production-XXXXXX";
    int fd;

    TEST_SECTION("Dynconf Production Tests");

    fd = mkstemp(path);
    TEST_ASSERT(fd >= 0, "temporary dynconf fixture should be created");
    close(fd);

    reset_state();
    write_file(path, "{\"schema_version\":1}");
    test_failure_paths_are_exact_once(path);
    reset_state();
    test_ffi_paths(path);

    unlink(path);
    TEST_PASS("All dynconf production tests passed");
    return 0;
}
