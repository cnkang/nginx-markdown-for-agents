#ifndef NGX_HTTP_MARKDOWN_METRICS_CONFIG_H
#define NGX_HTTP_MARKDOWN_METRICS_CONFIG_H

/*
 * Per-path metrics walks are disabled in production because their cardinality
 * is not bounded.  Debug builds may opt in explicitly, while unit tests may
 * define NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED before including this header.
 * Keep this policy in one place so every metrics renderer uses the same gate.
 */
#ifndef NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED
#ifdef MARKDOWN_METRICS_PER_PATH_DEBUG
#define NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED  1
#else
#define NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED  0
#endif
#endif

#endif /* NGX_HTTP_MARKDOWN_METRICS_CONFIG_H */
