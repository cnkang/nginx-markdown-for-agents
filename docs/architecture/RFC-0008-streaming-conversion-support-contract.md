# RFC 0008: Streaming Conversion and Support Contract

| Field          | Value                     |
|----------------|---------------------------|
| Status         | Accepted / Implemented in 0.8.0 |
| Target Version | 0.8.0                     |
| Author         | Kang                      |
| Created        | 2026-06-04                |
| Scope          | True streaming contract, defaults, fallback semantics, support matrix source |

## Implementation Note

The 0.8.0 release implemented RFC-0008, shipping the true streaming contract,
fallback semantics, and support matrix source of truth as specified in this
document. The 0.8.1 patch release added further hardening: streaming header
commit atomicity (Rule 39 rollback semantics), FFI streaming finish invalid
input handle ownership / cleanup contract, Content-Type OWS / HTAB compliance,
and full-buffer backpressure NGX_AGAIN resume tail duplication fix.

---

## 1. True Streaming Definition

In 0.8.0, "true streaming conversion" means: the module, upon receiving an
upstream response buffer chain in the NGINX response body filter, is able to
parse HTML incrementally, maintain conversion state, and produce Markdown
output **without buffering the entire upstream response body**.

True streaming MUST satisfy ALL of the following conditions simultaneously.

### 1.1 Incremental Input Processing

The module processes response body in the order buffers/chunks arrive via the
NGINX body filter chain. HTML tags, text nodes, attributes, entities, tables,
lists, and other structures MAY span chunk boundaries. The converter MUST
maintain the necessary state and correctly merge content across boundaries.

### 1.2 Incremental Output Emission

Once the converter has sufficient context and confirms the response remains
safely convertible, it MAY send Markdown output downstream. Output is NOT
required to correspond one-to-one with input chunks, but the module MUST NOT
wait until the entire HTML response arrives before producing any output.

### 1.3 Explainable Memory Bound

The peak memory of the true streaming path MUST NOT scale linearly with the
full HTML body size. The implementation MAY retain parser state, a look-behind
window, a pending output buffer, and a pre-commit raw replay buffer — but it
MUST NOT hold the complete response body as a default working set.

### 1.4 Representation Non-Reversibility

Once the module has committed a `text/markdown` response header or any Markdown
body bytes to the client, the response enters **committed** state. After commit,
the module MUST NOT revert to the original HTML representation.

### 1.5 Semantic Integrity Over Low Latency

The goal of true streaming is to reduce peak memory and time-to-first-byte for
large responses. However, the module MUST NOT sacrifice Markdown structural
correctness for earlier flushing. Lists, tables, code blocks, heading levels,
link text, and image alt text MUST remain structurally consistent across chunk
boundaries.

### 1.6 Distinction from Incremental Processing

The following patterns alone do **not** constitute true streaming:

- Receiving input in batches but buffering the complete body internally before
  producing output on finalize.
- Passing chunked responses through without conversion.
- Treating a response-shape heuristic as a guarantee that the module will skip
  conversion. In 0.9.2 the internal heuristic only selects a candidate path.
  Hard eligibility and configured resource limits still decide conversion or
  policy-driven fallback.

True streaming MUST produce valid Markdown output **before** the module
buffers the complete upstream body. The module emits output while the body still streams in.

### 1.7 Exclusions

True streaming does NOT cover:

- **Unbounded streams**: SSE, NDJSON, streaming JSON, long-lived HTML streams,
  or any response without a well-defined end boundary or with continuously
  mutating semantics.
- **Non-HTML content**: JSON APIs, file downloads, images, CSS, JavaScript.
- **Features requiring full-document correctness** unless that feature already
  has an incremental implementation — for example strict Markdown-variant ETag
  comparison, full-text-scan front matter, and normalization modes requiring
  the complete document.
- **Compressed responses** MUST pass through the controlled
  decompression budget before entering the streaming HTML converter. The module
  MUST NOT feed gzip, br, and deflate payloads directly to the streaming parser.

### 1.8 Terminology Boundary

In 0.8.0 product documentation, "streaming supported" MUST refer exclusively
to the true streaming path defined above. The existence of an "incremental API"
MUST NOT equate with "true streaming GA".

---

## 2. Defaults

The following defaults target 0.8.0. The goal is to let large responses benefit
automatically without breaking 0.7.x usability, and without impacting small
pages or normal browser traffic.

### 2.1 Core Engine Switch

```nginx
markdown_streaming auto;
```

| Value  | Behavior |
|--------|----------|
| `off`  | Disable true streaming. All convertible responses use the 0.7.x full-buffer path or are skipped per existing policy. |
| `auto` | The module selects full-buffer or true streaming based on response type, size, transfer mode, feature combination, and risk assessment. |
| `force` | Prefer true streaming. If the response does not meet streaming preconditions, fallback semantics apply — the module does not silently pretend it is streaming. |

**Default**: `auto`

### 2.2 Automatic Streaming Heuristic (active 0.9.2 contract)

The retired `markdown_stream_threshold` directive is historical and is not
active in 0.9.2. Current selection uses `markdown_streaming auto` and a bounded
internal response-shape heuristic. There is no replacement threshold
directive.

In `auto` mode, a response becomes a **streaming candidate** when ANY of the
following is true:

- `Content-Length` header is absent.
- Upstream uses chunked transfer encoding.
- known `Content-Length` is at or above the internal 1 MiB candidate boundary.

Absence of `Content-Length` only makes the response a streaming candidate. It
does not force streaming. The engine MUST still verify content type, feature
compatibility, parser readiness, and configured rollout policy before selecting
true streaming.

Responses below that internal candidate boundary with a known
`Content-Length` default to the full-buffer path. Missing length and chunked
responses are only candidates. Content type, cache validation, codec support,
parser readiness, and resource policy still gate true streaming.

### 2.3 Pre-commit Replay Buffer

The pre-commit replay window is internal in 0.9.2. The removed
`markdown_stream_precommit_buffer` directive (256k default) is gone.
`markdown_limits streaming_buffer=<size>` (2 MiB default, 64k-1g) is the
shared total per-request budget for the Rust streaming working set and the
pre-commit replay window. Neither component receives a separate budget.

The replay window holds raw upstream HTML bytes before the module
commits to Markdown output. If the streaming parser determines during
the pre-commit phase that the response is not convertible, the module
replays these raw bytes and continues as a passthrough.

This is NOT a full-response buffer. It is the replay portion of the shared
budget within which the module can still safely fall back to original HTML.

### 2.4 Output Flush Policy

Flushing is internal in 0.9.2. The removed `markdown_stream_flush_min`
directive (16k minimum output size) is gone. The module uses an
internal heuristic to decide when to flush pending Markdown output.

#### Reserved Directive (not implemented in 0.8.0)

```nginx
# markdown_stream_flush_interval 100ms;
```

`markdown_stream_flush_interval` stays reserved for a future release. This
section defines its semantics for completeness: when set, the module MAY also
flush if the maximum wait time elapses regardless of output size. This
directive MUST NOT appear in documentation as implemented or accepted in
configuration until actually supported. The 0.8.0 release gate does not
validate it.

### 2.5 Relationship to Existing Chunked Configuration

```nginx
markdown_buffer_chunked on;
```

**Default retained**: `on`

In 0.8.0, this directive means: whether chunked responses may enter
the Markdown conversion pipeline.

| `markdown_buffer_chunked` | `markdown_streaming` | Behavior |
|---------------------------|-----------------------------|----------|
| `on`  | `auto` or `force` | Chunked HTML responses may enter true streaming. |
| `on`  | `off`           | Chunked HTML responses use full-buffer conversion. |
| `off` | any             | Chunked responses pass through without conversion. |

This preserves 0.7.x user mental models for chunked behavior while allowing
0.8.0 true streaming to handle qualifying large responses.

### 2.6 Default Error Policy

```nginx
markdown_error_policy pass;
```

**Default retained**: `pass`

Rationale: Markdown is an alternative representation. Conversion failure MUST
NOT compromise the HTML availability of the site.

### 2.7 Hard-Excluded Streaming Content Types

The following content types are unconditionally excluded from streaming
conversion:

- `text/event-stream`
- `application/x-ndjson`
- `application/stream+json`

Users MAY add exclusions via `markdown_stream_excluded_types`. The effective
excluded type set is the union of built-in hard exclusions and user-configured
exclusions. User configuration MUST NOT replace or remove built-in hard
exclusions. These types have inherent continuous-stream semantics incompatible
with HTML-to-Markdown conversion.

```nginx
# User-configured exclusions are additive to built-in hard exclusions.
markdown_stream_excluded_types application/xml;
```

---

## 3. Fallback Semantics

0.8.0 MUST classify fallback into **pre-commit** and **post-commit** phases.
All documentation, logs, metrics, and test cases MUST use this terminology
consistently.

### 3.1 Pre-commit Fallback

**Pre-commit** means the module has NOT yet sent a Markdown response header and
has NOT sent any Markdown body bytes to the client.

Triggers for pre-commit fallback:

- HTML parser determines input is unsuitable for streaming.
- Response matches a hard-excluded content type.
- A feature requiring full-document correctness turns on.
- Decompression budget, parser budget, or stream state initialization fails.

Pre-commit fallback is only available when the raw replay buffer can still cover
all upstream bytes read so far. It also requires that the module has not yet
committed downstream response headers.

If downstream response headers or Markdown body bytes have already been sent,
the response has transitioned to committed state (see §3.4).

If the raw replay buffer can no longer cover all upstream bytes read so far
but the module has not yet committed headers, HTML passthrough fallback is no
longer available. Full-buffer conversion may run only when a complete body
buffer retains every upstream byte read so far and remains within its
configured resource limits. Otherwise the module MUST continue safe streaming
conversion or apply `markdown_error_policy` before committing headers.

#### `markdown_error_policy pass`

- Return the original HTML.
- Replay already-read raw bytes from the pre-commit replay buffer.
- Pass through subsequent upstream body directly.
- Preserve or restore upstream HTML response headers.
- Metric: `streaming_fallback_total{phase="precommit",action="pass"}`

Original-HTML passthrough is available **only while the replay buffer still
covers every upstream byte read so far** (see §3.1). When replay is
incomplete and headers are not yet committed, the module MUST NOT pass
through: it must continue safe streaming conversion or apply
`markdown_error_policy` (reject) before committing headers.

#### `markdown_error_policy fail_closed`

- If headers have not been sent, return 502.
- Metric: `streaming_fallback_total{phase="precommit",action="reject"}`

### 3.2 Full-buffer Fallback

The following scenarios MUST NOT use streaming and MUST automatically switch to
full-buffer conversion, provided the response remains within configured
full-buffer resource limits (for example `markdown_limits conversion_memory=`
and the memory budget):

When the module selects the full-buffer path, its deadline contract follows
[PARSER_BUDGET.md](../features/PARSER_BUDGET.md). At the pre-parse and
post-parse checkpoints, the converter checks `parser_timeout=` before
`conversion_timeout=`. The converter measures the parser deadline from
`conversion_start` for the pre-parse check and from `parse_start` for the
post-parse check. It measures `conversion_timeout=` from `conversion_start`
at both checkpoints. It then uses that overall deadline for traversal and
output. If elapsed time exceeds both deadlines at a shared checkpoint, the
converter reports parser timeout. A zero `conversion_timeout=` leaves a
nonzero `parser_timeout=` active. The two deadlines do not collapse into an
earlier-of deadline.

This ordering is specific to the full-buffer FFI checkpoints. True streaming
uses a wall-clock `conversion_timeout=` and checks it at `feed_chunk` and
`finalize` entry. It accumulates html5ever tokenizer work for
`parser_timeout=` and checks that allowance at tokenizer-slice and
finalization boundaries. Idle time between streaming chunks does not consume
the parser allowance.

- Strict ETag / `If-None-Match` handling that requires computing an ETag from
  the complete Markdown output.
- Front matter fields that depend on full-text scanning to produce stable
  values.
- Converter determines the HTML pattern requires full-document repair to
  maintain Markdown structure.
- Streaming parser encounters input exceeding its look-behind capacity but
  recoverable via full-buffer.
- `markdown_streaming auto` and the module assesses streaming risk
  outweighs benefit.

If the response would exceed the full-buffer **input-size** limit
(`markdown_limits conversion_memory=`) **and the size is
known at header time** (Content-Length present), the eligibility gate
rejects it **before any conversion attempt**: the request is recorded
as `not_eligible` (reason `not_eligible`) and does **not**
increment `nginx_markdown_conversion_attempts_total` nor assign
`engine="full_buffer"`, because conversion never starts. The module
forwards the original response without evaluating `markdown_error_policy`
(prechecked passthrough). Generated-output, transient-working-set, and
`parser_memory` failures are **not** prechecked at header time: they can
only be detected during conversion, so those cases go through
`markdown_error_policy` like any other conversion failure.

When the size is **not known at header time** (chunked or no
Content-Length), the request enters the buffering pipeline and the limit
is enforced by the body filter; exceeding it there counts as an attempted
conversion that failed (increments `conversion_attempts_total` and the
matching failure counter, then applies `markdown_error_policy`).

Full-buffer fallback is **not** an error. It is a normal `auto`-mode decision.

Metric: `nginx_markdown_conversion_attempts_total{engine="full_buffer"}`.
The frozen family has no reason label. The selected engine is the observable
classification for this normal `auto`-mode decision.

### 3.3 Passthrough Fallback

The following scenarios bypass conversion entirely:

- Response is not convertible HTML.
- Response exceeds `markdown_limits conversion_memory=` and current policy does not allow
  streaming to bypass this limit.
- Response is a hard-excluded streaming content type.
- Request did not pass Accept negotiation or UA policy.
- Authenticated request excluded by `markdown_auth_policy deny`.
- Upstream status code is not in the convertible range.

Passthrough preserves the original upstream response body without generating
Markdown.

### 3.4 Post-commit Failure

**Post-commit** means the module has already sent a `text/markdown` response
header OR has already sent Markdown body bytes.

**After commit, the module MUST NOT revert to original HTML.** This is the most
critical semantic boundary in 0.8.0 true streaming.

When an error occurs post-commit:

- `markdown_error_policy pass` can no longer mean "return original HTML".
- The module MUST log the error, update metrics, and end the response in a
  protocol-safe manner.
- If the response can still produce structurally valid Markdown, emit a minimal
  safe closure.
- If the module cannot guarantee Markdown structure, abort output and close the
  response chain. The module aborts rather than emitting invalid output. This keeps the response protocol-safe.
- The module MUST NOT append raw HTML fragments.
- The module MUST NOT mix partial Markdown with raw HTML.
- The module MUST NOT send content conflicting with the already-committed
  `Content-Type`.

**Metrics**:

- `nginx_markdown_streaming_events_total{transition="abort_start",reason="streaming_mid_flight_error"}`
- `nginx_markdown_streaming_events_total{transition="safe_finish_start",reason="converted"}`

**Suggested error log fields**:

```text
phase=postcommit engine=streaming committed=true fallback_available=false reason=<reason_code>
```

### 3.5 Precise Semantics of `markdown_error_policy` Under Streaming

| Phase        | `markdown_error_policy pass`                          | `markdown_error_policy fail_closed`                    |
|--------------|-------------------------------------------------------|---------------------------------------------------------|
| Pre-commit   | Return original HTML                                  | Return 502                                              |
| Full-buffer  | Return original HTML on conversion failure            | Return 502 on conversion failure                        |
| Post-commit  | Cannot return HTML; safe-finish or abort Markdown     | Cannot re-send 502; safe-finish or abort, log failure   |

### 3.6 Header Commit Rules

Before entering true streaming, the header filter MUST complete these decisions:

1. Is the request eligible for conversion?
2. Is the response content type convertible?
3. Is streaming allowed for this response?
4. Does a full-buffer fallback apply instead?
5. Must the module clear `Content-Length` or set it to unknown?
6. Set `Content-Type: text/markdown; charset=utf-8`.
7. Set `Vary: Accept`.
8. Disable or defer headers that depend on the complete body.

If the module selects true streaming:

- The module MUST remove `Content-Length` from the outgoing response headers.
  It MUST also mark the internal response length unknown (for example setting
  `r->headers_out.content_length_n = -1` and clearing the
  `content_length` header where applicable).
- Chunked downstream follows standard NGINX output behavior.
- The module MUST NOT generate output-derived ETag before streaming commit.
- If the module cannot compute the exact Markdown-variant ETag for the
  response, it MUST remove the upstream ETag from the outgoing headers for
  this response. The module MUST NOT weaken the upstream ETag (for example
  by downgrading it to weak semantics) and MUST NOT reuse the upstream HTML
  validator: any retained ETag must derive from the converted Markdown
  bytes, or the validator removes the ETag entirely.

---

## 4. Support Matrix Source

0.8.0 MUST establish a single, machine-readable, CI-verifiable support matrix
source of truth.

**Recommended file**: `tools/release-matrix.json`

If a `packaging/matrix.yaml` already exists in the repository, 0.8.0 MAY retain
it as a packaging input. However, the authoritative external support
declaration MUST come from `tools/release-matrix.json` or validate against it.

Documentation, README, release notes, and workflow matrices MUST NOT contain
hand-maintained, mutually conflicting version support tables.

### 4.1 Required Dimensions

The support matrix MUST cover at minimum:

| Dimension       | Examples                                                      |
|-----------------|---------------------------------------------------------------|
| NGINX version   | stable, oldstable, mainline — each with explicit support tier |
| OS / distro     | Ubuntu, Debian, RHEL-compatible, AlmaLinux, Rocky, Alpine, macOS |
| libc            | glibc, musl                                                   |
| CPU architecture| amd64, arm64                                                  |
| Artifact type   | source build, tar.gz module, DEB, RPM, Docker image, Homebrew, Helm chart |
| Test level      | build-only, unit-tested, integration-tested, e2e-tested, install-verified, release-blocking |
| Support tier    | supported, experimental, best-effort, unsupported             |
| Owner workflow  | The GitHub Actions workflow responsible for verifying the combination |

### 4.2 Support Tier Definitions

| Tier           | Definition |
|----------------|------------|
| **supported**      | CI builds pass. Corresponding artifact can be produced. Install verification passes. `nginx -t` passes. At least one E2E smoke test covers `Accept: text/markdown`. Release gate failure blocks publication. |
| **experimental**   | Build or source path available. No guarantee of complete install packages for every release. CI failure does not block release but MUST be noted in release notes. MUST NOT appear as the default recommended install path in README. |
| **best-effort**    | Users may source-build. No pre-built artifacts committed. Documentation guidance only. Not a release gate. |
| **unsupported**    | No artifacts provided. No compatibility commitment. Related failures are not treated as bugs unless they affect the supported matrix. |

### 4.3 Documentation Generation Rules

The following files MUST NOT contain hand-maintained support matrices:

- `README.md`
- `README_zh-CN.md`
- `docs/guides/PACKAGE_COMPATIBILITY.md`
- `docs/guides/INSTALLATION.md`
- `docs/project/PROJECT_STATUS.md`
- `docs/guides/PACKAGE_DISTRIBUTION.md`
- Release notes template

These files MUST come from `tools/release-matrix.json` or validate against
it via CI.

**Validation command (CI and release checks)**:

```sh
python3 tools/render_release_matrix_docs.py --check
```

Maintainers regenerate the checked-in documents only after changing the source
matrix:

```sh
python3 tools/render_release_matrix_docs.py --write
```

CI MUST run the `--check` mode. If generated output diverges from committed
documentation, CI fails.

### 4.4 Workflow Consumption Rules

GitHub Actions workflows MUST NOT define independent version matrices. Build,
test, release, install verification, Homebrew, Docker, and Helm workflows MUST
read from the same source.

Each workflow MAY consume only a subset, but subset filter conditions MUST be
explicit:

```yaml
# Example filter
filter: artifact_type == "deb" AND support_tier == "supported" AND release_blocking == true
```

### 4.5 Release Notes Rules

Every release MUST auto-generate a support matrix summary containing at minimum:

- NGINX versions supported in this release.
- Artifact types published.
- DEB/RPM coverage by distro and architecture.
- Docker / Helm / Homebrew support tiers.
- Experimental items.
- Unsupported or removed items.
- Additions, downgrades, and removals compared to the previous release.

### 4.6 Acceptance Criteria for 0.8.0

Before 0.8.0 ships, the following MUST hold:

1. The repository contains exactly one support matrix source of truth.
2. README, installation docs, compatibility docs, and release docs are
   consistent with that source.
3. Release workflows and install-verify workflows consume the same matrix.
4. Mainline NGINX, musl/Alpine, Homebrew, Docker, and Helm support tiers are
   explicitly declared.
5. No documentation presents an `experimental` item as `supported`.
6. No release artifact exceeds the matrix declaration scope.
7. No `supported`, `experimental`, or `release-blocking` matrix declaration
   lacks a corresponding verification workflow or explicit non-blocking
   validation policy.

---

## Appendix A: Glossary

| Term | Definition |
|------|------------|
| Committed state | The module has sent `text/markdown` headers or Markdown body bytes downstream. |
| Pre-commit phase | Period before committed state where fallback to HTML is possible. |
| Post-commit phase | Period after committed state where HTML fallback is impossible. |
| Replay buffer | Pre-commit raw-byte buffer enabling HTML fallback. |
| Full-buffer path | 0.7.x-style conversion: buffer entire response, convert, send. |
| True streaming | 0.8.0 incremental conversion per section 1 definition. |
| Passthrough | Response bypasses conversion entirely. |

---

## Appendix B: Related Documents

- `docs/architecture/ADR/0004-streaming-bounded-memory-conversion.md`
- `docs/architecture/ADR/0007-streaming-default.md`
- `docs/architecture/LARGE_RESPONSE_DESIGN.md`
- `docs/guides/streaming-rollout-cookbook.md`
- `docs/features/` (per-feature streaming notes)
- `AGENTS.md` Rules 1, 2, 38 (streaming backpressure and replay buffer)
