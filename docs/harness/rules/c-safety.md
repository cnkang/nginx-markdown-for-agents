---
domain: c-safety
rules: [24, 42]
paths:
  - "components/nginx-module/src/**"
  - "components/nginx-module/include/**"
---

## C99 Declaration & Type Safety

### 24. C99 declaration/type safety and integer conversion hardening
Historical issues: Sonar `c:S819`, `c:S5276`, `c:S859`, `c:S995`, `c:S5350`.

Required:
- No implicit declarations anywhere in C paths. Every called symbol must have
  a visible prototype at the call site in the same translation unit (including
  implementation headers analyzed standalone). If include order cannot
  guarantee visibility, add explicit forward declarations in the impl header.
- Do not silence declaration warnings by relying on compiler extensions.
  Implicit-int/implicit-function behavior is forbidden; C99-or-newer strict
  semantics are the baseline.
- Any narrowing conversion (`size_t/off_t/ngx_int_t` to narrower integer types
  such as `uInt`, `int`, `uint32_t`) requires:
  1) explicit upper-bound check against destination max,
  2) explicit cast after the check,
  3) error handling/logging on overflow path.
- For decoder/API structs that use narrower counters (for example zlib
  `avail_in/avail_out`), validate bounds before assignment and fail safely
  rather than truncating.
- Const-correctness is required for read-only data paths (parameters and local
  pointers). When writing or modifying a function, qualify pointer parameters
  as `const` when the function does not modify the pointed-to data — this
  applies to struct pointers, array pointers, and string pointers alike.
  Do not drop `const` qualifiers via cast unless the callee contract
  explicitly requires mutable memory and the data is truly mutable.
  When changing a parameter from non-const to const (or vice versa), update
  all call sites, forward declarations, and header prototypes in the same
  change set.
- **NGINX callback signature exception**: do not add `const` to parameters
  whose type is dictated by NGINX framework callback signatures.  Key
  examples:
  - `ngx_command_t.set` handler: `ngx_command_t *cmd` must remain non-const
    (the NGINX `ngx_command_s` struct defines `set` as
    `char *(*set)(ngx_conf_t *, ngx_command_t *, void *)`)
  - `ngx_http_handler_pt`: `ngx_http_request_t *r` must remain non-const
  - `ngx_http_output_header_filter_pt` / `ngx_http_output_body_filter_pt`:
    parameters must match the NGINX-defined function pointer types exactly
  
  Adding `const` to these parameters causes function-pointer type mismatches
  that may compile with warnings but produce undefined behavior at runtime.
  Before adding `const` to any callback parameter, verify the parameter type
  against the NGINX header that defines the callback signature.
- Forward declarations must not shadow or redeclare NGINX macro identifiers
  (for example `ngx_log_error`, `ngx_memzero`, `ngx_str_set`). Before adding a
  declaration in impl headers, confirm the symbol is not a macro in NGINX
  headers. If a callable API is needed, declare the underlying function symbol
  (for example `ngx_log_error_core`) and keep signature parity with the
  canonical declaration in the owning header, including `const` qualifiers.
- **Forward declaration ordering**: Forward declarations must appear
  **after** all type definitions (typedefs, struct definitions, enum
  definitions) that the declaration references.  A forward declaration
  that names a type defined later in the same header causes a
  compilation error (unknown type) or, with some compilers, an implicit
  `int` fallback.  When adding a forward declaration, scan the header
  for the typedef it depends on and place the declaration below it.
  Forward declarations belong at file scope in the impl header, not
  inside function bodies or local blocks.
- Treat static-analysis findings that imply undefined behavior, data truncation,
  or invalid memory access risk as correctness/security issues and fix them in
  code; do not defer as cosmetic cleanup.
- When fixing declaration/type-safety findings, run at least one compile+unit
  target for the touched C area and report residual warnings explicitly.


### Rule 42 — volatile vs atomic memory visibility

- `volatile` is acceptable **only** for single-threaded compiler-barrier
  scenarios where the code path is explicitly documented as
  "single-threaded by design" (e.g. NGINX event loop with no thread pool).
- Do not use GCC `__atomic_load` / `__atomic_store` directly on aggregate
  structs or snapshots.  Clang can diagnose those accesses as large or
  misaligned atomic operations, and coverage builds promote that warning to a
  hard failure.
- If shared state truly needs cross-thread publication, publish a scalar,
  pointer, or version word through a reviewed helper with documented
  alignment, lock-free size, and explicit memory ordering.  Request-lifetime
  NGINX worker event-loop snapshots should use a plain struct copy with the
  single-threaded lifecycle assumption documented at the copy site.
- Rationale: `volatile` prevents compiler reordering but provides no
  happens-before semantics.  Direct `__atomic_*` builtins on aggregate state
  are not a safe fallback; use a reviewed scalar/pointer publication strategy
  if the code path actually becomes cross-threaded.
- Verification:
  1. `grep -rn 'volatile' components/nginx-module/src/` should return zero
     hits, OR each hit must have an adjacent comment justifying single-threaded use.
  2. `bash tools/harness/detect_volatile_atomic.sh` exits 0.
