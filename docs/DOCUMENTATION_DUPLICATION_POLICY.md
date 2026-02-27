# Documentation Duplication and Sync Policy

## Purpose

The repository now follows a **single-source documentation policy**.

- Canonical docs live under `docs/`.
- Component directories should link to canonical docs instead of mirroring them.

## Rules

1. Do not create mirrored Markdown copies under `components/`.
2. Update canonical docs in `docs/` directly.
3. Keep `docs/archive/` for historical notes only.
4. Use tooling checks before finishing documentation changes.

## Canonical Locations

- Project overview: `README.md`
- Build/install/config/ops guides: `docs/guides/`
- Feature docs: `docs/features/`
- Testing docs: `docs/testing/`
- Project status: `docs/project/`
- Repository structure: `docs/architecture/REPOSITORY_STRUCTURE.md`

## Validation Commands

```bash
python3 tools/docs/check_duplicate_docs.py
python3 tools/docs/check_docs.py
```

## Archive Rule

`docs/archive/` is not source-of-truth for active behavior and should not be referenced as current guidance.
