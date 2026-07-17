"""Regression tests for the v0.9.0 release-gate validator."""

from tools.release.gates import validate_release_gates_090 as validator


def test_production_examples_reject_default_gzip_type_redeclaration(tmp_path):
    """NGINX warns when gzip_types redundantly lists its text/html default."""
    examples = tmp_path / "examples/production"
    examples.mkdir(parents=True)
    for index in range(4):
        content = "gzip_types text/markdown;\n"
        if index == 2:
            content = "gzip_types text/markdown text/html;\n"
        (examples / f"example-{index}.conf").write_text(content)

    result = validator.check_production_examples(tmp_path)

    assert result["name"] == "production_examples"
    assert result["status"] == "fail"
    assert "text/html" in result["message"]


def test_diagnostics_schema_gate_rejects_docs_only_contract(tmp_path):
    """Documentation cannot substitute for the production C emission."""
    docs = tmp_path / "docs/architecture/observability-schema-v1.md"
    renderer = (
        tmp_path
        / "components/nginx-module/src/ngx_http_markdown_diagnostics.c"
    )
    docs.parent.mkdir(parents=True)
    renderer.parent.mkdir(parents=True)
    docs.write_text("`schema_version` is the integer `1`.\n")
    renderer.write_text(
        r'p = ngx_slprintf(p, last, "  \"schema_version\": 2,\n");'
        "\n"
    )

    result = validator.check_diagnostics_schema_version(tmp_path)

    assert result["name"] == "diagnostics_schema_v1"
    assert result["status"] == "fail"
    assert "production C renderer" in result["message"]


def test_no_stale_symbols_gate_passes_without_diagnostics(monkeypatch, tmp_path):
    """A clean stale-symbol scan should map to a passing gate result."""
    monkeypatch.setattr(
        validator,
        "run_stale_symbol_check",
        lambda repo: (0, "No stale 0.8 symbols found.", ""),
    )

    result = validator.check_no_stale_symbols(tmp_path)

    assert result == {
        "name": "no_stale_symbols",
        "status": "pass",
        "message": "",
    }


def test_no_stale_symbols_gate_reports_tail_of_stdout_and_stderr(
    monkeypatch, tmp_path
):
    """Failure diagnostics should include recent stdout and stderr lines."""
    stdout = "\n".join([f"finding-{i}" for i in range(1, 8)])
    stderr = "read-error"
    monkeypatch.setattr(
        validator,
        "run_stale_symbol_check",
        lambda repo: (1, stdout, stderr),
    )

    result = validator.check_no_stale_symbols(tmp_path)

    assert result["name"] == "no_stale_symbols"
    assert result["status"] == "fail"
    assert result["message"] == "\n".join(
        ["finding-4", "finding-5", "finding-6", "finding-7", "read-error"]
    )
