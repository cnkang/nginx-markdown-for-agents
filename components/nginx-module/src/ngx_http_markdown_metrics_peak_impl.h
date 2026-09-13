#ifndef NGX_HTTP_MARKDOWN_METRICS_PEAK_IMPL_H
#define NGX_HTTP_MARKDOWN_METRICS_PEAK_IMPL_H

/*
 * Run-wide peak gauge update, shared by the streaming and the full-buffer
 * engines.
 *
 * The gauge holds the largest conversion working-set estimate observed since
 * process start, so both engines raise it with a compare-and-swap loop.  The
 * loop lives here once: the local copy of the gauge value is plain (the field
 * itself stays atomic), it holds a single loop, and the retry carries the only
 * exit.  A macro keeps the requirement on ``ngx_atomic_cmp_set`` at the call
 * site, where that helper is always visible.
 */
#define ngx_http_markdown_metrics_update_peak(gauge, peak_bytes)              \
    do {                                                                      \
        ngx_atomic_uint_t  observed_;                                         \
                                                                              \
        observed_ = *(gauge);                                                 \
        while (observed_ < (ngx_atomic_uint_t) (peak_bytes)                    \
               && !ngx_atomic_cmp_set((gauge), observed_,                     \
                                      (ngx_atomic_uint_t) (peak_bytes)))      \
        {                                                                     \
            observed_ = *(gauge);                                             \
        }                                                                     \
    } while (0)

#endif /* NGX_HTTP_MARKDOWN_METRICS_PEAK_IMPL_H */
