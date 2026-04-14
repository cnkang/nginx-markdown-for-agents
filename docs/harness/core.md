# Harness Core

This document describes how the repo-native harness should behave during
spec-driven work. It does not restate HTML conversion, NGINX lifecycle, or FFI
semantics. Those stay in canonical docs and in `AGENTS.md`.

## Core Loop

1. Bind the current task to a concrete spec when possible.
2. Emit a short risk card before editing.
3. Route through the canonical manifest.
4. Pick one primary risk pack and any supporting packs.
5. Build a phased verification matrix before broad edits.
6. Execute, retry once on drift, then escalate or ask for outside voice if the
   work does not converge.
7. Record bounded reflection and promotion evidence in the user-local state
   carrier, not in repo truth files.

## Spec Resolver Contract

- Start from the current user task.
- If an active spec pointer exists, use it to refine the target.
- If multiple specs are in motion, explain why the current task binds to the
  chosen spec.
- If no spec can be bound confidently, degrade explicitly. Do not guess.

Use the repo helper when you want a concrete resolution pass:

```bash
python3 tools/harness/resolve_spec.py --hint "continue streaming parity work"
```

Optional local pointer files:

- `.kiro/active-spec.json` with `{"spec": "<dir-name-or-specId>"}`
- `.kiro/active-spec.txt` with one dir name or specId

## Preflight Risk Card

The default card stays short:

- touched area
- likely primary pack
- supporting packs, if any
- minimum cheap blockers
- one-sentence risk note

Expand to a detailed card only when:

- routing confidence is low
- multiple high-risk surfaces overlap
- warnings keep repeating
- the diff starts to widen instead of converge

## Verification Matrix

Run checks in phases:

1. Cheap blockers
   - `make harness-check`
   - other low-cost docs or config checks for the touched area
2. Focused semantic checks
   - one or more pack-specific commands from the routing manifest
3. Broader umbrella checks
   - runtime smoke, replay-driven comparisons, or release-level checks

Verification may raise the effective risk level above the initial route. If it
does, the route changes. The initial guess does not get special authority.

The default path should stay cheap. Prefer a fast blocker-first flow and widen
only when the touched surface, warnings, or drift signals justify more cost.
Do not spend replay or history-scan budget on every task by default.

Warnings are not cleanup theater. Do not silence a warning by weakening checks,
shrinking coverage, or deleting behavior unless the warning itself proves the
behavior is invalid. Fix the underlying problem or escalate it explicitly.

## Status Semantics

- `PASS`: the check ran and matched the contract
- `FAIL`: the contract is broken and the task is blocked
- `SKIP_NOT_PRESENT`: optional local-only input was not present
- `WARN_NEEDS_AUTHOR_REVIEW`: the harness found a likely drift that should be
  reviewed by the author, but it is not a public-repo failure by itself

Harness tools must map malformed or unreadable inputs into these explicit
statuses whenever possible. Public manifests should fail clearly. Optional local
inputs and user-local state should degrade explicitly instead of crashing with a
raw traceback.

## Conflict Protocol

- The user task and bound spec set the goal.
- `AGENTS.md` and harness rules set correctness, safety, and engineering
  boundaries.
- If the goal appears to violate the contract, stop and explain the mismatch.
- Require human confirmation before proceeding with a contract-breaking path.

## Loop and Drift Rescue

On the first drift trigger:

1. shrink the diff
2. recompute route and verification
3. retry once

If the same pattern repeats, escalate or ask for outside voice. The harness
must not burn tokens pretending every retry is fresh work.

## Proving Grounds

When validating harness evolution across broad scenarios, keep the first proving
ground on runtime-risk surfaces and add static-quality proving grounds second.
This preserves the priority order from the design reviews: correctness and
safety on the real request path first, quality-discipline expansion second.

## Outside Voice

Use CLI-based outside voice when:

- the route keeps flipping
- warnings recur without narrowing
- loop/drift triggers more than once
- the harness cannot decide between safety-preserving options

Prefer a different model family than the current driver:

- Codex driver -> `qwen` or Claude Code
- Qwen driver -> Codex or Claude Code
- Claude Code driver -> Codex or `qwen`

## State Carrier

Keep short-lived state outside repo truth files. The user-local state carrier is
for:

- retry and drift counts
- bounded reflection summaries
- evidence that a small mistake has repeated enough to justify a rule upgrade

Repo-owned docs keep durable truth. The state carrier keeps execution memory.
