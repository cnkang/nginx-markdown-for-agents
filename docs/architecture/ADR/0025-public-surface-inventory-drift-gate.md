# ADR-0025: Public Surface Inventory and Drift Gate

## Status

Accepted

## Date

2026-07-30

## Context

The compatibility surface is distributed across the NGINX command table,
Rust reason and FFI definitions, dynamic-configuration parsing, metrics
rendering, generated headers, and operator documentation. Checking names or
prose alone does not detect changes to accepted values, handler behavior,
labels, ABI signatures, or reject-only status. The 1.0 freeze also needs a
deterministic check that can run in a clean checkout without compiling NGINX
or invoking a local specification adapter.

## Decision

Maintain `docs/harness/public-surface-inventory.json` as the repository-owned
declaration of the compatibility surfaces that are currently tracked. The
fail-closed `tools/harness/detect_public_surface_drift.py` extractor reads the
live C and Rust source metadata, checks the generated FFI header ABI, and
compares metadata as well as names for directives, OTel controls,
dynamic-configuration keys, metrics, reason codes, and FFI exports.

This is a **source metadata and ABI drift gate**, not a runtime behavior
contract. Directive defaults, syntax, status, and migration targets are
read from the source command table, handler signatures, and inline
metadata; dynconf type/allowed/default/inheritance are read from parser
tables and field declarations; metric bounded cardinality is declared in
the inventory and checked against label value sources. The gate does not
execute directive create/merge functions, dynconf apply paths, or runtime
metric rendering. Runtime behavior is verified by the existing unit,
integration, and E2E test suites. A source comment or detector constant
change alone can satisfy this gate; it cannot by itself prove runtime
behavior is unchanged.

The inventory, implementation, tests, and the relevant operator documentation
must be updated in the same change set. The check is exposed through
`make public-surface-drift-check` and is a blocking input to
`make release-gates-check-092`. It is a compatibility-contract check, not a
second runtime schema or a promise that every listed internal symbol is a
third-party ABI.

Malformed source registries, duplicate entries, missing fields, unsafe paths,
and source/inventory disagreement fail the check. Inventory validation runs
before source extraction so a malformed declaration cannot be mistaken for a
clean implementation comparison.

## Consequences

### Positive Consequences

- Contract drift is visible in a clean checkout before a release gate runs.
- The check covers semantic metadata instead of only detecting renamed items.
- The inventory gives the 1.0 freeze review one stable, searchable surface.

### Negative Consequences

- Public-surface changes require maintaining the inventory and its tests.
- Static extraction cannot replace runtime tests for request lifecycle and
  wire behavior.
- Generated headers and compiler-only declarations may require an explicit
  inventory or test adjustment when the source layout changes.

## Alternatives Considered

- **Names-only inventory:** rejected because it misses flags, labels, defaults,
  handler classification, signatures, and ABI drift.
- **Documentation-only contract:** rejected because prose cannot reliably be
  compared with source in a clean release checkout.
- **Compiler/reflection-based extraction:** deferred because it adds build
  dependencies and does not provide a portable check for every release gate.

## References

- [Public Surface Inventory](../PUBLIC_SURFACE_INVENTORY.md)
- [Machine-readable inventory](../../harness/public-surface-inventory.json)
- [Drift detector](../../../tools/harness/detect_public_surface_drift.py)
- [ADR-0005: Repo-Owned Harness](0005-repo-owned-harness.md)
- [ADR-0018: Observability Schema and Reason Registry](0018-090-observability-schema-v1-reason-registry.md)
- [ADR-0019: Production Readiness Release Gates](0019-090-production-readiness-release-gates.md)
