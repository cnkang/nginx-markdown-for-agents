"""Pytest tests for detect_metrics_event_conservation.py.

Validates the v1 terminal-outcome conservation audit:
- clean renderer passes with zero findings
- stale aborted source (streaming_failure_postcommit_abort) is a violation
- failed_closed derivation missing failopen_count deduction is a violation
- missing renderer is a hard violation
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import detect_metrics_event_conservation as module

CLEAN_RENDERER = """\
ngx_int_t
ngx_http_markdown_metrics_to_v1(ngx_http_markdown_metrics_snapshot_t *snapshot,
    ngx_http_markdown_metrics_v1_t *v1)
{
    ngx_atomic_uint_t  failed_closed;

    v1->requests.failed_open = snapshot->results.failopen_count;
#ifdef MARKDOWN_STREAMING_ENABLED
    failed_closed = snapshot->conversions_failed
        >= snapshot->results.failopen_count
        ? snapshot->conversions_failed - snapshot->results.failopen_count
        : 0;
    failed_closed = failed_closed
        >= snapshot->streaming.terminal_aborted_total
        ? failed_closed - snapshot->streaming.terminal_aborted_total
        : 0;
    v1->requests.aborted =
        snapshot->streaming.terminal_aborted_total;
#else
    failed_closed = snapshot->conversions_failed >= snapshot->results.failopen_count
        ? snapshot->conversions_failed - snapshot->results.failopen_count : 0;
    v1->requests.aborted = 0;
#endif
    v1->requests.failed_closed = failed_closed;
    return NGX_OK;
}
"""

STALE_ABORT_RENDERER = CLEAN_RENDERER.replace(
    "v1->requests.aborted =\n        snapshot->streaming.terminal_aborted_total;",
    "v1->requests.aborted =\n        snapshot->streaming.streaming_failure_postcommit_abort;",
)

MISSING_FAILOPEN_RENDERER = CLEAN_RENDERER.replace(
    """    failed_closed = snapshot->conversions_failed
        >= snapshot->results.failopen_count
        ? snapshot->conversions_failed - snapshot->results.failopen_count
        : 0;""",
    """    failed_closed = snapshot->conversions_failed
        >= 0
        ? snapshot->conversions_failed
        : 0;""",
).replace(
    "failed_closed = snapshot->conversions_failed >= snapshot->results.failopen_count\n"
    "        ? snapshot->conversions_failed - snapshot->results.failopen_count : 0;",
    "failed_closed = snapshot->conversions_failed >= 0\n"
    "        ? snapshot->conversions_failed : 0;",
)


def _audit_text(content: str, tmp_path):
    """Audit a fixture file created beneath the per-test tmp_path directory
    instead of a shared tools path, so concurrent runs cannot clobber the
    fixture and cleanup is scoped to the generated temporary file."""
    path = tmp_path / "_fixture_metrics_conservation.h"
    path.write_text(content, encoding="utf-8")
    try:
        return module.audit(path)
    finally:
        path.unlink(missing_ok=True)


def test_clean_renderer_has_no_violations(tmp_path) -> None:
    violations, reviews = _audit_text(CLEAN_RENDERER, tmp_path)
    assert violations == []
    assert reviews == []


def test_stale_aborted_source_is_violation(tmp_path) -> None:
    violations, _ = _audit_text(STALE_ABORT_RENDERER, tmp_path)
    assert any(
        "streaming_failure_postcommit_abort" in v for v in violations
    )


def test_missing_failopen_deduction_is_violation(tmp_path) -> None:
    violations, _ = _audit_text(MISSING_FAILOPEN_RENDERER, tmp_path)
    assert any("failopen_count" in v for v in violations)


def test_missing_renderer_is_hard_violation(tmp_path) -> None:
    violations, _ = _audit_text("/* no renderer here */\n", tmp_path)
    assert len(violations) >= 1


def test_missing_input_path_is_an_operational_error(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "missing-metrics.h"
    monkeypatch.setattr("sys.argv", ["detect_metrics_event_conservation", str(missing)])

    assert module.main() == 2


def test_comment_blanking_preserves_newline_structure() -> None:
    source = "before /* first line\n second line */ after\n// line comment\nend\n"
    blanked = module._strip_c_comments(source)

    assert len(blanked) == len(source)
    assert blanked.count("\n") == source.count("\n")
    assert blanked.splitlines()[0].startswith("before ")
    assert blanked.splitlines()[1].endswith(" after")
    assert blanked.splitlines()[-1] == "end"
    assert "first" not in blanked
    assert "second" not in blanked
    assert "line comment" not in blanked
