---
domain: c-safety
rules: [24]
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
- Forward declarations must not shadow or redeclare NGINX macro identifiers
  (for example `ngx_log_error`, `ngx_memzero`, `ngx_str_set`). Before adding a
  declaration in impl headers, confirm the symbol is not a macro in NGINX
  headers. If a callable API is needed, declare the underlying function symbol
  (for example `ngx_log_error_core`) and keep signature parity with the
  canonical declaration in the owning header, including `const` qualifiers.
- Treat static-analysis findings that imply undefined behavior, data truncation,
  or invalid memory access risk as correctness/security issues and fix them in
  code; do not defer as cosmetic cleanup.
- When fixing declaration/type-safety findings, run at least one compile+unit
  target for the touched C area and report residual warnings explicitly.
