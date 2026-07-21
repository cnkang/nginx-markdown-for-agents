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
  - Nested quantifiers with non-separator content: `(aa+)+`, `(a\d+)+`,
    `(.*)+`.  A group with an unbounded inner quantifier repeated by an
    unbounded outer quantifier is flagged unless every alternation branch
    starts with a strong literal separator (a multi-char literal or a single
    non-alphanumeric char like `-`, `_`, `.`) that the inner unbounded atom
    cannot consume.
  - Adjacent overlapping `.*` quantifiers: `(.*)(.*)`
  - Backreferences after `.*` or `.*?`: `(.*?)\1`
  - Unbounded repetition inside repeated groups: `(.*,)+`
  - Overlapping alternation with quantifiers: `(a|ab)+`
  - Repeated nullable groups: `(a?)+`, `(a*)+`
  - Dynamic regex injection: CLI args, env vars, file content as patterns
  - `re.DOTALL` with `.*` over full documents
- Python regex usage is extracted via the standard library `ast` module,
  covering: `re.compile`, `re.search`, `re.match`, `re.fullmatch`,
  `re.findall`, `re.finditer`, `re.split`, `re.sub`, `re.subn`, and
  compiled-pattern methods.  Both positional and keyword argument forms
  (`pattern=`, `string=`, `flags=`) are supported.  Import aliases
  (`import re as X`, `from re import Y as Z`) are resolved.
- Conservative scope-aware static string constant propagation: module-level
  and function-local constants (`PATTERN = r"..."`) are resolved when used as
  patterns.  Reassignment to a non-constant value invalidates the binding.
  Cross-module imports are NOT resolved (those resolve to REVIEW).
- Pattern composition is decomposed into segments: `re.escape()` only
  protects its own operand; a concatenation like
  `re.escape(x) + r"(a+)+$"` is still analyzed for the static tail, and any
  dangerous static segment produces an ERROR regardless of escaped/dynamic
  neighbors.
- Pattern source classification: `STATIC_LITERAL`, `STATIC_CONCAT`,
  `STATIC_FORMATTED`, `ESCAPED_DYNAMIC`, `DYNAMIC`, `UNKNOWN`.
- Severity levels: `ERROR` (confirmed ReDoS or dangerous static segment),
  `REVIEW` (dynamic, escaped-dynamic composition, or unresolved UNKNOWN),
  `INFO` (static safe).
- UNKNOWN pattern sources are treated as REVIEW (never silently downgraded
  to INFO) so unresolvable regex origins require manual attention.
- Shell regex: `grep -E` / `sed -E` (POSIX ERE, DFA-based, safe),
  `grep -P` / `rg -P` / `perl` (PCRE, backtracking, risk).  Pattern extraction
  is command-aware: it locates the regex command past pipes and env
  assignments, skips options, supports `-e`/`--regexp`/`-P` and `--`, and
  does not pick up patterns from preceding commands in a pipeline.
- Rust `regex` crate uses NFA (non-backtracking) — safe by default.
  Backtracking crates (`fancy-regex`, `pcre2`, `onig`) are banned via `deny.toml`.
- C/PCRE2: NGINX build depends on PCRE2, but the module does not own
  PCRE2 pattern/match context. No `pcre2_compile()` or `pcre2_match()`
  calls in module code.
- Suppression: `# nosec:regex-safety -- <justification>` with non-empty reason.
  File-level or directory-level suppressions are not allowed.  Suppressions
  are checked at the call site, the preceding line, and upward through
  consecutive comment/blank lines (up to 30) so multi-line `re.compile(...)`
  calls can be suppressed by a comment block above the statement.

CLI contract:
- Default (no flags): advisory exit 0 regardless of findings.
- `--strict`: exit 1 on ERROR findings, parse errors, or scan/read errors.
  REVIEW findings are non-blocking.
- `--fail-on-review`: implies `--strict` and additionally exits 1 on REVIEW.
- `--format json`: structured output with `findings`, `errors`, `summary`.

Verification:
- `make regex-security-check`
- `python3 tools/harness/detect_regex_safety.py --strict`
- `python3 tools/harness/detect_regex_safety.py --strict --fail-on-review`
- `python3 -m pytest tools/harness/tests/test_detect_regex_safety.py -q`
- `bash tools/harness/tests/test_detect_regex_safety.sh`
- CodeQL Python: `security-extended,security-and-quality`
- SonarLint rule S8786 (super-linear regex backtracking)
