# Scope Evaluation Process

Requirements references: 13.1, 13.2, 13.3, 13.4

This document defines the process for evaluating new proposals that arise during 0.4.0 development. Its purpose is to prevent scope creep by providing a structured evaluation path that checks proposals against existing sub-specs, the non-goals list, and the target capability boundary.

## Evaluation Steps

1. **New proposal received.** A proposed addition or change is submitted for consideration.

2. **Check if covered by an existing sub-spec.** If the proposal falls within the scope of an existing 0.4.0 sub-spec, evaluate it within that sub-spec's scope. No further steps needed.

3. **Check against the non-goals list.** If the proposal matches an item on the non-goals list (see below), reject it and record it as a 0.5.x candidate.

4. **Determine if clearly in-scope.** If the proposal is clearly within the 0.4.0 target capability boundary, accept it and update the affected sub-spec documents.

5. **Handle ambiguous proposals.** If the proposal is not clearly in-scope or out-of-scope, write a Boundary Description using the standard template (capability name, 0.4.0 scope, 0.5.x scope, rationale, prerequisites for deferred work) and submit it for review.

6. **Review the Boundary Description.** Evaluate the Boundary Description to determine whether the proposal should be accepted or rejected.

7. **Record the outcome.** If accepted, record the scope expansion with rationale and update the affected sub-spec documents. If rejected, record as a 0.5.x candidate.

## Rules

- Any proposal not covered by an existing sub-spec must be evaluated against the target capability boundary and non-goals list.
- Proposals matching non-goals are rejected and recorded as 0.5.x candidates.
- Ambiguous proposals require a Boundary Description and review before acceptance.
- Accepted scope expansions are recorded with rationale and reflected in affected sub-spec documents.

## 0.4.0 Non-Goals List

The following are explicitly out of scope for 0.4.0. Any proposal matching these items is rejected and recorded as a 0.5.x candidate:
This list is mirrored in `tools/release/release_constants.py::NON_GOALS` and must stay in sync.

- True streaming HTML-to-Markdown conversion
- Streaming-aware or chunk-driven FFI contract evolution
- New output format negotiation (JSON, text/plain, MDX)
- OpenTelemetry tracing
- High-cardinality metrics
- GUI, console, or dashboard
- Cross-web-server ecosystem support (Apache, Caddy, Envoy, Traefik)
- Enterprise control plane or policy center
- AI post-processing capabilities (summarization, rewriting, extraction)
- Complex shadow streaming replacement
- Positioning 0.4.0 as a "1.0.0 pre-release"
