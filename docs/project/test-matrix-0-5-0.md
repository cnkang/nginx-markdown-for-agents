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

Each sub-spec fills in the following template to identify its covered cells:

```markdown
## Test Matrix Coverage — [Sub-Spec Name]

| Dimension | Covered Values | Test Type |
|-----------|---------------|-----------|
| Platform | [Ubuntu, macOS] | [CI matrix / manual] |
| NGINX Version | [1.24.x, 1.26.x, 1.27.x] | [CI matrix] |
| Response Size Tier | [Small, Medium, Large, Extra-Large] | [Unit / e2e / benchmark] |
| Conversion Engine | [full-buffer, streaming] | [Unit / e2e / diff test] |
| Conversion Path | [convert, skip, fallback/fail-open] | [Unit / e2e] |
```

## Gap Record Format

If a cell cannot be covered due to infrastructure or resource constraints, the gap and rationale must be recorded:

| Dimension | Uncovered Value | Rationale | Risk Assessment |
|-----------|----------------|-----------|-----------------|
| — | — | — | — |

## Aggregate Coverage Status

Before release, aggregate all sub-spec coverage mappings to ensure each value in each dimension is covered by at least one sub-spec:

| Dimension | Value | Covering Sub-Spec | Status |
|-----------|-------|-------------------|--------|
| Platform | Ubuntu | — | Pending |
| Platform | macOS | — | Pending |
| NGINX Version | 1.24.x | — | Pending |
| NGINX Version | 1.26.x | — | Pending |
| NGINX Version | 1.27.x | — | Pending |
| Response Size Tier | Small | — | Pending |
| Response Size Tier | Medium | — | Pending |
| Response Size Tier | Large | — | Pending |
| Response Size Tier | Extra-Large | — | Pending |
| Conversion Engine | full-buffer | — | Pending |
| Conversion Engine | streaming | — | Pending |
| Conversion Path | convert | — | Pending |
| Conversion Path | skip | — | Pending |
| Conversion Path | fallback/fail-open | — | Pending |
