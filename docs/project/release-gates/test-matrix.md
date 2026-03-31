# Cross-Spec Test Matrix

Requirements references: 8.1, 8.2, 8.3, 8.4

The test matrix defines the key dimensions that 0.4.0 testing must cover across all sub-specs. Each sub-spec maps its test plan against this matrix. The combined coverage of all sub-specs must address every cell, with no dimension left entirely untested.

## Dimensions

| Dimension | Values |
|-----------|--------|
| Platform | Ubuntu (primary), macOS (secondary) |
| NGINX Version | 1.24.x (LTS), 1.26.x (stable), 1.27.x (mainline) |
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

If a test matrix cell cannot be covered due to infrastructure or resource constraints, the gap must be documented with justification. Use the following format in your sub-spec test plan:

| Dimension | Uncovered Value | Justification |
|-----------|----------------|---------------|
| [dimension] | [value] | [reason the cell cannot be covered] |

Gaps are reviewed during the Go/No-Go review. A gap without justification is treated as an unresolved testing gate failure.
