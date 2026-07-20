---
domain: parser-regex
rules: [10]
paths:
  - "tools/**"
  - "components/rust-converter/src/**"
---

## Parser & Regex Safety

### 10. Regex ReDoS and parser fragility in tooling
Historical issues: `19875fe`, `10ea6ac`, `163f3e2`, `ea982d7`, `2103658`.

Required:
- Avoid regex patterns with overlapping quantifiers/backtracking hotspots in validators/parsers.
- Prefer deterministic parsing (positional cells, explicit token splitting, substring checks) for structured docs/tables.
- Treat static-analysis ReDoS findings as design issues, not cosmetic lint.
- `detect_regex_safety.py` provides automated detection of common ReDoS patterns:
  nested quantifiers, adjacent overlapping quantifiers, backreferences after `.*`,
  unbounded repetition inside repeated groups, and overlapping alternation with quantifiers.
- Use `# nosec:regex-safety` suppression comment for intentional patterns with documented justification.

Verification:
- `python3 tools/harness/detect_regex_safety.py --strict`
- SonarLint rule S8786 (super-linear regex backtracking)
