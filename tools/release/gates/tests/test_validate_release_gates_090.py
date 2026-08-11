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


def test_diagnostics_schema_gate_finds_schema_key_after_other_json_keys(tmp_path):
    """The schema marker may occur anywhere in the emitted JSON object."""
    docs = tmp_path / "docs/architecture/observability-schema-v1.md"
    renderer = (
        tmp_path
        / "components/nginx-module/src/ngx_http_markdown_diagnostics.c"
    )
    docs.parent.mkdir(parents=True)
    renderer.parent.mkdir(parents=True)
    docs.write_text("`schema_version` is the integer `1`.\n")
    renderer.write_text(
        r'p = ngx_slprintf(p, last, "{\"product_version\":\"0.9.2\",'
        r'\"schema_version\":1}");' + "\n"
    )

    result = validator.check_diagnostics_schema_version(tmp_path)

    assert result == {
        "name": "diagnostics_schema_v1",
        "status": "pass",
    }


def test_diagnostics_schema_gate_accepts_active_v2_contract(tmp_path):
    """The historical gate must accept the active 0.9.2 schema version."""
    docs = tmp_path / "docs/architecture/observability-schema-v1.md"
    renderer = (
        tmp_path
        / "components/nginx-module/src/ngx_http_markdown_diagnostics.c"
    )
    docs.parent.mkdir(parents=True)
    renderer.parent.mkdir(parents=True)
    docs.write_text("`schema_version`: integer constant `2`.\n")
    renderer.write_text(
        r'p = ngx_slprintf(p, last, "{\"schema_version\":2,\"product_version\":\"0.9.2\"}");'
        "\n"
    )

    result = validator.check_diagnostics_schema_version(tmp_path)

    assert result == {
        "name": "diagnostics_schema_v1",
        "status": "pass",
    }


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


def test_inflight_guard_accepts_effective_error_status(tmp_path):
    """The 0.9.2 runtime policy must satisfy the legacy regression gate."""
    inflight = (
        tmp_path
        / "components/nginx-module/src/ngx_http_markdown_inflight_impl.h"
    )
    request_impl = (
        tmp_path
        / "components/nginx-module/src/ngx_http_markdown_request_impl.h"
    )
    inflight.parent.mkdir(parents=True)
    request_impl.parent.mkdir(parents=True, exist_ok=True)
    inflight.write_text("/* inflight guard */\n")
    request_impl.write_text(
        "return ngx_http_markdown_effective_error_status("
        "ctx->effective_conf, conf);\n"
    )

    result = validator.check_inflight_guard(tmp_path)

    assert result == {"name": "inflight_guard", "status": "pass"}


def test_removed_directive_gate_expands_canonical_name_macros(tmp_path):
    """Removed-directive checks must inspect macro-backed command entries."""
    directives = tmp_path / "components/nginx-module/src"
    directives.mkdir(parents=True)
    (directives / "ngx_http_markdown_config_directives_impl.h").write_text(
        "static ngx_command_t ngx_http_markdown_filter_commands[] = {\n"
        "    ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_REMOVED),\n"
        "};\n"
    )
    (directives / "ngx_http_markdown_directive_names.h").write_text(
        '#define NGX_HTTP_MARKDOWN_DIRECTIVE_REMOVED \\\n'
        '    "markdown_max_size"\n'
    )
    migration = tmp_path / "docs/guides/MIGRATION-0.9.md"
    migration.parent.mkdir(parents=True)
    migration.write_text("markdown_memory_budget\n")

    result = validator.check_config_v2_removed_directives(tmp_path)

    assert result["status"] == "fail"
    assert "markdown_max_size" in result["message"]
