---
domain: naming-docs
rules: [26]
paths:
  - "components/nginx-module/src/**"
  - "components/rust-converter/src/**"
  - "tools/**"
---

## Naming & Documentation Discipline

### 26. Naming and documentation discipline

Required:
- Use meaningful, descriptive names for all functions, variables, types, and
  constants.  Avoid single-letter names outside of short loop counters (`i`,
  `j`, `n`) and well-established NGINX conventions (`r`, `cf`, `ctx`, `cl`).
  When a name encodes a unit or semantic (for example `elapsed_ms`,
  `pattern_count`), the name must match what the value actually represents.
- NGINX numbered macros (`ngx_log_debug0` through `ngx_log_debug8`,
  `ngx_log_error`) are framework conventions and must not be renamed.  The
  trailing digit indicates the argument count, not a version or sequence.
- Every non-trivial function must have a block comment immediately before its
  definition describing purpose, parameters, return values, and side effects.
  For C code use `/* */` block comments per NGINX style.  For Rust use `///`
  doc comments.  For Python use docstrings.  For shell functions use `#`
  comment blocks.
- Complex or non-obvious logic must have inline comments explaining the
  reasoning — not restating what the code does, but why it does it and what
  invariants it relies on.
- When a test reimplements production logic (because the production function
  cannot be linked), the test must document the divergence risk and the
  semantic contract it mirrors.
- Struct and enum type definitions must have a comment describing the purpose
  of the type and the meaning of each field/variant, especially for types
  that cross language or module boundaries (FFI structs, shared config types).
- Shell scripts must document each function with a comment block stating
  purpose, arguments, output, and exit behaviour.  Repeated string constants
  must be extracted into `readonly` variables with a descriptive name.
