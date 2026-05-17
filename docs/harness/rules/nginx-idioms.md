---
domain: nginx-idioms
rules: [28, 29, 30, 31]
paths:
  - "components/nginx-module/src/**"
---

# NGINX Idioms & API Correctness

### 28. Full ngx_list_part_t chain iteration and multi-value HTTP header semantics
Historical issues: `4c2b8d9`, `e7a1f03`, `b3d9c52`, `f1a8e64`.

Required:
- Any function iterating `ngx_list_t` (for example output headers, variables)
  must traverse the **full** `part → part->next` chain.  Iterating only the
  first `ngx_list_part_t` silently drops headers on requests with more than
  `NGX_LIST_PART_SIZE` (typically 8) headers.
- Use the canonical NGINX iteration pattern:
  ```
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
Historical issues: `8b3d1a5`, `a2c6e90`.

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
- CI configuration should flag diffs exceeding 100 lines for mandatory review
  (not auto-merge).
- After rebasing or cherry-picking, run `make test-nginx-unit` (or the
  narrowest relevant test target) before pushing to catch silent conflict
  artifacts.

Verification:
- `git diff --check` after merge/rebase
- `make test-nginx-unit` after any change touching `components/nginx-module/`
- `make test-rust` after any change touching `components/rust-converter/`
