---
domain: html-sanitizer
rules: [5, 6, 27]
paths:
  - "components/rust-converter/src/sanitizer/**"
  - "components/rust-converter/src/emitter/**"
  - "components/nginx-module/src/**"
---

## HTML Sanitizer & Output Safety

### 5. Sanitizer and HTML semantics mismatches
Historical issues: `8440ac3`, `dbcdad8`, `77a46d6`.

Required:
- Treat HTML void elements as self-closing by semantics, not only tokenizer flags.
- Skip-mode exit for dangerous elements must be name-aware; mismatched closing tags must not prematurely exit skip mode.
- Keep nesting-depth accounting saturation-safe and invariant-preserving under malformed HTML.
- When using tokenizer-level streams, do not assume tree-builder implicit tags (for example implicit `</head>`); state transitions must be explicit.

---

### 6. Emitter structural correctness bugs
Historical issues: `1688e80`, `77a46d6`, `2c7d6a9`.

Required:
- In-link formatting markers (bold/italic/inline-code) must be accumulated in link text, not flushed outside link context.
- Code-block output must preserve raw content (blank lines/trailing spaces) and bypass generic normalization.
- Blockquote markers must be emitted consistently on entry and after newline boundaries.
- URL extraction parity must include media-bearing elements, not only `img`
  (at minimum `video`, `audio`, `source`, `track`, and `area` where
  applicable), with regression tests that cover missing-attribute and
  attribute-present branches.
- Human-readable fallback/detail strings that are carried in enums or result
  types (for example `UnsupportedStructure(...)`) must use stable internal
  identifiers, not user-influenced payloads.

---

### 27. Markdown output escaping and injection prevention
Historical issues: `3e1f7a2`, `a9b4c01`, `d72e8f3`, `b1a09c4`, `f5d3e12`, `c8e2a07`.

Required:
- All text emitted inside Markdown link labels `[...]` must escape `\`, `[`,
  `]`, and newline characters before interpolation.  Unescaped brackets break
  link structure and enable content-injection attacks.
- All text emitted inside Markdown link destinations `(...)` or autolinks
  `<...>` must escape `\`, `(`, `)`, `<`, `>`, spaces, and control characters.
  In Rust, iterate over `chars()` not `bytes()` so multi-byte code points are
  handled atomically.
- All URL values must reject percent-encoded control characters (`%00`–`%1F`,
  `%7F`) before scheme validation.  Scheme-prefix checks alone are
  insufficient for obfuscated payloads.
- All forwarded header values (for example `X-Forwarded-Host`, `X-Forwarded-Proto`)
  must be validated: extract first-hop value only, reject control characters /
  spaces / path separators, validate IPv6 bracket literals, and fall back to
  server name on invalid input.
- Every new emission site for links, images, or URLs must call the shared
  escaping helper rather than formatting raw strings directly.  Introducing a
  new `write!` or `format!` that interpolates unescaped text is a bug.

Verification:
- `grep -rn 'output.push_str.*normalized_text\|format!.*\[{.*}\]({' components/rust-converter/src/`
- `cargo test --features security` (if security feature gate exists)
- `grep -rn 'ngx_http_markdown_escape\|escape_link_label\|escape_link_destination' components/rust-converter/src/`
