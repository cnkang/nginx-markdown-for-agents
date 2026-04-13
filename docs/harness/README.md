# Harness Overview

This directory is the repo-owned entrypoint for the spec-native harness.

Use it when you need to answer one of these questions quickly:

- what is the harness flow for a new task
- which risk pack owns the current change surface
- which checks are cheap blockers vs deeper validation
- where Kiro adapters should point instead of inventing local truth

Start here, then branch out:

| Need | Read |
|------|------|
| Overall workflow, conflict policy, and escalation rules | [core.md](core.md) |
| Canonical machine-readable routing source | [routing-manifest.json](routing-manifest.json) |
| Human-readable routing summary | [routing-manifest.md](routing-manifest.md) |
| Risk-pack index | [risk-packs/README.md](risk-packs/README.md) |
| System design and rationale for the harness itself | [../architecture/HARNESS_ARCHITECTURE.md](../architecture/HARNESS_ARCHITECTURE.md) |
| Contributor maintenance workflow for evolving harness rules | [../guides/HARNESS_MAINTENANCE.md](../guides/HARNESS_MAINTENANCE.md) |

## Harness Flow

```mermaid
flowchart TD
    A["Task or spec"] --> B["Spec resolver"]
    B --> C["Short risk card"]
    C --> D["Routing manifest"]
    D --> E["Primary pack + supporting packs"]
    E --> F["Verification matrix"]
    F --> G["Execute"]
    G --> H{"Loop or drift?"}
    H -- "no" --> I["Finish with evidence"]
    H -- "yes" --> J["Shrink diff + recompute route/verify"]
    J --> K{"Converges?"}
    K -- "yes" --> G
    K -- "no" --> L["Escalate or outside voice"]
```

## Truth Surfaces

```mermaid
flowchart LR
    A["AGENTS.md<br/>map + contract"] --> B["docs/harness/<br/>core + manifest + packs"]
    B --> C["Make / CI / checkers"]
    B --> D["Optional .kiro/steering adapters"]
    B --> E["Rule-maintenance skills"]
    F[".kiro/specs (read-only input)"] --> B
    G["User-local state carrier"] --> E
    E --> B
```

## Guardrails

- `AGENTS.md` remains the Codex-first contract.
- `docs/harness/` owns reusable harness truth, not domain semantics already documented elsewhere.
- `.kiro/specs/` is read-only input, not a place for harness caches or annotations.
- `.kiro/steering/` is an adapter layer. It can summarize and link, but it does not get its own rules.
- Outside voice is advisory. A different model family can challenge the current path, but it does not overrule the user.

## Canonical References

- [../architecture/HARNESS_ARCHITECTURE.md](../architecture/HARNESS_ARCHITECTURE.md)
- [../guides/HARNESS_MAINTENANCE.md](../guides/HARNESS_MAINTENANCE.md)
- [../architecture/ADR/0005-repo-owned-harness.md](../architecture/ADR/0005-repo-owned-harness.md)
- [../architecture/README.md](../architecture/README.md)
- [../testing/README.md](../testing/README.md)
- [../DOCUMENTATION_DUPLICATION_POLICY.md](../DOCUMENTATION_DUPLICATION_POLICY.md)
