# Feature Documentation

This directory contains feature-focused documentation for the Rust converter and NGINX module.

## Contents

- `CONFIGURATION_STRUCTURE.md` - configuration model and directive structure details
- `AUTOMATIC_DECOMPRESSION.md` - automatic upstream decompression behavior and safeguards
- `COOPERATIVE_TIMEOUT.md` - cooperative timeout design and behavior
- `DETERMINISTIC_OUTPUT_IMPLEMENTATION.md` - implementation notes for deterministic output handling
- `TOKEN_ESTIMATOR.md` - token estimation behavior and rationale
- `YAML_FRONT_MATTER.md` - YAML front matter generation rules
- `charset-detection.md` - charset detection cascade and fallback behavior
- `deterministic-output.md` - deterministic output behavior overview
- `html-entity-decoding.md` - HTML entity decoding behavior
- `non-content-element-removal.md` - removal of non-content HTML elements
- `security.md` - security protections (XSS, XXE, SSRF-related safeguards)

## How to Use These Documents

Use this section when you need implementation details that are too specific for the top-level guides.

Recommended reading paths:

- Content quality and output stability:
  - `deterministic-output.md`
  - `DETERMINISTIC_OUTPUT_IMPLEMENTATION.md`
  - `html-entity-decoding.md`
  - `non-content-element-removal.md`
- Metadata and enrichment:
  - `YAML_FRONT_MATTER.md`
  - `TOKEN_ESTIMATOR.md`
- Input handling and safety:
  - `AUTOMATIC_DECOMPRESSION.md`
  - `charset-detection.md`
  - `security.md`
- NGINX configuration internals:
  - `CONFIGURATION_STRUCTURE.md`

## Maintenance Notes

When adding a new feature document:

1. Prefer one topic per file.
2. Include behavior, constraints, and edge cases.
3. Reference tests or verification docs when useful.
4. Keep user-facing setup steps in the top-level guides instead of duplicating them here.
