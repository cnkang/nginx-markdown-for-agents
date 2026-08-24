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

#### Executable-trust PATH distinction

Two independent trusted-PATH mechanisms exist in this repository. They serve
different threat models and MUST NOT merge or import from each other:

- `tools/lib/executable_validation.py`: serves dev/CI machines for
  non-privileged repository tooling scripts. Includes `/usr/local/bin` and
  `/opt/homebrew/bin` (developer tools), excludes `/sbin` (not needed for
  non-root repo operations). Validated by the `validate_executable()` helper.

- Packaging trusted PATH (maintainer scripts): serves production hosts
  running as root during package install/remove. Uses the strict POSIX subset
  `/usr/sbin:/usr/bin:/sbin:/bin` — includes `/sbin` for system administration
  commands (for example, `nginx` on some distros), excludes developer-only paths.
  Enforced by unconditional `PATH=...` assignment at script top.

Rationale: a dev/CI tool that includes `/sbin` in its PATH resolution risks
resolving system administration binaries in contexts where they are
unnecessary. Conversely, a root-run maintainer script that includes
`/usr/local/bin` or `/opt/homebrew/bin` widens the attack surface to
user-writable directories.

Verification:
- `bash tools/release/gates/check_postinst_safety.sh` (structural gate)
- `bash packaging/tests/test-maintainer-script-executable-trust.sh` (runtime negative control)

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
- In Rust, converting a signed value to `usize`, `u32`, or `u8` requires an
  explicit non-negative check first (for example `if value < 0 { return Err(...) }`
  or `value.try_into()`), followed by the appropriate upper-bound check or
  clamp against the destination type's maximum.  `#[cfg(target_pointer_width = "64")]`
  architecture guards alone do NOT satisfy signed-input validation: a guard
  only narrows the target range for `usize` width, it cannot reject a
  negative `i64`/`isize` value.  Keep the existing 32-bit target handling
  for `usize` (runtime bounds check or explicit 64-bit-only guard) in
  addition to the non-negative check.
- Addition of two `size_t` values that will feed memory allocation or
  buffer sizing must include an overflow guard:
  `if (a > (size_t)-1 - b) { /* saturate or error */ }`.
- Casting the result of pointer subtraction to `(size_t)` (for example
  `(size_t)(last - pos)`) is forbidden unless:
  a. A bounds check on the pointers (for example `if (pos <= last)`) precedes the
     cast, or
  b. The code uses the safe wrapper `ngx_http_markdown_buf_len_safe(buf)` instead.
  A comparison context alone does NOT excuse the cast: comparison operators
  evaluate the subtracted value, so an underflowed (wrapped) difference can
  still steer control flow wrong before any explicit bounds check runs.
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
