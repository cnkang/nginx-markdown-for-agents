---
domain: shell
rules: [11, 18]
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
  (`<<EOF ... $var ... EOF`) must be defined before the heredoc.  Under
  `set -u`, an undefined variable inside a heredoc causes immediate script
  termination without a clear error message.  Use `[[ -n "${var:-}" ]]`
  guards or default values for optional heredoc variables.

---

### 18. Shell script hygiene in e2e/tooling scripts
SonarCloud rules: `shelldre:S131`, `shelldre:S7677`, `shelldre:S1066`, `shelldre:S1192`, `shelldre:S7682`.

Required:
- Every `case` statement must include a default `*)` clause, even if it only logs an error to stderr.
- Every shell function must end with an explicit `return` statement
  (`return 0` on success, or the appropriate status on failure), so static
  analysis and callers do not inherit an accidental exit status from the last
  command. This applies to all functions, not only those that emit no output.
- Diagnostic and informational messages (INFO, WARN, DEBUG) must be redirected to stderr (`>&2`) so they do not pollute stdout when scripts are piped or their output is captured.
- Merge nested `if` statements that have no `else` branch into a single compound condition (`if [[ cond1 ]] && cmd; then`).
- Extract string literals used 4+ times into `readonly` constants defined near the top of the script. Grep patterns, expected header values, and expected body tokens are common candidates.
- For repeated assertions in multi-case e2e scripts, centralize checks in helper
  functions (for example HTTP status/header/body assertions) to keep failure
  semantics consistent and reduce copy/paste drift.
- Checks documented as required assertions must fail the case/run when missing;
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
  parse; verify against the callee's `usage()`/option parser in the same
  changeset.
- Cross-script invocations in CI/tooling paths must not assume executable bits
  are preserved in all environments. Prefer `bash path/to/script.sh` (or ensure
  the executable bit is enforced) so coverage/release pipelines do not fail with
  `Permission denied`.
- Under `set -e`, command substitutions that intentionally inspect failure-path
  responses (for example truncated-stream curl probes) must not abort before
  assertions run. Use explicit tolerance (`|| true`) and then enforce behavior
  via subsequent checks on status/header/body artifacts.
- Under `set -e`, command substitutions whose exit status is expected to drive
  an error-reporting branch must be placed directly in the `if` condition
  (`if output=$(cmd); then ... else ... fi`) or otherwise made explicitly
  tolerant. Do not assign first and check `$?` afterward; a non-zero command
  substitution can exit the script before diagnostics, summaries, or artifact
  generation run.
- For HTTP HEAD validation in curl-based harness scripts, use `curl --head`
  (or `-I`) instead of `-X HEAD`, and create any expected empty body artifact
  explicitly when downstream checks read a body file.
