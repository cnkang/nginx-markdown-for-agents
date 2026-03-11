# Feature Documentation

This directory contains feature-level references for the NGINX module and Rust converter.

These documents are for readers who already understand the high-level product behavior and want to study specific implementation areas, constraints, or safety properties.

Read this section when the top-level guides answer what to configure, but you still need to understand why the implementation behaves the way it does.

If you need rollout steps, operator checklists, or directive syntax, go back to:

- [../guides/README.md](../guides/README.md) for user- and operator-facing guidance
- [../architecture/README.md](../architecture/README.md) for runtime flow and design rationale

## Read by Topic

### Output quality and determinism

- [deterministic-output.md](deterministic-output.md)
- [DETERMINISTIC_OUTPUT_IMPLEMENTATION.md](DETERMINISTIC_OUTPUT_IMPLEMENTATION.md)
- [html-entity-decoding.md](html-entity-decoding.md)
- [non-content-element-removal.md](non-content-element-removal.md)

### Input handling and safety

- [AUTOMATIC_DECOMPRESSION.md](AUTOMATIC_DECOMPRESSION.md)
- [charset-detection.md](charset-detection.md)
- [security.md](security.md)

### Content negotiation and caching

- [CONTENT_NEGOTIATION.md](CONTENT_NEGOTIATION.md)
- [CACHE_AWARE_RESPONSES.md](CACHE_AWARE_RESPONSES.md)

### Metadata and enrichment

- [TOKEN_ESTIMATOR.md](TOKEN_ESTIMATOR.md)
- [YAML_FRONT_MATTER.md](YAML_FRONT_MATTER.md)

### Configuration internals

- [CONFIGURATION_STRUCTURE.md](CONFIGURATION_STRUCTURE.md)
- [COOPERATIVE_TIMEOUT.md](COOPERATIVE_TIMEOUT.md)

## What Belongs Here

- behavior that is too detailed for user-facing guides
- rationale for non-obvious implementation choices
- edge cases, constraints, and safeguards
- source-level structures and implementation contracts

Keep setup instructions and rollout steps in [../guides/README.md](../guides/README.md) instead of duplicating them here.
