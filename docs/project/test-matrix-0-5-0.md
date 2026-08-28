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
| EXAMPLE-001 (illustrative sample; not aggregate coverage) | Ubuntu | 1.26.x | Small | full-buffer | convert | CI / e2e | [sub-spec name] |
| EXAMPLE-002 (illustrative sample; not aggregate coverage) | macOS | 1.27.x | Large | streaming | fallback/fail-open | manual / benchmark | [sub-spec name] |
```

These two rows illustrate the format only. They are not counted toward the
required cross-product coverage or the 144 required tuples.

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
sufficient. The following table enumerates the complete required set in
deterministic lexicographic order. A tuple remains Pending until the release
process records a covering sub-spec:

| Combination ID | Platform | NGINX Version | Response Size Tier | Conversion Engine | Conversion Path | Covering Sub-Spec | Status |
|---------------|----------|---------------|--------------------|-------------------|-----------------|------------------|--------|
| TM-001 | Ubuntu | 1.24.x | Small | full-buffer | convert | — | Pending |
| TM-002 | Ubuntu | 1.24.x | Small | full-buffer | skip | — | Pending |
| TM-003 | Ubuntu | 1.24.x | Small | full-buffer | fallback/fail-open | — | Pending |
| TM-004 | Ubuntu | 1.24.x | Small | streaming | convert | — | Pending |
| TM-005 | Ubuntu | 1.24.x | Small | streaming | skip | — | Pending |
| TM-006 | Ubuntu | 1.24.x | Small | streaming | fallback/fail-open | — | Pending |
| TM-007 | Ubuntu | 1.24.x | Medium | full-buffer | convert | — | Pending |
| TM-008 | Ubuntu | 1.24.x | Medium | full-buffer | skip | — | Pending |
| TM-009 | Ubuntu | 1.24.x | Medium | full-buffer | fallback/fail-open | — | Pending |
| TM-010 | Ubuntu | 1.24.x | Medium | streaming | convert | — | Pending |
| TM-011 | Ubuntu | 1.24.x | Medium | streaming | skip | — | Pending |
| TM-012 | Ubuntu | 1.24.x | Medium | streaming | fallback/fail-open | — | Pending |
| TM-013 | Ubuntu | 1.24.x | Large | full-buffer | convert | — | Pending |
| TM-014 | Ubuntu | 1.24.x | Large | full-buffer | skip | — | Pending |
| TM-015 | Ubuntu | 1.24.x | Large | full-buffer | fallback/fail-open | — | Pending |
| TM-016 | Ubuntu | 1.24.x | Large | streaming | convert | — | Pending |
| TM-017 | Ubuntu | 1.24.x | Large | streaming | skip | — | Pending |
| TM-018 | Ubuntu | 1.24.x | Large | streaming | fallback/fail-open | — | Pending |
| TM-019 | Ubuntu | 1.24.x | Extra-Large | full-buffer | convert | — | Pending |
| TM-020 | Ubuntu | 1.24.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-021 | Ubuntu | 1.24.x | Extra-Large | full-buffer | fallback/fail-open | — | Pending |
| TM-022 | Ubuntu | 1.24.x | Extra-Large | streaming | convert | — | Pending |
| TM-023 | Ubuntu | 1.24.x | Extra-Large | streaming | skip | — | Pending |
| TM-024 | Ubuntu | 1.24.x | Extra-Large | streaming | fallback/fail-open | — | Pending |
| TM-025 | Ubuntu | 1.26.x | Small | full-buffer | convert | — | Pending |
| TM-026 | Ubuntu | 1.26.x | Small | full-buffer | skip | — | Pending |
| TM-027 | Ubuntu | 1.26.x | Small | full-buffer | fallback/fail-open | — | Pending |
| TM-028 | Ubuntu | 1.26.x | Small | streaming | convert | — | Pending |
| TM-029 | Ubuntu | 1.26.x | Small | streaming | skip | — | Pending |
| TM-030 | Ubuntu | 1.26.x | Small | streaming | fallback/fail-open | — | Pending |
| TM-031 | Ubuntu | 1.26.x | Medium | full-buffer | convert | — | Pending |
| TM-032 | Ubuntu | 1.26.x | Medium | full-buffer | skip | — | Pending |
| TM-033 | Ubuntu | 1.26.x | Medium | full-buffer | fallback/fail-open | — | Pending |
| TM-034 | Ubuntu | 1.26.x | Medium | streaming | convert | — | Pending |
| TM-035 | Ubuntu | 1.26.x | Medium | streaming | skip | — | Pending |
| TM-036 | Ubuntu | 1.26.x | Medium | streaming | fallback/fail-open | — | Pending |
| TM-037 | Ubuntu | 1.26.x | Large | full-buffer | convert | — | Pending |
| TM-038 | Ubuntu | 1.26.x | Large | full-buffer | skip | — | Pending |
| TM-039 | Ubuntu | 1.26.x | Large | full-buffer | fallback/fail-open | — | Pending |
| TM-040 | Ubuntu | 1.26.x | Large | streaming | convert | — | Pending |
| TM-041 | Ubuntu | 1.26.x | Large | streaming | skip | — | Pending |
| TM-042 | Ubuntu | 1.26.x | Large | streaming | fallback/fail-open | — | Pending |
| TM-043 | Ubuntu | 1.26.x | Extra-Large | full-buffer | convert | — | Pending |
| TM-044 | Ubuntu | 1.26.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-045 | Ubuntu | 1.26.x | Extra-Large | full-buffer | fallback/fail-open | — | Pending |
| TM-046 | Ubuntu | 1.26.x | Extra-Large | streaming | convert | — | Pending |
| TM-047 | Ubuntu | 1.26.x | Extra-Large | streaming | skip | — | Pending |
| TM-048 | Ubuntu | 1.26.x | Extra-Large | streaming | fallback/fail-open | — | Pending |
| TM-049 | Ubuntu | 1.27.x | Small | full-buffer | convert | — | Pending |
| TM-050 | Ubuntu | 1.27.x | Small | full-buffer | skip | — | Pending |
| TM-051 | Ubuntu | 1.27.x | Small | full-buffer | fallback/fail-open | — | Pending |
| TM-052 | Ubuntu | 1.27.x | Small | streaming | convert | — | Pending |
| TM-053 | Ubuntu | 1.27.x | Small | streaming | skip | — | Pending |
| TM-054 | Ubuntu | 1.27.x | Small | streaming | fallback/fail-open | — | Pending |
| TM-055 | Ubuntu | 1.27.x | Medium | full-buffer | convert | — | Pending |
| TM-056 | Ubuntu | 1.27.x | Medium | full-buffer | skip | — | Pending |
| TM-057 | Ubuntu | 1.27.x | Medium | full-buffer | fallback/fail-open | — | Pending |
| TM-058 | Ubuntu | 1.27.x | Medium | streaming | convert | — | Pending |
| TM-059 | Ubuntu | 1.27.x | Medium | streaming | skip | — | Pending |
| TM-060 | Ubuntu | 1.27.x | Medium | streaming | fallback/fail-open | — | Pending |
| TM-061 | Ubuntu | 1.27.x | Large | full-buffer | convert | — | Pending |
| TM-062 | Ubuntu | 1.27.x | Large | full-buffer | skip | — | Pending |
| TM-063 | Ubuntu | 1.27.x | Large | full-buffer | fallback/fail-open | — | Pending |
| TM-064 | Ubuntu | 1.27.x | Large | streaming | convert | — | Pending |
| TM-065 | Ubuntu | 1.27.x | Large | streaming | skip | — | Pending |
| TM-066 | Ubuntu | 1.27.x | Large | streaming | fallback/fail-open | — | Pending |
| TM-067 | Ubuntu | 1.27.x | Extra-Large | full-buffer | convert | — | Pending |
| TM-068 | Ubuntu | 1.27.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-069 | Ubuntu | 1.27.x | Extra-Large | full-buffer | fallback/fail-open | — | Pending |
| TM-070 | Ubuntu | 1.27.x | Extra-Large | streaming | convert | — | Pending |
| TM-071 | Ubuntu | 1.27.x | Extra-Large | streaming | skip | — | Pending |
| TM-072 | Ubuntu | 1.27.x | Extra-Large | streaming | fallback/fail-open | — | Pending |
| TM-073 | macOS | 1.24.x | Small | full-buffer | convert | — | Pending |
| TM-074 | macOS | 1.24.x | Small | full-buffer | skip | — | Pending |
| TM-075 | macOS | 1.24.x | Small | full-buffer | fallback/fail-open | — | Pending |
| TM-076 | macOS | 1.24.x | Small | streaming | convert | — | Pending |
| TM-077 | macOS | 1.24.x | Small | streaming | skip | — | Pending |
| TM-078 | macOS | 1.24.x | Small | streaming | fallback/fail-open | — | Pending |
| TM-079 | macOS | 1.24.x | Medium | full-buffer | convert | — | Pending |
| TM-080 | macOS | 1.24.x | Medium | full-buffer | skip | — | Pending |
| TM-081 | macOS | 1.24.x | Medium | full-buffer | fallback/fail-open | — | Pending |
| TM-082 | macOS | 1.24.x | Medium | streaming | convert | — | Pending |
| TM-083 | macOS | 1.24.x | Medium | streaming | skip | — | Pending |
| TM-084 | macOS | 1.24.x | Medium | streaming | fallback/fail-open | — | Pending |
| TM-085 | macOS | 1.24.x | Large | full-buffer | convert | — | Pending |
| TM-086 | macOS | 1.24.x | Large | full-buffer | skip | — | Pending |
| TM-087 | macOS | 1.24.x | Large | full-buffer | fallback/fail-open | — | Pending |
| TM-088 | macOS | 1.24.x | Large | streaming | convert | — | Pending |
| TM-089 | macOS | 1.24.x | Large | streaming | skip | — | Pending |
| TM-090 | macOS | 1.24.x | Large | streaming | fallback/fail-open | — | Pending |
| TM-091 | macOS | 1.24.x | Extra-Large | full-buffer | convert | — | Pending |
| TM-092 | macOS | 1.24.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-093 | macOS | 1.24.x | Extra-Large | full-buffer | fallback/fail-open | — | Pending |
| TM-094 | macOS | 1.24.x | Extra-Large | streaming | convert | — | Pending |
| TM-095 | macOS | 1.24.x | Extra-Large | streaming | skip | — | Pending |
| TM-096 | macOS | 1.24.x | Extra-Large | streaming | fallback/fail-open | — | Pending |
| TM-097 | macOS | 1.26.x | Small | full-buffer | convert | — | Pending |
| TM-098 | macOS | 1.26.x | Small | full-buffer | skip | — | Pending |
| TM-099 | macOS | 1.26.x | Small | full-buffer | fallback/fail-open | — | Pending |
| TM-100 | macOS | 1.26.x | Small | streaming | convert | — | Pending |
| TM-101 | macOS | 1.26.x | Small | streaming | skip | — | Pending |
| TM-102 | macOS | 1.26.x | Small | streaming | fallback/fail-open | — | Pending |
| TM-103 | macOS | 1.26.x | Medium | full-buffer | convert | — | Pending |
| TM-104 | macOS | 1.26.x | Medium | full-buffer | skip | — | Pending |
| TM-105 | macOS | 1.26.x | Medium | full-buffer | fallback/fail-open | — | Pending |
| TM-106 | macOS | 1.26.x | Medium | streaming | convert | — | Pending |
| TM-107 | macOS | 1.26.x | Medium | streaming | skip | — | Pending |
| TM-108 | macOS | 1.26.x | Medium | streaming | fallback/fail-open | — | Pending |
| TM-109 | macOS | 1.26.x | Large | full-buffer | convert | — | Pending |
| TM-110 | macOS | 1.26.x | Large | full-buffer | skip | — | Pending |
| TM-111 | macOS | 1.26.x | Large | full-buffer | fallback/fail-open | — | Pending |
| TM-112 | macOS | 1.26.x | Large | streaming | convert | — | Pending |
| TM-113 | macOS | 1.26.x | Large | streaming | skip | — | Pending |
| TM-114 | macOS | 1.26.x | Large | streaming | fallback/fail-open | — | Pending |
| TM-115 | macOS | 1.26.x | Extra-Large | full-buffer | convert | — | Pending |
| TM-116 | macOS | 1.26.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-117 | macOS | 1.26.x | Extra-Large | full-buffer | fallback/fail-open | — | Pending |
| TM-118 | macOS | 1.26.x | Extra-Large | streaming | convert | — | Pending |
| TM-119 | macOS | 1.26.x | Extra-Large | streaming | skip | — | Pending |
| TM-120 | macOS | 1.26.x | Extra-Large | streaming | fallback/fail-open | — | Pending |
| TM-121 | macOS | 1.27.x | Small | full-buffer | convert | — | Pending |
| TM-122 | macOS | 1.27.x | Small | full-buffer | skip | — | Pending |
| TM-123 | macOS | 1.27.x | Small | full-buffer | fallback/fail-open | — | Pending |
| TM-124 | macOS | 1.27.x | Small | streaming | convert | — | Pending |
| TM-125 | macOS | 1.27.x | Small | streaming | skip | — | Pending |
| TM-126 | macOS | 1.27.x | Small | streaming | fallback/fail-open | — | Pending |
| TM-127 | macOS | 1.27.x | Medium | full-buffer | convert | — | Pending |
| TM-128 | macOS | 1.27.x | Medium | full-buffer | skip | — | Pending |
| TM-129 | macOS | 1.27.x | Medium | full-buffer | fallback/fail-open | — | Pending |
| TM-130 | macOS | 1.27.x | Medium | streaming | convert | — | Pending |
| TM-131 | macOS | 1.27.x | Medium | streaming | skip | — | Pending |
| TM-132 | macOS | 1.27.x | Medium | streaming | fallback/fail-open | — | Pending |
| TM-133 | macOS | 1.27.x | Large | full-buffer | convert | — | Pending |
| TM-134 | macOS | 1.27.x | Large | full-buffer | skip | — | Pending |
| TM-135 | macOS | 1.27.x | Large | full-buffer | fallback/fail-open | — | Pending |
| TM-136 | macOS | 1.27.x | Large | streaming | convert | — | Pending |
| TM-137 | macOS | 1.27.x | Large | streaming | skip | — | Pending |
| TM-138 | macOS | 1.27.x | Large | streaming | fallback/fail-open | — | Pending |
| TM-139 | macOS | 1.27.x | Extra-Large | full-buffer | convert | — | Pending |
| TM-140 | macOS | 1.27.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-141 | macOS | 1.27.x | Extra-Large | full-buffer | fallback/fail-open | — | Pending |
| TM-142 | macOS | 1.27.x | Extra-Large | streaming | convert | — | Pending |
| TM-143 | macOS | 1.27.x | Extra-Large | streaming | skip | — | Pending |
| TM-144 | macOS | 1.27.x | Extra-Large | streaming | fallback/fail-open | — | Pending |

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-15 | Hermes | Define the required coverage set as the complete 144-tuple Cartesian product |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
