---
domain: security-cwe
rules: [12, 32, 33, 68]
paths:
  - "tools/**"
  - "components/nginx-module/src/**"
  - "components/rust-converter/src/**"
---

## Security (CWE)

### 12. Security hardening for file paths and code injection
Historical issues: `13c47c2`, `702c39d`.

Required:
- Sanitize metadata-derived path components and verify resolved paths stay within target directories.
- Never interpolate untrusted shell values into inline Python code strings.
- Pass dynamic file paths via environment variables or safe argument passing.

---

### 32. Integer overflow in ssize_t→size_t and narrowing conversions (CWE-190)
Historical issues: Snyk SNYK-CWE-190 (dynconf size parsing overflow).

Required:
- Every conversion from `ssize_t` or `ngx_int_t` to `size_t` or `ngx_uint_t`
  requires an explicit non-negative check before the cast. The pattern
  `if (parsed < 0) return NGX_ERROR;` must appear before any `(size_t) parsed`
  assignment.
- Every narrowing conversion (for example `size_t → uInt`, `ngx_uint_t → uint8_t`,
  `ngx_uint_t → uint32_t`) requires an explicit upper-bound check before the cast,
  against the destination type's maximum value, with an error/clamp path.
- Size-value parsing via `ngx_parse_size()` must go through
  `ngx_http_markdown_dynconf_parse_size_safe()` (or an equivalent
  parse→validate→safe-cast helper).  Direct calls to `ngx_parse_size()` with
  immediate `(size_t)` cast are forbidden in new code.
- In Rust, `as usize` casts from `u64` or `i64` must include a runtime bounds
  check on 32-bit targets or a `#[cfg(target_pointer_width = "64")]` guard.
  `as u32` / `as u8` narrowing casts must include a bounds check or clamp.
- Addition of two `size_t` values that will feed memory allocation or
  buffer sizing must include an overflow guard:
  `if (a > (size_t)-1 - b) { /* saturate or error */ }`.
- Casting the result of pointer subtraction to `(size_t)` (for example
  `(size_t)(last - pos)`) is forbidden unless:
  a. The subtraction appears in a comparison context (`>=`, `<=`, `==`) that
     self-guards, or
  b. A bounds check on the pointers (for example `if (pos <= last)`) precedes the
     cast, or
  c. The code uses the safe wrapper `ngx_http_markdown_buf_len_safe(buf)` instead.
  The `detect_cwe190_casts.sh` Pattern (d) flags all other cases.

Verification:
- `tools/harness/detect_cwe190_casts.sh components/nginx-module/src/`
- `make harness-security-checks`
- Regression tests for each new size-parsing path

---

### 33. Path traversal in Python tooling scripts (CWE-22)
Historical issues: Snyk SNYK-CWE-22 (unvalidated path inputs in CLI tooling).

Required:
- Every `open(path, ...)` call in `tools/` Python scripts where `path` comes
  from CLI arguments, function parameters, or environment variables must
  pass through `validate_read_path()` (from `tools/lib/path_validation.py`)
  before the `open()` call.  Hard-coded paths within the repo (for example
  `REPO_ROOT / "known-file"`) are exempt.
- Every write-path construction (`Path(path)`) where `path` comes from CLI
  arguments must call `.resolve()` before containment checks,
  `.mkdir(parents=True)`, or `open()` to prevent symlink traversal. The code
  must check containment on that canonical target with `Path.relative_to()` or an
  equivalent path-aware operation, lexical prefix checks such as
  `abspath().startswith()` are insufficient.
- `Path(path).parent.mkdir(parents=True)` must use a resolved path:
  `resolved = Path(path).resolve()` then `resolved.parent.mkdir(...)`.
- New Python tooling scripts that accept file paths as CLI arguments must
  import and use `path_validation` helpers.  Scripts that intentionally accept
  arbitrary paths must document "trusted input only" in their `--help` text.
- Subprocess calls with executable paths from CLI must use list form (not
  string interpolation with `shell=True`), resolve symlinks, and require the
  canonical executable to match a fixed allowlist. Checking only that a path
  is inside the repository and executable is insufficient because an in-tree
  symlink can target an arbitrary external command. The executable passed to
  `subprocess` must come from the fixed allowlist, not from the raw CLI value.
- **ValueError propagation from validate_read_path**: When wrapping
  `validate_read_path()` in a try/except block, do NOT catch `ValueError`.
  `validate_read_path()` raises `ValueError` when the path validator catches a
  traversal attempt (for example `..` components). Catching `ValueError` alongside
  `ImportError` or `FileNotFoundError` silently swallows the traversal
  rejection, allowing an attacker-controlled path to proceed to `open()`.
  The correct exception tuple is `(ImportError, FileNotFoundError)` — these
  cover the "path_validation module unavailable" and "directory does not
  exist" fallback cases, while letting `ValueError` propagate as an
  explicit rejection.  When refactoring a `try/except` around
  `validate_read_path`, audit the caught exception types to ensure
  `ValueError` is not in the tuple.
- For single-artifact CLI tools, prefer emitting the artifact to stdout and
  letting the trusted caller redirect it. Do not accept a caller-controlled
  output path when the Python process does not need filesystem ownership of
  the artifact.
- **Path.read_text() / write_text() file access methods**: These are
  equivalent to `open()` for path-traversal purposes.  Chained calls like
  `Path(user_input).read_text()` (Pattern e) or standalone calls on
  user-derived variable names like `args_path.read_text()` (Pattern f) get
  flagged by `detect_cwe22_paths.py`.

Verification:
- `tools/harness/detect_cwe22_paths.py tools/`
- `make harness-security-checks`
- Regression tests covering symlink escapes for both write targets and
  CLI-selected executables

---

### 68. Access control before method handling in HTTP handlers
Historical issues: access-after-method ordering in the diagnostics
endpoint handler regressed repeatedly (three separate review rounds).
Denied requests disclosed the endpoint's 405 behavior and Allow header
to unauthorized callers.

Required:
- HTTP content handlers that reject unsupported methods (405
  `NGX_HTTP_NOT_ALLOWED` or a `*_method_not_allowed()` helper) must
  evaluate access control **before** the method-rejection branch.
- A denied request must not receive a 405 response, an `Allow` header,
  or any handler-behavior signal.  Access denial takes precedence over
  method rejection.
- The access check (`check_access` / `ngx_http_core_access_phase` or a
  module-specific access helper) must appear earlier in source order
  than any `NGX_HTTP_NOT_ALLOWED` assignment.
- Handlers that never reject methods are exempt.

Verification:
- `python3 tools/harness/detect_access_before_method.py` — advisory
  local gate; `--strict` promotes findings to violations (blocking
  harness-tooling CI check via `make harness-security-checks` for
  selected `harness_tooling` paths).
- `PYTHONPATH=tools python3 -m pytest
  tools/harness/tests/test_detect_access_before_method.py -q --tb=short`
  — detector regression tests (clean ordering, bad ordering, exempt
  handlers, non-handler skip).
