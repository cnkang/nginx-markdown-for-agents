# Tooling Path Security Pack

Use this pack when Python or shell tooling changes may affect path validation,
file write safety, subprocess argument safety, shell hygiene, or C const-correctness.

## Triggers

- touched tooling paths such as `tools/perf/**`, `tools/tests/**`, or
  `skills/nginx-markdown-harness-maintenance/scripts/**`
- touched path-validation helpers in `tools/lib/path_validation.py`
- touched harness detection scripts in `tools/harness/detect_*`
- keywords like `path validation`, `path traversal`, `CWE-22`, `CWE-190`,
  `command injection`, `repo-relative`, `validate_read_path`,
  `validate_write_path_within_root`, `shell hygiene`, `return statement`,
  `stderr redirect`, or `const correctness`

## Risks

- CLI-derived paths bypass validation and allow traversal outside repo root
- write paths are created from unresolved parents and follow symlink escapes
- subprocess invocations use shell interpolation with user-influenced values
- security checks exist but are not routed by harness families for changed files
- repeated PR rounds patch the same path-safety class without durable routing
- shell functions missing explicit return statements cause accidental exit status
  inheritance (SonarCloud S7682, AGENTS.md Rule 18)
- diagnostic messages sent to stdout instead of stderr pollute captured output
  (SonarCloud S1066, AGENTS.md Rule 18)
- curl -X HEAD used instead of --head for HTTP HEAD validation (AGENTS.md Rule 18)
- C pointer parameters not const-qualified when function only reads through them
  (AGENTS.md Rule 24, SonarCloud const-correctness findings)

## Common Supporting Packs

- `docs-tooling-drift` when operator docs, validators, or workflow filters
  changed with tooling behavior
- `release-governance` when release tooling reads/writes evidence artifacts or
  matrix outputs

## Sync Points

- Path-validation contracts in `AGENTS.md` and `tools/lib/path_validation.py`
  must match actual tooling callsites (`validate_read_path`,
  `validate_write_path_within_root`, resolved parent handling).
- If new tooling surfaces accept path-like CLI inputs, include those paths in
  `docs/harness/routing-manifest.json` so routing does not degrade to zero-hit
  blind spots.
- Security-focused verification must stay explicitly routable through a focused
  family (`harness-security`), not only via umbrella checks.
- Tooling fixes must include targeted regression tests for both valid and
  rejected path cases.

## Minimum Verification

```bash
make harness-check
make harness-security-checks
PYTHONPATH=. pytest -q tools/perf/tests
```

## Canonical References

- [../../../AGENTS.md](../../../AGENTS.md)
- [../../../tools/lib/path_validation.py](../../../tools/lib/path_validation.py)
- [../../../tools/harness/detect_shell_hygiene.sh](../../../tools/harness/detect_shell_hygiene.sh)
- [../../../tools/harness/detect_const_correctness.py](../../../tools/harness/detect_const_correctness.py)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.2 | 2026-05-11 | Kang | Initial pack for recurring tooling path-security fixes and routing coverage |
| 0.6.3 | 2026-05-14 | Kang | Extended with shell hygiene (S7682/S1066/Rule 18), const-correctness detection, and curl --head patterns |
