# FFI Migration Contract — Historical v0.9.1 / Final v0.9.2

## Purpose and ownership

This document inventories the coordinated Rust↔C boundary after the v0.9.2
release post-freeze. Rust owns conversion and pure decision logic. C owns
NGINX lifecycle, pools, chains, filters, headers, and request finalization.

This is a bundled internal ABI, not an external converter SDK. The canonical
declarations are Rust source plus the generated header. The NGINX module is
the supported consumer.

## Historical ABI identity

The v0.9.1 baseline identifier was `MARKDOWN_ABI_VERSION = 1`. The current
0.9.2 bundled boundary uses ABI version 2. See
[FFI_ABI_COMPATIBILITY.md](FFI_ABI_COMPATIBILITY.md) for the active values.
`markdown_abi_version()` returns the linked Rust value. NGINX checks it during
preconfiguration and refuses directive parsing and startup on mismatch. Cargo
package version is
release metadata and is not a substitute for this ABI identifier.

## Production FFI registry

The following families have current NGINX production consumers:

| Family | Exports | Contract |
|--------|---------|----------|
| Converter | `markdown_converter_new`, `markdown_convert`, `markdown_result_free`, `markdown_converter_free` | Per-worker handle and full-buffer conversion |
| Options/results | `markdown_options_init`, `markdown_result_init` | Complete semantic initialization |
| Accept | `markdown_negotiate_accept` | Rust-owned RFC negotiation |
| Eligibility | `markdown_decide_eligibility` | Pure eligibility decision |
| Conditional | `markdown_decide_conditional` | Cache mode, precedence, and bypass decision |
| Header plan | `markdown_build_header_plan`, `markdown_header_plan_init`, `markdown_header_plan_free` | Rust-owned atomic plan, C application |
| Base URL | `markdown_trusted_proxies_new`, `markdown_trusted_proxies_push`, `markdown_trusted_proxies_free`, `markdown_decide_base_url` | CIDR-aware trusted forwarding decision |
| Initialization helpers | `markdown_base_url_input_init` | Base URL input initialization for trusted-proxy decision |
| Decompression | `markdown_decompress_bounded`, `markdown_decomp_result_init`, `markdown_decompress_free` | Bounded Rust decompression path |
| Error/reason | `markdown_classify_error_code`, `markdown_reason_code_str`, `markdown_reason_code_metric_key`, `markdown_reason_code_count` | Canonical cross-language classification and labels |
| Streaming | `markdown_streaming_new_with_code`, `markdown_streaming_feed`, `markdown_streaming_finalize`, `markdown_streaming_abort`, `markdown_streaming_safe_finish`, `markdown_streaming_output_free` | Streaming request lifecycle |
| ABI alignment | `markdown_abi_version`, `markdown_abi_header_hash`, `markdown_abi_symbol_set_hash`, `markdown_abi_layout_fingerprint` | Startup-enforced version, generated-header, exported-symbol, and layout match |
| Dynamic configuration | `markdown_dynconf_parse`, `markdown_dynconf_result_init`, `markdown_dynconf_result_free` | Runtime dynconf parsing and result handling |
| Encoding chain and hash helpers | `markdown_chain_decode_free`, `markdown_chain_decode_result_init`, `markdown_decode_encoding_chain`, `markdown_parse_encoding_chain`, `markdown_sha256_hex` | Bounded decompression chain decode and SHA-256 hex helpers |

The generated header contains only the bundled production boundary. Test-only
Rust helpers are not emitted as C declarations.

The canonical reason-code source is
`components/rust-converter/reason_registry.toml`. The generated
`components/rust-converter/src/decision/reason_code.rs` is a projection. The
`markdown_reason_code_str`, `markdown_reason_code_metric_key`, and
`markdown_reason_code_count` exports expose that source to the C consumer.
Diagnostics use the generated C reason metadata header for bounded lookup of
canonical names. Reason-code variants and discriminants must
remain synchronized across both generated projections and this FFI boundary.

## Historical v0.9.1 removals

| Removed entry | Evidence | Replacement |
|---------------|----------|-------------|
| `MarkdownFlavor::Mdx` / FFI flavor `2` | Selector produced no independent output semantics | `0` CommonMark or `1` GFM |
| `MarkdownFlavor::OrgMode` / FFI flavor `3` | Selector produced no independent output semantics | `0` CommonMark or `1` GFM |
| `FFIStreamingInput.engine` and Rust `StreamingEngine` | Duplicated the sole public streaming policy; no independent backend behavior | `FFIStreamingInput.policy` (`off`, `auto`, `force`) |
| `FFIConditionalResult` | Only served the old primitive conditional helper; `matched_etag_len` was reserved and always zero | `FFIConditionalDecision` |
| `markdown_check_conditional` | No production C consumer; superseded by complete mode/precedence/bypass API | `markdown_decide_conditional` |
| `markdown_conditional_result_init` | Its result type was removed | Initialize/use `FFIConditionalDecision` through its owning path |
| `markdown_build_base_url` | No production C consumer; lacked trusted-proxy/source decision context | `markdown_decide_base_url` |
| `markdown_accept_result_init`, `markdown_decision_result_init` | No production caller; decision result type was unused | Owning C path initializes active outputs |
| `markdown_make_decision`, `markdown_decide_streaming` | No production caller; C runtime owns the actual request/streaming decision | Production C decision paths |
| `markdown_decide_error_behavior`, `markdown_error_to_reason_code` | No production caller | C error policy plus active reason accessors |
| `markdown_validate_url`, `markdown_is_dangerous_url` | No production caller | Rust converter's internal URL validation |
| `markdown_get_diagnostics_schema`, `markdown_free_diagnostics` | Separate Rust specimen drifted from the C endpoint | C diagnostics renderer and schema document |
| `markdown_incremental_new`, `markdown_streaming_new` | Redundant wrappers hid constructor error codes | Corresponding `_new_with_code` exports |
| `markdown_streaming_finish`, `markdown_streaming_free`, `markdown_streaming_reason` | No production caller; duplicated finalize/abort/error-code paths | `finalize`, `abort`, `safe_finish`, and return codes |

## Final v0.9.2 removals

| Removed entry | Evidence | Replacement |
|---------------|----------|-------------|
| Profile/conflict FFI snapshots and `markdown_*conflicts` | No production C consumer; the pre-v1 profile model was not part of the active request boundary | C owns the active merged configuration, while Rust exposes only production APIs consumed by C — explicitly including dynamic-configuration parsing and request-path decisions |

## Shared struct policy

### `MarkdownOptions`

`MarkdownOptions` is the single option layout shared by full-buffer and
streaming FFI entry points. Both flavor fields accept only 0 (CommonMark) and
1 (GFM) in the final v0.9.2 contract.

### Results and handles

Result pointer fields are Rust-owned until their matching free function.
The documented finalizer/free operation consumes each opaque converter,
streaming, header-plan, and trusted-proxy handle. C must not use a handle or
borrowed pointer after consumption.

## Initialization contract

Shared FFI structs with semantic defaults use their matching init helper rather
than a partial literal or caller-side `memset`. The active helpers include:

- `markdown_options_init`
- `markdown_result_init`
- `markdown_header_plan_init`
- `markdown_decomp_result_init`

Tests may use a single centralized helper that calls the production initializer.
Adding a field requires an ABI version increment plus updates to the
initializer, reset/free path, Rust layout test, C layout assertion, and all
semantic consumers in the same change. The only future exception would be an
explicitly adopted and validated size-tagged struct protocol.

## Error and panic contract

Rust defines error constants and emits them to the header. C classification
must cover every code in the relevant category. Non-trivial exports catch Rust
panics. Output structs are fail-safe before the catch and committed only after
success. Cleanup helpers also catch panics so unwinding never crosses C.

Both sides of the boundary validate NULL and empty inputs independently at
the boundary. Empty output buffers appear as `NULL`/0. No C allocator may
free Rust-owned memory.

## v1 freeze

After the v0.9.2 release post-freeze, existing layouts, discriminants,
ownership rules, and export signatures are frozen for the bundled v1 contract.
Prefer new structs or
exports for additive work. Any permitted incompatible change increments
`MARKDOWN_ABI_VERSION`, updates both halves atomically, adds mismatch and layout
tests, and the release notes call it out as breaking behavior.

The project can promise an external third-party ABI only through a separate decision
that publishes a standalone SDK/library, support matrix, symbol/versioning
policy, and conformance suite. Until then, third-party consumers must not infer
support from the generated header.

## Required verification

```bash
cargo fmt --all -- --check
cargo check --locked --all-targets --all-features
make test-rust
make check-headers
make test-nginx-unit
bash tools/harness/detect_ffi_panic_safety.sh --strict
bash tools/doctor/tests/test_doctor_config.sh
make docs-check
```
