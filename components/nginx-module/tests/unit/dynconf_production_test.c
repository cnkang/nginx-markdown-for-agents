/*
 * Test: dynconf_production
 *
 * Exercises the production Rust-backed dynconf wrapper.  The target
 * deliberately compiles without test-only implementation substitutions so
 * file and FFI failure accounting cannot regress unnoticed.
 */

#if defined(__linux__) && !defined(_DEFAULT_SOURCE)
#define _DEFAULT_SOURCE
#endif

#include "../include/test_common.h"

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define MARKDOWN_STREAMING_ENABLED 1

#include "../../src/ngx_http_markdown_filter_module.h"

#ifndef NGX_OK
#define NGX_OK 0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR (-1)
#endif
#ifndef NGX_CONF_UNSET
#define NGX_CONF_UNSET (-1)
#endif
#ifndef NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED
#define NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED 0
#endif
#ifndef NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE
#define NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE 1
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
#ifndef NGX_LOG_ERR
#define NGX_LOG_ERR 1
#endif
#ifndef NGX_LOG_DEBUG
#define NGX_LOG_DEBUG 8
#endif
#ifndef NGX_LOG_WARN
#define NGX_LOG_WARN 2
#endif
#ifndef NGX_HTTP_GET
#define NGX_HTTP_GET 0x0002
#endif
#ifndef NGX_HTTP_HEAD
#define NGX_HTTP_HEAD 0x0004
#endif
#ifndef NGX_HTTP_OK
#define NGX_HTTP_OK 200
#endif
#ifndef NGX_HTTP_NOT_ALLOWED
#define NGX_HTTP_NOT_ALLOWED 405
#endif
#ifndef NGX_HTTP_INTERNAL_SERVER_ERROR
#define NGX_HTTP_INTERNAL_SERVER_ERROR 500
#endif
#ifndef NGX_HTTP_FORBIDDEN
#define NGX_HTTP_FORBIDDEN 403
#endif
#ifndef NGINX_VERSION
#define NGINX_VERSION "1.26.3"
#endif
#ifndef ngx_pid
#define ngx_pid 1234
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

typedef struct ngx_connection_s ngx_connection_t;

struct ngx_connection_s {
    ngx_log_t        *log;
    struct sockaddr  *sockaddr;
};

typedef struct {
    ngx_uint_t  hash;
    ngx_str_t   key;
    ngx_str_t   value;
} ngx_table_elt_t;

typedef struct ngx_list_part_s ngx_list_part_t;

struct ngx_list_part_s {
    void            *elts;
    ngx_uint_t       nelts;
    ngx_list_part_t *next;
};

typedef struct {
    ngx_list_part_t  part;
    ngx_list_part_t *last;
} ngx_list_t;

struct ngx_chain_s {
    ngx_buf_t   *buf;
    ngx_chain_t *next;
};

typedef struct {
    ngx_uint_t  status;
    size_t      content_type_len;
    ngx_str_t   content_type;
    u_char     *content_type_lowcase;
    ngx_uint_t  content_type_hash;
    off_t       content_length_n;
    ngx_list_t  headers;
} ngx_http_headers_out_t;

struct ngx_http_request_s {
    ngx_uint_t              method;
    ngx_pool_t             *pool;
    ngx_connection_t       *connection;
    ngx_http_headers_out_t  headers_out;
    ngx_http_request_t     *main;
    void                    *loc_conf;
};

ngx_module_t ngx_http_markdown_filter_module;
ngx_str_t ngx_http_markdown_metrics_shm_name = ngx_string("");
ngx_shm_zone_t *ngx_http_markdown_metrics_shm_zone = NULL;

#define ngx_memzero(p, n) memset((p), 0, (n))
#define ngx_memcpy(dst, src, n) memcpy((dst), (src), (n))
#define ngx_strlen(s) strlen((const char *) (s))
#define ngx_close_file(fd) close(fd)
#define ngx_strcmp(s1, s2) strcmp((const char *) (s1), (const char *) (s2))
#define ngx_str_set(str, text)                         \
    do {                                               \
        (str)->len = sizeof(text) - 1;                 \
        (str)->data = (u_char *) (text);               \
    } while (0)

#define NGX_MAX_PATH 1024
#define NGX_FILE_ERROR (-1)
#define NGX_INVALID_FILE (-1)
#define NGX_FILE_RDONLY O_RDONLY
#define NGX_FILE_NONBLOCK O_NONBLOCK
#define NGX_FILE_OPEN 0
#define ngx_file_info_t struct stat
#define ngx_file_info(name, info) stat((const char *) (name), (info))
#define ngx_file_mtime(info) ((info)->st_mtime)

static ngx_uint_t g_reload_counts[256];
static int g_open_fail;

/*
 * Watchdog for special-file regression coverage: if a reload ever latches
 * inside open() on a writer-less FIFO again, SIGALRM aborts the test with
 * an explicit message instead of letting CI time out.
 */
static void
test_fifo_watchdog(int signo)
{
    const char  msg[] =
        "\nFAIL: dynconf reload hung inside open() on a FIFO "
        "(blocking-open regression)\n";

    UNUSED(signo);
    if (write(STDERR_FILENO, msg, sizeof(msg) - 1) < 0) {
        /* diagnostics are best-effort during watchdog exit */
    }
    _exit(2);
}
static int g_fd_info_fail;
static int g_read_fail;
static int g_alloc_fail;
static int g_invalid_digest;
static ngx_uint_t g_parse_calls;
static off_t g_forced_size = -1;
static ngx_uint_t g_masked_fields_warns;

static void
test_log_capture(ngx_uint_t level, const char *fmt)
{
    if (level == NGX_LOG_WARN && fmt != NULL
        && strstr(fmt, "masked by") != NULL)
    {
        g_masked_fields_warns++;
    }
}

#undef ngx_log_error
#define ngx_log_error(level, log, err, fmt, ...)             \
    do {                                                     \
        test_log_capture((level), (fmt));                    \
        UNUSED(level);                                       \
        UNUSED(log);                                         \
        UNUSED(err);                                         \
    } while (0)

static ngx_fd_t
ngx_open_file(u_char *name, int mode, int create, int access)
{
    UNUSED(create);
    UNUSED(access);
    if (g_open_fail) {
        return NGX_INVALID_FILE;
    }
    /* Honor the caller's access-mode flags (including O_NONBLOCK) so
     * special-file handling behaves like the real nginx wrapper. */
    return open((const char *) name, mode);
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

static ngx_http_markdown_dynconf_watcher_t ngx_http_markdown_dynconf_watcher;
static ngx_http_markdown_metrics_t *ngx_http_markdown_metrics;

ngx_atomic_uint_t
ngx_http_markdown_pending_output_current(void)
{
    return 0;
}

static ngx_atomic_int_t
ngx_http_markdown_inflight_current(void)
{
    return 0;
}

static ngx_atomic_int_t
ngx_http_markdown_inflight_overload_total(void)
{
    return 0;
}

/* Keep the dynconf accessor production-real while using narrow fixtures for
 * unrelated diagnostics fields in this focused contract binary. */
#undef NGINX_MARKDOWN_CONVERTER_H
#include "../../src/ngx_http_markdown_diagnostics_accessors_impl.h"

void
ngx_http_markdown_diagnostics_get_effective(
    const void *opaque_conf, ngx_http_markdown_diag_effective_t *out)
{
    const ngx_http_markdown_conf_t *conf;

    if (out == NULL) {
        return;
    }
    memset(out, 0, sizeof(*out));
    conf = (const ngx_http_markdown_conf_t *) opaque_conf;
    if (conf == NULL) {
        return;
    }
    out->filter = conf->enabled;
    out->prune_noise = conf->advanced.prune_noise;
    out->log_verbosity = conf->policy.log_verbosity;
    out->error_policy = conf->on_error;
    out->error_status = conf->error_status;
    out->streaming_buffer = conf->stream.budget;
}

ngx_int_t
ngx_http_markdown_diagnostics_get_static_digest(
    const void *request, ngx_pool_t *pool, u_char *out, size_t out_len)
{
    static const u_char digest[] =
        "sha256:fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210";

    UNUSED(request);
    UNUSED(pool);
    if (out == NULL || out_len < sizeof(digest)) {
        return NGX_ERROR;
    }
    memcpy(out, digest, sizeof(digest));
    return NGX_OK;
}

ngx_int_t
ngx_http_markdown_get_reason_code_str(uint32_t code, ngx_str_t *out_str)
{
    static u_char reason[] = "converted";

    UNUSED(code);
    if (out_str == NULL) {
        return NGX_ERROR;
    }
    out_str->data = reason;
    out_str->len = sizeof(reason) - 1;
    return NGX_OK;
}

static void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return malloc(size);
}

static ngx_table_elt_t g_diagnostics_header;

static void *
ngx_list_push(ngx_list_t *list)
{
    UNUSED(list);
    memset(&g_diagnostics_header, 0, sizeof(g_diagnostics_header));
    return &g_diagnostics_header;
}

static void *
ngx_http_get_module_loc_conf(ngx_http_request_t *request, ngx_module_t module)
{
    UNUSED(module);
    return request == NULL ? NULL : request->loc_conf;
}

static ngx_int_t
ngx_http_discard_request_body(ngx_http_request_t *request)
{
    UNUSED(request);
    return NGX_OK;
}

static ngx_int_t
ngx_http_send_header(ngx_http_request_t *request)
{
    UNUSED(request);
    return NGX_OK;
}

static ngx_int_t
ngx_http_output_filter(ngx_http_request_t *request, ngx_chain_t *out)
{
    UNUSED(request);
    UNUSED(out);
    return NGX_OK;
}

static u_char *
ngx_slprintf(u_char *buf, u_char *last, const char *fmt, ...)
{
    char translated[1024];
    char *dst;
    const char *src;
    va_list args;
    int n;
    size_t remaining;

    if (buf == NULL || last == NULL || buf >= last || fmt == NULL) {
        return last;
    }

    dst = translated;
    remaining = sizeof(translated);
    for (src = fmt; *src != '\0' && remaining > 1; src++) {
        if (*src == '%' && src[1] == 'P' && remaining >= 4) {
            *dst++ = '%';
            *dst++ = 'd';
            src++;
            remaining -= 2;
        } else if (*src == '%' && src[1] == 'M' && remaining >= 4) {
            *dst++ = '%';
            *dst++ = 'l';
            *dst++ = 'u';
            src++;
            remaining -= 3;
        } else if (*src == '%' && src[1] == 'u'
                   && src[2] == 'A' && remaining >= 4) {
            *dst++ = '%';
            *dst++ = 'l';
            *dst++ = 'u';
            src += 2;
            remaining -= 3;
        } else if (*src == '%' && src[1] == 'u'
                   && src[2] == 'i' && remaining >= 4) {
            *dst++ = '%';
            *dst++ = 'l';
            *dst++ = 'u';
            src += 2;
            remaining -= 3;
        } else if (*src == '%' && src[1] == 'u'
                   && src[2] == 'z' && remaining >= 4) {
            *dst++ = '%';
            *dst++ = 'z';
            *dst++ = 'u';
            src += 2;
            remaining -= 3;
        } else if (remaining >= 2) {
            *dst++ = *src;
            remaining--;
        } else {
            break;
        }
    }
    *dst = '\0';

    va_start(args, fmt);
    n = vsnprintf((char *) buf, (size_t) (last - buf), translated, args);
    va_end(args);
    if (n < 0 || (size_t) n >= (size_t) (last - buf)) {
        return last;
    }
    return buf + n;
}

#include "../../src/ngx_http_markdown_diagnostics.c"

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

static int
test_contains_bytes(const uint8_t *data, uintptr_t data_len,
    const char *needle)
{
    uintptr_t offset;
    size_t    needle_len;

    if (data == NULL || needle == NULL) {
        return 0;
    }

    needle_len = strlen(needle);
    if (needle_len > data_len) {
        return 0;
    }

    for (offset = 0; offset <= data_len - needle_len; offset++) {
        if (memcmp(data + offset, needle, needle_len) == 0) {
            return 1;
        }
    }

    return 0;
}

uint32_t
markdown_sha256_hex(const uint8_t *data, uintptr_t data_len,
    uint8_t *output, uintptr_t output_len)
{
    static const uint8_t digest[] =
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    static const uint8_t digest_v2[] =
        "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789";
    const uint8_t *selected_digest;

    if (output == NULL || output_len < 64
        || (data == NULL && data_len != 0))
    {
        return DYNCONF_ERR_INVALID_TYPE;
    }

    selected_digest = test_contains_bytes(data, data_len,
                                          "\"schema_version\":2")
        ? digest_v2
        : digest;
    memcpy(output, selected_digest, 64);
    return DYNCONF_OK;
}

void
markdown_dynconf_parse(const uint8_t *data, uintptr_t data_len,
    FFIDynconfResult *result)
{
    static const uint8_t digest[] =
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    static const uint8_t digest_v2[] =
        "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789";
    static const uint8_t invalid_message[] = "invalid JSON";
    static uint8_t invalid_digest[64];
    const uint8_t *selected_digest;

    g_parse_calls++;

    if (data == NULL || data_len == 0
        || (data_len >= sizeof("invalid") - 1
            && memcmp(data, "invalid", sizeof("invalid") - 1) == 0))
    {
        result->error_code = DYNCONF_ERR_INVALID_JSON;
        result->error_message = invalid_message;
        result->error_message_len = sizeof(invalid_message) - 1;
        if (markdown_sha256_hex(data, data_len,
                                invalid_digest, sizeof(invalid_digest))
            == DYNCONF_OK)
        {
            result->source_digest = invalid_digest;
            result->source_digest_len = sizeof(invalid_digest);
        }
        return;
    }

    selected_digest = digest;
    if (test_contains_bytes(data, data_len, "\"schema_version\":2"))
    {
        selected_digest = digest_v2;
    }

    result->error_code = DYNCONF_OK;
    result->source_digest = selected_digest;
    result->source_digest_len = g_invalid_digest ? 63 : 64;
    result->active_digest = selected_digest;
    result->active_digest_len = g_invalid_digest ? 63 : 64;
    if (test_contains_bytes(data, data_len, "\"filter\":\"off\"")) {
        result->filter = DYNCONF_FILTER_OFF;
    } else if (test_contains_bytes(data, data_len, "\"filter\":\"on\"")) {
        result->filter = DYNCONF_FILTER_ON;
    }
    if (test_contains_bytes(data, data_len, "\"prune_noise\":\"off\""))
    {
        result->prune_noise = DYNCONF_PRUNE_NOISE_OFF;
    } else if (test_contains_bytes(data, data_len,
                                   "\"prune_noise\":\"on\""))
    {
        result->prune_noise = DYNCONF_PRUNE_NOISE_ON;
    }
    if (test_contains_bytes(data, data_len,
                            "\"log_verbosity\":\"debug\""))
    {
        result->log_verbosity = DYNCONF_LOG_DEBUG;
    }
    if (test_contains_bytes(data, data_len,
                            "\"error_policy\":\"pass\""))
    {
        result->error_policy = DYNCONF_POLICY_PASS;
    }
    if (test_contains_bytes(data, data_len,
                            "\"streaming_buffer\":65536"))
    {
        result->streaming_buffer = 65536;
    }
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
    g_invalid_digest = 0;
    g_parse_calls = 0;
    g_forced_size = -1;
    g_masked_fields_warns = 0;
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
test_fifo_watch_path_is_rejected_promptly(void)
{
    char fifo_path[128];
    ngx_http_markdown_dynconf_watcher_t watcher;
    ngx_http_markdown_conf_t conf;
    ngx_log_t log;
    ngx_int_t rc;

    TEST_SUBSECTION("special-file watch paths fail fast instead of latching");

    snprintf(fifo_path, sizeof(fifo_path),
             "/tmp/nginx-markdown-dynconf-fifo-%ld",
             (long) getpid());
    TEST_ASSERT(mkfifo(fifo_path, 0600) == 0,
                "fixture FIFO should be created");

    /* A blocking O_RDONLY open() on a writer-less FIFO parks the caller
     * inside open(); with the non-blocking production open this reload
     * must return an IO error instead of hanging here. */
    signal(SIGALRM, test_fifo_watchdog);
    alarm(30);
    reset_state();
    init_watcher(&watcher, &conf, fifo_path);
    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &log);
    alarm(0);

    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR,
                "FIFO watch path should return IO error");
    TEST_ASSERT(g_reload_counts[NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO] == 1,
                "FIFO watch path should record one IO error");

    alarm(0);
    unlink(fifo_path);
    TEST_PASS("FIFO watch path fails fast without latching the worker");
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
    TEST_ASSERT(watcher.diagnostic_state.last_rejected_source_digest[0]
                    != '\0',
                "invalid FFI result should record source digest");
    TEST_ASSERT(watcher.diagnostic_state.last_rejected_error_len > 0,
                "invalid FFI result should record rejection error");
    {
        ngx_uint_t parse_calls = g_parse_calls;

        rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &log);
        TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE,
                    "repeated invalid input should remain invalid");
        TEST_ASSERT(g_parse_calls == parse_calls,
                    "unchanged rejected content should skip reparsing");
    }
    reset_state();
    write_file(path, "{\"schema_version\":1,\"filter\":\"on\"}");
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

    reset_state();
    write_file(path, " \n\t{\"schema_version\":1}");
    init_watcher(&watcher, &conf, path);
    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "JSON with leading OWS should apply successfully");
    reset_state();
    g_invalid_digest = 1;
    init_watcher(&watcher, &conf, path);
    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE,
                "malformed digest should reject the candidate");
    TEST_ASSERT(g_reload_counts[DYNCONF_ERR_INTERNAL] == 1,
                "malformed digest should record one internal error");
    TEST_ASSERT(watcher.diagnostic_state.last_error_len > 0,
                "malformed digest should set a bounded last_error");

    TEST_PASS("production dynconf FFI paths are covered");
}

static void
test_unset_dry_run_is_not_validation_only(const char *path)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_log_t                             log;
    ngx_int_t                             rc;

    TEST_SUBSECTION("production dynconf unset dry-run flag");

    reset_state();
    write_file(path, "{\"schema_version\":1,\"filter\":\"on\"}");
    init_watcher(&watcher, &conf, path);
    conf.advanced.dynconf_dry_run = NGX_CONF_UNSET;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "unset dry-run flag should use normal reload behavior");
    TEST_ASSERT(watcher.active_snapshot.valid == 1,
                "unset dry-run flag should publish the valid snapshot");
    TEST_ASSERT(watcher.digest_state.generation == 1,
                "unset dry-run flag should advance the active generation");

    TEST_PASS("unset dry-run flag is treated as off");
}

static void
test_successful_reload_is_idempotent(const char *path)
{
    ngx_http_markdown_dynconf_watcher_t  watcher;
    ngx_http_markdown_conf_t             conf;
    ngx_log_t                             log;
    ngx_int_t                             rc;
    ngx_uint_t                            generation;
    ngx_uint_t                            version;
    time_t                                last_success;
    ngx_flag_t                            active_enabled;

    TEST_SUBSECTION("production dynconf identical reload");

    reset_state();
    write_file(path, "{\"schema_version\":1,\"filter\":\"on\"}");
    init_watcher(&watcher, &conf, path);
    conf.advanced.dynconf_block_mask = NGX_HTTP_MARKDOWN_BLOCK_FILTER;
    watcher.active_snapshot.valid = 1;
    watcher.active_snapshot.enabled = 0;
    watcher.active_snapshot.prune_noise = 1;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "first production reload should apply");
    TEST_ASSERT(watcher.last_known_good.enabled == 0,
                "first reload should preserve the bootstrap LKG snapshot");
    TEST_ASSERT(watcher.active_snapshot.enabled == 1,
                "first reload should publish the active snapshot");
    TEST_ASSERT(g_masked_fields_warns == 1,
                "first changed candidate should emit one masked-field warning");

    generation = watcher.digest_state.generation;
    version = watcher.diagnostic_state.version;
    last_success = watcher.diagnostic_state.last_success;
    active_enabled = watcher.active_snapshot.enabled;

    rc = ngx_http_markdown_dynconf_reload(&watcher, &conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE,
                "identical source content should be a no-change reload");
    TEST_ASSERT(watcher.digest_state.generation == generation,
                "no-change reload must not increment generation");
    TEST_ASSERT(watcher.diagnostic_state.version == version,
                "no-change reload must not increment version");
    TEST_ASSERT(watcher.diagnostic_state.last_success == last_success,
                "no-change reload must not update last_success");
    TEST_ASSERT(watcher.active_snapshot.enabled == active_enabled,
                "no-change reload must preserve active snapshot");
    TEST_ASSERT(g_masked_fields_warns == 1,
                "no-change reload must not emit a masked-field warning");
    TEST_ASSERT(g_reload_counts[DYNCONF_OK] == 1,
                "no-change reload must not increment success metrics");
    TEST_ASSERT(watcher.diagnostic_state.last_result
                == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE,
                "no-change reload records its result code");

    TEST_PASS("production dynconf identical reload is a no-op");
}


static void
test_reload_replaces_omitted_fields(const char *path)
{
    ngx_http_markdown_dynconf_watcher_t  sequence_watcher;
    ngx_http_markdown_dynconf_watcher_t  direct_watcher;
    ngx_http_markdown_conf_t             sequence_conf;
    ngx_http_markdown_conf_t             direct_conf;
    ngx_log_t                            log;
    ngx_int_t                            rc;
    static const char                    first_config[] =
        "{\"schema_version\":1,\"filter\":\"off\","
        "\"prune_noise\":\"off\",\"log_verbosity\":\"debug\","
        "\"error_policy\":\"pass\",\"streaming_buffer\":65536}";
    static const char                    partial_config[] =
        "{\"schema_version\":2}";

    TEST_SUBSECTION("dynconf replacement resets omitted fields");

    init_watcher(&sequence_watcher, &sequence_conf, path);
    sequence_conf.enabled = 1;
    sequence_conf.advanced.prune_noise = 1;
    sequence_conf.policy.log_verbosity = 2;
    sequence_conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    sequence_conf.error_status = NGX_HTTP_SERVICE_UNAVAILABLE;
    sequence_conf.stream.budget = 131072;
    sequence_conf.limits.conversion_memory = 524288;
    ngx_http_markdown_dynconf_snapshot_from_conf(
        &sequence_watcher.static_snapshot, &sequence_conf);
    ngx_http_markdown_dynconf_snapshot_from_conf(
        &sequence_watcher.active_snapshot, &sequence_conf);

    write_file(path, first_config);
    rc = ngx_http_markdown_dynconf_reload(
        &sequence_watcher, &sequence_conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "first generation should apply");
    TEST_ASSERT(sequence_watcher.active_snapshot.enabled == 0,
                "first generation should turn filter off");
    TEST_ASSERT(sequence_watcher.active_snapshot.prune_noise == 0,
                "first generation should turn prune_noise off");
    TEST_ASSERT(sequence_watcher.active_snapshot.log_verbosity == 3,
                "first generation should set debug logging");
    TEST_ASSERT(sequence_watcher.active_snapshot.error_policy
                == NGX_HTTP_MARKDOWN_ON_ERROR_PASS,
                "first generation should set pass policy");
    TEST_ASSERT(sequence_watcher.active_snapshot.streaming_budget == 65536,
                "first generation should set streaming budget");

    write_file(path, partial_config);
    rc = ngx_http_markdown_dynconf_reload(
        &sequence_watcher, &sequence_conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "partial second generation should apply");
    TEST_ASSERT(sequence_watcher.active_snapshot.enabled == 1,
                "omitted filter must return to the static baseline");
    TEST_ASSERT(sequence_watcher.active_snapshot.prune_noise == 1,
                "omitted prune_noise must return to the static baseline");
    TEST_ASSERT(sequence_watcher.active_snapshot.log_verbosity == 2,
                "omitted log verbosity must return to the static baseline");
    TEST_ASSERT(sequence_watcher.active_snapshot.error_policy
                == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT,
                "omitted error policy must return to the static baseline");
    TEST_ASSERT(sequence_watcher.active_snapshot.error_status
                == NGX_HTTP_SERVICE_UNAVAILABLE,
                "omitted error status must return to the static baseline");
    TEST_ASSERT(sequence_watcher.active_snapshot.streaming_budget == 131072,
                "omitted streaming budget must return to the static baseline");
    TEST_ASSERT(sequence_watcher.active_snapshot.conversion_memory == 524288,
                "omitted conversion memory must return to the static baseline");

    init_watcher(&direct_watcher, &direct_conf, path);
    direct_conf.enabled = 1;
    direct_conf.advanced.prune_noise = 1;
    direct_conf.policy.log_verbosity = 2;
    direct_conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    direct_conf.error_status = NGX_HTTP_SERVICE_UNAVAILABLE;
    direct_conf.stream.budget = 131072;
    direct_conf.limits.conversion_memory = 524288;
    ngx_http_markdown_dynconf_snapshot_from_conf(
        &direct_watcher.static_snapshot, &direct_conf);
    ngx_http_markdown_dynconf_snapshot_from_conf(
        &direct_watcher.active_snapshot, &direct_conf);
    rc = ngx_http_markdown_dynconf_reload(
        &direct_watcher, &direct_conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "direct partial generation should apply");
    TEST_ASSERT(direct_watcher.active_snapshot.enabled
                == sequence_watcher.active_snapshot.enabled,
                "direct and cross-generation filter state must agree");
    TEST_ASSERT(direct_watcher.active_snapshot.prune_noise
                == sequence_watcher.active_snapshot.prune_noise,
                "direct and cross-generation prune state must agree");
    TEST_ASSERT(direct_watcher.active_snapshot.log_verbosity
                == sequence_watcher.active_snapshot.log_verbosity,
                "direct and cross-generation log state must agree");
    TEST_ASSERT(direct_watcher.active_snapshot.error_policy
                == sequence_watcher.active_snapshot.error_policy,
                "direct and cross-generation error policy must agree");
    TEST_ASSERT(memcmp(direct_watcher.digest_state.active_digest,
                       sequence_watcher.digest_state.active_digest,
                       sizeof(direct_watcher.digest_state.active_digest)) == 0,
                "direct and cross-generation active digests must agree");
    TEST_ASSERT(direct_watcher.diagnostic_state.last_result
                == sequence_watcher.diagnostic_state.last_result,
                "direct and cross-generation diagnostics result must agree");

    TEST_PASS("dynconf replacement is independent of reload history");
}


static void
test_diagnostics_renderer_tracks_dynconf_lkg(const char *path)
{
    static const char first_config[] =
        "{\"schema_version\":1,\"filter\":\"on\"}";
    static const char second_config[] =
        "{\"schema_version\":2,\"filter\":\"on\"}";
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_diag_dynconf_t dynconf;
    ngx_http_request_t request;
    ngx_connection_t connection;
    ngx_http_markdown_conf_t *request_conf;
    ngx_buf_t buffer;
    ngx_cycle_t cycle;
    ngx_pool_t pool;
    ngx_log_t log;
    ngx_str_t path_str;
    ngx_int_t rc;
    char expected_lkg[128];
    char first_active[NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN + 1];
    const char *json;

    TEST_SUBSECTION("dynconf watcher to diagnostics renderer contract");

    reset_state();
    memset(&ngx_http_markdown_dynconf_watcher, 0,
           sizeof(ngx_http_markdown_dynconf_watcher));
    write_file(path, first_config);
    memset(&conf, 0, sizeof(conf));
    conf.advanced.dynconf_block_mask = NGX_HTTP_MARKDOWN_BLOCK_FILTER;
    cycle.pool = &pool;
    cycle.log = &log;
    path_str.data = (u_char *) path;
    path_str.len = strlen(path);

    rc = ngx_http_markdown_dynconf_start(
        &ngx_http_markdown_dynconf_watcher, &cycle, &path_str, &conf, &log);
    TEST_ASSERT(rc == NGX_OK, "watcher init should apply the first valid file");
    TEST_ASSERT(ngx_http_markdown_dynconf_watcher.digest_state.generation == 1,
                "watcher init should publish the first generation");
    TEST_ASSERT(ngx_http_markdown_dynconf_watcher.digest_state.lkg_valid == 1,
                "static snapshot should become bootstrap LKG");
    TEST_ASSERT(ngx_http_markdown_dynconf_watcher.digest_state.lkg_digest[0]
                == '\0',
                "bootstrap static LKG must not receive a dynconf digest");
    TEST_ASSERT(g_masked_fields_warns == 1,
                "first changed candidate should emit one masked-field warning");

    memcpy(first_active,
           ngx_http_markdown_dynconf_watcher.digest_state.active_digest,
           NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN);
    first_active[NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN] = '\0';

    memset(&request, 0, sizeof(request));
    memset(&connection, 0, sizeof(connection));
    request.pool = &pool;
    request.connection = &connection;
    request.main = &request;
    request_conf = &conf;
    request.loc_conf = request_conf;
    connection.log = &log;
    memset(&buffer, 0, sizeof(buffer));
    rc = ngx_http_markdown_diagnostics_build_json(&request, &buffer);
    TEST_ASSERT(rc == NGX_OK, "first diagnostics render should succeed");
    json = (const char *) buffer.pos;
    TEST_ASSERT(strstr(json, "\"lkg_digest\":null") != NULL,
                "bootstrap static LKG must render a JSON null digest");
    TEST_ASSERT(strstr(json, "\"lkg_digest\":\"\"") == NULL,
                "bootstrap LKG must not render an empty-string digest");
    ngx_http_markdown_diagnostics_get_dynconf_state(&dynconf);
    TEST_ASSERT(dynconf.lkg_valid == 1 && dynconf.lkg_digest[0] == '\0',
                "real diagnostics accessor must preserve bootstrap empty state");

    write_file(path, second_config);
    rc = ngx_http_markdown_dynconf_reload(
        &ngx_http_markdown_dynconf_watcher, &conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED,
                "changed valid file should publish a second generation");
    TEST_ASSERT(g_masked_fields_warns == 2,
                "second changed candidate should emit a second warning");

    memset(&buffer, 0, sizeof(buffer));
    rc = ngx_http_markdown_diagnostics_build_json(&request, &buffer);
    TEST_ASSERT(rc == NGX_OK, "second diagnostics render should succeed");
    json = (const char *) buffer.pos;
    snprintf(expected_lkg, sizeof(expected_lkg),
             "\"lkg_digest\":\"%s\"", first_active);
    TEST_ASSERT(strstr(json, expected_lkg) != NULL,
                "second render must expose the previous active digest as LKG");

    write_file(path, "invalid");
    rc = ngx_http_markdown_dynconf_reload(
        &ngx_http_markdown_dynconf_watcher, &conf, &log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE,
                "invalid file should preserve the previous LKG");
    memset(&buffer, 0, sizeof(buffer));
    rc = ngx_http_markdown_diagnostics_build_json(&request, &buffer);
    TEST_ASSERT(rc == NGX_OK, "failed reload diagnostics render should succeed");
    json = (const char *) buffer.pos;
    TEST_ASSERT(strstr(json, "\"state\":\"lkg_preserved\"") != NULL,
                "failed reload should render the LKG-preserved state");
    TEST_ASSERT(strstr(json, expected_lkg) != NULL,
                "failed reload must retain the previous active LKG digest");

    TEST_PASS("Production watcher, accessor, renderer, and schema shape agree");
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
    test_fifo_watch_path_is_rejected_promptly();
    reset_state();
    test_ffi_paths(path);
    test_unset_dry_run_is_not_validation_only(path);
    test_successful_reload_is_idempotent(path);
    test_reload_replaces_omitted_fields(path);
    test_diagnostics_renderer_tracks_dynconf_lkg(path);

    unlink(path);
    TEST_PASS("All dynconf production tests passed");
    return 0;
}
