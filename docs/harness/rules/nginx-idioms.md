---
domain: nginx-idioms
rules: [28, 29, 30, 31, 39, 40, 50]
paths:
  - "components/nginx-module/src/**"
---

## NGINX Idioms & API Correctness

### 28. Full ngx_list_part_t chain iteration and multi-value HTTP header semantics
Historical issues: `4c2b8d9`, `e7a1f03`, `b3d9c52`, `f1a8e64`.

Required:
- Any function iterating `ngx_list_t` (for example output headers, variables)
  must traverse the **full** `part → part->next` chain.  Iterating only the
  first `ngx_list_part_t` silently drops headers on requests with more than
  `NGX_LIST_PART_SIZE` (typically 8) headers.
- Use the canonical NGINX iteration pattern:
  ```c
  part = &list->part;
  while (part) {
      h = part->elts;
      for (i = 0; i < part->nelts; i++) { /* process h[i] */ }
      part = part->next;
  }
  ```
- Single-header shortcut functions (for example "find first occurrence of
  header X") must document the first-match-only limitation explicitly in
  their doc comment.
- When aggregating flags or values from multiple headers of the same name
  (for example `X-Forwarded-For`), check the aggregated result before
  branching on per-header flags, so a later header cannot override an
  earlier decision.

Verification:
- `grep -rn 'part.nelts\|part.elts' components/nginx-module/src/` — confirm
  every hit is inside a `while (part)` / `for (;; part = part->next)` loop.
- `grep -rn 'r->headers_in.headers.part\b' components/nginx-module/src/`

---

### 29. Flag and state clearing ordering (clear-after-success, not before)
Historical issues: `5e9a2b1`, `c4d7f80`.

Required:
- Flags that gate operations (for example `reload_pending`, `staging_dirty`,
  `config_applied`) must be cleared **after** the gated operation succeeds,
  not before the attempt.  Clearing before the operation removes the retry
  path on failure.
- Correct pattern: `if (flag) { rc = op(); if (rc == NGX_OK) flag = 0; }`
- Incorrect pattern: `if (flag) { flag = 0; rc = op(); /* no retry on failure */ }`
- When a flag guards a multi-step operation, clear it only after all steps
  succeed.  If any step fails, the flag remains set so the next cycle retries.
- Doc comments on flag fields must state the clearing contract: "Cleared by
  X after Y succeeds" or "Set by Z, cleared only on successful reload."

Verification:
- `grep -rn 'reload_pending\|staging_dirty\|config_applied\|_pending\b' components/nginx-module/src/`
- For each hit, verify the flag is not unconditionally zeroed before the
  guarded operation.

---

### 30. NUL-termination of ngx_str_t before C API calls and EOF boundary handling
Historical issues: `8b3d1a5`, `a2c6e90`, `327bfe99`, `4b97d0a7`.

Required:
- `ngx_str_t` values are **not** NUL-terminated.  Before passing an
  `ngx_str_t`'s `data` pointer to any C API that requires NUL-terminated
  input (for example `ngx_strcasecmp`, `stat()`, `ngx_file_info()`,
  `opendir()`, `strstr()`), copy to a stack or pool buffer and append
  `'\0'`.
- Prefer length-bounded NGINX APIs (`ngx_strncasecmp`, `ngx_strlchr`,
  `ngx_strnstr`) when the length is known, avoiding the copy entirely.
- When a length-bounded API is not available and a copy is needed, use
  `ngx_pnalloc(pool, len + 1)` and `ngx_memcpy` + NUL-terminate.  Free the
  buffer from the pool when the pool lifetime covers the usage.
- Line-oriented parsers that read files or buffers must handle the final
  line that lacks a trailing `\n`.  Treating `\n` as the sole line delimiter
  silently drops the last line if the file does not end with a newline.
- When comparing `ngx_str_t` values for directive matching, require exact
  length equality first and use `ngx_strncasecmp(..., expected_len)` to
  prevent out-of-bounds reads on short or truncated inputs.
- **Cross-translation-unit visibility**: When a configuration-derived field
  (for example `effective_body_buffer_limit`) is consumed in multiple
  source files, its declaration and accessor function must be in a shared
  header file (for example `filter_module.h`), not declared `static` in
  one `.c` file.  A `static` declaration in a source file is invisible to
  other translation units, causing them to use stale defaults or
  `NGX_CONF_UNSET_SIZE` sentinels.  When moving a declaration from a
  source file to a header, update all consumers in the same change set.
- **Sentinel consistency**: When using `NGX_CONF_UNSET_SIZE` as the
  "unset" sentinel for `size_t` fields, use it consistently throughout
  the accessor chain.  Do not mix `NGX_CONF_UNSET_SIZE` and literal
  `(size_t)-1` in different accessors for the same field — they are the
  same value but mixing forms obscures intent and can mask bugs if
  `NGX_CONF_UNSET_SIZE` changes in a future NGINX version.

Verification:
- `grep -rn 'ngx_strcasecmp\|ngx_file_info\|stat(' components/nginx-module/src/`
- For each hit, verify the input is guaranteed NUL-terminated or that a
  length-bounded alternative should be used instead.
- `grep -rn "while.*\\\\n\|split_on.*\\\\n" components/rust-converter/src/` —
  verify EOF-last-line handling in Rust line iterators.

---

### 31. Residual code integrity after merge and large commits
Historical issues: `d6a4b2c`, `9f1e3a8`.

Required:
- After a merge commit or any change that modifies more than 500 lines in a
  single file, verify: (a) the file compiles, (b) `git diff --check` shows
  no whitespace conflicts, (c) the function/method count matches the expected
  value (no functions silently dropped by merge conflict resolution), and
  (d) no duplicate adjacent blocks exist (identical consecutive code blocks
  are a common merge residual).
- For any single file change exceeding 30 lines, verify the last function in
  the file still has its closing brace and the file is not truncated.
- **Organic duplicate code detection**: Duplicate adjacent code blocks are
  not only a merge residual — they can also accumulate organically when
  a developer copies a block within the same file (for example config
  handlers, auth/otel helper functions).  When adding a new code block
  that is similar to an existing block in the same file, check whether the
  two blocks can be unified into a shared helper.  If a code block of 5+
  lines is duplicated (non-adjacent) within the same file, extract the
  common logic into a function.  CI tooling (`detect_duplicate_code.py`)
  should flag both adjacent duplicates (3+ identical consecutive lines
  immediately repeated) and non-adjacent duplicates (5+ identical
  consecutive lines appearing elsewhere in the same file).
- CI configuration should flag diffs exceeding 100 lines for mandatory review
  (not auto-merge).
- After rebasing or cherry-picking, run `make test-nginx-unit` (or the
  narrowest relevant test target) before pushing to catch silent conflict
  artifacts.

Verification:
- `git diff --check` after merge/rebase
- `make test-nginx-unit` after any change touching `components/nginx-module/`
- `make test-rust` after any change touching `components/rust-converter/`


---

### 39. NGX_DONE terminal semantics and double-finalize prevention
Historical issues: `90aafbae`, `ebcf7a3c` (H-02).

Required:
- After calling `ngx_http_finalize_request(r, rc)`, the function must return
  immediately.  The typical return value is `NGX_DONE` to signal callers that
  the request lifecycle is complete.
- Callers receiving `NGX_DONE` from a subroutine must treat it as terminal
  success: do not invoke further downstream filters, do not send additional
  body data, and do not call `ngx_http_finalize_request` again.
- Correct pattern:
  ```c
  rc = send_304_response(r);
  if (rc == NGX_DONE) {
      return NGX_OK;  /* request already finalized */
  }
  /* continue only if rc != NGX_DONE */
  ```
- Incorrect pattern:
  ```c
  send_304_response(r);  /* ignores return value */
  rc = ngx_http_output_filter(r, &out);  /* double-send after finalize */
  ```
- When a helper function calls `ngx_http_finalize_request`, its doc comment
  must state "Returns NGX_DONE after finalizing the request; caller must not
  continue processing."
- Multi-step header/response modification operations (for example header plan
  apply with ETag set, Vary append, custom header add) must be atomic: if any
  step fails (allocation failure, header set failure), abort the entire
  operation and return `NGX_ERROR`.  Do not log the error and continue with
  partially applied headers — downstream consumers will see inconsistent state.
- Fixed-capacity transaction snapshots must detect capacity overflow before
  the first mutation and return `NGX_ERROR`.  Never silently omit matching
  state from a rollback snapshot; a rollback set that does not cover every
  mutable entry cannot satisfy the atomicity contract.

Verification:
- `grep -rn 'ngx_http_finalize_request' components/nginx-module/src/`
- For each hit, verify the function returns immediately after the call.
- For each caller of a function that may finalize, verify it checks for
  `NGX_DONE` before continuing.
- `grep -rn 'header_plan\|update_headers' components/nginx-module/src/`
- Verify multi-step header operations abort on first failure.

---

### 40. Invalidated header filtering in header lookup functions
Historical issues: `ebcf7a3c` (L-01).

Required:
- All header lookup/iteration functions must skip entries where `hash == 0`.
  NGINX marks deleted or invalidated headers by zeroing the hash field; reading
  such entries returns stale or garbage data.
- The filter must appear inside the iteration loop, before any field access:
  ```c
  for (i = 0; i < part->nelts; i++) {
      if (h[i].hash == 0) {
          continue;
      }
      /* safe to access h[i].key, h[i].value */
  }
  ```
- This applies to all variants: `find_request_header`, `find_output_header`,
  custom header iteration in `conditional.c`, `accept.c`, `filter_module.c`,
  `conversion_impl.h`, and any new header lookup code.
- When adding a new header lookup function, copy the `hash == 0` guard from
  an existing function rather than writing from scratch.

Verification:
- `grep -rn 'part->nelts\|part.nelts' components/nginx-module/src/`
- For each header iteration loop, verify `hash == 0` is checked before
  accessing header fields.
- `bash tools/harness/detect_header_hash_filter.sh`

---

### 50. OWS separator handling in Content-Type parsing
Historical issues: 69750e02 (HTAB not accepted as OWS), 7e3718bf (trailing OWS).

Required:
- When parsing `Content-Type` headers for streaming eligibility checks,
  accept both space (`SP`, 0x20) and horizontal tab (`HTAB`, 0x09) as
  optional whitespace (OWS) separators per RFC 7230.  Some clients send
  `text/markdown;\tcharset=utf-8` with a tab after the semicolon; rejecting
  HTAB causes false-negative eligibility decisions.
- Exclude trailing OWS after the parameter value.  A Content-Type like
  `text/markdown; charset=utf-8` (trailing space) must not fail the
  charset check — strip trailing OWS before comparing the parameter
  value.
- When adding or modifying a Content-Type parser, test with both SP and
  HTAB separators, and with trailing OWS on the parameter value.

Verification:
- `grep -rn 'Content.Type\|content.type\|charset' components/nginx-module/src/`
  — for each parser, verify it handles HTAB as OWS.
- `make test-nginx-unit` — eligibility tests cover HTAB separator and
  trailing OWS exclusion.
