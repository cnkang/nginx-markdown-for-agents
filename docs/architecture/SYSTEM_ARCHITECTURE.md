# System Architecture

This document explains the runtime structure of `nginx-markdown-for-agents`, the responsibilities of each component, and the reasoning behind the main technology choices.

## Design Goals

The system is designed to do four things well:

- add a Markdown representation to existing HTML responses without changing the application
- keep request handling inside standard NGINX deployment and operations patterns
- keep HTML parsing and conversion safe, deterministic, and testable
- keep failure behavior explicit and observable

## High-Level Structure

At runtime, the system has two main components:

- the NGINX module in C, which participates in the NGINX request and filter pipeline
- the conversion engine in Rust, which parses HTML and produces Markdown through a small FFI boundary

```text
Client
  -> NGINX request processing
  -> Markdown filter module (C)
     -> eligibility checks
     -> buffering / decompression / header policy
     -> Rust FFI call
  -> Rust converter
     -> HTML parsing
     -> sanitization
     -> Markdown generation
     -> metadata / ETag support
  -> NGINX response delivery
```

## Why C + Rust

The split is deliberate.

### Why the NGINX-facing layer is C

NGINX modules are built around NGINX's C APIs, request phases, memory pools, buffers, and filter-chain model. Putting the request-path integration in C means the module can:

- fit naturally into normal NGINX module loading and configuration
- manage NGINX request and response objects directly
- participate in header and body filtering without an additional translation layer
- preserve familiar deployment and debugging workflows for operators

### Why the conversion engine is Rust

HTML parsing and Markdown generation are the parts most exposed to malformed or hostile input and the parts most likely to grow in complexity over time. Rust is a better fit there because it provides:

- stronger memory-safety guarantees for parsing untrusted input
- a strong ecosystem for HTML parsing and supporting utilities
- better ergonomics for testing, property testing, and output normalization
- clearer ownership and error-handling rules than a pure C implementation

### Why not pure C

A pure C implementation would reduce build complexity, but it would move the riskiest parsing and transformation code into the least forgiving part of the stack. For this project, maintainability and input safety matter more than keeping the entire system in one language.

### Why not an external service

An external converter service would simplify NGINX module logic, but it would add network hops, new failure modes, deployment overhead, and an extra operational surface. This project is explicitly designed to keep conversion inline with the existing reverse-proxy path.

## Responsibility Split

### NGINX module responsibilities

The C module is responsible for:

- deciding whether a request/response pair is eligible for conversion
- handling configuration, request policy, and negotiation rules
- buffering response bodies and coordinating decompression when needed
- applying response-header changes for Markdown variants
- selecting fail-open or fail-closed behavior on conversion failure
- exposing runtime metrics

### Rust converter responsibilities

The Rust converter is responsible for:

- parsing incoming HTML
- removing or neutralizing unsafe or non-content elements
- generating deterministic Markdown output
- producing optional metadata such as token estimates and front matter
- returning structured results through a stable C-compatible interface

## Why the FFI Boundary Is Small

The C/Rust boundary is intentionally narrow. The NGINX module does not ask Rust to understand NGINX internals, and the Rust converter does not try to manage the HTTP lifecycle.

That keeps the contract easier to reason about:

- C owns request-path orchestration
- Rust owns conversion logic
- the boundary passes bytes, options, and results

This reduces coupling and makes it easier to test each side independently.

## Request Flow

For an eligible Markdown request, the runtime flow is:

1. NGINX receives the request and evaluates the configured `markdown_filter` behavior.
2. The module checks request and response eligibility such as method, status, content type, and policy exclusions.
3. If needed, the module buffers the upstream body and decompresses supported encodings.
4. The buffered payload is passed to the Rust converter through FFI.
5. The converter returns Markdown output and optional metadata.
6. The module updates headers such as `Content-Type`, `Vary`, and variant `ETag`, then sends the Markdown response.

For non-eligible requests, the module stays out of the way and the original response continues through NGINX unchanged.

## Key Architectural Tradeoffs

### Full buffering in v1

The current architecture buffers the full eligible response before conversion. That makes correctness, deterministic output, and header handling much simpler, but it also means:

- larger responses consume more memory
- conversion cannot start streaming output immediately
- very large or streaming-style content should usually be bypassed

This tradeoff is documented in [ADR-0002](ADR/0002-full-buffering-approach.md).

### Inline conversion instead of offline publishing

The project chooses inline conversion because it keeps representation negotiation close to the request and avoids duplicating content pipelines. The tradeoff is that conversion now sits inside the request path, so limits, failure policy, and observability become first-class concerns.

## Where to Go Next

- Runtime decision rationale: [ADR/README.md](ADR/README.md)
- Rust-selection decision: [ADR/0001-use-rust-for-conversion.md](ADR/0001-use-rust-for-conversion.md)
- Buffering decision: [ADR/0002-full-buffering-approach.md](ADR/0002-full-buffering-approach.md)
- Repository layout: [REPOSITORY_STRUCTURE.md](REPOSITORY_STRUCTURE.md)
- Operator-facing behavior: [../guides/CONFIGURATION.md](../guides/CONFIGURATION.md)
