# Production Configuration Examples

Ready-to-use NGINX configurations for common production deployment patterns.
Each example includes the complete configuration, security annotations,
verification commands, and operational notes.

## Available Examples

| Example | Streaming policy | Use Case |
|---------|---------|----------|
| [blog-balanced](../../examples/production/blog-balanced.conf) | `auto` | General-purpose blog/CMS with trusted proxies and metrics |
| [docs-strict-cache](../../examples/production/docs-strict-cache.conf) | `off` | Documentation site with CDN/caching proxy and full ETag |
| [rag-streaming-first](../../examples/production/rag-streaming-first.conf) | `force` | RAG/AI workload with large documents and inflight guard |
| [private-internal](../../examples/production/private-internal.conf) | `auto` | Internal service with basic auth and restricted access |

The `private-internal` example intentionally keeps its Basic-authenticated
backend on `127.0.0.1`. A co-located TLS terminator is mandatory and must be
the only client-facing endpoint; clients must never send credentials directly
to the cleartext backend listener.

## Choosing a Streaming Policy

- **`auto`** — recommended starting point for most deployments. The module
  chooses streaming only when its bounded eligibility heuristic and cache
  constraints allow it.

- **`off`** — optimized for deployments that require full-buffer conversion and
  deterministic cache validation.

- **`force`** — optimized for AI agent workloads that fetch large documents.
  It requests streaming whenever the response is otherwise eligible; full
  cache validation can still require the bounded full-buffer path.

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
