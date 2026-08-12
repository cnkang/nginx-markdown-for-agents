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

| Combination ID | Dimension | Covered Values | Test Type | Covering Sub-Spec |
|---------------|-----------|----------------|-----------|------------------|
| TM-PLATFORM-01 | Platform | [Ubuntu, macOS] | [CI matrix / manual] | [sub-spec name] |
| TM-NGINX-01 | NGINX Version | [1.24.x, 1.26.x, 1.27.x] | [CI matrix] | [sub-spec name] |
| TM-SIZE-01 | Response Size Tier | [Small, Medium, Large, Extra-Large] | [Unit / e2e / benchmark] | [sub-spec name] |
| TM-ENGINE-01 | Conversion Engine | [full-buffer, streaming] | [Unit / e2e / diff test] | [sub-spec name] |
| TM-PATH-01 | Conversion Path | [convert, skip, fallback/fail-open] | [Unit / e2e] | [sub-spec name] |
```

## Gap Record Format

If infrastructure or resource constraints block a cell, the sub-spec must record the gap and rationale:

| Dimension | Uncovered Value | Rationale | Risk Assessment |
|-----------|----------------|-----------|-----------------|
| — | — | — | — |

## Aggregate Coverage Status

Before release, aggregate all sub-spec coverage mappings. Ensure every
required combination of values across the matrix has at least one covering
sub-spec. Covering each value independently is not sufficient:

| Combination ID | Dimension | Value | Covering Sub-Spec | Status |
|---------------|-----------|-------|-------------------|--------|
| TM-PLATFORM-01 | Platform | Ubuntu | — | Pending |
| TM-PLATFORM-01 | Platform | macOS | — | Pending |
| TM-NGINX-01 | NGINX Version | 1.24.x | — | Pending |
| TM-NGINX-01 | NGINX Version | 1.26.x | — | Pending |
| TM-NGINX-01 | NGINX Version | 1.27.x | — | Pending |
| TM-SIZE-01 | Response Size Tier | Small | — | Pending |
| TM-SIZE-01 | Response Size Tier | Medium | — | Pending |
| TM-SIZE-01 | Response Size Tier | Large | — | Pending |
| TM-SIZE-01 | Response Size Tier | Extra-Large | — | Pending |
| TM-ENGINE-01 | Conversion Engine | full-buffer | — | Pending |
| TM-ENGINE-01 | Conversion Engine | streaming | — | Pending |
| TM-PATH-01 | Conversion Path | convert | — | Pending |
| TM-PATH-01 | Conversion Path | skip | — | Pending |
| TM-PATH-01 | Conversion Path | fallback/fail-open | — | Pending |

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
