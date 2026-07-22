---
domain: e2e-runner
rules: [37, 60]
paths:
  - "tools/e2e-harness/**"
  - "tools/e2e/**"
---

## E2E Test Runner

### 37. Rust-first E2E test runner (e2e-harness)

Required:
- New product-level HTTP E2E scenarios must be implemented in
  `tools/e2e-harness/` as Rust scenario modules under
  `src/scenarios/`.  Each scenario must follow the established pattern:
  Reuse_Mode executes actual HTTP tests against a provided NGINX binary;
  Bootstrap_Mode must resolve a runnable runtime directly or through a
  documented bootstrap path while preserving harness-owned scenario
  execution and assertions.
- Adding new Python pytest files under
  `components/nginx-module/tests/e2e/` is forbidden.  That directory is
  classified as Remove in the 0.6.3 test surface audit; no new files may be
  added.
- Adding new independent shell E2E scenario scripts under `tools/e2e/`
  that contain embedded assertion logic is forbidden for product-level HTTP
  behavior.  New scenarios must use the Rust harness.
- Allowed exceptions:
  - Thin shell wrappers in `tools/e2e/` that delegate to
    `e2e-harness scenario <name>` are permitted.
  - Shell scripts for 0.6.3-deferred scenarios (streaming, security, etc.)
    remain on their current paths until migrated in a future release.
  - `tools/e2e/run_e2e_suite.sh` retains its role as the canonical
    `make test-e2e` orchestrator, delegating migrated scenarios to the
    e2e-harness binary.
- `make test-e2e-rust` builds and runs the Rust harness migrated suite.
  `make test-rust` continues to refer only to the Rust converter test suite.
- Every migrated scenario must have a parity entry in
  `docs/project/0.6.3-e2e-parity.md` documenting the shell source,
  Rust scenario, case mapping, and any parity gaps.

Verification:
- `cargo build --manifest-path tools/e2e-harness/Cargo.toml`
- `cargo test --manifest-path tools/e2e-harness/Cargo.toml`
- `cargo fmt --check --manifest-path tools/e2e-harness/Cargo.toml`
- `cargo clippy --manifest-path tools/e2e-harness/Cargo.toml --all-targets --all-features -- -D warnings`
- `make test-e2e-rust`

### 60. E2E config directive consistency — streaming mode must match test intent

Historical issues: PR #188, CI run 29727392197 (startup warning noise from
contradictory `markdown_streaming auto` + `markdown_cache_validation full`).

Required:
- E2E nginx.conf location blocks that use `markdown_cache_validation full`
  must have an explicit `markdown_streaming` directive (`off`, `auto`, or
  `force`).  Do not rely on the implicit default (`auto`) when the location
  also uses `markdown_cache_validation full` — the implicit `auto` plus the
  blocking directive generates a startup warning and obscures the test's
  intent.  Location blocks that do NOT use `markdown_cache_validation full`
  are out of scope for this detector and may rely on the implicit default.
- When a test intends to exercise the **full-buffer** path, use
  `markdown_streaming off`.  Do not use `markdown_streaming auto` combined
  with a blocking directive — this is contradictory configuration that
  generates a startup warning and obscures the test's intent.
- When a test intends to exercise the **streaming** path, use
  `markdown_streaming force` (or `auto` with no blocking directives).
  Ensure the assertions actually verify streaming-specific behavior
  (e.g., chunked transfer encoding, absence of Content-Length, streaming
  metrics).
- The one exception: tests that **intentionally validate the runtime-block
  mechanism** (that is, verifying that `auto` + `full` correctly falls back
  to full-buffer) may use the contradictory combination, but must document
  this intent in a comment and assert streaming-block-specific indicators
  (e.g., ETag presence, Content-Length, full-buffer metric delta).
- `detect_e2e_streaming_config.py` provides advisory detection of implicit
  `auto` + `markdown_cache_validation full` combinations, and the
  `auto + full` contradiction.  It is block-aware (comment-masked brace
  parsing, direct-depth directive extraction, fail-closed scan errors).
  Location block headers are parsed by a deterministic character scanner
  (`_scan_location_headers`, `_parse_location_header`, `_read_location_path`)
  instead of a regex, supporting quoted regex locations (with spaces),
  regex quantifiers `{m,n}`, and escaped quotes.  Strict UTF-8 file reading
  rejects encoding errors (produces ScanError).  Overall config structure
  is validated (`_validate_config_structure`) to detect malformed configs
  before location analysis.

Detector contract:
- Comments containing `{` or `}` are masked before brace parsing so they
  cannot inflate location depth or satisfy a parent location's directive.
- A parent location's directive check only considers its direct-depth
  directives; nested ``location`` sub-blocks do not satisfy the parent and
  are checked independently.
- Read failures (OSError), malformed heredocs, and unmatched braces surface
  as scan errors.  In `--strict` mode they cause a non-zero exit.  The
  detector never silently swallows read/parse errors with "no findings".

Rationale:
- Contradictory configs produce NGINX startup warnings that pollute CI output
  and mask real problems.
- Tests that claim to validate streaming but actually run full-buffer (or
  vice versa) give false confidence and cannot detect regressions in the
  intended code path.
- Explicit directives make test intent self-documenting.

Naming clarity:
- E2E location path names must clearly communicate the test's behavioral
  intent.  Do not use path names that suggest one processing mode when the
  config uses another (for example `/stream/` with `markdown_streaming off`).
- Preferred naming conventions:
  - `/streaming/`, `/streaming-*` — streaming path (`force` or profile
    `streaming_first`)
  - `/buffered/`, `/fullbuffer/`, `/cache-full/`, `/non-streaming/` —
    full-buffer path (`markdown_streaming off`)
  - `/passthrough/` — no conversion
- Variables and fixture names should similarly reflect intent: use names
  like `SMALL_END_TOKEN`, `OVERSIZE_LEN` over ambiguous short names.
- When renaming a location path, update all references (curl URLs, readiness
  probes, assertion labels, comments) in the same changeset.

Verification:
- `python3 tools/harness/detect_e2e_streaming_config.py`
- `python3 tools/harness/detect_e2e_streaming_config.py --strict` (CI-blocking mode)
- `python3 -m pytest tools/harness/tests/test_detect_e2e_streaming_config.py -q`
- `bash tools/harness/tests/test_detect_e2e_streaming_config.sh`
- Visual inspection: every location block with `markdown_cache_validation full`
  must have either `markdown_streaming off` or a comment explaining why `auto`
  is intentional.
- Visual inspection: location path names match the declared `markdown_streaming`
  mode.
