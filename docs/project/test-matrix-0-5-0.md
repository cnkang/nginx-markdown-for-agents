# 0.5.0 Cross-Spec Test Matrix

## Overview

All sub-specs map their test plans to this matrix. Combined coverage must address all cells.

## Dimension Definitions

| Dimension | Values |
|-----------|--------|
| Platform | Ubuntu (primary), macOS (secondary) |
| NGINX Version | 1.24.x (LTS), 1.26.x (stable), 1.27.x (mainline) |
| Response Size Tier | Small ([0, 10KB)), Medium ([10KB, 1MB)), Large ([1MB, 64MB)), Extra-Large ([64MB, ∞)) — half-open disjoint boundaries: exactly 10KB maps to Medium, exactly 1MB maps to Large, exactly 64MB maps to Extra-Large |
| Conversion Engine | full-buffer, streaming |
| Conversion Path | convert (successful conversion), skip (ineligible skip), fallback/fail-open (pre-commit fallback) |

## Coverage Mapping Template

Each sub-spec fills in the following template to identify complete covered
tuples. A row is one executable combination across all dimensions. Listing a
value in isolation does not establish coverage for the cross-product.
The **Combination ID** must be deterministic and globally unique: derive it
from every listed dimension value (for example a stable hash of the
canonical dimension tuple), or specify the complete dimension tuple as the
canonical key — never reuse the same ID for different tuples.

```markdown
## Test Matrix Coverage — [Sub-Spec Name]

| Combination ID | Platform | NGINX Version | Response Size Tier | Conversion Engine | Conversion Path | Test Type | Covering Sub-Spec |
|---------------|----------|---------------|--------------------|-------------------|-----------------|-----------|------------------|
| TM-001 (placeholder — derive from the full tuple, e.g. a stable hash) | Ubuntu | 1.26.x | Small | full-buffer | convert | CI / e2e | [sub-spec name] |
| TM-002 (placeholder — derive from the full tuple, e.g. a stable hash) | macOS | 1.27.x | Large | streaming | fallback/fail-open | manual / benchmark | [sub-spec name] |
```

## Gap Record Format

If infrastructure or resource constraints block a cell, the sub-spec must record the gap and rationale:

| Combination ID | Missing Tuple | Rationale | Risk Assessment |
|---------------|---------------|-----------|-----------------|
| — | — | — | — |

## Aggregate Coverage Status

The required coverage set is the complete Cartesian product of the listed
dimensions: 2 platforms x 3 NGINX versions x 4 response size tiers x 2
conversion engines x 3 conversion paths = 144 tuples. Before release,
aggregate all sub-spec coverage mappings. Ensure every required tuple has at
least one covering sub-spec. Covering each value independently is not
sufficient:

| Combination ID | Platform | NGINX Version | Response Size Tier | Conversion Engine | Conversion Path | Covering Sub-Spec | Status |
|---------------|----------|---------------|--------------------|-------------------|-----------------|------------------|--------|
| TM-001 | Ubuntu | 1.26.x | Small | full-buffer | convert | — | Pending |
| TM-002 | macOS | 1.27.x | Large | streaming | fallback/fail-open | — | Pending |

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-15 | Hermes | Define the required coverage set as the complete 144-tuple Cartesian product |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
