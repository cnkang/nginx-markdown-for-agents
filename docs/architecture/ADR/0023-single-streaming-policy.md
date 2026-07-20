# ADR-0023: Single Public Streaming Policy Before v1.0

## Status

Accepted

## Context

The Config V2 surface exposed both `markdown_streaming off|auto|force` and
`markdown_streaming_engine off|auto|on`. Production tracing showed that the
engine field did not select a separate backend with an independent operator
use case. It only gated the same full-buffer or streaming path already owned
by the policy, while profiles and the Rust effective-configuration model used
the policy as their source of truth.

Keeping both controls would freeze contradictory inheritance, diagnostics,
dynconf, and FFI state into v1.0.

The experimental `markdown_flavor mdx` and `org-mode` values were also
registered without distinct converter behavior. Accepting them implied an
output contract the implementation did not provide.

## Decision

`markdown_streaming off|auto|force` is the sole public processing-path
selector:

- `off` requires the full-buffer path.
- `auto` uses the response size and shape after hard eligibility gates.
- `force` selects streaming for every eligible response after hard request,
  content-type, and cache-validation gates.

`markdown_streaming_engine` remains only as a reject-only parser entry so
`nginx -t` reports an exact migration:

| Removed value | Replacement |
|---------------|-------------|
| `off` | `markdown_streaming off` |
| `auto` | `markdown_streaming auto` |
| `on` | `markdown_streaming force` |

There is no compatibility alias. The redundant C, Rust decision, profile,
dynconf, and FFI fields are removed in the same v0.9.1 baseline reset.

`markdown_flavor` supports only `commonmark` and `gfm`. The non-semantic `mdx`
and `org-mode` values are rejected with an actionable message.

## Consequences

Operators upgrading from v0.9.0 must update the removed directive before
reload. Helm values move from `markdown.streaming.engine` to
`markdown.streaming.mode`. Diagnostics, examples, and tests now describe one
policy and one inheritance chain.

Metrics such as `nginx_markdown_streaming_engine_choice_total` retain their
names because they report the processing engine actually selected at runtime;
they are not configuration selectors.

Historical ADRs 0007 and 0013 remain accurate records of their release-time
decisions. This ADR supersedes only their active directive recommendation.

## Alternatives Considered

- Keep both selectors: rejected because no independent runtime behavior or
  operator use case was found.
- Preserve a silent alias: rejected because it would perpetuate two public
  names and hide incomplete migrations.
- Keep MDX and Org-mode as experimental values: rejected because the converter
  emitted no distinct format to test or preserve.

## References

- [ADR-0007: Streaming Engine as Default](0007-streaming-default.md)
- [ADR-0013: Streaming Default Policy](0013-streaming-default-policy.md)
- [ADR-0015: Config V2 Breaking Migration](0015-090-config-v2-breaking-migration.md)
- [Configuration Guide](../../guides/CONFIGURATION.md)

## Date

2026-07-14

## Authors

Codex

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.1 | 2026-07-14 | Codex | Established one public streaming policy and removed non-semantic flavor selectors before v1.0 |
