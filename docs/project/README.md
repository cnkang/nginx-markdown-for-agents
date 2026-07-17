# Project Documentation

This directory contains project-level status and maintenance-oriented documentation.

## Contents

- [PROJECT_STATUS.md](PROJECT_STATUS.md) - current implementation and validation status (aligned to code and recent verification)
- [HARNESS_HISTORY.md](HARNESS_HISTORY.md) - why the repo-owned harness exists, which failures it is meant to prevent, and what remains intentionally out of scope
- [VERSION_PLANNING.md](VERSION_PLANNING.md) - recommended release framing and version cut lines for `0.4.0` through `0.6.x+`
- 0.5.0 release-gate working set (canonical `release-gates-check` inputs):
  - [release-gates-0-5-0.md](release-gates-0-5-0.md)
  - [release-checklist-0-5-0.md](release-checklist-0-5-0.md)
  - [compatibility-matrix-0-5-0.md](compatibility-matrix-0-5-0.md)
  - [test-matrix-0-5-0.md](test-matrix-0-5-0.md)
  - [go-nogo-template-0-5-0.md](go-nogo-template-0-5-0.md)
  - [naming-conventions-0-5-0.md](naming-conventions-0-5-0.md)
- 0.5.5 gate inputs:
  - [release-spec.md](0.5.5-release-spec.md)
  - [release-checklist-0-5-5.md](release-checklist-0-5-5.md)
  - [test-matrix-0-5-5.md](test-matrix-0-5-5.md)
- Recent Git/steering analysis reports (consumed by `harness-check`):
  - [recent-git-harness-steering-analysis-2026-04-24.md](recent-git-harness-steering-analysis-2026-04-24.md)
  - [recent-git-harness-steering-analysis-2026-05-03.md](recent-git-harness-steering-analysis-2026-05-03.md)

### Subdirectories

- `release-gates/` — release-gate governance templates and 0.7.0 gate definitions
  (the four `*-0-5-0` markdown templates are now colocated here with the rest of
  the gate artifacts).
- `../archive/project-history/` — local historical notes only (gitignored,
  per `docs/DOCUMENTATION_DUPLICATION_POLICY.md`: not source-of-truth and not
  referenced as current guidance). Contains release-past working drafts such as
  `0.5.5-*`, `0.6.3-*`, `0.7.0-*`, `0.9.0-migration-tracking`,
  `v070-deprecated-directives-verification`, `documentation-audit-2026-04-25`,
  release-notes templates, and `release-notes-0-6-*`. These are intentionally
  **not tracked** — treat the `docs/project/` top-level set as the maintained
  surface.
- `../archive/release-0.4.0/` — legacy 0.4.0 release records (gitignored local
  copy): `GO_NO_GO_0.4.0.md`, `RELEASE_CHECKLIST_0.4.0.md`.

Harness-related repository posture is summarized in `PROJECT_STATUS.md` and
detailed in `HARNESS_HISTORY.md`, `docs/harness/`,
`docs/architecture/HARNESS_ARCHITECTURE.md`, and
`docs/guides/HARNESS_MAINTENANCE.md`.

Use this section for repository-wide status, maintenance posture, and other project-level notes that should stay aligned with the current tracked codebase.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
