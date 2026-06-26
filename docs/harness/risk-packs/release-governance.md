# Release Governance Pack

Use this as the primary pack when release gates, release matrices, source-build
CI, scope governance, or go/no-go tooling changes.

## Triggers

- touched release gate validators, release matrix tooling, install/release CI,
  or release gate documents
- touched source-build scripts, binary release workflows, or install
  verification commands
- keywords like `release gate`, `scope`, `go/no-go`, `matrix`,
  `source-build`, `release-binaries`, or `governance`

## Common Supporting Packs

- `docs-tooling-drift` when release docs and validators must stay aligned
- `harness-remediation` when release findings come from broad history analysis
- `nginx-protocol-safety` when release gates classify auth, cache-control, or
  conditional request capability rows

## Sync Points

- release gate documents and validators agree on section names, required
  artifacts, and pass/fail semantics
- regex or table parsers avoid backtracking-prone patterns and use deterministic
  parsing where practical
- source-build CI targets, package prerequisites, CA certificates, and
  unprivileged-worker temporary directories are explicitly validated
- Make targets, workflow steps, and release scripts call each other through real
  supported interfaces, not synthetic flags
- install-verify and update-matrix workflows remain covered by workflow
  regression tests when supported NGINX versions, upstream-vs-release matrix
  sources, or JS action gates change
- release matrix tooling keeps upstream-discovery data separate from shipped
  release artifacts so install docs do not claim unsupported binaries
- legacy and current release gate validators remain intentionally separated
  unless a change updates both paths and tests
- release-gate validators keep SonarCloud quality rules green: split complex
  validation functions into helper checks and promote repeated gate IDs to
  named constants
- after merge or >500-line single-file changes, verify compilation,
  `git diff --check`, function count, and no duplicate adjacent blocks (Rule 31)
- after >30-line single-file changes, verify the file is not truncated
  (closing brace present) (Rule 31)
- release manifest generation, validation, and path traversal protection must
  stay synchronized: `generate-release-manifest.py` and
  `validate-release-manifest.py` must agree on schema, and path traversal
  guards must reject filenames that escape the artifact directory (Rule 54)

## Minimum Verification

```bash
make harness-check
make docs-check
make release-gates-check
make release-gates-check-080
```

When release gate schema, legacy validation, or matrix governance changes, also
run:

```bash
make release-gates-check-strict
```

When release manifest generation or validation changes, also run:

```bash
python3 packaging/scripts/test_release_manifest.py
```

For 0.8.x release readiness, treat `make release-gates-check-080` (or the
`make release-gates-check-08x` alias) as the release target. `make release-gates-check`
remains the framework baseline; it is not the full release gate.

Run `make release-gates-check-legacy` only when the legacy spec inputs it
requires are present.  In clones where those inputs are absent, record the
absence instead of treating legacy validation as default evidence.

## Canonical References

- [../../project/release-gates/README.md](../../project/release-gates/README.md)
- [../../guides/INSTALLATION.md](../../guides/INSTALLATION.md)
- [../../../AGENTS.md](../../../AGENTS.md)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.5 | 2026-04-24 | Codex | Added 60-day release governance routing |
| 0.5.5 | 2026-04-25 | Codex | Added SonarCloud quality sync points for release-gate validators |
| 0.6.0 | 2026-05-03 | Codex | Added install-verify/update-matrix regression sync points |
| 0.6.1 | 2026-05-06 | Kang | Added merge-integrity and residual-code sync points (Rule 31) |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
