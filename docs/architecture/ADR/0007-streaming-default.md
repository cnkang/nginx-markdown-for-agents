# ADR-0007: Streaming Engine as Default (auto mode)

> Historical decision for the v0.6.0 directive surface. ADR-0023 supersedes
> its active configuration recommendation in v0.9.1; use
> `markdown_streaming off|auto|force`.

**Status**: Superseded by ADR-0023
**Date**: 2026-04-28
**Context**: v0.6.0 Production Readiness Release

## Context

v0.5.x ships streaming engine as opt-in (`markdown_streaming_engine off` by default). After two minor releases of production hardening (0.5.0–0.5.5), streaming engine has:

- Bounded-memory guarantees enforced at runtime
- Pre-commit/post-commit fallback semantics with explicit reason codes
- Differential parity tests against full-buffer output
- Streaming-specific metrics (TTFB, flush count, peak memory estimate, post-commit error)

Operators must currently opt in per-location. This creates adoption friction and means most deployments never benefit from streaming's lower TTFB and bounded memory.

## Decision

Change `markdown_streaming_engine` default from `off` to `auto` in v0.6.0.

In `auto` mode, the engine selector chooses streaming or full-buffer per-request based on:

1. **Content-Length** threshold: responses larger than `markdown_streaming_auto_threshold` (default 32 KiB) use streaming
2. **Transfer-Encoding**: chunked responses always use streaming
3. **Content-Type**: non-HTML content types bypass both engines (existing skip logic)

### New Directive

```
syntax:  markdown_streaming_auto_threshold size
default: markdown_streaming_auto_threshold 32k
context: http, server, location
```

### New Reason Codes

| Reason Code | Semantics |
|---|---|
| `ELIGIBLE_STREAMING_AUTO` | Auto mode selected streaming (Content-Length > threshold or chunked) |
| `ELIGIBLE_FULLBUFFER_AUTO` | Auto mode selected full-buffer (Content-Length <= threshold) |

### Configuration Semantics

| Value | Behavior |
|---|---|
| `off` | Full-buffer only (identical to 0.5.x default) |
| `on` | Streaming only (identical to 0.5.x `on`) |
| `auto` | Per-request selection (new 0.6.0 default) |

## Rationale

1. Streaming engine is production-proven across 0.5.x releases with no known data-loss regressions.
2. `auto` mode provides a safe graduated rollout: small responses use the simpler full-buffer path, large/chunked responses benefit from streaming's bounded memory and lower TTFB.
3. Operators who need identical 0.5.x behavior can set `markdown_streaming_engine off` explicitly — no behavior change for explicit configurations.
4. The auto threshold (32 KiB default) is conservative: full-buffer is cheap for small responses, streaming is beneficial for large ones.

## Consequences

- **Positive**: Operators get streaming benefits by default for large/chunked responses. Smaller responses stay on full-buffer for simplicity. No manual per-location configuration needed for most deployments.
- **Negative**: Default behavior change from 0.5.x. Operators who relied on `off` default and did not set explicit `markdown_streaming_engine` will see auto mode behavior.
- **Mitigation**: Migration guide with explicit rollback instructions. Deprecation notice in 0.6.0 release notes. `markdown_streaming_engine off` produces identical 0.5.x behavior.

## Compatibility Contract

| Default | 0.5.x | 0.6.0 |
|---|---|---|
| `markdown_streaming_engine` | `off` | `auto` |

**Rollback**: Set `markdown_streaming_engine off;` at http level to restore 0.5.x default behavior.

### v0.8.0 Compatibility Bridge Removal

The v0.6.0 compatibility bridge that mapped `markdown_streaming_auto_threshold`
to `markdown_stream_threshold` during configuration merge has been removed in
v0.8.0. The `ngx_http_markdown_streaming_cfg_t` struct, `conf->streaming`
field, and the `ngx_http_markdown_bridge_legacy_stream_values` /
`ngx_http_markdown_merge_streaming_values` functions no longer exist. Runtime
code reads from `conf->stream.*` exclusively. `markdown_streaming_auto_threshold`
is no longer a registered directive — `nginx -t` will fail if it appears in
configuration.

## Implementation Sketch

1. Change `ngx_conf_merge_uint_value(conf->streaming_engine, prev->streaming_engine, NGX_HTTP_MARKDOWN_STREAMING_AUTO)` in merge_conf
2. Add `markdown_streaming_auto_threshold` directive with `ngx_conf_set_size_slot` handler
3. Add `auto_threshold` field to `ngx_http_markdown_conf_t`
4. Update `ngx_http_markdown_select_processing_path()` to check `auto_threshold` in AUTO mode
5. Add `ELIGIBLE_STREAMING_AUTO` and `ELIGIBLE_FULLBUFFER_AUTO` reason codes in `reason.c`
6. Add unit tests for engine selection at threshold boundary
7. Add E2E test for auto mode with Content-Length and chunked responses

## Relationship to Other ADRs

- [ADR-0004](0004-streaming-bounded-memory-conversion.md): this ADR changes the default rollout phase from "disabled by default" (Phase 1) to "auto by default".
- [ADR-0006](0006-otel-integration.md): auto mode selection is observable via OTel span attribute `nginx.markdown.engine`.

## References

- Engine selection logic: `components/nginx-module/src/ngx_http_markdown_streaming_impl.h`
- Streaming mode constants: `components/nginx-module/src/ngx_http_markdown_filter_module.h`
- Config merge: `components/nginx-module/src/ngx_http_markdown_config_core_impl.h`
- Migration guide: `docs/guides/streaming-default-migration.md`

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.8.0 | 2026-06-16 | Kang | Documented v0.8.0 compatibility bridge removal; markdown_streaming_auto_threshold removed |
| 0.6.0 | 2026-04-28 | v060-prod | Initial ADR for streaming-default |
