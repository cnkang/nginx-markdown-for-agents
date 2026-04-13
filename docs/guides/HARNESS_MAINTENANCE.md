# Harness Maintenance Guide

This guide explains how to maintain the repo-owned harness without turning it
into a second product or a local-only workflow.

Use it when you need to:

- update agent-facing repository rules
- add or refine a risk pack
- wire new validation paths into the harness
- reconcile public repo truth with optional local `.kiro/` helpers

## What the Harness Owns

The harness owns repository-level execution guidance for spec-driven work:

- spec resolution policy
- routing and pack selection
- verification family wiring
- conflict and escalation policy
- cheap and full validation entrypoints

It does **not** own:

- runtime NGINX semantics already documented elsewhere
- private spec details that live only in `.kiro/specs/`
- ephemeral task logs that belong in local state, not tracked docs

## Public Source of Truth

When you change harness behavior, update repo-owned truth first:

- `AGENTS.md`
- `docs/harness/`
- `tools/harness/`
- `Makefile`
- `.github/workflows/ci.yml`

Optional local files may summarize or refine behavior, but they must follow the
tracked contract instead of inventing one.

## Normal Maintenance Workflow

1. Bind the change to a concrete task or spec when possible.
2. Decide whether the change is:
   - sync work
   - rule evolution
   - or a new risk surface
3. Update tracked harness truth first.
4. Run cheap validation:

```bash
make harness-check
```

5. If docs, release-gate wiring, or broader validation changed, run:

```bash
make harness-check-full
```

6. If optional local `.kiro/` adapters exist, keep them aligned after the
   tracked change is correct.

## Adding or Changing a Risk Pack

Create or update a risk pack only when the surface is distinct and durable.

Good reasons:

- repeated historical failures in the same technical area
- a new cross-boundary hazard that needs explicit checks
- an existing pack becoming too broad to reason about safely

Poor reasons:

- renaming a pack without changing behavior
- copying runtime docs into harness docs
- adding a pack only because a single task felt inconvenient

When updating a pack:

1. keep the pack focused on task overlays and verification, not domain restatement
2. update the canonical routing manifest
3. update the readable manifest summary if needed
4. add or update targeted regression tests
5. run `make harness-check`

## Evolving Rules from Evidence

The harness should learn from repeated mistakes, but it should not promote
every noisy warning into a permanent rule.

Preferred evidence sources:

1. repeated fixes in git history, especially against `main...HEAD`
2. cross-spec replay recurrences
3. explicit user policy decisions
4. repeated drift or warning patterns captured in local state

Before promoting a rule, ask:

- is the pattern repeated rather than isolated
- does it cross tasks or specs
- would a stronger rule or pack actually prevent the mistake

## Optional Local Inputs

The project deliberately allows richer local workflows without making them a
public requirement.

Supported optional inputs include:

- `.kiro/specs/`
- `.kiro/active-spec.json`
- `.kiro/active-spec.txt`
- `.kiro/steering/*.md`

Rules for optional inputs:

- absence is reported as `SKIP_NOT_PRESENT`, not failure
- presence may enable stricter local checks
- repo-owned truth still wins if there is disagreement
- `.kiro/specs/` is read-only input, not a cache or annotation store

## Outside Voice

Use outside voice when the harness stops converging cleanly:

- the route keeps flipping
- warnings recur without narrowing
- drift fires more than once
- a safe path exists, but the harness cannot choose confidently

Prefer a different model family than the current driver so the second opinion
is more likely to challenge the current assumptions.

## Open-Source Documentation Expectations

When you add harness behavior, document it in the right layer:

- `docs/architecture/` for system design and decision rationale
- `docs/guides/` for maintenance and contributor procedure
- `docs/harness/` for the canonical execution overlays themselves
- `docs/project/` for current status if the change materially alters repository
  posture

Do not leave important design rationale only in ephemeral review threads or
private local notes.

## Verification Checklist

Before closing substantial harness work:

- run `make harness-check`
- run `make harness-check-full` when broader docs or release wiring changed
- make sure new docs are linked from the relevant index pages
- make sure the readable summary matches the structured manifest
- make sure local-only helpers are still optional rather than required

## References

- [../harness/README.md](../harness/README.md)
- [../harness/core.md](../harness/core.md)
- [../architecture/HARNESS_ARCHITECTURE.md](../architecture/HARNESS_ARCHITECTURE.md)
- [../architecture/ADR/0005-repo-owned-harness.md](../architecture/ADR/0005-repo-owned-harness.md)
