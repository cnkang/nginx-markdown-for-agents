---
domain: memory-budget
rules: [3, 43]
paths:
  - "components/nginx-module/src/**"
  - "components/rust-converter/src/**"
---

## Memory Budget

### 3. Memory leaks and budget bypass in streaming/decompression
Historical issues: `23165d9`, `2c7d6a9`, `0eae34b`, `1b0df51`.

Required:
- Enforce all configured budgets (including total working-set budget), not only per-buffer budgets.
- Any auxiliary heap expansion buffer must be explicitly freed on all exits; copy final data back to pool-owned memory if needed.
- Any collector strings/buffers (for example link text, sniff buffers) must be bounded by configured limits.
- Track peak memory from real resident state, not only counters.
- Do not hardcode stage limits in downstream components when a configured
  budget field already exists (for example charset sniff bytes, stack bytes,
  output buffer bytes). Thread budget values through constructors and enforce
  them at the runtime write/check site.
- Budget guard helper APIs and production enforcement must not drift: if
  helper methods exist for stage checks, production code should call them; if
  a helper is not part of production enforcement, remove it in the same change
  set. Avoid parallel inline checks and helper checks that can diverge.
- Never apply hidden floor/ceiling behavior that weakens a configured budget
  (for example enforcing a minimum depth that exceeds `state_stack` for small
  budgets) unless explicitly documented in spec and tested as intended behavior.


### Rule 43 — Resizable buffer backing store allocation

- `ngx_http_markdown_buffer_t::data` is a resizable backing store
  allocated exclusively with `ngx_alloc()` (NOT pool allocation).
  Always release with `ngx_free()`.
- The design rationale (documented in `buffer.c:36-39` and
  `buffer.c:220-224`): pool allocations cannot be individually freed,
  so repeated buffer expansions on large responses would grow the pool
  indefinitely.  `ngx_alloc/ngx_free` allows superseded buffers to be
  released immediately.
- A pool cleanup handler (`ngx_pool_cleanup_add`) is registered at
  `buffer_init` time to ensure the backing store is freed when the
  request pool is destroyed, even if no explicit `ngx_free` is called.
- When code outside `buffer.c` needs to replace `ctx->buffer.data`
  (e.g. decompression output exceeds capacity), it MUST use `ngx_alloc`
  for the new allocation and `ngx_free` for the old — never `ngx_palloc`
  or `ngx_pfree`.
- **Generalized pool-vs-heap rule**: Any buffer that is independently
  allocated, resized, or freed during request processing — not only
  `ctx->buffer.data` but also streaming decompression workspaces (for
  example zlib `finish()` output buffers, `apply_limits` temporary
  buffers, buffer-expansion scratch space) — MUST use `ngx_alloc` /
  `ngx_free`.  Pool-allocating such a buffer and later calling
  `ngx_free` on it is undefined behavior: `ngx_free` calls the system
  `free()`, which expects a heap pointer from `malloc`/`ngx_alloc`, not
  a pool-internal pointer from `ngx_palloc`/`ngx_pcalloc`.  Conversely,
  a buffer allocated with `ngx_alloc` must never be freed with
  `ngx_pfree` — use `ngx_free` exclusively.
- **Decompression finish workspace**: The zlib `finish()` path may
  need to grow its output buffer when compressed data expands beyond
  the initial allocation.  This workspace must be `ngx_alloc`-backed
  so it can be `ngx_free`-d on every exit path (success, error, and
  overflow).  Leaking the workspace on an error path causes a
  per-request heap leak that accumulates under load.
  **Exception**: Fixed-size decompression workspaces that are NOT
  resized and have pool-lifetime semantics may use `ngx_pnalloc`/
  `ngx_pfree` to avoid mixing allocation lifetimes. Historical issue:
  commit `1956124f` switched decompression from heap to pool-backed
  to prevent pool expansion side effects.
- **Free-on-all-exits discipline**: Every `ngx_alloc`-backed buffer
  introduced in a streaming or decompression path must have a matching
  `ngx_free` on every exit path — success, error, overflow, and
  panic/abort.  Use `goto` cleanup labels or NGINX pool cleanup handlers
  to ensure no exit skips the free.  When adding a new exit path to an
  existing function that already holds an `ngx_alloc`-backed buffer,
  audit all exits in the same changeset.

Verification:
- `grep -n 'ngx_p.*alloc.*buffer\\\\.data\\|ngx_pfree.*buffer'
  components/nginx-module/src/` must return zero hits.
- `grep -rn 'ngx_palloc\\|ngx_pcalloc\\|ngx_pnalloc' components/nginx-module/src/`
  — for each hit in a streaming or decompression source file, verify the
  resulting pointer is NOT later passed to `ngx_free`.
- `grep -rn 'ngx_free' components/nginx-module/src/` — for each hit,
  trace the freed pointer back to its allocation and confirm it was
  `ngx_alloc`-backed, not pool-allocated.
- `grep -rn 'ngx_alloc' components/nginx-module/src/` — for each hit,
  verify every exit path from the allocating function calls `ngx_free`.
