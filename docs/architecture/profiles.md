# Profile System Design

| Field | Value |
|-------|-------|
| Version | 0.9.0 |
| Feature | Profiles Production Defaults |
| Status | Implemented |
| Created | 2026-06-28 |

---

## Design Rationale

The module exposes 50+ configuration directives. While this granularity is
useful for advanced operators, it creates a high-friction onboarding experience
and increases the risk of invalid combinations (such as enabling full ETag
generation alongside forced streaming).

Profiles address this by providing named, tested combinations of defaults that
cover the most common deployment patterns. A single `markdown_profile` directive
replaces many individual settings with a coherent, validated configuration
preset.

Design constraints:

- Profiles are **additive defaults**, not opaque presets — operators retain full
  visibility into what each profile sets and can override most fields.
- Profiles do not introduce new runtime behavior; they only set existing
  Config V2 directive defaults.
- The profile set is small (three profiles) and frozen at 1.0.0. New profiles
  may be added after 1.0 (additive-only), but existing profile semantics must
  not change.

---

## The Three Profiles

### `strict_cache`

Target: CDN / caching proxy deployments where conditional request support
(full ETag + If-None-Match) is the priority.

Key characteristics:
- `markdown_cache_validation full` — generates a transformed Markdown-variant
  ETag for every converted response.
- `markdown_streaming off` (**forced**) — streaming cannot produce an ETag
  because headers commit before the body is fully known.
- Tighter resource limits (8m memory, 2s timeout) than the built-in defaults.

### `balanced`

Target: general-purpose deployment. Recommended starting point for most sites.

Key characteristics:
- `markdown_cache_validation ims_only` — avoids ETag computation overhead
  while still supporting `If-Modified-Since` via the upstream's
  `Last-Modified`.
- `markdown_streaming auto` — large responses stream; small ones buffer.
- No forced fields — all defaults can be overridden.
- Values are intentionally close to Config V2 built-in defaults to minimize
  migration surprise.

### `streaming_first`

Target: AI agent workloads with large documents where streaming throughput and
low memory usage are the priority.

Key characteristics:
- `markdown_streaming force` (**forced**) — all eligible responses stream.
- `markdown_cache_validation off` (**forced**) — no caching overhead; streaming
  responses cannot carry an ETag.
- `markdown_accept wildcard` — converts on `*/*` and `text/*` Accept headers,
  which many AI crawlers send.

---

## Merge Order

```text
effective = builtin_defaults
if profile is set:
    effective.apply(profile.defaults())     ← profile overrides builtins
effective.apply(explicit_directives)        ← explicit overrides profile
```

Priority (highest first):
1. Explicit directives written in `nginx.conf`.
2. Profile defaults from the active `markdown_profile`.
3. Config V2 built-in defaults (compile-time constants).

When no `markdown_profile` is declared, only built-in defaults apply (no
implicit profile). This is intentional — 0.9.0 is a breaking release and does
not default to any profile.

---

## Forced Fields and Conflict Rules

### Profile Forced Fields

Certain profiles force specific values to maintain internal consistency. An
explicit directive that sets a conflicting value causes `nginx -t` to fail.

| Profile | Forced Field | Forced Value | Rationale |
|---------|-------------|:---:|-----------|
| `strict_cache` | `markdown_streaming` | off | Full ETag requires complete buffered output |
| `streaming_first` | `markdown_cache_validation` | off | Streaming cannot generate transformed ETag |
| `streaming_first` | `markdown_streaming` | force | Profile purpose is streaming-first |

`balanced` has no forced fields.

### General Conflict Rules (profile-independent)

These apply regardless of whether a profile is active:

| Combination | Level | Explanation |
|-------------|-------|-------------|
| `cache_validation full` + `streaming force` | error | Mutually exclusive (full ETag requires buffering) |
| `cache_validation full` + `streaming auto` | warning | Streaming blocked at runtime; suggest `ims_only` |
| Duplicate `markdown_profile` in same context | error | Only one profile per block |
| Unknown profile name | error | Directive parse failure |

### Conflict Detection Timing

- **`nginx -t`**: all conflicts are detected at configuration test time.
- **Dynconf dry-run**: conflict detection also runs during dynamic
  configuration validation.

---

## FFI Boundary Design

Profile logic is implemented entirely in Rust. The C module handles only:
1. Parsing the `markdown_profile` directive (enum value storage).
2. Calling the Rust merge/conflict FFI during config merge.
3. Applying the resulting effective values to the C config struct.

### FFI Types

```text
FFIProfile          — enum: None, StrictCache, Balanced, StreamingFirst
FFIProfileDefaults  — struct: all profile-relevant field values
FFIConflict         — struct: level (error/warning) + message
FFIConflictLevel    — enum: Error, Warning
```

### FFI Functions

```text
markdown_profile_get_defaults(profile) → FFIProfileDefaults
markdown_detect_conflicts(profile, explicit_flags, effective) → FFIConflict[]
```

Profile expansion happens at config parse time. There is no runtime overhead —
the effective config is computed once during `nginx -t` and cached in the
merged `ngx_http_markdown_conf_t` struct.

---

## Inheritance

`markdown_profile` follows standard NGINX configuration inheritance:

- A `server` block inherits the `http`-level profile unless it declares its own.
- A `location` block inherits from `server`.
- A child context's explicit `markdown_profile` fully replaces the parent's
  profile (no profile "stacking" or merging between contexts).

---

## Future Extensibility

### Adding New Profiles (post-1.0)

New profiles can be added following these rules:
1. Add a variant to `Profile` enum (Rust) and `FFIProfile` (C header).
2. Implement `defaults()` for the new profile.
3. Declare any forced fields.
4. Update documentation.
5. Existing profiles must not change semantics.

### Field Coverage

If a future directive should be profile-controlled, add it to
`ProfileDefaults` and update all three profile `defaults()` implementations.
The merge logic handles new fields automatically via the "profile value as
default argument" pattern.

---

## Related Documents

- [Configuration Guide — Profiles section](../guides/CONFIGURATION.md#profiles)
- [Deployment Examples — Profile-Based Deployments](../guides/DEPLOYMENT_EXAMPLES.md#profile-based-deployments-v090)
- [Profile Inventory (field mapping)](profile-inventory.md)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.0 | 2026-06-28 | Kang | Initial creation |
