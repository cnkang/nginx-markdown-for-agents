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
    `(.*)+`. The detector flags an unbounded outer quantifier around a group
    containing an unbounded inner quantifier unless every alternation branch
  starts with a strong literal separator (a multi-char literal or a single
  non-alphanumeric char like `-`, `_`, `.`) that the inner unbounded atom
  cannot consume. For multi-character separators, the outer iteration must
  start from the first character. Thus, the inner unbounded atom need only
  reject that first character.
  - Adjacent overlapping `.*` quantifiers: `(.*)(.*)`
  - Backreferences after `.*` or `.*?`: `(.*?)\1`
  - Unbounded repetition inside repeated groups: `(.*,)+`
  - Overlapping alternation with quantifiers: `(a|ab)+`
  - Repeated nullable groups: `(a?)+`, `(a*)+`
  - Unanchored open-ended repetitions overlapping the implicit leading `.*`
    of partial-match APIs (SonarCloud S8786 ALWAYS_QUADRATIC): a repetition
    with an open-ended quantifier (`+`, `*`, `{n,}`) that is reachable from
    the pattern start without consuming input and without crossing a
    boundary anchor (`^`, `$`, `\A`, `\Z`, `\b`, `\B`), and whose
    continuation can fail (a hard literal, group, or `$`/`\Z` anchor
    follows), causes guaranteed quadratic backtracking.  Examples:
    `(\d+)\s*metric\s+famili`, `x*yx*`, `\s*,`, `(ab)*a(ba)*`.  The
    check covers only partial-match APIs (`re.search`, `re.findall`,
    `re.finditer`, `re.split`, `re.sub`, `re.subn`, and the same compiled
    methods). `re.match`/`re.fullmatch` are not partial matches and are
    exempt, matching Sonar's `RedosMatchTypeHelper`.  Bounded quantifiers
    (`{m,n}`), anchored patterns, and repetitions whose element contains an
    intersecting inner repetition (Sonar reports those as
    QUADRATIC_WHEN_OPTIMIZED, not S8786) are not flagged.  PCRE shell
    contexts (`grep -P`, `rg -P`, `perl`) are inherently partial matches
    and get the same check.
  - Dynamic regex injection: CLI args, env vars, file content as patterns
  - `re.DOTALL` with an unescaped greedy `.*` over full documents.  Lazy
    `.*?`, possessive `.*+`, escaped dots, and character-class literals do
    not trigger this broad advisory check. They still pass through the normal
    structural ReDoS checks.
- The detector extracts Python regex usage through the standard library `ast`
  module, covering: `re.compile`, `re.search`, `re.match`, `re.fullmatch`,
  `re.findall`, `re.finditer`, `re.split`, `re.sub`, `re.subn`, and
  compiled-pattern methods. The detector supports both positional and keyword
  argument forms (`pattern=`, `string=`, `flags=`). Import aliases the detector
  resolves (`import re as X`, `from re import Y as Z`).
- Scope-aware static string constant propagation using a unified `_Binding` /
  `_Scope` model with LEGB (Local → Enclosing → Global → Builtin) lookup.
  Module-level and function/class/lambda-local constants (`PATTERN = r"..."`)
  the detector resolves them when used as patterns. It propagates finite static
  string collections and fixed-width string rows through `for` loops and
  comprehensions, including destructured targets. Their use in f-strings or the
  detector expands string concatenation as a bounded Cartesian product (at most
  256 alternatives), and analyzes every resulting pattern. Expressions that
  exceed the bound or contain an unknown component remain REVIEW.
  Function/class/lambda parameters, with/except targets, and augmented
  assignments shadow outer bindings.

Reassignment of a `re` alias to a non-`re` value invalidates the alias.
  Reassignment of a compiled-pattern variable to a dynamic value upgrades the
  binding to `COMPILED_DYNAMIC_PATTERN` (not plain `DYNAMIC_VALUE`). Subsequent
  `compiled.search/match/sub/findall` calls then emit a REVIEW referencing the
  original `compile_line`. This applies to Assign, AnnAssign, and AugAssign.
  An initially dynamic `re.compile()` emits its REVIEW at the compile site only.
  A second compiled-method REVIEW stays reserved for actual reassignment.
  Lexical `del` writes a DELETED tombstone in the current scope instead of
  walking outer scopes.

A function-local `del p` no longer removes a module binding, `global`/`nonlocal`
  are partially modeled (honored for delete routing) and otherwise conservative.
  Function/lambda defaults, decorators, return annotations, and parameter
  annotations evaluate in the enclosing scope. As in Python, they evaluate
  before the function name binds and before the body scope opens. Modules
  that import `from __future__ import annotations` use postponed evaluation:
  their annotation AST nodes are inert string constants at runtime. The visitor
  detects the future import **explicitly** from the AST (`visit_ImportFrom`
  matching `__future__` + `annotations`) and skips return/parameter/annotation
  traversal entirely under that flag. It never infers postponed mode from
  `ast.Constant` node shape because that shape is ambiguous. Without the
  future import, a genuine string literal annotation evaluates eagerly and
  still requires analysis. Preserving the annotation AST nodes matters for
  eager mode: the visitor walks their expressions (names, attribute accesses,
  and any embedded constants) normally.

Modules without the future import analyze eagerly. Postponed annotation strings
  intentionally produce no REVIEW. Unknown RHS expressions produce DYNAMIC_VALUE
  (not the old static binding). Cross-module imports are NOT resolved (those
  resolve to REVIEW).
- Tokenizer: `_merge_literal_atoms` preserves the last literal atom before a
  quantifier as a separate atom so that separator detection is not lost when
  the detector merges adjacent literals.  For example, `abc\w+` keeps `abc` as a
  separate literal atom so the detector recognizes `\w+` as having a multi-char
  literal separator prefix. Segment analysis treats `re.escape()` as
  protecting only its own operand. The detector still analyzes the static
  tail of a concatenation like `re.escape(x) + r"(a+)+$"`. Any dangerous
  static segment produces an ERROR regardless of escaped/dynamic neighbors.
  The detector represents an escaped-only composition with a literal atom
  and analyzes its static scaffold, safe scaffolds produce no REVIEW.
- Pattern source classification: `STATIC_LITERAL`, `STATIC_CONCAT`,
  `STATIC_FORMATTED`, `ESCAPED_DYNAMIC`, `DYNAMIC`, `UNKNOWN`.
- Severity levels: `ERROR` (confirmed ReDoS or dangerous static scaffold),
  `REVIEW` (unescaped dynamic or unresolved UNKNOWN), `INFO` (static safe).
- The detector treats UNKNOWN pattern sources as REVIEW (never silently downgraded
  to INFO) so unresolvable regex origins require manual attention.
- Shell regex: pattern-bearing options (`-e`/`--regexp`) and PCRE-enabling
  flags (`grep -P`/`--perl-regexp`, `rg -P`/`--pcre2`, `perl` itself) are
  separate. Without a PCRE flag, `grep -e`/`rg -e` use the command's default
  engine (BRE/ERE for grep, Rust regex NFA for rg). The detector does NOT
  analyze these commands for PCRE catastrophic backtracking. Only `grep -P`,
  `rg -P`/`--pcre2`, and `perl
  -e`/`-E` produce `shell-pcre` findings. Pattern extraction is command-aware:
  it locates the regex command past pipes and env assignments. It skips
  options and supports `-e`/`--regexp`/`-P` and `--`. It does not pick up
  patterns from preceding commands in a pipeline. Broken multi-line command
  recovery matches exact command tokens rather than substrings in prose or
  arguments. The detector models common engine/output flags as boolean
  options, such as `grep -E`/`-F`/`-G`/`-c` and `rg -F`.

They do not become spurious pattern candidates. Shell argument parsing
  classifies options as boolean flags, required-value options, optional-value
  options, pattern options, pattern-file options, or PCRE flags. Boolean flags
  consume nothing. Required-value options consume the next token.
  Optional-value options do not consume the next token. After an unknown
  option, the detector collects additional non-option tokens as pattern
  candidates so a dangerous pattern cannot be hidden behind an unrecognized
  option. Multiple `-e` patterns are all extracted. The detector supports
  multiple pattern-file options (`-f`/`--file`). It resolves paths relative
  to the shell script directory and validates that they stay within the
  repository root. It rejects absolute paths, `..` traversal, and symlink
  escapes. It reads the files as UTF-8 and analyzes them per line.
  Read/encoding failures (OSError, UnicodeDecodeError, IsADirectoryError)
  produce a ScanError plus a conservative REVIEW instead of crashing.

`shlex` parse errors produce ScanError.
- Rust `regex` crate uses NFA (non-backtracking) — safe by default.
  `deny.toml` bans backtracking crates (`fancy-regex`, `pcre2`, `onig`).
- C/PCRE2: NGINX build depends on PCRE2, but the module does not own
  PCRE2 pattern/match context. No `pcre2_compile()` or `pcre2_match()`
  calls in module code.
- Suppression: `# nosec:regex-safety -- <justification>` with non-empty reason.
  File-level or directory-level suppressions are not allowed. The detector
  checks them at the call site, the preceding line, and upward through
  consecutive comment/blank lines (up to 30). A comment block above a multi-line
  `re.compile(...)` statement can suppress calls.

CLI contract:
- Default (no flags): advisory exit 0 regardless of findings. Scan/read errors
  exit 1 even here, because an incomplete scan is never a pass.
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
