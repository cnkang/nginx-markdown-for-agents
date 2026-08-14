# Profile System Design (Retired)

> **Archived**: The `markdown_profile` directive and the profile system
> described below were **removed in 0.9.2** (see
> [MIGRATION-0.9.2.md](../guides/MIGRATION-0.9.2.md) and
> [0.9.2-breaking-changes.md](../guides/0.9.2-breaking-changes.md)). This
> document stays as a historical design record of the pre-0.9.2
> surface. It does not describe any active 0.9.2 behavior. The named profile
> directive appears here only as an archived reference.

| Field | Value |
|-------|-------|
| Version | 0.9.1 (historical) |
| Feature | Profiles Production Defaults |
| Status | Retired in 0.9.2 |
| Created | 2026-06-28 |
| Retired | 2026-08-05 |

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
- Profiles do not introduce new runtime behavior. They only set existing
  Config V2 directive defaults.
- The profile set is small (three profiles) and frozen at 1.0.0. The project
  may add new profiles after 1.0 (additive-only), but existing profile
  semantics must not change.

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
- `markdown_streaming auto` — large responses stream. Small ones buffer.
- No forced fields — all defaults can be overridden.
- Values are intentionally close to Config V2 built-in defaults to minimize
  migration surprise.

### `streaming_first`

Target: AI agent workloads with large documents where streaming throughput and
low memory usage are the priority.

Key characteristics:
- `markdown_streaming force` (**forced**) — all eligible responses stream.
- `markdown_cache_validation off` (**forced**) — no caching overhead. Streaming
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

When no `markdown_profile` appears in config, only built-in defaults apply (no
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

### Conflict Detection Timing (historical)

When this profile design was active, profile expansion and conflict detection
occurred during configuration parsing for normal startup and reload. `nginx -t`
was one invocation of that same parser, not a separate production-only phase.

---

## Former FFI Boundary Design (historical)

The profile merge/conflict FFI described in earlier revisions was never a
production NGINX consumer. The project removed it before the 0.9.2 pre-v1 freeze
along
with the `markdown_profile` directive. The active generated header therefore
contains no `FFIProfile`, conflict-list, or profile snapshot types/functions.

The profile tables and merge rules above remain as historical design context.
They are not an implementation or compatibility promise.

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

The project can add new profiles following these rules:
1. Reopen the archived design and add a new, explicitly reviewed production
   contract. The former FFI types are not an extension point.
2. Implement `defaults()` for the new profile.
3. Declare any forced fields.
4. Update documentation.
5. Existing profiles must not change semantics.

### Field Coverage

If a future directive should be profile-controlled, add it to
`ProfileDefaults` and update all three profile `defaults()` implementations.
The merge logic handles new fields automatically via the "profile value as
default argument" pattern. The removed `FFIProfile`, conflict-list, and
profile snapshot types are not a future extension point. They remain as
historical context only (see the Former FFI Boundary Design section above).

---

## Related Documents

- [Profile Inventory (historical field mapping)](profile-inventory.md)
- [MIGRATION-0.9.2.md](../guides/MIGRATION-0.9.2.md) — removed surface and replacement directives
- [0.9.2-breaking-changes.md](../guides/0.9.2-breaking-changes.md) — breaking-change reference

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-06 | Kang | Archived: the profile system (including the markdown_profile directive) was removed in 0.9.2. The document remains a historical record |
| 0.9.0 | 2026-06-28 | Kang | Initial creation |
