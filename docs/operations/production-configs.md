# Production Configuration Examples

Ready-to-use NGINX configurations for common production deployment patterns.
Each example includes the complete configuration, security annotations,
verification commands, and operational notes.

## Available Examples

| Example | Profile | Use Case |
|---------|---------|----------|
| [blog-balanced](../../examples/production/blog-balanced.conf) | `balanced` | General-purpose blog/CMS with trusted proxies and metrics |
| [docs-strict-cache](../../examples/production/docs-strict-cache.conf) | `strict_cache` | Documentation site with CDN/caching proxy and full ETag |
| [rag-streaming-first](../../examples/production/rag-streaming-first.conf) | `streaming_first` | RAG/AI workload with large documents and inflight guard |
| [private-internal](../../examples/production/private-internal.conf) | `balanced` | Internal service with basic auth and restricted access |

The `private-internal` example intentionally keeps its Basic-authenticated
backend on `127.0.0.1`. A co-located TLS terminator is mandatory and must be
the only client-facing endpoint; clients must never send credentials directly
to the cleartext backend listener.

## Choosing a Profile

- **`balanced`** — recommended starting point for most deployments. Full-buffer
  by default with auto-mode streaming for large responses. Suitable for blogs,
  CMS sites, and general web applications.

- **`strict_cache`** — optimized for CDN and caching proxy deployments. Full
  ETag support with content-based validation. Streaming is disabled to ensure
  deterministic cache keys.

- **`streaming_first`** — optimized for AI agent workloads that fetch large
  documents. Streaming engine is always active with elevated inflight limits.
  Trade-off: no full ETag support (uses If-Modified-Since only).

## Usage

1. Copy the example closest to your use case.
2. Adjust `upstream`, `listen`, and CIDR ranges for your environment.
3. Run `nginx -t` to validate.
4. Deploy and verify with the curl commands included in each example.

## Related Documentation

- [Configuration Reference](../guides/CONFIGURATION.md)
- [Deployment Examples](../guides/DEPLOYMENT_EXAMPLES.md)
- [Migration Guide: 0.9.0](../guides/MIGRATION-0.9.md)
- [Operations Guide](../guides/OPERATIONS.md)
