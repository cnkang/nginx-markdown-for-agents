# Documentation Duplication and Sync Policy

## Purpose

The repository follows a **single-source documentation policy**.

- Canonical docs live under `docs/`.
- Component directories should link to canonical docs instead of mirroring them.
- One canonical document per volatile contract. Other documents explain,
  supplement, or link. They do not copy-maintain the same contract.

## Document Classes

| Class | Definition | Examples |
|---|---|---|
| Current normative docs | Active guidance for the current release line | `docs/guides/`, `docs/features/`, `docs/architecture/` |
| Historical docs | Records of past releases or decisions; accurate as history, not current guidance | `docs/guides/MIGRATION-0.8.md`, `docs/guides/MIGRATION-0.9.md`, `docs/guides/MIGRATION-0.9.1.md`, `docs/project/*-0-5-*`, `docs/project/release-gates/` |
| Generated docs | Machine-produced from a canonical source; do not hand-edit | `docs/guides/PACKAGE_COMPATIBILITY.md` sections rendered by `tools/render_release_matrix_docs.py` |
| Navigation/index docs | Entry points that list canonical documents | `docs/README.md`, `docs/guides/README.md`, `docs/features/README.md` |
| Release records | Per-version release notes and checklists | `docs/releases/` |

## Rules

1. Do not create mirrored Markdown copies under `components/`.
2. Update canonical docs in `docs/` directly.
3. Keep `docs/archive/` for historical notes only (gitignored local archive).
4. Use tooling checks before finishing documentation changes.
5. **Current indexes MUST NOT link a historical document as if it were
   active guidance.** Historical migration guides stay reachable from
   current guides only as clearly-labeled historical references.
6. **One canonical document per volatile contract.** Configuration syntax,
   metrics families, schemas, and defaults have exactly one source of truth.
   Other documents link to it.
7. Navigation stubs (for example `docs/guides/INSTALL.md`) exist only for
   link compatibility or package layout contracts. They must not carry
   content that duplicates the canonical document.

## Canonical Locations

- Project overview: `README.md`
- Build/install/config/ops guides: `docs/guides/`
- Feature docs: `docs/features/`
- Testing docs: `docs/testing/`
- Project status: `docs/project/`
- Release notes, release checklists, and release matrices: `docs/releases/`
- In-flight implementation plans: `docs/development/`
- Machine-readable evidence artifacts: `docs/evidence/`
- Deployment and operational reference: `docs/operations/`
- Knowledge-base contracts and maintained context: `docs/knowledge-base/`
- Repository structure: `docs/architecture/REPOSITORY_STRUCTURE.md`
- Contributor writing policy: `docs/WRITING_GUIDE.md`
- Harness maintenance and skill setup: `docs/harness/`

## Validation Commands

```bash
python3 tools/docs/check_duplicate_docs.py
python3 tools/docs/check_docs.py
```

## Archive Rule

`docs/archive/` is not source-of-truth for active behavior and must not serve
as current guidance. Git ignores it, and it holds local historical notes only.
The repository keeps historical records in tracked `docs/` paths (for example
`docs/guides/MIGRATION-0.8.md`) when release gates or historical accuracy
require them.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-28 | Hermes | Added document classes, historical-link rule, canonical-per-contract rule, and navigation-stub rule; updated canonical locations |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, added mermaid diagrams where applicable, verified directive accuracy against code, added update tracking section |
