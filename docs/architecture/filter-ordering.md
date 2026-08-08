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
| Standard (compiled-in) | `proxy` → `proxy_cache` → `gunzip` → `gzip` → `brotli` (ngx_brotli) |
| Dynamic (load_module) | `markdown_filter` (this module) |

The runtime initialises dynamic modules after standard modules, so
`markdown_filter` registers its header and body filters **after** all
standard filters.  This means:

> **The markdown filter runs FIRST (closest to upstream) in the response
> filter chain, before gzip/gunzip/Brotli/proxy_cache body filters.**

### 1.2 Data Flow Diagram

```text
Upstream response (HTML, possibly Content-Encoding: gzip/br)
  │
  ▼
┌─────────────────────────────────┐
│ markdown header filter          │  ← runs first (top of chain)
│  · eligibility / Accept check   │
│  · decompression detection       │
│  · streaming vs full-buffer      │
│  · HeaderPlan apply              │
└─────────────────────────────────┘
  │ (passes headers downstream)
  ▼
┌─────────────────────────────────┐
│ markdown body filter            │  ← runs first (top of chain)
│  · HTML → Markdown conversion    │
│  · streaming or full-buffer      │
│  · auto-decompress if needed     │
└─────────────────────────────────┘
  │ (Markdown body downstream)
  ▼
┌─────────────────────────────────┐
│ gunzip body filter (if enabled) │  ← decompresses upstream gzip
│  · operates on upstream bytes    │
│  · sees Markdown if markdown     │
│    already converted             │
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
┌─────────────────────────────────┐
│ brotli body filter (if present) │  ← compresses response for client
│  · compresses Markdown output    │
│  · adds Content-Encoding: br     │
└─────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────┐
│ proxy_cache header/body filter  │  ← caches the final response
│  · caches converted Markdown     │
│  · serves cached Markdown on hit │
└─────────────────────────────────┘
  │
  ▼
Client response (Markdown, possibly gzip/br compressed)
```

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

**Key invariant:** The gzip filter compresses the **converted Markdown**,
not the original HTML.  Because markdown runs first in the chain, gzip
sees Markdown bytes.

### 2.2 markdown + gunzip (upstream sends gzip)

| Property | Value |
|----------|-------|
| Client sends | `Accept: text/markdown` |
| Upstream sends | `Content-Encoding: gzip` (HTML bytes compressed) |
| gunzip decompresses | gzip → HTML (if `gunzip on`) |
| markdown converts | HTML → Markdown |
| Client receives | `Content-Type: text/markdown; charset=utf-8`, no `Content-Encoding` |

**Two decompression paths:**

1. **gunzip filter** (`gunzip on` in nginx.conf): The gunzip filter
   decompresses upstream gzip **before** markdown sees the body.  Markdown
   receives plain HTML and converts it.  Use `markdown_auto_decompress off`
   to avoid double-decompression.

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

**Key invariant:** If the Brotli filter module (`ngx_brotli`) loads,
it compresses the **converted Markdown** output.  Markdown's built-in
decompression handles upstream Brotli regardless of whether the Brotli
filter is present, because the Brotli filter is a **compressor** (response
out), not a **decompressor** (response in).  Upstream Brotli
decompression is always handled by markdown's `auto_decompress`.

### 2.4 markdown + proxy_cache

| Property | Value |
|----------|-------|
| First request | Upstream HTML → markdown converts → Markdown stored in cache |
| Subsequent requests | Cache hit → Markdown served directly (no re-conversion) |
| Client receives | `Content-Type: text/markdown; charset=utf-8` (cached) |

**Key invariant:** `proxy_cache` caches the **final response** after all
filters have run.  Because markdown runs before proxy_cache in the chain,
the cached entity is the converted Markdown, not the original HTML.
Subsequent cache hits serve Markdown directly without re-conversion.

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
| on     | off                      | gunzip decompresses | markdown decompresses | gunzip for gzip, markdown for br |
| on     | on (default)             | **double-decompress risk** | markdown decompresses | Avoid: set auto_decompress off |
| off    | off                      | passthrough (gzip to client) | passthrough (br to client) | No decompression; client handles |

---

## 4. E2E Test Coverage

E2E test script: `tests/e2e/filter_ordering_test.sh`

| Test | Interaction | Verifies |
|------|-------------|----------|
| 1 | markdown + gzip | Client sends `Accept-Encoding: gzip`, receives `Content-Encoding: gzip` + `Content-Type: text/markdown` |
| 2 | markdown + gunzip | Upstream sends gzip, gunzip decompresses, markdown converts, client receives `text/markdown` uncompressed |
| 3 | markdown + Brotli | Client sends `Accept-Encoding: br`, receives `Content-Encoding: br` + `Content-Type: text/markdown` |
| 4 | markdown + proxy_cache | First request converts + caches, second request serves cached Markdown with correct Content-Type |
| 5 | markdown + no compression | Client sends no Accept-Encoding, receives `text/markdown` uncompressed |