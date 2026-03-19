# ADR-0003: Inline Origin-Near Conversion

## Status

Accepted

## Context

The module converts HTML to Markdown inside the NGINX request path, at the reverse-proxy layer closest to the origin application. This is a deliberate positioning choice. Alternative approaches exist — most notably, converting at the CDN or edge layer, as Cloudflare's Markdown for Agents demonstrates.

This ADR documents why the project chose inline origin-near conversion and what tradeoffs that creates.

## Decision

Conversion runs as an NGINX output filter at the reverse-proxy layer, inline with the request that produced the HTML response.

The key properties of this positioning:

1. The HTML being converted is the direct output of the application or CMS, before any downstream infrastructure (CDN, edge functions, client-side rendering) has modified it.
2. The operator controls the module version, configuration, failure policy, and rollout scope within their own infrastructure.
3. Representation negotiation happens at the origin (or its reverse proxy), which aligns with the HTTP content negotiation model where the server selects the best representation of a resource.

## Consequences

### Positive Consequences

1. **Proximity to application output**: The module converts HTML as it leaves the application. This is the earliest point in the delivery chain where the full rendered page is available, before any downstream transformation or augmentation.

2. **Operator control**: Module version, conversion configuration, failure behavior, size limits, and rollout scope are all managed within the operator's own infrastructure. There is no dependency on a third-party platform's feature release cycle or configuration surface.

3. **HTTP content negotiation alignment**: The origin (or its reverse proxy) is the natural place for representation selection in the HTTP model. `Vary: Accept` and variant ETags are straightforward to manage at this layer.

4. **Simpler cache semantics**: When conversion happens at the origin, the CDN layer caches the already-converted Markdown variant like any other response. The origin controls `Vary`, `ETag`, and `Cache-Control` directly, without requiring the CDN to manage conversion-aware cache keys.

5. **Variable-driven flexibility**: Because the module runs inside NGINX, operators can use `map` directives, variables, and standard NGINX logic to control conversion behavior per-request — including User-Agent-based bot targeting.

### Negative Consequences

1. **Requires infrastructure access**: The operator must be able to install and configure an NGINX module. This is a higher barrier than enabling a feature toggle on a CDN dashboard.

2. **Conversion cost on the origin path**: Conversion consumes CPU and memory on the origin or reverse-proxy server, not on distributed edge nodes. For high-traffic sites, this concentrates the conversion workload.

3. **No coverage for sites you don't operate**: Edge-layer conversion can be applied to any site proxied through the CDN, even without the site operator's involvement. Origin-near conversion only works for sites where the operator has chosen to deploy the module.

## Alternatives Considered

### Edge-layer conversion (CDN)

**Approach**: Convert HTML to Markdown at the CDN edge, as Cloudflare's Markdown for Agents does.

**Strengths:**
- Zero-touch enablement — no origin changes required
- Distributed conversion across edge nodes
- Can be applied to any site behind the CDN

**Tradeoffs:**
- The CDN converts HTML that may have been modified by edge functions, injected scripts, or other CDN-layer processing
- Conversion configuration and failure behavior are managed by the CDN provider
- Cache key management for `Vary: Accept` adds complexity at the edge layer

**Why not chosen for this project:** This project targets operators who want conversion within their own infrastructure, with direct control over what gets converted and how. The two approaches serve different operational models and can coexist.

### Offline pre-generation

**Approach**: Generate Markdown variants at build or publish time, serve them as static files.

**Strengths:**
- No runtime conversion cost
- Simplest possible serving path
- Can produce hand-tuned Markdown

**Tradeoffs:**
- Requires a parallel content pipeline
- Content must be regenerated on every change
- Does not work for dynamic or personalized pages

**Why not chosen:** The project's goal is to add Markdown representation to existing sites without changing the content production workflow. Offline generation requires exactly the kind of pipeline change the project aims to avoid.

## Relationship to Other ADRs

- [ADR-0001](0001-use-rust-for-conversion.md): The choice of Rust for conversion is partly motivated by the fact that conversion runs inline — input safety and predictable performance matter more when conversion is in the request path.
- [ADR-0002](0002-full-buffering-approach.md): Full buffering is a consequence of inline conversion — the module must produce a complete, correct Markdown response before sending headers.

## References

- System Architecture: [../SYSTEM_ARCHITECTURE.md](../SYSTEM_ARCHITECTURE.md)
- Cloudflare Markdown for Agents: https://blog.cloudflare.com/markdown-for-agents/
- HTTP Content Negotiation: https://developer.mozilla.org/en-US/docs/Web/HTTP/Content_negotiation

## Date

2026-03-18

## Authors

Project Team
