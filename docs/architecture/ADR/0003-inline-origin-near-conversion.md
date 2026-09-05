# ADR-0003: Inline Origin-Near Conversion

## Status

Accepted

## Context

The module converts HTML to Markdown inside the NGINX request path, at the reverse-proxy layer closest to the origin application. This is a deliberate positioning choice. Alternative approaches exist — most notably, converting at the CDN or edge layer, as Cloudflare's Markdown for Agents demonstrates.

This ADR documents why the project chose inline origin-near conversion and what tradeoffs that creates.

## Decision

Conversion runs as an NGINX output filter at the reverse-proxy layer, inline with the request that produced the HTML response.

The key properties of this positioning:

1. The HTML under conversion is the direct output of the application or CMS. No downstream infrastructure (CDN, edge functions, client-side rendering) has modified it yet.
2. The operator controls the module version, configuration, failure policy, and rollout scope within their own infrastructure.
3. Representation negotiation happens at the origin (or its reverse proxy). This aligns with the HTTP content negotiation model, where the server selects the best representation of a resource.

## Consequences

### Positive Consequences

1. **Proximity to application output**: The module converts HTML as it leaves the application. This is the earliest point where the full rendered page is available. It precedes any downstream transformation or augmentation.

2. **Operator control**: The operator manages every conversion knob in-house. Module version, configuration, failure policy, size limits, and rollout scope never leave their infrastructure. There is no dependency on a third-party platform's release cycle. The operator owns the full surface.

3. **HTTP content negotiation alignment**: The origin (or its reverse proxy) is the natural place for representation selection. This matches the HTTP model. Managing `Vary: Accept` and variant ETags stays straightforward here. The server picks the best representation.

4. **Simpler cache semantics**: The CDN caches the converted Markdown variant like any other response. This works when conversion happens at the origin. The origin controls `Vary`, `ETag`, and `Cache-Control` directly. The CDN must honor representation variants declared by `Vary: Accept` when matching cached responses, so that clients receive the correct representation.

5. **Variable-driven flexibility**: Because the module runs inside NGINX, operators can use `map` directives and variables to control conversion per request. This includes User-Agent-based bot targeting. When targeting variables (such as a bot-detection map) change the representation served to different clients, the operator must preserve cache separation: when the targeting input is a request header field, add that **request header field name** to `Vary` (for example `Vary: Accept, User-Agent`); only for non-header map inputs, include a normalized targeting-variable value in the cache key instead, so caches do not serve the Markdown variant to clients that should receive HTML (or the reverse). `Vary` may list only request header field names; for non-header map inputs, the normalized targeting-variable value must go in the cache key.

### Negative Consequences

1. **Requires infrastructure access**: The operator must be able to install and configure an NGINX module. This is a higher barrier than enabling a feature toggle on a CDN dashboard.

2. **Conversion cost on the origin path**: Conversion consumes CPU and memory on the origin or reverse-proxy server. It does not spread across distributed edge nodes. For high-traffic sites, this concentrates the conversion workload. The origin absorbs the cost.

3. **No coverage for sites you do not operate**: Edge-layer conversion can apply to any site proxied through the CDN. It works even without the site operator's involvement. Origin-near conversion only works where the operator has chosen to deploy the module. The module requires operator deployment. Coverage requires the module.

## Alternatives Considered

### Edge-layer conversion (CDN)

**Approach**: Convert HTML to Markdown at the CDN edge, as Cloudflare's Markdown for Agents does.

**Strengths:**
- Zero-touch enablement — no origin changes required
- Distributed conversion across edge nodes
- Applies to any site behind the CDN

**Tradeoffs:**
- The CDN converts HTML that edge functions, injected scripts, or other CDN-layer processing may have modified
- The CDN provider manages conversion configuration and failure behavior
- Cache key management for `Vary: Accept` adds complexity at the edge layer

**Why not chosen for this project:** This project targets operators who want conversion within their own infrastructure. They get direct control over what gets converted and how. The two approaches serve different operational models. They can coexist. Each model fits a different operator. The project chose the self-hosted model.

### Offline pre-generation

**Approach**: Generate Markdown variants at build or publish time, serve them as static files.

**Strengths:**
- No runtime conversion cost
- Simplest possible serving path
- Can produce hand-tuned Markdown

**Tradeoffs:**
- Requires a parallel content pipeline
- Content must regenerate on every change
- Does not work for dynamic or personalized pages

**Why not chosen:** The project's goal is to add Markdown representation to existing sites without changing the content production workflow. Offline generation requires exactly the kind of pipeline change the project aims to avoid.

## Relationship to Other ADRs

- [ADR-0001](0001-use-rust-for-conversion.md): The choice of Rust for conversion is partly motivated by the fact that conversion runs inline. Input safety and predictable performance matter more when conversion is in the request path.
- [ADR-0002](0002-full-buffering-approach.md): Full buffering is a consequence of inline conversion. On the full-buffer engine/path, the module must produce a complete, correct Markdown response before sending headers. The streaming engine/path sends headers once the commit decision is made, see ADR-0011.

## References

- System Architecture: [../SYSTEM_ARCHITECTURE.md](../SYSTEM_ARCHITECTURE.md)
- Cloudflare Markdown for Agents: https://blog.cloudflare.com/markdown-for-agents/
- HTTP Content Negotiation: https://developer.mozilla.org/en-US/docs/Web/HTTP/Content_negotiation

## Date

2026-03-18

## Authors

Project Team

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-15 | Kang | ADR-0003 clarifies Vary/cache-key guidance for User-Agent-targeted representations |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
