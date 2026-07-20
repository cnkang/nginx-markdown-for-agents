# 0.4.0 Release Gates

This directory contains governance artifacts, templates, naming convention references, and quality gates for the `nginx-markdown-for-agents` 0.4.0 release.

## Release Model

The 0.4.0 release is managed as a release train with six sub-specs:

| # | Sub-Spec | Priority |
|---|----------|----------|
| 1 | overall-scope-release-gates | P0 |
| 2 | packaging-and-first-run | P0 |
| 3 | benchmark-corpus-and-evidence | P0 |
| 4 | rollout-safety-controlled-enablement | P0 |
| 5 | prometheus-module-metrics | P0 |
| 6 | parser-path-optimization | P1 |

All five P0 sub-specs must ship in 0.4.0. The P1 sub-spec ships if ready; otherwise it defers to 0.5.x without blocking the release.

## Artifacts

| File | Description |
|------|-------------|
| `naming-conventions.md` | Cross-spec naming convention reference for directives, metrics, reason codes, and benchmark fields |
| `dod-template.md` | Unified Definition of Done template (six checkpoints) |
| `release-checklist.md` | Unified release checklist (documentation, testing, compatibility, operations gates) |
| `risk-register-template.md` | Risk register template for sub-specs |
| `test-matrix.md` | Cross-spec test matrix (platform, NGINX version, response size, conversion path) |
| `boundary-description-template.md` | 0.4.0 vs 0.5.x boundary description template |
| `go-no-go-template.md` | Go/No-Go decision record template |
| `scope-evaluation-process.md` | Scope evaluation process for new proposals |

The four `*-0-5-0` stamped templates (`dod-template-0-5-0.md`,
`boundary-description-template-0-5-0.md`, `risk-register-template-0-5-0.md`,
`scope-creep-evaluation-0-5-0.md`) were relocated here from `docs/project/` on
2026-07-09 so that all release-gate artifacts colocate in one directory. They
are retained for historical reference; the unstamped `*.md` templates above are
the active ones consumed by tooling.

## Usage

Each sub-spec consumes these artifacts during development and completion. Sub-spec owners use the templates to record DoD evaluations, risk registers, boundary descriptions, and test matrix coverage. The release checklist and Go/No-Go template are used at release time to verify all gates pass before shipping 0.4.0.

For the full requirements and design, see the `specs/5-overall-scope-release-gates/` directory.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
