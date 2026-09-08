# Pre-LTS lifecycle and FFI invariant mapping

This mapping is the retained-test record for LTS-R018. It covers the
post-convergence request lifecycle after convergence removed the dynamic
configuration subsystem. Each row names the production path that owns the
invariant and at least one test that would fail if the invariant regressed.

| LTS-R018 criterion | Retained production path | Protecting tests and observable assertion |
| --- | --- | --- |
| 1. Headers precede body bytes | [`ngx_http_markdown_streaming_header_filter`](../../components/nginx-module/src/ngx_http_markdown_streaming_impl.h) defers body delivery until the header commit path succeeds. [`ngx_http_markdown_stream_commit_headers`](../../components/nginx-module/src/ngx_http_markdown_stream_commit.c) performs the atomic header transaction. | [`stream_commit_test.c`](../../components/nginx-module/tests/unit/stream_commit_test.c) (`test_commit_success`, `test_commit_vary_failure_no_content_type_leak`) and [`streaming_impl_test.c`](../../components/nginx-module/tests/unit/streaming_impl_test.c) (`test_update_headers_paths`) verify commit ordering and rollback. |
| 2. `NGX_AGAIN` suspends and resumes without duplicate or overwritten data | [`ngx_http_markdown_streaming_send_output`](../../components/nginx-module/src/ngx_http_markdown_streaming_impl.h), [`ngx_http_markdown_streaming_resume_pending`](../../components/nginx-module/src/ngx_http_markdown_streaming_impl.h), and the module-owned pending-input/replay chains preserve ownership across retries. | [`stream_postcommit_test.c`](../../components/nginx-module/tests/unit/stream_postcommit_test.c) (`test_safe_finish_backpressure_preserves_pending_chain`, `test_safe_finish_data_only_pending_continues_to_terminal`, `test_subrequest_terminal_delivery_lifecycle`) checks retained chains, `NGX_AGAIN`, and terminal metadata. [`streaming_impl_test.c`](../../components/nginx-module/tests/unit/streaming_impl_test.c) (`test_send_output_and_resume_paths`) covers module-level resume. |
| 3. `NGX_DONE` is terminal and does not re-enter header/body processing | [`ngx_http_markdown_streaming_finish_terminal`](../../components/nginx-module/src/ngx_http_markdown_streaming_impl.h) and request finalization callers return immediately after terminal completion. | [`stream_postcommit_test.c`](../../components/nginx-module/tests/unit/stream_postcommit_test.c) (`test_safe_finish_then_abort_does_not_double_send`, `test_subrequest_terminal_delivery_lifecycle`) asserts one terminal send and no main-request latch for subrequests. [`streaming_impl_test.c`](../../components/nginx-module/tests/unit/streaming_impl_test.c) (`test_send_output_error_and_deferred_paths`) covers terminal return propagation. |
| 4. Commit/delivery latches are set only after successful downstream delivery | [`ngx_http_markdown_streaming_record_send_delivery`](../../components/nginx-module/src/ngx_http_markdown_streaming_impl.h) and pending-terminal metadata update latches only for `NGX_OK`/`NGX_DONE`. `NGX_AGAIN` remains pending. | [`stream_postcommit_test.c`](../../components/nginx-module/tests/unit/stream_postcommit_test.c) (`test_subrequest_terminal_delivery_lifecycle`, `test_data_only_pending_output_drain`) explicitly asserts that `NGX_AGAIN` leaves terminal latches clear and success sets them. |
| 5. Disconnect/backpressure/subrequest/cleanup releases request-lifetime buffers | [`ngx_http_markdown_streaming_cleanup`](../../components/nginx-module/src/ngx_http_markdown_streaming_impl.h), replay-buffer cleanup, pending-output drain, and post-commit abort paths release module-owned buffers and stop delivery after disconnect. | [`stream_replay_test.c`](../../components/nginx-module/tests/unit/stream_replay_test.c) (`test_cleanup_registration_failure`, `test_append_overflow`) covers replay cleanup/overflow. [`stream_postcommit_test.c`](../../components/nginx-module/tests/unit/stream_postcommit_test.c) (`test_safe_finish_send_terminal_fails`, `test_safe_finish_then_abort_does_not_double_send`) covers abort and cleanup after terminal errors. Native smoke exercises client abort and slow-reader backpressure. |
| 6. Allocation/free, NULL/bounds, and overflow safety | Rust FFI exports in [`ffi/streaming.rs`](../../components/rust-converter/src/ffi/streaming.rs) validate pointers, consume handles exactly once, and route output through `markdown_streaming_output_free`. C replay/decompression helpers use bounded arithmetic and cleanup handlers. | [`streaming.rs` unit tests](../../components/rust-converter/src/ffi/streaming.rs) (`test_streaming_null_options`, `test_safe_finish_null_handle`, `test_safe_finish_null_output_pointers`, `test_streaming_output_memory_management`) cover NULL and ownership rules. [`streaming_replay_overflow_security_test.c`](../../components/nginx-module/tests/unit/streaming_replay_overflow_security_test.c) covers overflow rejection and buffer integrity. |
| 7. FFI NULL/empty inputs, panic containment, ownership, and ABI match | [`markdown_streaming_new_with_code`](../../components/rust-converter/src/ffi/streaming.rs), feed/finalize/safe-finish exports, `panic::catch_unwind`, and generated [`markdown_converter.h`](../../components/rust-converter/include/markdown_converter.h) ABI fingerprints enforce the C↔Rust boundary. | [`ffi_test.rs`](../../components/rust-converter/tests/ffi_test.rs) and [`streaming.rs` unit tests](../../components/rust-converter/src/ffi/streaming.rs) cover NULL/empty arguments, panic/error return codes, and output freeing. `make check-headers` and [`ffi_layout_check_test.c`](../../components/nginx-module/tests/unit/ffi_layout_check_test.c) cover generated-header/layout agreement. |
| 8. Event-site metrics and separate decision/delivery counters | [`ngx_http_markdown_streaming_record_send_delivery`](../../components/nginx-module/src/ngx_http_markdown_streaming_impl.h), [`ngx_http_markdown_metrics_record_*`](../../components/nginx-module/src/ngx_http_markdown_metrics_impl.h), and the v1 renderer record delivery only after downstream success while preserving outcome partitions. | [`metrics_v1_renderer_test.c`](../../components/nginx-module/tests/unit/metrics_v1_renderer_test.c), [`streaming_metrics_increment_test.c`](../../components/nginx-module/tests/unit/streaming_metrics_increment_test.c), and [`metrics_endpoint_test.c`](../../components/nginx-module/tests/unit/metrics_endpoint_test.c) assert event-site increments and non-overlapping families. `PYTHONPATH=. python3 tools/harness/detect_metrics_event_conservation.py` checks the conservation invariant. |

The lifecycle tests are intentionally split across the C filter, replay and
post-commit seams and the Rust FFI seam. A green Rust-only run cannot certify
downstream ownership. A green C-only run cannot certify panic containment.
The mapping therefore requires both suites plus generated-header validation.

## Verification commands

The minimum retained mapping check is:

```text
make check-headers
make -C components/nginx-module/tests unit-streaming unit-streaming_decomp unit-streaming_impl unit-stream_postcommit unit-stream_replay unit-stream_commit
cargo test --manifest-path components/rust-converter/Cargo.toml --locked --features streaming --lib --tests
PYTHONPATH=. python3 tools/harness/detect_metrics_event_conservation.py
```

Native smoke adds the client-observed boundary (HTTP/1.1, compressed and
uncompressed input, backpressure, abort, and truncated members):

```text
bash tools/e2e/verify_chunked_streaming_native_e2e.sh --profile smoke
```

These commands are evidence for the retained invariants. They do not turn the
separate external 72-hour observation requirement into a local pass.
