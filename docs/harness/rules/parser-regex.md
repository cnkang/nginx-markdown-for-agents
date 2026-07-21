---
domain: parser-regex
rules: [10]
paths:
  - "tools/**"
  - "packaging/**"
  - "tests/**"
  - "skills/**"
  - "components/rust-converter/src/**"
---

## Parser & Regex Safety

### 10. Regex ReDoS and parser fragility in tooling
Historical issues: `19875fe`, `10ea6ac`, `163f3e2`, `ea982d7`, `2103658`.

Required:
- Avoid regex patterns with overlapping quantifiers/backtracking hotspots in validators/parsers.
- Prefer deterministic parsing (positional cells, explicit token splitting, substring checks) for structured docs/tables.
- Treat static-analysis ReDoS findings as design issues, not cosmetic lint.
- `detect_regex_safety.py` provides AST-based automated detection of:
  - Nested quantifiers: `(a+)+`, `(.*)+`
  - Adjacent overlapping `.*` quantifiers: `(.*)(.*)`
  - Backreferences after `.*` or `.*?`: `(.*?)\1`
  - Unbounded repetition inside repeated groups: `(.*,)+`
  - Overlapping alternation with quantifiers: `(a|ab)+`
  - Repeated nullable groups: `(a?)+`, `(a*)+`
  - Dynamic regex injection: CLI args, env vars, file content as patterns
  - `re.DOTALL` with `.*` over full documents
- Python regex usage is extracted via the standard library `ast` module, covering:
  `re.compile`, `re.search`, `re.match`, `re.fullmatch`, `re.findall`,
  `re.finditer`, `re.split`, `re.sub`, `re.subn`, and compiled-pattern methods.
  Import aliases (`import re as X`, `from re import Y`) are resolved.
- Pattern source classification: `STATIC_LITERAL`, `STATIC_CONCAT`,
  `STATIC_FORMATTED`, `ESCAPED_DYNAMIC`, `DYNAMIC`, `UNKNOWN`.
- Severity levels: `ERROR` (confirmed ReDoS), `REVIEW` (dynamic/unknown),
  `INFO` (static safe or unresolved).
- Shell regex: `grep -E` (POSIX ERE, DFA-based, safe), `grep -P` / `rg -P`
  (PCRE, backtracking, risk), `sed -E`, `perl`.
- Rust `regex` crate uses NFA (non-backtracking) — safe by default.
  Backtracking crates (`fancy-regex`, `pcre2`, `onig`) are banned via `deny.toml`.
- C/PCRE2: NGINX build depends on PCRE2, but the module does not own
  PCRE2 pattern/match context. No `pcre2_compile()` or `pcre2_match()`
  calls in module code.
- Suppression: `# nosec:regex-safety -- <justification>` with non-empty reason.
  File-level or directory-level suppressions are not allowed.
  Suppressions are checked at both the call site and the `re.compile()` line.

Verification:
- `make regex-security-check`
- `python3 tools/harness/detect_regex_safety.py --strict`
- `python3 -m pytest tools/harness/tests/test_detect_regex_safety.py -q`
- `bash tools/harness/tests/test_detect_regex_safety.sh`
- CodeQL Python: `security-extended,security-and-quality`
- SonarLint rule S8786 (super-linear regex backtracking)
