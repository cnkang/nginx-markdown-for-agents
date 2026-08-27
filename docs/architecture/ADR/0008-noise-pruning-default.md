# ADR-0008: Noise Pruning Enabled by Default

**Status**: Accepted
**Date**: 2026-04-28
**Context**: v0.6.0 Production Readiness Release

## Context

v0.5.x ships noise pruning as an opt-in Cargo feature (`prune_noise_regions`).
When enabled, it removes the built-in structural selectors `nav`, `footer`,
and `aside`, which have little value for AI-agent consumption. This typically
reduces output volume by 15–60% depending on page noise ratio. Because
tag-name matching also removes meaningful content inside those regions (for
example a navigation block that contains inline text), pruning is **not**
guaranteed to touch only non-content regions.

Current limitations:
1. Pruning is opt-in at compile time — operators must rebuild with `--features prune_noise_regions`
2. No runtime configuration — selectors stay hardcoded defaults
3. No protection mechanism — operators cannot exclude specific subtrees from pruning
4. No empty-output fallback — if pruning removes everything, the result is an empty Markdown string

## Decision

Make noise pruning a default-enabled feature in v0.6.0 with runtime configuration and protection selectors.

### Feature Gate Change

- Cargo.toml: `default = ["prune_noise_regions"]` (was `default = []`)
- `prune_noise_regions` remains a Cargo feature for conditional compilation, but is now included in default features

### New C Configuration Directives

| Directive | Default | Context | Description |
|---|---|---|---|
| `markdown_prune_noise` | `on` | http, server, location | Enable/disable noise pruning at runtime |
| `markdown_prune_selectors` | built-in defaults | http, server, location | Tag names for regions to prune (replaces defaults) |
| `markdown_prune_protection_selectors` | (empty) | http, server, location | Tag names for regions to protect from pruning |

### Default Prune Selectors

```
nav, footer, aside
```

> **v0.6.0 scope**: Pruning matches by HTML tag name only. CSS
> selector syntax (`.class`, `#id`, `[role="..."]`) defers to a
> future release. Operators should use tag-name selectors.

### Protection Selectors

When an element matches both a prune selector and a protection selector, protection wins. This allows operators to keep entire tag-name families (for example all `nav` elements) while pruning others.

Tag-name-only selectors apply to **all** elements matching the selected tag names. A protection selector such as `nav` protects every `nav` element, not a specific instance. Tag-name selectors alone cannot protect individual nav or footer instances. Class/id-based protection (`.class`, `#id`) defers to a future release.

### Empty-Output Fallback

If pruning removes all content from the output:
1. Log reason code `PRUNE_EMPTY_FALLBACK`
2. Return the unpruned conversion result only when the converter has not
   committed output **and** all consumed input remains fully replayable
3. Increment `prune_empty_fallback_total` metric

When streaming has exceeded the replay buffer or output is post-commit, apply
the configured streaming failure policy instead of returning unpruned content.

This prevents data loss when pruning selectors are too aggressive.

### New FFI Fields

| Struct | Field | Type | Description |
|---|---|---|---|
| `MarkdownOptions` | `prune_noise` | `u32` | 0=off, 1=on |
| `MarkdownOptions` | `prune_selectors_ptr` | `*const c_char` | NUL-delimited selector string (pool-allocated) |
| `MarkdownOptions` | `prune_selectors_len` | `usize` | Byte length of selectors string |
| `MarkdownOptions` | `prune_protection_selectors_ptr` | `*const c_char` | NUL-delimited protection selector string |
| `MarkdownOptions` | `prune_protection_selectors_len` | `usize` | Byte length of protection selectors |

### Rust PruneConfig

```rust
pub struct PruneConfig {
    pub enabled: bool,
    pub selectors: Vec<CssSelector>,
    pub protection_selectors: Vec<CssSelector>,
}
```

## Rationale

1. Noise pruning is the highest-impact feature for AI agent consumers — reducing output volume improves token efficiency and response quality.
2. Making it default-enabled removes the build-time opt-in barrier that prevented most deployments from using it.
3. Runtime configuration (on/off, custom selectors) provides escape hatches for operators who need different behavior.
4. Protection selectors solve the "pruning too much" problem without requiring operators to rebuild with different defaults.
5. Empty-output fallback ensures data safety — aggressive pruning cannot silently drop all content.

## Consequences

- **Positive**: AI agents get cleaner, more focused Markdown by default. Output volume decreases 15–60%. Operators can customize selectors at runtime without rebuild.
- **Negative**: Default behavior change from 0.5.x (where pruning was opt-in). Some HTML pages may have content removed that operators expect to see.
- **Mitigation**: `markdown_prune_noise off` restores 0.5.x behavior. Protection selectors allow fine-grained control. Empty-output fallback prevents data loss. Migration guide documents the change.

## Compatibility Contract

| Default | 0.5.x | 0.6.0 |
|---|---|---|
| `prune_noise_regions` feature | opt-in | default-enabled |
| `markdown_prune_noise` | N/A | `on` |

**Rollback**: Set `markdown_prune_noise off;` at http level, or rebuild with `--no-default-features`.

## Implementation Sketch

1. Cargo.toml: add `prune_noise_regions` to default features
2. Add `prune_noise`, `prune_selectors_ptr/len`, `prune_protection_selectors_ptr/len` to `MarkdownOptions` FFI struct
3. Add `PruneConfig` struct in Rust `pruning.rs`
4. Add `markdown_prune_noise`, `markdown_prune_selectors`, `markdown_prune_protection_selectors` directives in C
5. Add `prune_noise`, `prune_selectors`, `prune_protection_selectors` fields to `ngx_http_markdown_conf_t`
6. Implement protection-selector priority (protection wins over prune)
7. Implement empty-output fallback with `PRUNE_EMPTY_FALLBACK` reason code and metric
8. Update `ngx_http_markdown_prepare_conversion_options()` to set FFI fields
9. Update `decode_options()` in Rust to read new fields
10. Add unit tests for selector matching, protection priority, empty-output fallback
11. Add E2E tests for pruning with custom/protection selectors

## Relationship to Other ADRs

- [ADR-0007](0007-streaming-default.md): pruning applies in both streaming and full-buffer paths. In streaming, pruned subtrees skip at tokenizer level. In full-buffer, pruned nodes drop out during DOM traversal.
- [ADR-0004](0004-streaming-bounded-memory-conversion.md): pruning reduces output volume, which helps streaming stay within memory budget.

## References

- Pruning implementation: `components/rust-converter/src/converter/pruning.rs`
- FFI ABI: `components/rust-converter/src/ffi/abi.rs`
- FFI options: `components/rust-converter/src/ffi/options.rs`
- Archived migration guide: `docs/archive/streaming-default-migration.md`

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.0 | 2026-04-28 | v060-prod | Initial ADR for noise-pruning-default |
