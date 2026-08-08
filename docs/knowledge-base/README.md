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
| L1 contract facts | Frozen for 0.9.2 (directive names, defaults, counts) | `config-contract.md` + `public-surface-inventory.json` |
| L2 architectural decisions | Stable design rationale (ADRs) | `docs/architecture/ADR/` |
| L3 living documents | Change frequently (changelog, release notes, plans) | `CHANGELOG.md`, `docs/releases/`, `docs/development/` |

Rule of thumb: if a fact can change this week, it belongs in L3 — reference
it, never copy it into this directory.

### 2. Public surface is the frozen contract

0.9.2 freezes the public surface: **25 active directives, 0 reject-only
directives (all stubs removed), 6 dynconf keys, 12 metric families, 27
reason codes, 47 FFI exports, ABI v2**. Anything outside this surface is
either removed (38 directives total) or not part of the contract. The
authoritative source is
`docs/harness/public-surface-inventory.json`, validated by the drift gate
(`make public-surface-drift-check`).

### 3. Removed ≠ deprecated

Directives removed in 0.9.2 fail `nginx -t` with NGINX's standard
`unknown directive` error — there are no migration stubs left. If docs or
configs reference a removed directive, they are stale. See
`docs/guides/0.9.2-breaking-changes.md` for the full removal list.

## Key Numbers (0.9.2 frozen contract)

| Item | Value | Source |
|---|---|---|
| Active directives | 25 | `config-contract.md` |
| Removed directives (total) | 38 | `docs/guides/0.9.2-breaking-changes.md` |
| Dynconf keys | 6 (`filter`, `prune_noise`, `log_verbosity`, `error_policy`, `streaming_buffer`, `schema_version`) | `config-contract.md` |
| Metric families | 12 | `config-contract.md` |
| Reason codes | 27 (0–26) | `config-contract.md` |
| FFI exports | 47, ABI v2, all `INTERNAL_ONLY` | `config-contract.md` |
| MSRV / toolchain | Rust 1.97.0 (MSRV 1.97) | `rust-toolchain.toml` |
| OTel | Removed (ADR-0027) | `docs/architecture/ADR/0027-otel-removal-reintroduction-conditions.md` |
| Profiles | Removed (`markdown_profile` gone) | `docs/guides/0.9.2-breaking-changes.md` |

## Contract Files (load on demand)

- `config-contract.md` — **The frozen 0.9.2 contract in one place**: full
  25-directive table (syntax, default, context), the 6 dynconf keys with
  allowed values, the 12 metric families, reason-code list, FFI summary, and
  `markdown_limits` key semantics. Load this for any directive/default/
  metric question.

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
3. When a directive/metric/reason-code changes, update `config-contract.md`
   AND the Document Updates table of this file in one commit (descending
   order, newest on top).

## Document Updates

| Version | Date | Changes |
| --- | --- | --- |
| 0.9.2 | 2026-08-07 | Pilot: create decision-layer KB skeleton (L1 index + L2 config contract) from inventory ground truth. |
