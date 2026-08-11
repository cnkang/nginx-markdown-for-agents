# Knowledge Base: nginx-markdown-for-agents

This directory is the project's **decision-layer knowledge base** (pilot).
It does NOT duplicate the guides, architecture docs, or changelogs — those
remain the living source of truth. This layer answers "what do I need to
remember / which rule applies here?" with the stable contract facts, mental
models, and pointers.

## When to Use This Directory

- You need the frozen 0.9.2 contract facts fast (directive list, defaults,
  dynconf keys, metrics) without re-reading CONFIGURATION.md end to end.
- You are reviewing or writing docs and must check a claim against the
  contract (for example "is `markdown_profile` still active?" → no, removed).
- You need to know which document to consult for a given question.
- You are onboarding a new contributor to the 0.9.2 contract.

## Core Mental Models

### 1. Three-layer fact hierarchy

| Layer | Nature | Where it lives |
|---|---|---|
| L1 contract facts | Frozen for 0.9.2 (directive names, defaults, counts) | `config-contract.md` + `docs/harness/public-surface-inventory.json` |
| L2 architectural decisions | Stable design rationale (ADRs) | `docs/architecture/ADR/` |
| L3 living documents | Change frequently (changelog, release notes, plans) | `CHANGELOG.md`, `docs/releases/`, `docs/development/` |

Rule of thumb: if a fact can change this week, it belongs in L3 — reference
it, never copy it into this directory.

### 2. Public surface is the frozen contract

0.9.2 freezes the public surface. The complete tables, counts, and values
live in `config-contract.md`. The machine-readable authoritative source is
`docs/harness/public-surface-inventory.json`, validated by the drift gate
(`make public-surface-drift-check`). Anything outside this surface is either
removed or not part of the contract.

### 3. Removed ≠ deprecated

Directives removed in 0.9.2 fail `nginx -t` with NGINX's standard
`unknown directive` error — there are no migration stubs left. If docs or
configs reference a removed directive, they are stale. See
`docs/guides/0.9.2-breaking-changes.md` for the full removal list.

## Contract Loading

This README intentionally does not duplicate frozen numeric facts. Load
`config-contract.md` for the current directive, dynconf, metric, reason-code,
limit, and FFI tables. Use `public-surface-inventory.json` when you need a
machine-readable value or count.

## Contract Files (load on demand)

- `config-contract.md` — **The frozen 0.9.2 contract in one place**: full
  directive, dynconf, metric, reason-code, and limit tables, plus an FFI
  surface summary. Load this for any directive/default/metric question. For
  complete FFI export names and signatures, use the inventory or generated
  header referenced below.

## Index: Where to Find What

| Question | Document |
|---|---|
| Directive syntax, defaults, contexts | `docs/guides/CONFIGURATION.md` (authoritative prose) |
| Frozen contract machine-readable | `docs/harness/public-surface-inventory.json` |
| Full config contract tables | `config-contract.md` (this KB) |
| Breaking changes & migration | `docs/guides/0.9.2-breaking-changes.md`, `docs/guides/MIGRATION-0.9.2.md` |
| Rollback | `docs/guides/ROLLBACK-0.9.2.md` |
| Architecture / ADRs | `docs/architecture/ADR/` (0025 drift gate, 0026 dynconf restore, 0027 OTel removal) |
| Release notes & checklist | `docs/releases/0.9.2-release-notes.md`, `0.9.2-release-checklist.md` |
| History of changes | `CHANGELOG.md` (L3 — never copied here) |
| Metrics & diagnostics schema | `docs/architecture/observability-schema-v1.md` |
| Dynconf semantics | `docs/architecture/ADR/0026-dynconf-file-restore-contract.md` |
| Project status & version planning | `docs/project/PROJECT_STATUS.md`, `docs/project/VERSION_PLANNING.md` |

## Maintenance Rules

1. **Never copy L3 content** (changelog entries, release-note text, plans)
   into this directory — link it.
2. **Keep L1 facts in sync with `public-surface-inventory.json`.** If the
   drift gate or `make harness-check` reports a change, update
   `config-contract.md` in the same batch.
3. When a directive, dynconf key, limit, metric, reason code, or FFI surface
   changes, update `config-contract.md`
   AND the Document Updates table of this file in one commit (descending
   order, newest on top).

## Document Updates

| Version | Date | Changes |
| --- | --- | --- |
| 0.9.2 | 2026-08-07 | Pilot: create decision-layer KB skeleton (L1 index + L2 config contract) from inventory ground truth. |
