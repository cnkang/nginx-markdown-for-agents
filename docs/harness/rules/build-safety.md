---
domain: build-safety
rules: [56, 57, 58, 59]
paths:
  - "components/nginx-module/src/**"
  - "components/rust-converter/src/**"
  - ".github/workflows/**"
  - "tools/**"
---

# Build Safety Rules (56–59)

## Rule 56: Orphan Comment Closers

**Principle**: Every `*/` in C source must have a matching `/*`. A missing
comment opening — typically lost during an edit — leaves a bare `*/` that
causes a C syntax error and blocks all compilation. The pattern occurs when
an agent or developer edits a comment block and accidentally drops the
opening `/*` while preserving the closing `*/`.

**Historical issue**: `fbe08c6f` — the comment block before
`ngx_http_markdown_streaming_decomp_create` lost its `/*` opening during the
header-sniffing edit, leaving a bare `*/` that blocked all C module
compilation.

**Detection**: `python3 tools/harness/detect_orphan_comment_close.py`
- Scans C source files (*.c, *.h) character by character
- Tracks block comment state, string literals, and line comments
- Flags any `*/` found outside a block comment as an orphan
- String literals containing `*/` are correctly skipped

**Verification**: `python3 tools/harness/detect_orphan_comment_close.py`

## Rule 57: #ifdef-Guarded Function Visibility

**Principle**: Functions declared inside `#ifdef FEATURE_GUARD` blocks must
not reference it outside that guard. When both builds need a function
(feature-enabled and feature-disabled), declare it outside the
`#ifdef` guard. This catches the common mistake of adding a function
declaration inside an `#ifdef` but forgetting to move it outside when the
code references the function from non-feature-gated paths.

**Historical issue**: `a29d1a7b` — `ngx_http_markdown_reason_streaming_skip_compressed`
the function sat inside `#ifdef MARKDOWN_STREAMING_ENABLED` but code referenced it
from non-streaming code paths. The team had to move the function outside the
`#ifdef` guard so it remains available in feature-disabled builds.

**Detection**: `bash tools/harness/detect_ifdef_guard_visibility.sh`
- Parses the header file to find function identifiers declared inside
  `#ifdef MARKDOWN_STREAMING_ENABLED` blocks
- For each guarded function, searches all .c and .h files for references
  that appear outside the `#ifdef` guard
- Flags any reference found outside the guard as a visibility gap

**Verification**: `bash tools/harness/detect_ifdef_guard_visibility.sh`

## Rule 58: Workflow Input Injection

**Principle**: Direct interpolation of GitHub Actions inputs (`${{ inputs.* }}`)
into shell `run:` blocks allows command injection via crafted input values.
Inputs must route through environment variables (`env:`) and reference
only as env vars in shell scripts. This prevents command substitution attacks
where a malicious input value contains shell metacharacters.

**Historical issue**: `d0d5730c` — the workflow interpolated `inputs.version` directly
into shell `run:` blocks before regex validation, allowing command substitution
in the signing environment. Also found in `release-rpm.yml` during harness
analysis.

**Detection**: `bash tools/harness/detect_workflow_input_injection.sh`
- Scans GitHub Actions workflow YAML files
- Tracks `run:` and `env:` block context
- Flags `${{ inputs.* }}` or `${{ github.event.inputs.* }}` used inside
  `run:` blocks without env routing
- Allows the same expressions inside `env:` blocks (safe pattern)

**Verification**: `bash tools/harness/detect_workflow_input_injection.sh`

## Rule 59: Hardcoded HTTP Status in Reject Paths

**Principle**: Reject/error paths that return a hardcoded HTTP status code
(`NGX_HTTP_BAD_GATEWAY`) instead of the operator-configured
`conf->error_status` silently ignore the user's `error_policy` configuration.
All reject paths must return `conf->error_status` so clients receive the
configured rejection code.

**Historical issue**: `d9d508f9` — Full-buffer reject_or_fail_open and
streaming pre-commit reject paths returned `NGX_HTTP_BAD_GATEWAY` (502)
instead of `conf->error_status`, ignoring the operator-configured rejection
code (429/503). The fix returned `conf->error_status` in all reject paths.

**Detection**: `bash tools/harness/detect_hardcoded_http_status.sh`
- Scans C source files containing reject/error handling code
- Flags `return NGX_HTTP_BAD_GATEWAY` in reject/error context
- Skips lines containing `conf->error_status` (correct pattern)
- Advisory mode by default; `--strict` for blocking

**Verification**: `bash tools/harness/detect_hardcoded_http_status.sh`