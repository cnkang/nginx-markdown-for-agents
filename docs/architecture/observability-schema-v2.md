# Observability Contract v2

The 0.9.2 diagnostics wire contract is Schema v2. The canonical validator
path remains [observability-schema-v1.md](observability-schema-v1.md) for
compatibility with existing release and harness gates. This v2-named document
serves as the operator-facing alias for that same contract.

Use the canonical schema at
[schemas/diagnostics.schema.json](../../schemas/diagnostics.schema.json) when
validating a response. The diagnostics endpoint is built-in loopback-only,
accepts only GET and HEAD. Native NGINX access-phase directives can narrow
that boundary further.
