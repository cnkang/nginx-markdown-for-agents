# ADR-0005: Repo-Owned Harness for Agent Workflow and Spec Routing

## Status

Accepted

## Context

The project already had strong runtime documentation and a growing body of
agent-facing rules in `AGENTS.md`, but the execution workflow around spec
resolution, risk routing, validation, and maintenance was still fragmented.

Important behavior lived in several places:

- `AGENTS.md`
- local `.kiro/steering/` files
- local `.kiro/specs/`
- CI and Make targets
- maintainer habit and review history

This caused three structural problems:

1. public readers could not see the full workflow contract in tracked files
2. local-only steering risked drifting away from repo-owned rules
3. validation behavior was partly implicit instead of executable

At the same time, the project needed to remain open-source friendly. Private
`.kiro/` assets are useful for local work, but the repository must not require
them in order to validate or understand the public contract.

## Decision

Adopt a repo-owned harness with the following structure:

- `AGENTS.md` remains the top-level contract and map
- `docs/harness/` becomes the canonical repo-owned harness surface
- `docs/harness/routing-manifest.json` is the canonical structured routing
  source
- `docs/harness/routing-manifest.md` is the human-readable summary of that same
  source
- risk packs are organized by technical hazard, not by task label
- `.kiro/specs/` remains read-only input
- `.kiro/steering/` becomes an optional thin adapter layer, not a second source
  of truth
- short-lived execution memory moves to a user-local state carrier rather than
  tracked docs
- cheap and full harness validation are exposed through Make targets and Python
  helpers

## Consequences

### Positive Consequences

- Open-source readers can understand the harness from tracked repository files.
- Codex-first repository rules become durable and reviewable.
- Public validation does not depend on private `.kiro/` contents.
- Human-readable and machine-readable routing stay aligned around one canonical
  structured source.
- Repeated failures can drive measured harness evolution instead of relying only
  on oral history.

### Negative Consequences

- The repository gains another maintained documentation and tooling surface.
- Maintainers must keep harness docs, tools, Make targets, and CI wiring in
  sync.
- Local adapter files still need occasional updates, even though they are no
  longer authoritative.

## Alternatives Considered

### 1. Keep workflow logic in `AGENTS.md` and local steering only

Rejected because it keeps too much important behavior outside the tracked
repository or compresses too much into one file.

### 2. Make `.kiro/` the primary harness source

Rejected because `.kiro/` is intentionally private and optional. The public
repository cannot depend on it.

### 3. Use prose-only harness docs with no canonical structured manifest

Rejected because routing and verification relationships need an executable
source of truth, not only narrative explanation.

### 4. Build a larger agent platform first

Rejected because the project needed a boring, reviewable repository contract
before expanding into broader automation.

## References

- [../HARNESS_ARCHITECTURE.md](../HARNESS_ARCHITECTURE.md)
- [../../harness/README.md](../../harness/README.md)
- [../../harness/core.md](../../harness/core.md)
- [../../harness/routing-manifest.json](../../harness/routing-manifest.json)
- [../../guides/HARNESS_MAINTENANCE.md](../../guides/HARNESS_MAINTENANCE.md)
