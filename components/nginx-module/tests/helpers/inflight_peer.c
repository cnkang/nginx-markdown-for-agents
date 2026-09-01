typedef volatile long ngx_atomic_t;

typedef struct {
    ngx_atomic_t  current;
    ngx_atomic_t  high_watermark;
    ngx_atomic_t  overload_total;
} ngx_http_markdown_inflight_t;

extern ngx_http_markdown_inflight_t ngx_http_markdown_g_inflight;

long
test_inflight_peer_current(void)
{
    return ngx_http_markdown_g_inflight.current;
}
