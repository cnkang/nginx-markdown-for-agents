# Project Documentation

This directory contains project-level status and maintenance-oriented documentation.

## Contents

- [PROJECT_STATUS.md](PROJECT_STATUS.md) - current implementation and validation status (aligned to code and recent verification)
- [HARNESS_HISTORY.md](HARNESS_HISTORY.md) - why the repo-owned harness exists, which failures it is meant to prevent, and what remains intentionally out of scope
- [VERSION_PLANNING.md](VERSION_PLANNING.md) - active v0.9.2 development release-line planning and the v1.0 frozen compatibility contract that follows it
- 0.5.0 release-gate working set (historical gate records):
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

- `release-gates/` — reusable release-gate governance templates and historical
  0.7.0 gate definitions. The 0.5.x working sets listed above remain at this
  directory's top level as historical records. Current release validation uses
  the feature-oriented targets in the repository Makefile.
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

`PROJECT_STATUS.md` and the dedicated pages summarize the harness-related
repository posture. The details live in `docs/project/HARNESS_HISTORY.md`, `docs/harness/`,
`docs/architecture/HARNESS_ARCHITECTURE.md`, and
`docs/guides/HARNESS_MAINTENANCE.md`.

Use this section for repository-wide status and maintenance posture. Keep other project-level notes aligned with the current tracked codebase.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
