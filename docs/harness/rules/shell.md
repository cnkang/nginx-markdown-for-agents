---
domain: shell
rules: [11, 18, 41]
paths:
  - "tools/**/*.sh"
  - "tools/e2e/**"
---

## Shell Script Hygiene & Portability

### 11. Shell portability and environment assumptions
Historical issues: `f0a98fc`, `55b9170`, `5a8b5ee`, `092f04f`, `fe8b8cdd`.

Required:
- Assume macOS bash 3.2 compatibility unless script is explicitly version-pinned.
- Avoid GNU/PCRE-only flags (for example `grep -P`) in portable SOP/scripts.
- Use null-delimited file traversal for file-path safety.
- Ensure temporary directories are traversable by unprivileged worker processes when runtime depends on them.
- **Empty array expansion under `set -u`**: bash 3.2 treats `${arr[@]}` as an
  unbound variable error when the array is empty.  Use the conditional
  expansion pattern `${arr[@]+"${arr[@]}"}` (or `${arr[@]:+"${arr[@]}"}`
  with the colon variant) to safely expand potentially-empty arrays.
  This applies to all scripts that use `set -u` or `set -euo pipefail`.
- **Heredoc variable references**: variables referenced inside heredocs
  (`<<EOF ... $var ... EOF`) must have a definition before the heredoc. Under
  `set -u`, an undefined variable inside a heredoc causes immediate script
  termination without a clear error message.  Use `[[ -n "${var:-}" ]]`
  guards or default values for optional heredoc variables.

---

### 18. Shell script hygiene in e2e/tooling scripts
SonarCloud rules: `shelldre:S131`, `shelldre:S7677`, `shelldre:S1066`, `shelldre:S1192`, `shelldre:S7682`, `shelldre:S7688`.

Required:
- Every `case` statement must include a default `*)` clause, even if it only logs an error to stderr.
- **Use `[[` instead of `[` for conditional tests in bash scripts.** The `[[`
  construct is safer (no word splitting, no pathname expansion on variables)
  and more feature-rich (regex matching, pattern globbing).  Since all project
  scripts use `#!/usr/bin/env bash`, `[[` is always available and preferred.
  Reserve `[` (or `test`) only for POSIX sh scripts (none exist in this repo).
- Every shell function must end with an explicit `return` statement
  (`return 0` on success, or the appropriate status on failure), so static
  analysis and callers do not inherit an accidental exit status from the last
  command. This applies to all functions, not only those that emit no output.
- Diagnostic and informational messages (INFO, WARN, DEBUG) must redirect to stderr (`>&2`). This prevents stdout pollution when scripts pipe output or capture it.
- Merge nested `if` statements that have no `else` branch into a single compound condition (`if [[ cond1 ]] && cmd; then`).
- Extract string literals used 4+ times into `readonly` constants defined near the top of the script. Grep patterns, expected header values, and expected body tokens are common candidates.
- For repeated assertions in multi-case e2e scripts, centralize checks in helper
  functions (for example HTTP status/header/body assertions) to keep failure
  semantics consistent and reduce copy/paste drift.
- Checks documented as required assertions must fail the case/run when missing,
  do not leave them as INFO-only log lines.
- `--plan`/dry-run style modes must short-circuit unconditionally before
  runtime prerequisites (for example `NGINX_BIN` checks), regardless of other
  option values.
- Script usage/help text must stay synchronized with parsed flags and defaults
  (for example every parsed `--flag` appears in `usage()` with the same default
  variable shown to users).
- When one tooling script orchestrates other repo scripts, the caller must match
  the callee's real interface contract exactly (flag vs environment variable vs
  positional argument). Do not pass synthetic flags that the callee does not
  parse, verify against the callee's `usage()`/option parser in the same
  changeset.
- Callers in CI/tooling paths must not assume that executable bits remain set in
  all environments. Prefer `bash path/to/script.sh` (or ensure the executable
  bit stays enforced) so coverage/release pipelines do not fail with
  `Permission denied`.
- Under `set -e`, command substitutions that intentionally inspect failure-path
  responses (for example truncated-stream curl probes) must not abort before
  assertions run. Use explicit tolerance (`|| true`) and then enforce behavior
  via subsequent checks on status/header/body artifacts.
- Under `set -e`, command substitutions whose exit status drives
  an error-reporting branch must sit directly in the `if` condition
  (`if output=$(cmd); then ... else ... fi`) or otherwise made explicitly
  tolerant. Do not assign first and check `$?` afterward; a non-zero command
  substitution can exit the script before diagnostics, summaries, or artifact
  generation run.
- For HTTP HEAD validation in curl-based harness scripts, use `curl --head`
  (or `-I`) instead of `-X HEAD`, and create any expected empty body artifact
  explicitly when downstream checks read a body file.
- **Never read `$?` inside a negated conditional body** (2026-08-21,
  `6fcf1bb9`).  Inside `if ! run_case; then rc=$?`, bash's `$?` reflects
  the NEGATED status (always 0 when the command failed), so the branch
  can never observe the original exit code and failure-classification
  branches become dead.  Capture the status before branching with
  `run_case || rc=$?` (safe under `set -e`), then test `${rc}`.  The same
  applies to `while !`/`until !` bodies.

The portable status-capture pattern is:

```bash
rc=0
run_case || rc=$?
```


### Rule 41 — Shell harness detectors must use POSIX ERE

- `tools/harness/detect_*.sh` scripts must use POSIX Extended Regular
  Expressions.  Use `[[:space:]]` instead of `\s`, `[[:digit:]]` instead
  of `\d`, and avoid BRE-only `\(...\)` backreference syntax.
- When extended patterns become needed, pass `grep -E` explicitly.
- Rationale: BRE/ERE confusion caused the `detect_header_hash_filter.sh`
  detector to silently produce false negatives (the `\s` pattern matched
  nothing on macOS BSD grep), allowing Rule 40 violations to go undetected.
- Verification: run the grep pipeline through an explicit conditional so
  the check fails when prohibited matches are found and succeeds when
  none are. The gate must cover **all** prohibited regex forms — the PCRE
  character classes `\s`, `\d`, `\w` and the BRE-only grouping syntax
  `\(...\)` — on **any** line that feeds a regex-capable command or a
  regex variable assignment, not only literal `grep`/`sed`/`awk` lines.
  This includes **arbitrary variable assignments** whose value later feeds
  a regex-capable command (for example `needle=...` assigned before
  `grep -E "$needle"`), not just variables named `pattern=` or `regex=`:
  ```bash
  # 1) PCRE classes on regex-command or pattern-assignment lines. Construct
  #    the backslash at runtime so the gate can scan its own source without
  #    matching the detector's example as a prohibited literal.
  _backslash='\'
  _pcre_needle="${_backslash}${_backslash}s|${_backslash}${_backslash}d|${_backslash}${_backslash}w"
  # Match regex-capable commands OR any shell variable assignment (the
  # assigned value may later feed a regex command, e.g. needle=... before
  # grep -E "$needle").  Known non-regex assignments (e.g. entry_pattern
  # used to build allowlists) are skipped by the allowlist in the
  # detector, not by this filter.
  if grep -REn "${_pcre_needle}" tools/harness/detect_*.sh \
      | grep -E 'grep|sed|awk|egrep|(^|[^a-zA-Z0-9_-])rg([^a-zA-Z0-9_-]|$)|perl|pattern=|regex=|([[:space:]]|^)[A-Za-z_][A-Za-z0-9_]*=' \
      | grep -vE ':[0-9]+:[[:space:]]*(#|//)' >/dev/null 2>&1; then
     echo "FAIL: prohibited PCRE regex classes found" >&2
     exit 1
  fi
  # 2) BRE-only grouping on regex-capable command lines and shell pattern
  #    assignments. Embedded Python heredocs are exempt: they use Python re
  #    semantics.
  _bre_needle="${_backslash}${_backslash}[()]"   # constructed at runtime
  if grep -REn "${_bre_needle}" tools/harness/detect_*.sh \
      | grep -E 'grep|sed|awk|egrep|(^|[^a-zA-Z0-9_-])rg([^a-zA-Z0-9_-]|$)|pattern=|regex=|([[:space:]]|^)[A-Za-z_][A-Za-z0-9_]*=' \
      | grep -vE ':[0-9]+:[[:space:]]*(#|//)' >/dev/null 2>&1; then
     echo "FAIL: BRE-only grouping syntax found" >&2
     exit 1
  fi
  ```
  (Python `re` patterns inside detectors are exempt: they use Python
  regex semantics, not POSIX ERE. The `detect_header_hash_filter.sh`
  historical false-negative came from `\\s` matching nothing on macOS
  BSD grep; `[[:space:]]`/`[[:digit:]]`/`[[:alnum:]_]` remain the only
  accepted forms in detector scripts.)
