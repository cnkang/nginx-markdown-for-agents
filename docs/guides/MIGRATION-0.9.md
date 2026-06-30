# Migration Guide: 0.9.0 (Breaking Release)

0.9.0 is a breaking release. Several legacy directives are removed and replaced
by Config V2 directives. Removed directives are kept as **reject-only stubs**:
the parser entry still exists, but the only behavior is to fail `nginx -t` with
an actionable migration hint. There is **no alias compatibility** and **no
legacy fallback behavior** — this keeps the breaking-release boundary
unambiguous.

This guide is organized by owning spec. Sections are appended as each 0.9.0
spec lands; if a directive you use is not yet listed, consult the
`nginx -t` error message, which always names the replacement.

---

## Trusted proxies / forwarded headers (spec 47)

### `markdown_trust_forwarded_headers` → `markdown_trusted_proxies`

The boolean trust model is removed. A request's forwarded headers
(`Forwarded`, `X-Forwarded-Proto`, `X-Forwarded-Host`) are now honored only
when the request's direct source IP matches a configured trusted-proxy CIDR.

`markdown_trust_forwarded_headers` and the never-shipped
`markdown_forwarded_headers` are reject-only stubs:

```
nginx: [emerg] "markdown_trust_forwarded_headers" directive has been removed
in 0.9.0; use "markdown_trusted_proxies <CIDR>..." instead
(see docs/guides/MIGRATION-0.9.md)
```

**Migration:**

| 0.8.x | 0.9.0 |
|-------|-------|
| `markdown_trust_forwarded_headers on;` (in `http`/`server`/`location`) | `markdown_trusted_proxies <CIDR>...;` (in `http` only — list your proxy ranges) |
| `markdown_trust_forwarded_headers off;` | omit `markdown_trusted_proxies` (the default ignores forwarded headers) |

**Before (0.8.x):**

```nginx
server {
    markdown_trust_forwarded_headers on;
}
```

**After (0.9.0):**

```nginx
http {
    # Honor forwarded headers only from these proxy ranges.
    markdown_trusted_proxies 10.0.0.0/8 172.16.0.0/12 2001:db8::/32;
}
```

### Key differences

- **http context only.** `markdown_trusted_proxies` is rejected in `server` and
  `location` blocks (per-location trust creates a local trust-bypass risk that
  is hard to audit):

  ```
  nginx: [emerg] "markdown_trusted_proxies" directive is only valid in the
  http context, not in server or location (see docs/guides/MIGRATION-0.9.md)
  ```

- **CIDR-gated trust.** Only requests whose direct source IP matches a
  configured CIDR have their forwarded headers honored. A direct public client
  can no longer spoof `X-Forwarded-Host`.

- **IPv4 and IPv6** CIDRs are validated at config time; an invalid CIDR fails
  `nginx -t`:

  ```
  nginx: [emerg] invalid CIDR "10.0.0.0/99" in "markdown_trusted_proxies"
  directive; expected an IPv4 or IPv6 CIDR (e.g. 10.0.0.0/8, 2001:db8::/32)
  or "off"
  ```

- **`off`** disables trust entirely:

  ```nginx
  http {
      markdown_trusted_proxies off;
  }
  ```

- **Source IP** is the direct connection peer (`realip` / PROXY-protocol
  resolved). The `X-Forwarded-For` header is never used as the source IP.

See [CONFIGURATION.md](CONFIGURATION.md) (`markdown_trusted_proxies`) for the
full directive reference and deployment guidance (`realip`, PROXY protocol,
Unix sockets).
