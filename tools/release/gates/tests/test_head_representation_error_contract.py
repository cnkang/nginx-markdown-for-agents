"""Regression contract for HEAD representation rollback failures."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
REQUEST_IMPL = (
    REPO_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_request_impl.h"
)
STREAMING_IMPL = (
    REPO_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_streaming_impl.h"
)


def _head_handler_source() -> str:
    source = REQUEST_IMPL.read_text(encoding="utf-8")
    start = source.index("ngx_http_markdown_body_filter_handle_head(")
    end = source.index("\n\n\n/*\n * Pass-through path", start)
    return source[start:end]


def test_head_snapshot_rollback_failure_is_terminal_system_failure() -> None:
    """HEAD must not expose the internal rollback sentinel to NGINX."""
    handler = _head_handler_source()

    assert "const ngx_http_markdown_conf_t *conf" in handler
    assert "NGX_HTTP_MARKDOWN_HEADER_SNAPSHOT_RESTORE_FAILED" in handler
    assert "ngx_http_markdown_record_system_failure(ctx)" in handler
    assert "NGX_HTTP_MARKDOWN_ERROR_SYSTEM" in handler
    assert "ngx_http_filter_finalize_request" in handler
    assert "ngx_http_markdown_effective_error_status" in handler
    assert "ngx_http_markdown_inflight_release(ctx)" in handler


def test_head_body_filter_passes_effective_configuration_to_handler() -> None:
    """The handler needs effective policy/status configuration for rollback."""
    source = REQUEST_IMPL.read_text(encoding="utf-8")

    assert "ngx_http_markdown_body_filter_handle_head(r, ctx, conf)" in source


def test_streaming_snapshot_rollback_failure_bypasses_fallback() -> None:
    """Streaming commit must fail closed on an unverifiable rollback."""
    source = STREAMING_IMPL.read_text(encoding="utf-8")
    start = source.index(
        "static ngx_int_t\nngx_http_markdown_streaming_commit("
    )
    end = source.index(
        "\n\n\nstatic void\nngx_http_markdown_streaming_add_output_bytes",
        start,
    )
    handler = source[start:end]

    sentinel = handler.index(
        "NGX_HTTP_MARKDOWN_HEADER_SNAPSHOT_RESTORE_FAILED"
    )
    terminal = handler.index(
        "ngx_http_markdown_streaming_handle_header_snapshot_failure",
        sentinel,
    )
    fallback = handler.index("ERROR_STREAMING_FALLBACK", sentinel)

    assert terminal < fallback


def test_streaming_snapshot_failure_handler_is_system_terminal() -> None:
    """The shared handler records system failure and uses configured status."""
    source = REQUEST_IMPL.read_text(encoding="utf-8")
    marker = "/*\n * A streaming header rollback failure"
    start = source.index(marker)
    end = source.index("\n\n#define NGX_HTTP_MARKDOWN", start)
    handler = source[start:end]

    assert "markdown_streaming_abort" in handler
    assert "ngx_http_markdown_record_system_failure(ctx)" in handler
    assert "NGX_HTTP_MARKDOWN_ERROR_SYSTEM" in handler
    assert "ngx_http_filter_finalize_request" in handler
    assert "ngx_http_markdown_effective_error_status" in handler
    assert "ngx_http_markdown_inflight_release(ctx)" in handler
