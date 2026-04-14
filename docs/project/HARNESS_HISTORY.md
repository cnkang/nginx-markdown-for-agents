# Harness History and Scope

This note records why the repo-owned harness exists, which mistakes it was
created to prevent, and what it intentionally does not try to become.

Use it when you want the project-level rationale behind the harness, not the
day-to-day execution contract. For execution rules, see
[../harness/core.md](../harness/core.md). For the architectural model, see
[../architecture/HARNESS_ARCHITECTURE.md](../architecture/HARNESS_ARCHITECTURE.md).

## Why the Harness Exists

The repository already had strong runtime documentation and a large set of
historical engineering rules in `AGENTS.md`, but the practical workflow for AI
assistance was still fragmented across:

- public tracked docs
- local `.kiro/steering/` files
- local `.kiro/specs/`
- review habits
- ad hoc execution memory inside individual sessions

That setup worked for one local environment, but it was not a stable open-source
contract. The harness was introduced to turn those scattered habits into a
tracked, reviewable, and executable repository asset.

## Problems It Was Created to Prevent

The harness exists to reduce a specific class of repeated engineering failures.

### 1. Hidden or drifting workflow rules

Important task-routing and validation behavior should not live only in private
steering files or in the memory of whichever agent or reviewer happens to be
running the task.

### 2. Silent misrouting on spec-driven work

Tasks that touch multiple high-risk surfaces can look similar while requiring
different validation depth. The harness introduces explicit spec resolution,
risk routing, and verification families so the repository does not rely on
guesswork.

### 3. Green checks without correctness or safety

The project had already learned the hard way that passing checks is not the same
as being correct or safe. The harness makes correctness and safety the
completion bar, with static checks treated as supporting evidence rather than
the final definition of success.

### 4. Local-only tooling becoming the real contract

The project still supports richer local workflows, especially through optional
`.kiro/` inputs, but those local files must remain adapters. Public repository
truth lives in tracked docs, checkers, and Make/CI entrypoints.

### 5. Repeating the same classes of fixes

This repository has accumulated many historical fixes in streaming, FFI,
observability, docs drift, and related operational surfaces. The harness turns
those recurring lessons into reusable routing, risk-pack, and maintenance
discipline instead of forcing contributors to rediscover them manually.

## What the Harness Intentionally Does Not Do

The harness is deliberately scoped.

- Runtime architecture docs remain separate; the harness complements them
  rather than replacing them.
- Private spec content stays out of tracked repository files.
- The repository is not an agent platform or orchestration service — the
  harness governs workflow, not infrastructure.
- Local adapters and outside voice tools cannot overrule the repository
  contract.
- Warnings require real triage and, when necessary, real fixes — they are
  never treated as cosmetic cleanup.

## Durable Design Outcomes

The design and review process for the harness produced a few durable rules that
are worth preserving at the project level:

- repo-owned truth must stay in tracked files
- optional local inputs must degrade explicitly rather than crash or silently
  override tracked behavior
- cheap validation should be the default path, with broader scans used as
  escalation tools
- sync checks must be executable, not only documented
- repeated fixes and repeated mistakes should feed back into rule evolution

Those outcomes now live in the harness docs and tooling, but this page explains
why they exist in the first place.

## What Stays Outside Public Docs

The repository keeps the durable outcomes, not every intermediate conversation.

Interactive review logs, branching A/B/C plan alternatives, raw review JSONL,
and timeline telemetry remain planning history rather than public contract.
They are useful during design and maintenance, but they would add noise and
duplicate transient reasoning if copied into the tracked docs.
