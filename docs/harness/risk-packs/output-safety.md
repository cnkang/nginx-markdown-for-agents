# Output Safety Pack

Use this as the primary pack when Markdown output escaping, link emission, URL
handling, header value forwarding, or content-injection prevention changes.

## Triggers

- touched Markdown emitter, link-writer, URL-normalizer, or escaping helper
  code in the Rust converter or NGINX module
- touched forwarded-header extraction (for example `X-Forwarded-Host`,
  `X-Forwarded-Proto`)
- keywords like `escape`, `sanitize`, `link label`, `link destination`,
  `autolink`, `X-Forwarded`, `injection`, or `content injection`

## Risks

- Unescaped brackets in Markdown link labels break link structure and enable
  content-injection attacks
- Unescaped parentheses in link destinations break Markdown rendering and may
  inject malicious URLs
- Percent-encoded control characters in URLs bypass scheme-prefix validation
- Forwarded header values with control characters, path separators, or
  malformed IPv6 brackets bypass host validation
- New `write!` / `format!` emission sites that interpolate raw strings instead
  of calling the shared escaping helper

## Common Supporting Packs

- `runtime-streaming` when streaming emitter changes affect link or URL output
- `ffi-boundary` when escaping helpers cross the Rust/C boundary
- `nginx-protocol-safety` when forwarded header validation changes request
  routing or base-URL computation
- `docs-tooling-drift` when operator docs describe link/URL behavior

## Sync Points

- Every link-label emission site calls `escape_link_label` (or C equivalent)
- Every link-destination / autolink emission site calls
  `escape_link_destination` (or C equivalent)
- URL validation rejects percent-encoded control characters (`%00`–`%1F`,
  `%7F`) before scheme checks
- Forwarded header extraction: first-hop only, control chars/spaces/path
  separators rejected, IPv6 bracket literals validated, fallback to server
  name on invalid input
- Rust escaping iterates over `chars()`, not `bytes()`, so multi-byte code
  points are handled atomically
- No new `write!` / `format!` that interpolates untrusted text in Markdown
  link labels `[...]` or destinations `(...)` / `<...>`

## Minimum Verification

```bash
make harness-check
cargo test --features security
make test-rust
make test-nginx-unit
```

## Canonical References

- [../../../AGENTS.md](../../../AGENTS.md) (Rule 27)
- [../../guides/CONFIGURATION.md](../../guides/CONFIGURATION.md)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.1 | 2026-05-06 | Kang | Initial pack from 14-day defect pattern analysis |
