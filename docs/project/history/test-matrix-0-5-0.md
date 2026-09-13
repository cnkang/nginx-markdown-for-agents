# 0.5.0 Cross-Spec Test Matrix

## Overview

All sub-specs map their test plans to this matrix. Combined coverage must address all cells.

## Dimension Definitions

| Dimension | Values |
|-----------|--------|
| Platform | Ubuntu (primary), macOS (secondary) |
| NGINX Version | 1.24.x (LTS), 1.26.x (stable), 1.27.x (mainline) |
| Response Size Tier | Small ([0, 10KiB)), Medium ([10KiB, 1MiB)), Large ([1MiB, 64MiB)), Extra-Large ([64MiB, ∞)) — half-open disjoint boundaries: exactly 10KiB maps to Medium, exactly 1MiB maps to Large, exactly 64MiB maps to Extra-Large, which is the conversion ceiling the implementation enforces |
| Conversion Engine | full-buffer, streaming — the engine column records the **initial** engine selected for the request; a streaming request that falls back to full-buffer is classified under the fallback path, not double-counted as a full-buffer conversion |
| Conversion Path | convert (successful conversion), skip (ineligible skip), fallback (pre-commit conversion fallback to full-buffer), fail-open (pre-commit fail-open serving the original HTML), fail-closed (controlled reject before headers), post-commit failure (stream terminated after headers) |

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
| EXAMPLE-002 (illustrative sample; not aggregate coverage) | macOS | 1.27.x | Large | streaming | fallback | manual / benchmark | [sub-spec name] |
```

These two rows illustrate the format only. They are not counted toward the
required cross-product coverage or the 282 required tuples.

## Gap Record Format

If infrastructure or resource constraints block a cell, the sub-spec must record the gap and rationale:

| Combination ID | Missing Tuple | Rationale | Risk Assessment |
|---------------|---------------|-----------|-----------------|
| — | — | — | — |

## Aggregate Coverage Status

The required coverage set is the complete Cartesian product of the listed
dimensions: 2 platforms x 3 NGINX versions x 4 response size tiers x 2
conversion engines x 6 conversion paths = 288 tuples.  Six of them — the
Extra-Large full-buffer convert tuples (TM-037, TM-085, TM-133, TM-181,
TM-229, TM-277) — hit the size-limit rejection path by design: the module
rejects inputs at or above the conversion ceiling, so this document excludes
them from the required set.  A further 24 full-buffer/fallback tuples are
unreachable: fallback is a streaming-to-full-buffer transition, so a request
whose initial engine is full-buffer can never take the fallback path.  This
document excludes those 24 tuples for the same reason it excludes the
Extra-Large convert rows, leaving 258 required tuples.  The fallback and fail-open
paths are separate coverage obligations: each carries its own reason code
and output, so no tuple may cover both at once. A streaming-to-full-buffer
fallback does not count an already counted full-buffer conversion again.
Before release,
aggregate all sub-spec coverage mappings. Ensure every required tuple has at
least one covering sub-spec. Covering each value independently is not
sufficient. The following table enumerates the complete required set in
deterministic lexicographic order. A tuple remains Pending until the release
process records a covering sub-spec:

| Combination ID | Platform | NGINX Version | Response Size Tier | Conversion Engine | Conversion Path | Covering Sub-Spec | Status |
|---------------|----------|---------------|--------------------|-------------------|-----------------|------------------|--------|
| TM-001 | Ubuntu | 1.24.x | Small | full-buffer | convert | — | Pending |
| TM-002 | Ubuntu | 1.24.x | Small | full-buffer | skip | — | Pending |
| TM-003 | Ubuntu | 1.24.x | Small | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-004 | Ubuntu | 1.24.x | Small | full-buffer | fail-open | — | Pending |
| TM-005 | Ubuntu | 1.24.x | Small | full-buffer | fail-closed | — | Pending |
| TM-006 | Ubuntu | 1.24.x | Small | full-buffer | post-commit failure | — | Pending |
| TM-007 | Ubuntu | 1.24.x | Small | streaming | convert | — | Pending |
| TM-008 | Ubuntu | 1.24.x | Small | streaming | skip | — | Pending |
| TM-009 | Ubuntu | 1.24.x | Small | streaming | fallback | — | Pending |
| TM-010 | Ubuntu | 1.24.x | Small | streaming | fail-open | — | Pending |
| TM-011 | Ubuntu | 1.24.x | Small | streaming | fail-closed | — | Pending |
| TM-012 | Ubuntu | 1.24.x | Small | streaming | post-commit failure | — | Pending |
| TM-013 | Ubuntu | 1.24.x | Medium | full-buffer | convert | — | Pending |
| TM-014 | Ubuntu | 1.24.x | Medium | full-buffer | skip | — | Pending |
| TM-015 | Ubuntu | 1.24.x | Medium | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-016 | Ubuntu | 1.24.x | Medium | full-buffer | fail-open | — | Pending |
| TM-017 | Ubuntu | 1.24.x | Medium | full-buffer | fail-closed | — | Pending |
| TM-018 | Ubuntu | 1.24.x | Medium | full-buffer | post-commit failure | — | Pending |
| TM-019 | Ubuntu | 1.24.x | Medium | streaming | convert | — | Pending |
| TM-020 | Ubuntu | 1.24.x | Medium | streaming | skip | — | Pending |
| TM-021 | Ubuntu | 1.24.x | Medium | streaming | fallback | — | Pending |
| TM-022 | Ubuntu | 1.24.x | Medium | streaming | fail-open | — | Pending |
| TM-023 | Ubuntu | 1.24.x | Medium | streaming | fail-closed | — | Pending |
| TM-024 | Ubuntu | 1.24.x | Medium | streaming | post-commit failure | — | Pending |
| TM-025 | Ubuntu | 1.24.x | Large | full-buffer | convert | — | Pending |
| TM-026 | Ubuntu | 1.24.x | Large | full-buffer | skip | — | Pending |
| TM-027 | Ubuntu | 1.24.x | Large | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-028 | Ubuntu | 1.24.x | Large | full-buffer | fail-open | — | Pending |
| TM-029 | Ubuntu | 1.24.x | Large | full-buffer | fail-closed | — | Pending |
| TM-030 | Ubuntu | 1.24.x | Large | full-buffer | post-commit failure | — | Pending |
| TM-031 | Ubuntu | 1.24.x | Large | streaming | convert | — | Pending |
| TM-032 | Ubuntu | 1.24.x | Large | streaming | skip | — | Pending |
| TM-033 | Ubuntu | 1.24.x | Large | streaming | fallback | — | Pending |
| TM-034 | Ubuntu | 1.24.x | Large | streaming | fail-open | — | Pending |
| TM-035 | Ubuntu | 1.24.x | Large | streaming | fail-closed | — | Pending |
| TM-036 | Ubuntu | 1.24.x | Large | streaming | post-commit failure | — | Pending |
| TM-037 | Ubuntu | 1.24.x | Extra-Large | full-buffer | convert | size-limit rejection by design (excluded from required set; see coverage-set note above) | Expected-Rejection |
| TM-038 | Ubuntu | 1.24.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-039 | Ubuntu | 1.24.x | Extra-Large | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-040 | Ubuntu | 1.24.x | Extra-Large | full-buffer | fail-open | — | Pending |
| TM-041 | Ubuntu | 1.24.x | Extra-Large | full-buffer | fail-closed | — | Pending |
| TM-042 | Ubuntu | 1.24.x | Extra-Large | full-buffer | post-commit failure | — | Pending |
| TM-043 | Ubuntu | 1.24.x | Extra-Large | streaming | convert | — | Pending |
| TM-044 | Ubuntu | 1.24.x | Extra-Large | streaming | skip | — | Pending |
| TM-045 | Ubuntu | 1.24.x | Extra-Large | streaming | fallback | — | Pending |
| TM-046 | Ubuntu | 1.24.x | Extra-Large | streaming | fail-open | — | Pending |
| TM-047 | Ubuntu | 1.24.x | Extra-Large | streaming | fail-closed | — | Pending |
| TM-048 | Ubuntu | 1.24.x | Extra-Large | streaming | post-commit failure | — | Pending |
| TM-049 | Ubuntu | 1.26.x | Small | full-buffer | convert | — | Pending |
| TM-050 | Ubuntu | 1.26.x | Small | full-buffer | skip | — | Pending |
| TM-051 | Ubuntu | 1.26.x | Small | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-052 | Ubuntu | 1.26.x | Small | full-buffer | fail-open | — | Pending |
| TM-053 | Ubuntu | 1.26.x | Small | full-buffer | fail-closed | — | Pending |
| TM-054 | Ubuntu | 1.26.x | Small | full-buffer | post-commit failure | — | Pending |
| TM-055 | Ubuntu | 1.26.x | Small | streaming | convert | — | Pending |
| TM-056 | Ubuntu | 1.26.x | Small | streaming | skip | — | Pending |
| TM-057 | Ubuntu | 1.26.x | Small | streaming | fallback | — | Pending |
| TM-058 | Ubuntu | 1.26.x | Small | streaming | fail-open | — | Pending |
| TM-059 | Ubuntu | 1.26.x | Small | streaming | fail-closed | — | Pending |
| TM-060 | Ubuntu | 1.26.x | Small | streaming | post-commit failure | — | Pending |
| TM-061 | Ubuntu | 1.26.x | Medium | full-buffer | convert | — | Pending |
| TM-062 | Ubuntu | 1.26.x | Medium | full-buffer | skip | — | Pending |
| TM-063 | Ubuntu | 1.26.x | Medium | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-064 | Ubuntu | 1.26.x | Medium | full-buffer | fail-open | — | Pending |
| TM-065 | Ubuntu | 1.26.x | Medium | full-buffer | fail-closed | — | Pending |
| TM-066 | Ubuntu | 1.26.x | Medium | full-buffer | post-commit failure | — | Pending |
| TM-067 | Ubuntu | 1.26.x | Medium | streaming | convert | — | Pending |
| TM-068 | Ubuntu | 1.26.x | Medium | streaming | skip | — | Pending |
| TM-069 | Ubuntu | 1.26.x | Medium | streaming | fallback | — | Pending |
| TM-070 | Ubuntu | 1.26.x | Medium | streaming | fail-open | — | Pending |
| TM-071 | Ubuntu | 1.26.x | Medium | streaming | fail-closed | — | Pending |
| TM-072 | Ubuntu | 1.26.x | Medium | streaming | post-commit failure | — | Pending |
| TM-073 | Ubuntu | 1.26.x | Large | full-buffer | convert | — | Pending |
| TM-074 | Ubuntu | 1.26.x | Large | full-buffer | skip | — | Pending |
| TM-075 | Ubuntu | 1.26.x | Large | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-076 | Ubuntu | 1.26.x | Large | full-buffer | fail-open | — | Pending |
| TM-077 | Ubuntu | 1.26.x | Large | full-buffer | fail-closed | — | Pending |
| TM-078 | Ubuntu | 1.26.x | Large | full-buffer | post-commit failure | — | Pending |
| TM-079 | Ubuntu | 1.26.x | Large | streaming | convert | — | Pending |
| TM-080 | Ubuntu | 1.26.x | Large | streaming | skip | — | Pending |
| TM-081 | Ubuntu | 1.26.x | Large | streaming | fallback | — | Pending |
| TM-082 | Ubuntu | 1.26.x | Large | streaming | fail-open | — | Pending |
| TM-083 | Ubuntu | 1.26.x | Large | streaming | fail-closed | — | Pending |
| TM-084 | Ubuntu | 1.26.x | Large | streaming | post-commit failure | — | Pending |
| TM-085 | Ubuntu | 1.26.x | Extra-Large | full-buffer | convert | size-limit rejection by design (excluded from required set; see coverage-set note above) | Expected-Rejection |
| TM-086 | Ubuntu | 1.26.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-087 | Ubuntu | 1.26.x | Extra-Large | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-088 | Ubuntu | 1.26.x | Extra-Large | full-buffer | fail-open | — | Pending |
| TM-089 | Ubuntu | 1.26.x | Extra-Large | full-buffer | fail-closed | — | Pending |
| TM-090 | Ubuntu | 1.26.x | Extra-Large | full-buffer | post-commit failure | — | Pending |
| TM-091 | Ubuntu | 1.26.x | Extra-Large | streaming | convert | — | Pending |
| TM-092 | Ubuntu | 1.26.x | Extra-Large | streaming | skip | — | Pending |
| TM-093 | Ubuntu | 1.26.x | Extra-Large | streaming | fallback | — | Pending |
| TM-094 | Ubuntu | 1.26.x | Extra-Large | streaming | fail-open | — | Pending |
| TM-095 | Ubuntu | 1.26.x | Extra-Large | streaming | fail-closed | — | Pending |
| TM-096 | Ubuntu | 1.26.x | Extra-Large | streaming | post-commit failure | — | Pending |
| TM-097 | Ubuntu | 1.27.x | Small | full-buffer | convert | — | Pending |
| TM-098 | Ubuntu | 1.27.x | Small | full-buffer | skip | — | Pending |
| TM-099 | Ubuntu | 1.27.x | Small | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-100 | Ubuntu | 1.27.x | Small | full-buffer | fail-open | — | Pending |
| TM-101 | Ubuntu | 1.27.x | Small | full-buffer | fail-closed | — | Pending |
| TM-102 | Ubuntu | 1.27.x | Small | full-buffer | post-commit failure | — | Pending |
| TM-103 | Ubuntu | 1.27.x | Small | streaming | convert | — | Pending |
| TM-104 | Ubuntu | 1.27.x | Small | streaming | skip | — | Pending |
| TM-105 | Ubuntu | 1.27.x | Small | streaming | fallback | — | Pending |
| TM-106 | Ubuntu | 1.27.x | Small | streaming | fail-open | — | Pending |
| TM-107 | Ubuntu | 1.27.x | Small | streaming | fail-closed | — | Pending |
| TM-108 | Ubuntu | 1.27.x | Small | streaming | post-commit failure | — | Pending |
| TM-109 | Ubuntu | 1.27.x | Medium | full-buffer | convert | — | Pending |
| TM-110 | Ubuntu | 1.27.x | Medium | full-buffer | skip | — | Pending |
| TM-111 | Ubuntu | 1.27.x | Medium | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-112 | Ubuntu | 1.27.x | Medium | full-buffer | fail-open | — | Pending |
| TM-113 | Ubuntu | 1.27.x | Medium | full-buffer | fail-closed | — | Pending |
| TM-114 | Ubuntu | 1.27.x | Medium | full-buffer | post-commit failure | — | Pending |
| TM-115 | Ubuntu | 1.27.x | Medium | streaming | convert | — | Pending |
| TM-116 | Ubuntu | 1.27.x | Medium | streaming | skip | — | Pending |
| TM-117 | Ubuntu | 1.27.x | Medium | streaming | fallback | — | Pending |
| TM-118 | Ubuntu | 1.27.x | Medium | streaming | fail-open | — | Pending |
| TM-119 | Ubuntu | 1.27.x | Medium | streaming | fail-closed | — | Pending |
| TM-120 | Ubuntu | 1.27.x | Medium | streaming | post-commit failure | — | Pending |
| TM-121 | Ubuntu | 1.27.x | Large | full-buffer | convert | — | Pending |
| TM-122 | Ubuntu | 1.27.x | Large | full-buffer | skip | — | Pending |
| TM-123 | Ubuntu | 1.27.x | Large | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-124 | Ubuntu | 1.27.x | Large | full-buffer | fail-open | — | Pending |
| TM-125 | Ubuntu | 1.27.x | Large | full-buffer | fail-closed | — | Pending |
| TM-126 | Ubuntu | 1.27.x | Large | full-buffer | post-commit failure | — | Pending |
| TM-127 | Ubuntu | 1.27.x | Large | streaming | convert | — | Pending |
| TM-128 | Ubuntu | 1.27.x | Large | streaming | skip | — | Pending |
| TM-129 | Ubuntu | 1.27.x | Large | streaming | fallback | — | Pending |
| TM-130 | Ubuntu | 1.27.x | Large | streaming | fail-open | — | Pending |
| TM-131 | Ubuntu | 1.27.x | Large | streaming | fail-closed | — | Pending |
| TM-132 | Ubuntu | 1.27.x | Large | streaming | post-commit failure | — | Pending |
| TM-133 | Ubuntu | 1.27.x | Extra-Large | full-buffer | convert | size-limit rejection by design (excluded from required set; see coverage-set note above) | Expected-Rejection |
| TM-134 | Ubuntu | 1.27.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-135 | Ubuntu | 1.27.x | Extra-Large | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-136 | Ubuntu | 1.27.x | Extra-Large | full-buffer | fail-open | — | Pending |
| TM-137 | Ubuntu | 1.27.x | Extra-Large | full-buffer | fail-closed | — | Pending |
| TM-138 | Ubuntu | 1.27.x | Extra-Large | full-buffer | post-commit failure | — | Pending |
| TM-139 | Ubuntu | 1.27.x | Extra-Large | streaming | convert | — | Pending |
| TM-140 | Ubuntu | 1.27.x | Extra-Large | streaming | skip | — | Pending |
| TM-141 | Ubuntu | 1.27.x | Extra-Large | streaming | fallback | — | Pending |
| TM-142 | Ubuntu | 1.27.x | Extra-Large | streaming | fail-open | — | Pending |
| TM-143 | Ubuntu | 1.27.x | Extra-Large | streaming | fail-closed | — | Pending |
| TM-144 | Ubuntu | 1.27.x | Extra-Large | streaming | post-commit failure | — | Pending |
| TM-145 | macOS | 1.24.x | Small | full-buffer | convert | — | Pending |
| TM-146 | macOS | 1.24.x | Small | full-buffer | skip | — | Pending |
| TM-147 | macOS | 1.24.x | Small | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-148 | macOS | 1.24.x | Small | full-buffer | fail-open | — | Pending |
| TM-149 | macOS | 1.24.x | Small | full-buffer | fail-closed | — | Pending |
| TM-150 | macOS | 1.24.x | Small | full-buffer | post-commit failure | — | Pending |
| TM-151 | macOS | 1.24.x | Small | streaming | convert | — | Pending |
| TM-152 | macOS | 1.24.x | Small | streaming | skip | — | Pending |
| TM-153 | macOS | 1.24.x | Small | streaming | fallback | — | Pending |
| TM-154 | macOS | 1.24.x | Small | streaming | fail-open | — | Pending |
| TM-155 | macOS | 1.24.x | Small | streaming | fail-closed | — | Pending |
| TM-156 | macOS | 1.24.x | Small | streaming | post-commit failure | — | Pending |
| TM-157 | macOS | 1.24.x | Medium | full-buffer | convert | — | Pending |
| TM-158 | macOS | 1.24.x | Medium | full-buffer | skip | — | Pending |
| TM-159 | macOS | 1.24.x | Medium | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-160 | macOS | 1.24.x | Medium | full-buffer | fail-open | — | Pending |
| TM-161 | macOS | 1.24.x | Medium | full-buffer | fail-closed | — | Pending |
| TM-162 | macOS | 1.24.x | Medium | full-buffer | post-commit failure | — | Pending |
| TM-163 | macOS | 1.24.x | Medium | streaming | convert | — | Pending |
| TM-164 | macOS | 1.24.x | Medium | streaming | skip | — | Pending |
| TM-165 | macOS | 1.24.x | Medium | streaming | fallback | — | Pending |
| TM-166 | macOS | 1.24.x | Medium | streaming | fail-open | — | Pending |
| TM-167 | macOS | 1.24.x | Medium | streaming | fail-closed | — | Pending |
| TM-168 | macOS | 1.24.x | Medium | streaming | post-commit failure | — | Pending |
| TM-169 | macOS | 1.24.x | Large | full-buffer | convert | — | Pending |
| TM-170 | macOS | 1.24.x | Large | full-buffer | skip | — | Pending |
| TM-171 | macOS | 1.24.x | Large | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-172 | macOS | 1.24.x | Large | full-buffer | fail-open | — | Pending |
| TM-173 | macOS | 1.24.x | Large | full-buffer | fail-closed | — | Pending |
| TM-174 | macOS | 1.24.x | Large | full-buffer | post-commit failure | — | Pending |
| TM-175 | macOS | 1.24.x | Large | streaming | convert | — | Pending |
| TM-176 | macOS | 1.24.x | Large | streaming | skip | — | Pending |
| TM-177 | macOS | 1.24.x | Large | streaming | fallback | — | Pending |
| TM-178 | macOS | 1.24.x | Large | streaming | fail-open | — | Pending |
| TM-179 | macOS | 1.24.x | Large | streaming | fail-closed | — | Pending |
| TM-180 | macOS | 1.24.x | Large | streaming | post-commit failure | — | Pending |
| TM-181 | macOS | 1.24.x | Extra-Large | full-buffer | convert | size-limit rejection by design (excluded from required set; see coverage-set note above) | Expected-Rejection |
| TM-182 | macOS | 1.24.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-183 | macOS | 1.24.x | Extra-Large | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-184 | macOS | 1.24.x | Extra-Large | full-buffer | fail-open | — | Pending |
| TM-185 | macOS | 1.24.x | Extra-Large | full-buffer | fail-closed | — | Pending |
| TM-186 | macOS | 1.24.x | Extra-Large | full-buffer | post-commit failure | — | Pending |
| TM-187 | macOS | 1.24.x | Extra-Large | streaming | convert | — | Pending |
| TM-188 | macOS | 1.24.x | Extra-Large | streaming | skip | — | Pending |
| TM-189 | macOS | 1.24.x | Extra-Large | streaming | fallback | — | Pending |
| TM-190 | macOS | 1.24.x | Extra-Large | streaming | fail-open | — | Pending |
| TM-191 | macOS | 1.24.x | Extra-Large | streaming | fail-closed | — | Pending |
| TM-192 | macOS | 1.24.x | Extra-Large | streaming | post-commit failure | — | Pending |
| TM-193 | macOS | 1.26.x | Small | full-buffer | convert | — | Pending |
| TM-194 | macOS | 1.26.x | Small | full-buffer | skip | — | Pending |
| TM-195 | macOS | 1.26.x | Small | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-196 | macOS | 1.26.x | Small | full-buffer | fail-open | — | Pending |
| TM-197 | macOS | 1.26.x | Small | full-buffer | fail-closed | — | Pending |
| TM-198 | macOS | 1.26.x | Small | full-buffer | post-commit failure | — | Pending |
| TM-199 | macOS | 1.26.x | Small | streaming | convert | — | Pending |
| TM-200 | macOS | 1.26.x | Small | streaming | skip | — | Pending |
| TM-201 | macOS | 1.26.x | Small | streaming | fallback | — | Pending |
| TM-202 | macOS | 1.26.x | Small | streaming | fail-open | — | Pending |
| TM-203 | macOS | 1.26.x | Small | streaming | fail-closed | — | Pending |
| TM-204 | macOS | 1.26.x | Small | streaming | post-commit failure | — | Pending |
| TM-205 | macOS | 1.26.x | Medium | full-buffer | convert | — | Pending |
| TM-206 | macOS | 1.26.x | Medium | full-buffer | skip | — | Pending |
| TM-207 | macOS | 1.26.x | Medium | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-208 | macOS | 1.26.x | Medium | full-buffer | fail-open | — | Pending |
| TM-209 | macOS | 1.26.x | Medium | full-buffer | fail-closed | — | Pending |
| TM-210 | macOS | 1.26.x | Medium | full-buffer | post-commit failure | — | Pending |
| TM-211 | macOS | 1.26.x | Medium | streaming | convert | — | Pending |
| TM-212 | macOS | 1.26.x | Medium | streaming | skip | — | Pending |
| TM-213 | macOS | 1.26.x | Medium | streaming | fallback | — | Pending |
| TM-214 | macOS | 1.26.x | Medium | streaming | fail-open | — | Pending |
| TM-215 | macOS | 1.26.x | Medium | streaming | fail-closed | — | Pending |
| TM-216 | macOS | 1.26.x | Medium | streaming | post-commit failure | — | Pending |
| TM-217 | macOS | 1.26.x | Large | full-buffer | convert | — | Pending |
| TM-218 | macOS | 1.26.x | Large | full-buffer | skip | — | Pending |
| TM-219 | macOS | 1.26.x | Large | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-220 | macOS | 1.26.x | Large | full-buffer | fail-open | — | Pending |
| TM-221 | macOS | 1.26.x | Large | full-buffer | fail-closed | — | Pending |
| TM-222 | macOS | 1.26.x | Large | full-buffer | post-commit failure | — | Pending |
| TM-223 | macOS | 1.26.x | Large | streaming | convert | — | Pending |
| TM-224 | macOS | 1.26.x | Large | streaming | skip | — | Pending |
| TM-225 | macOS | 1.26.x | Large | streaming | fallback | — | Pending |
| TM-226 | macOS | 1.26.x | Large | streaming | fail-open | — | Pending |
| TM-227 | macOS | 1.26.x | Large | streaming | fail-closed | — | Pending |
| TM-228 | macOS | 1.26.x | Large | streaming | post-commit failure | — | Pending |
| TM-229 | macOS | 1.26.x | Extra-Large | full-buffer | convert | size-limit rejection by design (excluded from required set; see coverage-set note above) | Expected-Rejection |
| TM-230 | macOS | 1.26.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-231 | macOS | 1.26.x | Extra-Large | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-232 | macOS | 1.26.x | Extra-Large | full-buffer | fail-open | — | Pending |
| TM-233 | macOS | 1.26.x | Extra-Large | full-buffer | fail-closed | — | Pending |
| TM-234 | macOS | 1.26.x | Extra-Large | full-buffer | post-commit failure | — | Pending |
| TM-235 | macOS | 1.26.x | Extra-Large | streaming | convert | — | Pending |
| TM-236 | macOS | 1.26.x | Extra-Large | streaming | skip | — | Pending |
| TM-237 | macOS | 1.26.x | Extra-Large | streaming | fallback | — | Pending |
| TM-238 | macOS | 1.26.x | Extra-Large | streaming | fail-open | — | Pending |
| TM-239 | macOS | 1.26.x | Extra-Large | streaming | fail-closed | — | Pending |
| TM-240 | macOS | 1.26.x | Extra-Large | streaming | post-commit failure | — | Pending |
| TM-241 | macOS | 1.27.x | Small | full-buffer | convert | — | Pending |
| TM-242 | macOS | 1.27.x | Small | full-buffer | skip | — | Pending |
| TM-243 | macOS | 1.27.x | Small | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-244 | macOS | 1.27.x | Small | full-buffer | fail-open | — | Pending |
| TM-245 | macOS | 1.27.x | Small | full-buffer | fail-closed | — | Pending |
| TM-246 | macOS | 1.27.x | Small | full-buffer | post-commit failure | — | Pending |
| TM-247 | macOS | 1.27.x | Small | streaming | convert | — | Pending |
| TM-248 | macOS | 1.27.x | Small | streaming | skip | — | Pending |
| TM-249 | macOS | 1.27.x | Small | streaming | fallback | — | Pending |
| TM-250 | macOS | 1.27.x | Small | streaming | fail-open | — | Pending |
| TM-251 | macOS | 1.27.x | Small | streaming | fail-closed | — | Pending |
| TM-252 | macOS | 1.27.x | Small | streaming | post-commit failure | — | Pending |
| TM-253 | macOS | 1.27.x | Medium | full-buffer | convert | — | Pending |
| TM-254 | macOS | 1.27.x | Medium | full-buffer | skip | — | Pending |
| TM-255 | macOS | 1.27.x | Medium | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-256 | macOS | 1.27.x | Medium | full-buffer | fail-open | — | Pending |
| TM-257 | macOS | 1.27.x | Medium | full-buffer | fail-closed | — | Pending |
| TM-258 | macOS | 1.27.x | Medium | full-buffer | post-commit failure | — | Pending |
| TM-259 | macOS | 1.27.x | Medium | streaming | convert | — | Pending |
| TM-260 | macOS | 1.27.x | Medium | streaming | skip | — | Pending |
| TM-261 | macOS | 1.27.x | Medium | streaming | fallback | — | Pending |
| TM-262 | macOS | 1.27.x | Medium | streaming | fail-open | — | Pending |
| TM-263 | macOS | 1.27.x | Medium | streaming | fail-closed | — | Pending |
| TM-264 | macOS | 1.27.x | Medium | streaming | post-commit failure | — | Pending |
| TM-265 | macOS | 1.27.x | Large | full-buffer | convert | — | Pending |
| TM-266 | macOS | 1.27.x | Large | full-buffer | skip | — | Pending |
| TM-267 | macOS | 1.27.x | Large | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-268 | macOS | 1.27.x | Large | full-buffer | fail-open | — | Pending |
| TM-269 | macOS | 1.27.x | Large | full-buffer | fail-closed | — | Pending |
| TM-270 | macOS | 1.27.x | Large | full-buffer | post-commit failure | — | Pending |
| TM-271 | macOS | 1.27.x | Large | streaming | convert | — | Pending |
| TM-272 | macOS | 1.27.x | Large | streaming | skip | — | Pending |
| TM-273 | macOS | 1.27.x | Large | streaming | fallback | — | Pending |
| TM-274 | macOS | 1.27.x | Large | streaming | fail-open | — | Pending |
| TM-275 | macOS | 1.27.x | Large | streaming | fail-closed | — | Pending |
| TM-276 | macOS | 1.27.x | Large | streaming | post-commit failure | — | Pending |
| TM-277 | macOS | 1.27.x | Extra-Large | full-buffer | convert | size-limit rejection by design (excluded from required set; see coverage-set note above) | Expected-Rejection |
| TM-278 | macOS | 1.27.x | Extra-Large | full-buffer | skip | — | Pending |
| TM-279 | macOS | 1.27.x | Extra-Large | full-buffer | fallback | — | Unreachable (dimension-invalid: fallback is only reachable after an initial streaming selection; full-buffer/fallback is not a valid combination) |
| TM-280 | macOS | 1.27.x | Extra-Large | full-buffer | fail-open | — | Pending |
| TM-281 | macOS | 1.27.x | Extra-Large | full-buffer | fail-closed | — | Pending |
| TM-282 | macOS | 1.27.x | Extra-Large | full-buffer | post-commit failure | — | Pending |
| TM-283 | macOS | 1.27.x | Extra-Large | streaming | convert | — | Pending |
| TM-284 | macOS | 1.27.x | Extra-Large | streaming | skip | — | Pending |
| TM-285 | macOS | 1.27.x | Extra-Large | streaming | fallback | — | Pending |
| TM-286 | macOS | 1.27.x | Extra-Large | streaming | fail-open | — | Pending |
| TM-287 | macOS | 1.27.x | Extra-Large | streaming | fail-closed | — | Pending |
| TM-288 | macOS | 1.27.x | Extra-Large | streaming | post-commit failure | — | Pending |

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-09-07 | Kang | Split fallback and fail-open into separate coverage obligations: Conversion Path becomes 6 values, the matrix grows to 288 tuples (282 required after the six Expected-Rejection exclusions), and every TM row is renumbered |
| 0.9.2 | 2026-09-07 | Kang | Mark all six Extra-Large full-buffer convert tuples Expected-Rejection and exclude them from the required set (240 → 234 required tuples) |
| 0.9.2 | 2026-09-06 | Kang | Annotate the Extra-Large full-buffer convert tuple as an expected size-limit rejection; no covering sub-spec required |
| 0.9.2 | 2026-08-15 | Hermes | Define the required coverage set as the complete 240-tuple Cartesian product (five conversion paths) |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
