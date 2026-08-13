# 0.5.0 Cross-Spec Test Matrix

## Overview

All sub-specs map their test plans to this matrix. Combined coverage must address all cells.

## Dimension Definitions

| Dimension | Values |
|-----------|--------|
| Platform | Ubuntu (primary), macOS (secondary) |
| NGINX Version | 1.24.x (LTS), 1.26.x (stable), 1.27.x (mainline) |
| Response Size Tier | Small (<10KB), Medium (10KB–1MB), Large (1MB–64MB), Extra-Large (>64MB) |
| Conversion Engine | full-buffer, streaming |
| Conversion Path | convert (successful conversion), skip (ineligible skip), fallback/fail-open (pre-commit fallback) |

## Coverage Mapping Template

Each sub-spec fills in the following template to identify complete covered
tuples. A row is one executable combination across all dimensions. Listing a
value in isolation does not establish coverage for the cross-product.

```markdown
## Test Matrix Coverage — [Sub-Spec Name]

| Combination ID | Platform | NGINX Version | Response Size Tier | Conversion Engine | Conversion Path | Test Type | Covering Sub-Spec |
|---------------|----------|---------------|--------------------|-------------------|-----------------|-----------|------------------|
| TM-001 | Ubuntu | 1.26.x | Small | full-buffer | convert | CI / e2e | [sub-spec name] |
| TM-002 | macOS | 1.27.x | Large | streaming | fallback/fail-open | manual / benchmark | [sub-spec name] |
```

## Gap Record Format

If infrastructure or resource constraints block a cell, the sub-spec must record the gap and rationale:

| Combination ID | Missing Tuple | Rationale | Risk Assessment |
|---------------|---------------|-----------|-----------------|
| — | — | — | — |

## Aggregate Coverage Status

Before release, aggregate all sub-spec coverage mappings. Ensure every
required tuple of values across the matrix has at least one covering sub-spec.
Covering each value independently is not sufficient:

| Combination ID | Platform | NGINX Version | Response Size Tier | Conversion Engine | Conversion Path | Covering Sub-Spec | Status |
|---------------|----------|---------------|--------------------|-------------------|-----------------|------------------|--------|
| TM-001 | Ubuntu | 1.26.x | Small | full-buffer | convert | — | Pending |
| TM-002 | macOS | 1.27.x | Large | streaming | fallback/fail-open | — | Pending |

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
