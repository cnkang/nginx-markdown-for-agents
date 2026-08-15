# Filter Ordering: Markdown Module Position Relative to gzip, gunzip, Brotli, proxy_cache

| Field | Value |
|-------|-------|
| Scope | Filter chain ordering and interaction with compression / cache modules |
| Task | 6.17 — Document filter ordering + E2E interaction tests |
| Requirements | 10.7, 15.3 |
| Date | 2026-08-05 |
| Status | **Active** |

---

## 1. NGINX Filter Chain Model

NGINX executes response filters as a singly-linked chain.  Each filter
module registers itself during `postconfiguration` by saving the current
`ngx_http_top_*_filter` into a module-static `next_*_filter` and then
installing its own handler as the new top:

```c
ngx_http_next_header_filter = ngx_http_top_header_filter;
ngx_http_top_header_filter   = my_header_filter;

ngx_http_next_body_filter   = ngx_http_top_body_filter;
ngx_http_top_body_filter     = my_body_filter;
```

Because each module pushes itself onto the **top**, the **last** module to
register runs **first** when a response flows through the chain.

### 1.1 Registration Order

| Phase | Modules (in registration order) |
|-------|--------------------------------|
| Standard (compiled-in) | `gzip` → `gunzip` |
| Optional (ngx_brotli, load_module or compiled-in) | `brotli` |
| Dynamic (load_module) | `markdown_filter` (this module) |

Because each module prepends itself, the **runtime** chain is the reverse of
the registration order: `markdown_filter` → `brotli` → `gunzip` → `gzip`.
`gunzip` is conditional: it decompresses only gzip-encoded upstream
responses, and only when the client does not accept gzip (when the client
accepts gzip, gunzip passes the content through untouched so the gzip
filter can re-compress the converted output). `brotli` and `gzip` do not
stack: the response receives a single `Content-Encoding` — whichever
compression filter runs first for the negotiated encoding.

The runtime initialises the loaded module according to the effective NGINX
module configuration.  The supported default path uses the markdown filter's
own bounded decompressor before conversion.  It does not depend on the
optional `gunzip` filter.  When operators configure an external decompressor,
they must verify its effective position for that NGINX build and set
`markdown_auto_decompress off`.

The standard decompression/compression registration order is `gzip` before
`gunzip`. Because each filter prepends itself, `gunzip` runs before `gzip` at
runtime. `ngx_http_proxy_module` supplies upstream content and is not an
output-filter stage in this diagram. Cache handling is likewise outside the
conversion/output-filter sequence shown below.

### 1.2 Data Flow Diagram

```text
Upstream response (HTML, possibly Content-Encoding: gzip/br)
  │
  ▼
┌─────────────────────────────────┐
│ markdown header filter          │  ← first when registered at the top
│  · eligibility / Accept check   │
│  · decompression detection       │
│  · streaming vs full-buffer      │
│  · HeaderPlan apply              │
└─────────────────────────────────┘
  │ (passes headers downstream)
  ▼
┌─────────────────────────────────┐
│ markdown body filter            │  ← first when registered at the top
│  · HTML → Markdown conversion    │
│  · streaming or full-buffer      │
│  · auto-decompress if needed     │
└─────────────────────────────────┘
  │ (Markdown body downstream)
  ▼
┌─────────────────────────────────┐
│ brotli body filter (if present) │  ← compresses response for client
│  · compresses Markdown output    │
│  · adds Content-Encoding: br     │
└─────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────┐
│ gunzip body filter (conditional)│  ← decompresses gzip-encoded upstream
│  · only when client rejects gzip│     responses for clients that do not
│  · strips Content-Encoding: gzip│     accept gzip; passes through otherwise
└─────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────┐
│ gzip body filter                │  ← compresses response for client
│  · compresses Markdown output    │
│  · adds Content-Encoding: gzip   │
└─────────────────────────────────┘
  │
  ▼
Client response (Markdown, possibly gzip/br compressed)
```

When operators enable an external decompressor and the effective chain places
it before Markdown, the body path is instead:

```text
Upstream response (gzip HTML)
  │
  ▼
┌─────────────────────────────────┐
│ gunzip body filter              │  ← external pre-decompressor
│  · removes upstream compression  │
└─────────────────────────────────┘
  │ (plain HTML)
  ▼
┌─────────────────────────────────┐
│ markdown body filter            │  ← converts the decompressed body
└─────────────────────────────────┘
  │
  ▼
Client response (Markdown)
```

This external path is separate from the normal chain above. Operators must
verify the effective order for their NGINX build and set
`markdown_auto_decompress off` to prevent double decompression.

---

## 2. Ordering Interactions

### 2.1 markdown + gzip (client requests gzip output)

| Property | Value |
|----------|-------|
| Client sends | `Accept-Encoding: gzip` |
| Upstream sends | uncompressed HTML |
| markdown converts | HTML → Markdown |
| gzip compresses | Markdown → gzip bytes |
| Client receives | `Content-Type: text/markdown; charset=utf-8`, `Content-Encoding: gzip` |

**Key invariant:** The gzip filter compresses the **converted Markdown** when
the effective chain places markdown before gzip. Verify that order for the
target NGINX build. Otherwise, gzip can see the original HTML.

### 2.2 markdown + gunzip (upstream sends gzip)

| Property | Value |
|----------|-------|
| Client sends | `Accept: text/markdown` |
| Upstream sends | `Content-Encoding: gzip` (HTML bytes compressed) |
| markdown auto-decompresses | gzip → HTML (default built-in path) |
| markdown converts | HTML → Markdown |
| Client receives | `Content-Type: text/markdown; charset=utf-8`, no `Content-Encoding` |

**Two decompression paths:**

1. **gunzip filter** (`gunzip on` in nginx.conf): Use this only when the
   effective filter chain places gunzip before markdown.  In that arrangement
   gunzip decompresses upstream gzip before markdown sees the body.  Markdown
   receives plain HTML and converts it.  Set
   `markdown_auto_decompress off` to avoid double-decompression.

2. **markdown built-in auto-decompress** (`markdown_auto_decompress on`,
   default): Markdown's own body filter decompresses gzip/deflate/Brotli
   from upstream.  No `gunzip` directive needed.  This is the preferred
   path for Brotli and for zero-config deployments.

**Conflict prevention:** When `gunzip on` is active, set
`markdown_auto_decompress off` to avoid double-decompression.  The module
detects already-decompressed content via `Content-Encoding` header absence
after gunzip has stripped it.

### 2.3 markdown + Brotli (client requests Brotli output)

| Property | Value |
|----------|-------|
| Client sends | `Accept-Encoding: br` |
| Upstream sends | uncompressed HTML |
| markdown converts | HTML → Markdown |
| Brotli compresses | Markdown → Brotli bytes |
| Client receives | `Content-Type: text/markdown; charset=utf-8`, `Content-Encoding: br` |

**Key invariant:** If the Brotli filter module (`ngx_brotli`) loads, it must be
downstream of the Markdown body filter so that it compresses the **converted
Markdown** output. Markdown's built-in
decompression handles upstream Brotli regardless of whether the Brotli
filter is present, because the Brotli filter is a **compressor** (response
out), not a **decompressor** (response in).  Upstream Brotli
decompression is always handled by markdown's `auto_decompress`.

### 2.4 markdown + proxy_cache

| Property | Value |
|----------|-------|
| First request | Upstream HTML → markdown converts → Markdown delivered to client; the cache stores the upstream response as received (original HTML, before output filters) |
| Subsequent requests | Cache hit → the stored upstream HTML is replayed through the output-filter chain, so markdown converts it again → Markdown delivered |
| Client receives | `Content-Type: text/markdown; charset=utf-8` on every request (converted on first request and on each cache hit) |

**Key invariant:** `proxy_cache` stores the response at the proxy-module
level — the upstream HTML as received, before the markdown body filter (and
other output filters) transform it. A cache hit replays those stored bytes
through the same filter chain, so the markdown filter runs again on every
request. The cache never serves Markdown without re-conversion. Because the
cached body is the original HTML, the response still varies by the same
request properties (such as `Accept` and any bot-targeting variables) as a
non-cached response, so `proxy_cache_key` must include those properties to
keep HTML and Markdown variants separated (see
[CACHE_AWARE_RESPONSES.md](../features/CACHE_AWARE_RESPONSES.md)).

**cache_validation interaction (Requirement 15.3):**
- `cache_validation full` requires full-buffer conversion to compute the
  transformed ETag.  The 304 response uses the transformed ETag (Markdown
  bytes digest), never the upstream HTML ETag.
- `streaming force` + `cache_validation full` is a conflicting configuration
  rejected at `nginx -t` time.
- `streaming auto` + `cache_validation full` deterministically falls back
  to full-buffer conversion.

---

## 3. Configuration Matrix

| gunzip | markdown_auto_decompress | Upstream gzip | Upstream br | Behaviour |
|--------|--------------------------|---------------|-------------|-----------|
| off    | on (default)             | markdown decompresses | markdown decompresses | Recommended: single decompressor |
| on     | off                      | gunzip decompresses only if it runs before markdown | no module decompression; an earlier Brotli decoder is required | External gzip path; verify the Brotli path |
| on     | on (default)             | **double-decompress risk if gunzip runs before markdown** | markdown decompresses | Avoid: set auto_decompress off for that chain |
| off    | off                      | passthrough (gzip to client) | passthrough (br to client) | No decompression; client handles |

---

## 4. E2E Test Coverage

E2E test script: `tests/e2e/filter_ordering_test.sh`

| Test | Interaction | Verifies |
|------|-------------|----------|
| 1 | markdown + gzip | Client sends `Accept-Encoding: gzip`, receives `Content-Encoding: gzip` + `Content-Type: text/markdown` |
| 2 | markdown + gunzip | Upstream sends gzip, gunzip decompresses, markdown converts, client receives `text/markdown` uncompressed |
| 3 | markdown + Brotli | Client sends `Accept-Encoding: br`, receives `Content-Encoding: br` + `Content-Type: text/markdown` |
| 4 | markdown + proxy_cache | First request converts and caches the upstream HTML; a cache hit replays the stored HTML through the markdown filter chain and converts it again, so the client receives fresh Markdown with correct Content-Type — the cache never serves pre-converted Markdown |
| 5 | markdown + no compression | Client sends no Accept-Encoding, receives `text/markdown` uncompressed |
