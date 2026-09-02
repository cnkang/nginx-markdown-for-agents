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
| Conversion Path | convert (successful conversion), skip (ineligible skip), fallback/fail-open (pre-commit fallback), fail-closed (controlled reject before headers), post-commit failure (stream terminated after headers) |

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
required cross-product coverage or the 240 required tuples.

## Gap Record Format

If infrastructure or resource constraints block a cell, the sub-spec must record the gap and rationale:

| Combination ID | Missing Tuple | Rationale | Risk Assessment |
|---------------|---------------|-----------|-----------------|
| — | — | — | — |

## Aggregate Coverage Status

The required coverage set is the complete Cartesian product of the listed
dimensions: 2 platforms x 3 NGINX versions x 4 response size tiers x 2
conversion engines x 5 conversion paths = 240 tuples. Before release,
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
| TM-004 | Ubuntu | 1.24.x | Small | full-buffer | fail-closed | — | Pending |
| TM-005 | Ubuntu | 1.24.x | Small | full-buffer | post-commit failure | — | Pending |
| TM-006 | Ubuntu | 1.24.x | Small | streaming | convert | — | Pending |
| TM-007 | Ubuntu | 1.24.x | Small | streaming | skip | — | Pending |
| TM-008 | Ubuntu | 1.24.x | Small | streaming | fallback/fail-open | — | Pending |
| TM-009 | Ubuntu | 1.24.x | Small | streaming | fail-closed | — | Pending |
| TM-010 | Ubuntu | 1.24.x | Small | streaming | post-commit failure | — | Pending |
| TM-011 | Ubuntu | 1.24.x | Medium | full-buffer | convert | — | Pending |
| TM-012 | Ubuntu | 1.24.x | Medium | full-buffer | skip | — | Pending |
| TM-013 | Ubuntu | 1.24.x | Medium | full-buffer | fallback/fail-open | — | Pending |
| TM-014 | Ubuntu | 1.24.x | Medium | full-buffer | fail-closed | — | Pending |
| TM-015 | Ubuntu | 1.24.x | Medium | full-buffer | post-commit failure | — | Pending |
| TM-016 | Ubuntu | 1.24.x | Medium | streaming | convert | — | Pending |
| TM-017 | Ubuntu | 1.24.x | Medium | streaming | skip | — | Pending |
| TM-018 | Ubuntu | 1.24.x | Medium | streaming | fallback/fail-open | — | Pending |
| TM-019 | Ubuntu | 1.24.x | Medium | streaming | fail-closed | — | Pending |
| TM-020 | Ubuntu | 1.24.x | Medium | streaming | post-commit failure | — | Pending |
| TM-021 | Ubuntu | 1.24.x | Large | full-buffer | convert | — | Pending |
| TM-022 | Ubuntu | 1.24.x | Large | full-buffer | skip | — | Pending |
| TM-023 | Ubuntu | 1.24.x | Large | full-buffer | fallback/fail-open | — | Pending |
| TM-024 | Ubuntu | 1.24.x | Large | full-buffer | fail-closed | — | Pending |
| TM-025 | Ubuntu | 1.24.x | Large | full-buffer | post-commit failure | — | Pending |
| TM-026 | Ubuntu | 1.24.x | Large | streaming | convert | — | Pending |
| TM-027 | Ubuntu | 1.24.x | Large | streaming | skip | — | Pending |
| TM-028 | Ubuntu | 1.24.x | Large | streaming | fallback/fail-open | — | Pending |
| TM-029 | Ubuntu | 1.24.x | Large | streaming | fail-closed | — | Pending |
| TM-030 | Ubuntu | 1.24.x | Large | streaming | post-commit failure | — | Pending |
| TM-031 | Ubuntu | 1.24.x | Extra-Large | full-buffer | convert | — | Pending |
| TM-032 | Ubuntu | 1.24.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-033 | Ubuntu | 1.24.x | Extra-Large | full-buffer | fallback/fail-open | — | Pending |
| TM-034 | Ubuntu | 1.24.x | Extra-Large | full-buffer | fail-closed | — | Pending |
| TM-035 | Ubuntu | 1.24.x | Extra-Large | full-buffer | post-commit failure | — | Pending |
| TM-036 | Ubuntu | 1.24.x | Extra-Large | streaming | convert | — | Pending |
| TM-037 | Ubuntu | 1.24.x | Extra-Large | streaming | skip | — | Pending |
| TM-038 | Ubuntu | 1.24.x | Extra-Large | streaming | fallback/fail-open | — | Pending |
| TM-039 | Ubuntu | 1.24.x | Extra-Large | streaming | fail-closed | — | Pending |
| TM-040 | Ubuntu | 1.24.x | Extra-Large | streaming | post-commit failure | — | Pending |
| TM-041 | Ubuntu | 1.26.x | Small | full-buffer | convert | — | Pending |
| TM-042 | Ubuntu | 1.26.x | Small | full-buffer | skip | — | Pending |
| TM-043 | Ubuntu | 1.26.x | Small | full-buffer | fallback/fail-open | — | Pending |
| TM-044 | Ubuntu | 1.26.x | Small | full-buffer | fail-closed | — | Pending |
| TM-045 | Ubuntu | 1.26.x | Small | full-buffer | post-commit failure | — | Pending |
| TM-046 | Ubuntu | 1.26.x | Small | streaming | convert | — | Pending |
| TM-047 | Ubuntu | 1.26.x | Small | streaming | skip | — | Pending |
| TM-048 | Ubuntu | 1.26.x | Small | streaming | fallback/fail-open | — | Pending |
| TM-049 | Ubuntu | 1.26.x | Small | streaming | fail-closed | — | Pending |
| TM-050 | Ubuntu | 1.26.x | Small | streaming | post-commit failure | — | Pending |
| TM-051 | Ubuntu | 1.26.x | Medium | full-buffer | convert | — | Pending |
| TM-052 | Ubuntu | 1.26.x | Medium | full-buffer | skip | — | Pending |
| TM-053 | Ubuntu | 1.26.x | Medium | full-buffer | fallback/fail-open | — | Pending |
| TM-054 | Ubuntu | 1.26.x | Medium | full-buffer | fail-closed | — | Pending |
| TM-055 | Ubuntu | 1.26.x | Medium | full-buffer | post-commit failure | — | Pending |
| TM-056 | Ubuntu | 1.26.x | Medium | streaming | convert | — | Pending |
| TM-057 | Ubuntu | 1.26.x | Medium | streaming | skip | — | Pending |
| TM-058 | Ubuntu | 1.26.x | Medium | streaming | fallback/fail-open | — | Pending |
| TM-059 | Ubuntu | 1.26.x | Medium | streaming | fail-closed | — | Pending |
| TM-060 | Ubuntu | 1.26.x | Medium | streaming | post-commit failure | — | Pending |
| TM-061 | Ubuntu | 1.26.x | Large | full-buffer | convert | — | Pending |
| TM-062 | Ubuntu | 1.26.x | Large | full-buffer | skip | — | Pending |
| TM-063 | Ubuntu | 1.26.x | Large | full-buffer | fallback/fail-open | — | Pending |
| TM-064 | Ubuntu | 1.26.x | Large | full-buffer | fail-closed | — | Pending |
| TM-065 | Ubuntu | 1.26.x | Large | full-buffer | post-commit failure | — | Pending |
| TM-066 | Ubuntu | 1.26.x | Large | streaming | convert | — | Pending |
| TM-067 | Ubuntu | 1.26.x | Large | streaming | skip | — | Pending |
| TM-068 | Ubuntu | 1.26.x | Large | streaming | fallback/fail-open | — | Pending |
| TM-069 | Ubuntu | 1.26.x | Large | streaming | fail-closed | — | Pending |
| TM-070 | Ubuntu | 1.26.x | Large | streaming | post-commit failure | — | Pending |
| TM-071 | Ubuntu | 1.26.x | Extra-Large | full-buffer | convert | — | Pending |
| TM-072 | Ubuntu | 1.26.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-073 | Ubuntu | 1.26.x | Extra-Large | full-buffer | fallback/fail-open | — | Pending |
| TM-074 | Ubuntu | 1.26.x | Extra-Large | full-buffer | fail-closed | — | Pending |
| TM-075 | Ubuntu | 1.26.x | Extra-Large | full-buffer | post-commit failure | — | Pending |
| TM-076 | Ubuntu | 1.26.x | Extra-Large | streaming | convert | — | Pending |
| TM-077 | Ubuntu | 1.26.x | Extra-Large | streaming | skip | — | Pending |
| TM-078 | Ubuntu | 1.26.x | Extra-Large | streaming | fallback/fail-open | — | Pending |
| TM-079 | Ubuntu | 1.26.x | Extra-Large | streaming | fail-closed | — | Pending |
| TM-080 | Ubuntu | 1.26.x | Extra-Large | streaming | post-commit failure | — | Pending |
| TM-081 | Ubuntu | 1.27.x | Small | full-buffer | convert | — | Pending |
| TM-082 | Ubuntu | 1.27.x | Small | full-buffer | skip | — | Pending |
| TM-083 | Ubuntu | 1.27.x | Small | full-buffer | fallback/fail-open | — | Pending |
| TM-084 | Ubuntu | 1.27.x | Small | full-buffer | fail-closed | — | Pending |
| TM-085 | Ubuntu | 1.27.x | Small | full-buffer | post-commit failure | — | Pending |
| TM-086 | Ubuntu | 1.27.x | Small | streaming | convert | — | Pending |
| TM-087 | Ubuntu | 1.27.x | Small | streaming | skip | — | Pending |
| TM-088 | Ubuntu | 1.27.x | Small | streaming | fallback/fail-open | — | Pending |
| TM-089 | Ubuntu | 1.27.x | Small | streaming | fail-closed | — | Pending |
| TM-090 | Ubuntu | 1.27.x | Small | streaming | post-commit failure | — | Pending |
| TM-091 | Ubuntu | 1.27.x | Medium | full-buffer | convert | — | Pending |
| TM-092 | Ubuntu | 1.27.x | Medium | full-buffer | skip | — | Pending |
| TM-093 | Ubuntu | 1.27.x | Medium | full-buffer | fallback/fail-open | — | Pending |
| TM-094 | Ubuntu | 1.27.x | Medium | full-buffer | fail-closed | — | Pending |
| TM-095 | Ubuntu | 1.27.x | Medium | full-buffer | post-commit failure | — | Pending |
| TM-096 | Ubuntu | 1.27.x | Medium | streaming | convert | — | Pending |
| TM-097 | Ubuntu | 1.27.x | Medium | streaming | skip | — | Pending |
| TM-098 | Ubuntu | 1.27.x | Medium | streaming | fallback/fail-open | — | Pending |
| TM-099 | Ubuntu | 1.27.x | Medium | streaming | fail-closed | — | Pending |
| TM-100 | Ubuntu | 1.27.x | Medium | streaming | post-commit failure | — | Pending |
| TM-101 | Ubuntu | 1.27.x | Large | full-buffer | convert | — | Pending |
| TM-102 | Ubuntu | 1.27.x | Large | full-buffer | skip | — | Pending |
| TM-103 | Ubuntu | 1.27.x | Large | full-buffer | fallback/fail-open | — | Pending |
| TM-104 | Ubuntu | 1.27.x | Large | full-buffer | fail-closed | — | Pending |
| TM-105 | Ubuntu | 1.27.x | Large | full-buffer | post-commit failure | — | Pending |
| TM-106 | Ubuntu | 1.27.x | Large | streaming | convert | — | Pending |
| TM-107 | Ubuntu | 1.27.x | Large | streaming | skip | — | Pending |
| TM-108 | Ubuntu | 1.27.x | Large | streaming | fallback/fail-open | — | Pending |
| TM-109 | Ubuntu | 1.27.x | Large | streaming | fail-closed | — | Pending |
| TM-110 | Ubuntu | 1.27.x | Large | streaming | post-commit failure | — | Pending |
| TM-111 | Ubuntu | 1.27.x | Extra-Large | full-buffer | convert | — | Pending |
| TM-112 | Ubuntu | 1.27.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-113 | Ubuntu | 1.27.x | Extra-Large | full-buffer | fallback/fail-open | — | Pending |
| TM-114 | Ubuntu | 1.27.x | Extra-Large | full-buffer | fail-closed | — | Pending |
| TM-115 | Ubuntu | 1.27.x | Extra-Large | full-buffer | post-commit failure | — | Pending |
| TM-116 | Ubuntu | 1.27.x | Extra-Large | streaming | convert | — | Pending |
| TM-117 | Ubuntu | 1.27.x | Extra-Large | streaming | skip | — | Pending |
| TM-118 | Ubuntu | 1.27.x | Extra-Large | streaming | fallback/fail-open | — | Pending |
| TM-119 | Ubuntu | 1.27.x | Extra-Large | streaming | fail-closed | — | Pending |
| TM-120 | Ubuntu | 1.27.x | Extra-Large | streaming | post-commit failure | — | Pending |
| TM-121 | macOS | 1.24.x | Small | full-buffer | convert | — | Pending |
| TM-122 | macOS | 1.24.x | Small | full-buffer | skip | — | Pending |
| TM-123 | macOS | 1.24.x | Small | full-buffer | fallback/fail-open | — | Pending |
| TM-124 | macOS | 1.24.x | Small | full-buffer | fail-closed | — | Pending |
| TM-125 | macOS | 1.24.x | Small | full-buffer | post-commit failure | — | Pending |
| TM-126 | macOS | 1.24.x | Small | streaming | convert | — | Pending |
| TM-127 | macOS | 1.24.x | Small | streaming | skip | — | Pending |
| TM-128 | macOS | 1.24.x | Small | streaming | fallback/fail-open | — | Pending |
| TM-129 | macOS | 1.24.x | Small | streaming | fail-closed | — | Pending |
| TM-130 | macOS | 1.24.x | Small | streaming | post-commit failure | — | Pending |
| TM-131 | macOS | 1.24.x | Medium | full-buffer | convert | — | Pending |
| TM-132 | macOS | 1.24.x | Medium | full-buffer | skip | — | Pending |
| TM-133 | macOS | 1.24.x | Medium | full-buffer | fallback/fail-open | — | Pending |
| TM-134 | macOS | 1.24.x | Medium | full-buffer | fail-closed | — | Pending |
| TM-135 | macOS | 1.24.x | Medium | full-buffer | post-commit failure | — | Pending |
| TM-136 | macOS | 1.24.x | Medium | streaming | convert | — | Pending |
| TM-137 | macOS | 1.24.x | Medium | streaming | skip | — | Pending |
| TM-138 | macOS | 1.24.x | Medium | streaming | fallback/fail-open | — | Pending |
| TM-139 | macOS | 1.24.x | Medium | streaming | fail-closed | — | Pending |
| TM-140 | macOS | 1.24.x | Medium | streaming | post-commit failure | — | Pending |
| TM-141 | macOS | 1.24.x | Large | full-buffer | convert | — | Pending |
| TM-142 | macOS | 1.24.x | Large | full-buffer | skip | — | Pending |
| TM-143 | macOS | 1.24.x | Large | full-buffer | fallback/fail-open | — | Pending |
| TM-144 | macOS | 1.24.x | Large | full-buffer | fail-closed | — | Pending |
| TM-145 | macOS | 1.24.x | Large | full-buffer | post-commit failure | — | Pending |
| TM-146 | macOS | 1.24.x | Large | streaming | convert | — | Pending |
| TM-147 | macOS | 1.24.x | Large | streaming | skip | — | Pending |
| TM-148 | macOS | 1.24.x | Large | streaming | fallback/fail-open | — | Pending |
| TM-149 | macOS | 1.24.x | Large | streaming | fail-closed | — | Pending |
| TM-150 | macOS | 1.24.x | Large | streaming | post-commit failure | — | Pending |
| TM-151 | macOS | 1.24.x | Extra-Large | full-buffer | convert | — | Pending |
| TM-152 | macOS | 1.24.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-153 | macOS | 1.24.x | Extra-Large | full-buffer | fallback/fail-open | — | Pending |
| TM-154 | macOS | 1.24.x | Extra-Large | full-buffer | fail-closed | — | Pending |
| TM-155 | macOS | 1.24.x | Extra-Large | full-buffer | post-commit failure | — | Pending |
| TM-156 | macOS | 1.24.x | Extra-Large | streaming | convert | — | Pending |
| TM-157 | macOS | 1.24.x | Extra-Large | streaming | skip | — | Pending |
| TM-158 | macOS | 1.24.x | Extra-Large | streaming | fallback/fail-open | — | Pending |
| TM-159 | macOS | 1.24.x | Extra-Large | streaming | fail-closed | — | Pending |
| TM-160 | macOS | 1.24.x | Extra-Large | streaming | post-commit failure | — | Pending |
| TM-161 | macOS | 1.26.x | Small | full-buffer | convert | — | Pending |
| TM-162 | macOS | 1.26.x | Small | full-buffer | skip | — | Pending |
| TM-163 | macOS | 1.26.x | Small | full-buffer | fallback/fail-open | — | Pending |
| TM-164 | macOS | 1.26.x | Small | full-buffer | fail-closed | — | Pending |
| TM-165 | macOS | 1.26.x | Small | full-buffer | post-commit failure | — | Pending |
| TM-166 | macOS | 1.26.x | Small | streaming | convert | — | Pending |
| TM-167 | macOS | 1.26.x | Small | streaming | skip | — | Pending |
| TM-168 | macOS | 1.26.x | Small | streaming | fallback/fail-open | — | Pending |
| TM-169 | macOS | 1.26.x | Small | streaming | fail-closed | — | Pending |
| TM-170 | macOS | 1.26.x | Small | streaming | post-commit failure | — | Pending |
| TM-171 | macOS | 1.26.x | Medium | full-buffer | convert | — | Pending |
| TM-172 | macOS | 1.26.x | Medium | full-buffer | skip | — | Pending |
| TM-173 | macOS | 1.26.x | Medium | full-buffer | fallback/fail-open | — | Pending |
| TM-174 | macOS | 1.26.x | Medium | full-buffer | fail-closed | — | Pending |
| TM-175 | macOS | 1.26.x | Medium | full-buffer | post-commit failure | — | Pending |
| TM-176 | macOS | 1.26.x | Medium | streaming | convert | — | Pending |
| TM-177 | macOS | 1.26.x | Medium | streaming | skip | — | Pending |
| TM-178 | macOS | 1.26.x | Medium | streaming | fallback/fail-open | — | Pending |
| TM-179 | macOS | 1.26.x | Medium | streaming | fail-closed | — | Pending |
| TM-180 | macOS | 1.26.x | Medium | streaming | post-commit failure | — | Pending |
| TM-181 | macOS | 1.26.x | Large | full-buffer | convert | — | Pending |
| TM-182 | macOS | 1.26.x | Large | full-buffer | skip | — | Pending |
| TM-183 | macOS | 1.26.x | Large | full-buffer | fallback/fail-open | — | Pending |
| TM-184 | macOS | 1.26.x | Large | full-buffer | fail-closed | — | Pending |
| TM-185 | macOS | 1.26.x | Large | full-buffer | post-commit failure | — | Pending |
| TM-186 | macOS | 1.26.x | Large | streaming | convert | — | Pending |
| TM-187 | macOS | 1.26.x | Large | streaming | skip | — | Pending |
| TM-188 | macOS | 1.26.x | Large | streaming | fallback/fail-open | — | Pending |
| TM-189 | macOS | 1.26.x | Large | streaming | fail-closed | — | Pending |
| TM-190 | macOS | 1.26.x | Large | streaming | post-commit failure | — | Pending |
| TM-191 | macOS | 1.26.x | Extra-Large | full-buffer | convert | — | Pending |
| TM-192 | macOS | 1.26.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-193 | macOS | 1.26.x | Extra-Large | full-buffer | fallback/fail-open | — | Pending |
| TM-194 | macOS | 1.26.x | Extra-Large | full-buffer | fail-closed | — | Pending |
| TM-195 | macOS | 1.26.x | Extra-Large | full-buffer | post-commit failure | — | Pending |
| TM-196 | macOS | 1.26.x | Extra-Large | streaming | convert | — | Pending |
| TM-197 | macOS | 1.26.x | Extra-Large | streaming | skip | — | Pending |
| TM-198 | macOS | 1.26.x | Extra-Large | streaming | fallback/fail-open | — | Pending |
| TM-199 | macOS | 1.26.x | Extra-Large | streaming | fail-closed | — | Pending |
| TM-200 | macOS | 1.26.x | Extra-Large | streaming | post-commit failure | — | Pending |
| TM-201 | macOS | 1.27.x | Small | full-buffer | convert | — | Pending |
| TM-202 | macOS | 1.27.x | Small | full-buffer | skip | — | Pending |
| TM-203 | macOS | 1.27.x | Small | full-buffer | fallback/fail-open | — | Pending |
| TM-204 | macOS | 1.27.x | Small | full-buffer | fail-closed | — | Pending |
| TM-205 | macOS | 1.27.x | Small | full-buffer | post-commit failure | — | Pending |
| TM-206 | macOS | 1.27.x | Small | streaming | convert | — | Pending |
| TM-207 | macOS | 1.27.x | Small | streaming | skip | — | Pending |
| TM-208 | macOS | 1.27.x | Small | streaming | fallback/fail-open | — | Pending |
| TM-209 | macOS | 1.27.x | Small | streaming | fail-closed | — | Pending |
| TM-210 | macOS | 1.27.x | Small | streaming | post-commit failure | — | Pending |
| TM-211 | macOS | 1.27.x | Medium | full-buffer | convert | — | Pending |
| TM-212 | macOS | 1.27.x | Medium | full-buffer | skip | — | Pending |
| TM-213 | macOS | 1.27.x | Medium | full-buffer | fallback/fail-open | — | Pending |
| TM-214 | macOS | 1.27.x | Medium | full-buffer | fail-closed | — | Pending |
| TM-215 | macOS | 1.27.x | Medium | full-buffer | post-commit failure | — | Pending |
| TM-216 | macOS | 1.27.x | Medium | streaming | convert | — | Pending |
| TM-217 | macOS | 1.27.x | Medium | streaming | skip | — | Pending |
| TM-218 | macOS | 1.27.x | Medium | streaming | fallback/fail-open | — | Pending |
| TM-219 | macOS | 1.27.x | Medium | streaming | fail-closed | — | Pending |
| TM-220 | macOS | 1.27.x | Medium | streaming | post-commit failure | — | Pending |
| TM-221 | macOS | 1.27.x | Large | full-buffer | convert | — | Pending |
| TM-222 | macOS | 1.27.x | Large | full-buffer | skip | — | Pending |
| TM-223 | macOS | 1.27.x | Large | full-buffer | fallback/fail-open | — | Pending |
| TM-224 | macOS | 1.27.x | Large | full-buffer | fail-closed | — | Pending |
| TM-225 | macOS | 1.27.x | Large | full-buffer | post-commit failure | — | Pending |
| TM-226 | macOS | 1.27.x | Large | streaming | convert | — | Pending |
| TM-227 | macOS | 1.27.x | Large | streaming | skip | — | Pending |
| TM-228 | macOS | 1.27.x | Large | streaming | fallback/fail-open | — | Pending |
| TM-229 | macOS | 1.27.x | Large | streaming | fail-closed | — | Pending |
| TM-230 | macOS | 1.27.x | Large | streaming | post-commit failure | — | Pending |
| TM-231 | macOS | 1.27.x | Extra-Large | full-buffer | convert | — | Pending |
| TM-232 | macOS | 1.27.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-233 | macOS | 1.27.x | Extra-Large | full-buffer | fallback/fail-open | — | Pending |
| TM-234 | macOS | 1.27.x | Extra-Large | full-buffer | fail-closed | — | Pending |
| TM-235 | macOS | 1.27.x | Extra-Large | full-buffer | post-commit failure | — | Pending |
| TM-236 | macOS | 1.27.x | Extra-Large | streaming | convert | — | Pending |
| TM-237 | macOS | 1.27.x | Extra-Large | streaming | skip | — | Pending |
| TM-238 | macOS | 1.27.x | Extra-Large | streaming | fallback/fail-open | — | Pending |
| TM-239 | macOS | 1.27.x | Extra-Large | streaming | fail-closed | — | Pending |
| TM-240 | macOS | 1.27.x | Extra-Large | streaming | post-commit failure | — | Pending |

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-15 | Hermes | Define the required coverage set as the complete 240-tuple Cartesian product (five conversion paths) |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
