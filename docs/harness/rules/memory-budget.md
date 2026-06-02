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
- Verification: `grep -n 'ngx_p.*alloc.*buffer\\.data\|ngx_pfree.*buffer'
  components/nginx-module/src/` must return zero hits.
