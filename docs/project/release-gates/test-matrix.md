# Cross-Spec Test Matrix

Requirements references: 8.1, 8.2, 8.3, 8.4

The test matrix defines the key dimensions that 0.4.0 testing must cover across all sub-specs. Each sub-spec maps its test plan against this matrix. The combined coverage of all sub-specs must address every cell, with no dimension left entirely untested.

## Dimensions

| Dimension | Values |
|-----------|--------|
| Platform | Ubuntu (primary), macOS (secondary) |
| NGINX Version | 1.24.x (LTS), 1.26.x (stable), 1.28.x (stable), 1.30.x (stable), 1.31.x (mainline) |
| Response Size | Small (<10KB), Medium (10KB–1MB), Large (>1MB) |
| Conversion Path | Convert (eligible, success), Skip (ineligible), Fallback/Fail-open (eligible, failure) |

## Coverage Map Template

Copy the table below into your sub-spec test plan and fill in the cells your tests cover.

| Dimension | Values Covered | Test Type |
|-----------|---------------|-----------|
| Platform | [values] | [CI matrix / unit / e2e] |
| NGINX Version | [values] | [CI matrix] |
| Response Size | [values] | [unit + e2e] |
| Conversion Path | [values] | [unit + e2e] |

## Gap Documentation

If infrastructure or resource constraints block a test matrix cell, the team must document the gap with justification. Use the following format in your sub-spec test plan:

| Dimension | Uncovered Value | Justification | Approved Exception |
|-----------|----------------|---------------|-------------------|
| [dimension] | [value] | [reason the cell cannot be covered] | [Go/No-Go record: eligibility (non-P0, non-safety gates only), rationale, risk assessment, mitigation evidence, named approver] |

Each documented uncovered cell remains a testing-gate failure until the
Go/No-Go review records a complete exception: eligibility for non-P0,
non-safety gates, exception rationale, risk assessment, mitigation evidence,
and a named approver. Do not treat a gap as approved unless all required
release evidence is present. The Go/No-Go review reviews gaps. A gap without
justification counts as an unresolved testing gate failure.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
