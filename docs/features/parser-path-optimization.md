# Parser Path Optimization

## Overview

The 0.4.0 release introduces three groups of internal optimizations to the Rust
HTML-to-Markdown converter. These optimizations reduce unnecessary work during
DOM traversal and output normalization without changing the converter's public
API, FFI surface, or operator-facing behavior.

The three optimization groups are:

1. **Noise Region Early Pruning** — Skips child-node traversal for elements
   that produce no meaningful Markdown output (`<script>`, `<style>`,
   `<noscript>`), with experimental support for semantic noise regions
   (`<nav>`, `<footer>`, `<aside>`) behind a compile-time feature flag.
2. **Simple Structure Fast Path** — Pre-scans the DOM to detect structurally
   simple documents and enables branch elimination during traversal for
   qualifying pages.
3. **Large Response Path Optimization** — Pre-allocates the output buffer based
   on input size and replaces the two-pass normalize-then-copy with a
   single-pass fused normalizer for documents exceeding the large-body
   threshold.

All optimizations are evidence-gated: each group must demonstrate measurable
benefit in the benchmark corpus and pass a corpus diff before merging.

## Evidence Status

Evidence collection for the 0.4.0 optimizations is pending. Before this
feature ships as a release claim, the following evidence must be gathered and
recorded here:

- **Benchmark results**: Latency measurements (p50, p95, p99) and output byte
  counts for each optimization group, compared against the pre-optimization
  baseline on the same corpus version and platform.
- **Corpus diff summary**: Full-corpus diff confirming output equivalence for
  well-formed content (no unexplainable differences).
- **Peak memory comparison**: Before/after peak RSS for representative large
  documents to confirm no memory regression.

Until this evidence is collected and reviewed, the optimizations are considered
internal implementation improvements, not a release-level performance claim.

## Architecture

All 0.4.0 optimizations operate within the existing buffer-based conversion
pipeline. They do not introduce streaming, chunk-driven parsing, or parser
replacement. The html5ever parsing phase (step 1) is untouched.

### Current pipeline

```
HTML bytes → parse_html() (html5ever) → RcDom
           → SecurityValidator checks
           → MarkdownConverter::convert() → traverse_node() / handle_element_internal()
           → normalize_output()
           → final Markdown string
```

### Integration points

| Optimization Group | Integration Point | What Changes |
|--------------------|-------------------|--------------|
| Noise Region Early Pruning | `handle_element_internal` in `converter/traversal.rs` | Early-return before `traverse_children` for prunable elements |
| Simple Structure Fast Path | `convert_with_context` in `converter.rs` | Pre-scan qualification gate before traversal begins |
| Large Response Path | `convert_with_context` in `converter.rs` | Buffer pre-allocation and fused normalization after traversal |

### Module layout

All optimization code lives within the `converter` module hierarchy:

```
components/rust-converter/src/
  converter.rs                    # convert(), convert_with_context()
  converter/
    pruning.rs                    # Noise region pruning logic
    fast_path.rs                  # Fast path qualification
    large_response.rs             # Buffer estimation and fused normalizer
    traversal.rs                  # traverse_node, handle_element_internal (modified)
```


## Optimization Group 1: Noise Region Early Pruning

### What it does

During DOM traversal, the converter checks each element's tag name against a
pruning list before visiting its children. Elements on the list are skipped
immediately, avoiding the cost of recursing into their subtrees.

For `<script>`, `<style>`, and `<noscript>`, the `SecurityValidator` already
removes these elements (they are in `DANGEROUS_ELEMENTS`). The pruning
optimization makes this explicit at the traversal layer and avoids visiting
child text nodes that would produce no output regardless.

For `<nav>`, `<footer>`, and `<aside>`, pruning skips the entire subtree
(element and all descendants). This is disabled by default in 0.4.0.

### Elements targeted

| Element | Decision | Behavior | Default in 0.4.0 |
|---------|----------|----------|-------------------|
| `<script>` | `SkipChildren` | Skip child nodes; element itself already removed by `SecurityValidator` | Active |
| `<style>` | `SkipChildren` | Skip child nodes; CSS content never produces Markdown | Active |
| `<noscript>` | `SkipChildren` | Skip child nodes; fallback content is redundant | Active |
| `<nav>` | `SkipSubtree` | Skip element and all descendants | Disabled (feature flag) |
| `<footer>` | `SkipSubtree` | Skip element and all descendants | Disabled (feature flag) |
| `<aside>` | `SkipSubtree` | Skip element and all descendants | Disabled (feature flag) |

All other elements return `Traverse` and are handled normally.

### Pruning rules

- Pruning decisions are based on tag name only. No attribute inspection, no
  content heuristics, no `role` attribute matching.
- The `RcDom` tree is never mutated. Pruning operates at the traversal layer
  by returning early from `handle_element_internal`.
- For `SkipChildren`: the element node itself is processed (though
  `SecurityValidator` removes it), but its children are never visited.
- For `SkipSubtree`: the element and its entire subtree are skipped.
- For elements not in the pruning list, the decision is deterministic:
  `Traverse`. There is no ambiguity state.

### Feature flag: `prune_noise_regions`

Semantic noise region pruning (`<nav>`, `<footer>`, `<aside>`) is gated behind
the `prune_noise_regions` Cargo feature flag. In 0.4.0 release builds, this
feature is disabled by default. It can be enabled at compile time for
benchmarking and corpus diff collection:

```bash
cargo test --features prune_noise_regions
```

Promotion to default-on requires benchmark evidence demonstrating clear benefit
with acceptable false-kill risk, reviewed during a future release cycle.

### Known limitations

- `<div role="navigation">` and similar attribute-based semantic regions are
  not detected. Only the six tag names listed above are recognized.
- When `prune_noise_regions` is enabled, pages that place primary content
  inside `<nav>`, `<footer>`, or `<aside>` elements will lose that content
  (false kill). This is a known trade-off and the reason the feature is
  disabled by default.
- Pruning runs before `SecurityValidator::check_element` for prunable
  elements. This is safe because `script`/`style`/`noscript` are already in
  `DANGEROUS_ELEMENTS`, and `nav`/`footer`/`aside` are not dangerous.

### Source files

- `components/rust-converter/src/converter/pruning.rs` — `PruneDecision` enum
  and `should_prune` function
- `components/rust-converter/src/converter/traversal.rs` — integration in
  `handle_element_internal`

## Optimization Group 2: Simple Structure Fast Path

### What it does

Before traversal begins, the converter performs a lightweight pre-scan of the
DOM tree. If every element node uses a tag in the allowed set and the maximum
nesting depth stays below the threshold, the document qualifies for the fast
path.

The fast path is a qualification gate, not a separate converter. Qualifying
documents use the same `traverse_node` code path, but the converter can
eliminate unreachable branches during traversal (e.g., table handling, form
control handling, embedded content handling are unreachable for fast-path
documents). `SecurityValidator` remains fully active.

If any unsupported node is found during the pre-scan, the entire document falls
back to the normal path. There is no partial fast path.

### Qualification criteria

A document qualifies when both conditions are met:

1. Every element node uses a tag in the `FAST_PATH_ELEMENTS` set, or is a
   prunable element (handled by `should_prune` returning non-`Traverse`).
2. The maximum DOM nesting depth does not exceed `FAST_PATH_MAX_DEPTH` (15).

Non-element nodes (text, comments, doctypes, processing instructions) are
always allowed.

### Allowed elements

| Category | Elements |
|----------|----------|
| Document structure | `html`, `head`, `body` |
| Headings | `h1`, `h2`, `h3`, `h4`, `h5`, `h6` |
| Block content | `p`, `br`, `hr`, `blockquote`, `pre` |
| Lists | `ul`, `ol`, `li` |
| Inline formatting | `strong`, `b`, `em`, `i`, `code` |
| Links and images | `a`, `img` |
| Containers | `div`, `span`, `section`, `article`, `main`, `header` |

### Disqualifying elements (examples)

Tables (`table`, `thead`, `tbody`, `tr`, `td`, `th`), forms (`form`, `input`,
`select`, `textarea`, `button`), embedded content (`iframe`, `object`,
`embed`), and media (`video`, `audio`, `source`, `track`, `area`, `map`) all
disqualify a document from the fast path.

### Depth threshold

The maximum nesting depth is 15 (`FAST_PATH_MAX_DEPTH`). This covers typical
article structures. Deeply nested documents likely have complex structures that
benefit from the full conversion path.

Depth is counted from the document root node (depth 0). A text node inside
`<html><body><div><p>text</p></div></body></html>` sits at depth 5.

### Known limitations

- The pre-scan walks the entire DOM tree, adding O(n) overhead. For documents
  that do not qualify, this is wasted work. In practice, the scan is
  lightweight (tag-name comparison only) and terminates early on the first
  disqualifying node.
- The allowed element set is intentionally conservative. Some elements that
  could be handled by the fast path (e.g., `<dl>`, `<dt>`, `<dd>`) are
  excluded to keep the qualification logic simple.
- The fast path does not skip `SecurityValidator` checks. It reduces branch
  overhead in the traversal, not security validation.

### Source files

- `components/rust-converter/src/converter/fast_path.rs` — `FastPathResult`
  enum, `qualifies` function, `FAST_PATH_ELEMENTS` list, `FAST_PATH_MAX_DEPTH`
  constant
- `components/rust-converter/src/converter.rs` — integration in
  `convert_with_context`

## Optimization Group 3: Large Response Path Optimization

### What it does

For documents exceeding the large-body threshold, this optimization reduces
memory allocation overhead in two ways:

1. **Buffer estimation** — Pre-allocates the output `String` based on input
   size, avoiding repeated reallocations during traversal.
2. **Fused normalizer** — Normalizes output incrementally as lines are
   appended, eliminating the separate full-pass `normalize_output` step that
   would otherwise allocate a second full-size string.

### Buffer estimation

The output buffer is pre-allocated at 40% of the input HTML size
(`OUTPUT_SIZE_ESTIMATE_FACTOR = 0.4`), reflecting the typical 60–85% size
reduction from HTML to Markdown. The estimate is clamped to a reasonable range:

| Input Size | Pre-allocated Capacity |
|------------|----------------------|
| < ~10 KB | 4 KB (minimum) |
| ~10 KB – ~10 MB | `input_size × 0.4` |
| > ~10 MB | 4 MB (maximum) |

### Fused normalizer

The `FusedNormalizer` struct replaces the two-pass normalize-then-copy pattern
for large documents. It applies normalization rules incrementally as each line
is appended:

- Tracks fenced code block state (`` ``` `` delimiters).
- Collapses consecutive blank lines to a single blank line.
- Trims trailing whitespace from each line.
- Normalizes inline whitespace (collapses multiple spaces) outside code blocks,
  while preserving leading indentation and content inside backtick spans.

The output produced by `FusedNormalizer` is identical to the output of
`MarkdownConverter::normalize_output` for the same input. This equivalence is
verified by property-based tests.

### Threshold

The fused normalizer is used for documents exceeding `markdown_large_body_threshold`
(the existing NGINX directive threshold for large-body handling). Documents
below this threshold use the standard `normalize_output` two-pass approach.

### Known limitations

- The fused normalizer processes the traversal output line-by-line after
  traversal completes. It does not interleave with traversal itself — the
  intermediate `output` string is still fully built before normalization
  begins. The saving is one O(n) allocation and copy, not the traversal cost.
- Buffer estimation uses a fixed 0.4 factor. Pages with unusually high or low
  HTML-to-Markdown compression ratios may over- or under-allocate, but the
  clamp bounds prevent pathological cases.
- `ConversionContext` timeout checks remain active throughout the large-response
  path. The fused normalizer does not bypass timeout enforcement.

### Source files

- `components/rust-converter/src/converter/large_response.rs` —
  `estimate_output_capacity` function, `FusedNormalizer` struct
- `components/rust-converter/src/converter.rs` — integration in
  `convert_with_context`

## Security Baseline

All `SecurityValidator` checks remain active regardless of which optimization
path is taken:

- Dangerous element removal (`script`, `style`, `noscript`, and others in
  `DANGEROUS_ELEMENTS`)
- Event handler attribute stripping (`on*` prefix)
- Dangerous URL detection (`javascript:`, `data:`, etc.)
- DOM depth validation

Pruning `<script>`, `<style>`, and `<noscript>` earlier is security-positive —
it avoids visiting their children, which the security validator would remove
anyway. Pruning `<nav>`, `<footer>`, `<aside>` is a content decision, not a
security decision; these elements are not in `DANGEROUS_ELEMENTS`.

No new `unsafe` code is introduced by any optimization in this spec.

## Stop Line

Optimization work stops and remaining ideas defer to 0.5.x when any of the
following conditions are met:

- The optimization requires changes to the FFI boundary
- The optimization alters the `MarkdownConverter` public API
- The optimization restructures the DOM traversal model
- The optimization introduces conditional compilation paths that fragment the
  conversion logic
- The optimization work threatens the 0.4.0 release timeline
- The optimization depends on unfinished benchmark tooling
- The optimization creates ambiguity about output stability

P0 specs always take priority. If this P1 spec risks the release, it defers
entirely rather than shipping a partial optimization set.

## Deferred to 0.5.x

The following items are explicitly out of scope for 0.4.0:

- **Streaming/chunk-driven conversion** — Requires fundamental architecture
  changes to the buffer-based pipeline.
- **Parser replacement** — Replacing html5ever or markup5ever_rcdom with a
  different parser/DOM library.
- **Content-aware heuristic pruning** — Readability-style algorithms that
  analyze content to decide what to prune.
- **Configurable pruning rules** — Operator-facing directives to control which
  elements are pruned.
- **Optimization of the html5ever parsing phase** — Step 1 of the pipeline is
  untouched.
- **Promotion of semantic pruning to default-on** — `<nav>`, `<footer>`,
  `<aside>` pruning remains behind a feature flag until evidence and rollout
  posture support promotion.
- **New NGINX configuration directives** for optimization control.
- **Optimization-specific metrics or logging** — No new reason codes or
  decision logging for optimization paths.
