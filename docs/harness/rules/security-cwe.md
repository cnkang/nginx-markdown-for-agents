---
domain: security-cwe
rules: [12, 32, 33]
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
  must be preceded by an explicit non-negative check.  The pattern
  `if (parsed < 0) return NGX_ERROR;` must appear before any `(size_t) parsed`
  assignment.
- Every narrowing conversion (e.g. `size_t → uInt`, `ngx_uint_t → uint8_t`,
  `ngx_uint_t → uint32_t`) must be preceded by an explicit upper-bound check
  against the destination type's maximum value, with an error/clamp path.
- Size-value parsing via `ngx_parse_size()` must go through
  `ngx_http_markdown_dynconf_parse_size_safe()` (or an equivalent
  parse→validate→safe-cast helper).  Direct calls to `ngx_parse_size()` with
  immediate `(size_t)` cast are forbidden in new code.
- In Rust, `as usize` casts from `u64` or `i64` must include a runtime bounds
  check on 32-bit targets or a `#[cfg(target_pointer_width = "64")]` guard.
  `as u32` / `as u8` narrowing casts must include a bounds check or clamp.
- Addition of two `size_t` values that will be used for memory allocation or
  buffer sizing must include an overflow guard:
  `if (a > (size_t)-1 - b) { /* saturate or error */ }`.

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
  before the `open()` call.  Hard-coded paths within the repo (e.g.
  `REPO_ROOT / "known-file"`) are exempt.
- Every write-path construction (`Path(path)`) where `path` comes from CLI
  arguments must call `.resolve()` before `.mkdir(parents=True)` or `open()`
  to prevent symlink traversal.
- `Path(path).parent.mkdir(parents=True)` must use a resolved path:
  `resolved = Path(path).resolve()` then `resolved.parent.mkdir(...)`.
- New Python tooling scripts that accept file paths as CLI arguments must
  import and use `path_validation` helpers.  Scripts that intentionally accept
  arbitrary paths must document "trusted input only" in their `--help` text.
- Subprocess calls with path arguments from CLI must use list form (not
  string interpolation with shell=True) and validate executability.

Verification:
- `tools/harness/detect_cwe22_paths.py tools/`
- `make harness-security-checks`
