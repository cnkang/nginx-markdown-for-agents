#ifndef NGX_HTTP_MARKDOWN_METRICS_CONFIG_H
#define NGX_HTTP_MARKDOWN_METRICS_CONFIG_H

/*
 * Per-path metrics walks are disabled in production because their cardinality
 * is not bounded.  Debug builds and unit tests must opt in through the
 * named debug macro so a standalone definition cannot accidentally expose
 * snapshot->per_path in a production build.
 */
#ifndef NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED
#ifdef MARKDOWN_METRICS_PER_PATH_DEBUG
#define NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED  1
#else
#define NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED  0
#endif
#elif NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED \
      && !defined(MARKDOWN_METRICS_PER_PATH_DEBUG)
#error "per-path metrics walks require MARKDOWN_METRICS_PER_PATH_DEBUG"
#endif

#endif /* NGX_HTTP_MARKDOWN_METRICS_CONFIG_H */
