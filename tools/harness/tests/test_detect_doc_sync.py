"""Regression tests for documentation and public config contract drift."""

from __future__ import annotations

from pathlib import Path

from tools.harness import detect_doc_sync as detector


def _write(root: Path, relative_path: Path | str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_valid_fixture(root: Path) -> None:
    _write(
        root,
        detector.DIRECTIVES_PATH,
        """
static ngx_command_t commands[] = {
    {
        ngx_string("markdown_streaming"),
        NGX_CONF_TAKE1,
        ngx_http_markdown_streaming,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },
    {
        ngx_string("markdown_streaming_engine"),
        NGX_CONF_TAKE1,
        ngx_http_markdown_reject_streaming_engine,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },
};
""",
    )
    _write(
        root,
        detector.HANDLERS_PATH,
        """
static char *
ngx_http_markdown_flavor(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    static u_char cm_str[] = "commonmark";
    static u_char gfm_str[] = "gfm";
    static u_char mdx_str[] = "mdx";
    static u_char org_str[] = "org-mode";
    if (ngx_http_markdown_arg_equals(&value[1], cm_str, sizeof(cm_str))) {
        mcf->flavor = NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK;
    } else if (ngx_http_markdown_arg_equals(&value[1], gfm_str, sizeof(gfm_str))) {
        mcf->flavor = NGX_HTTP_MARKDOWN_FLAVOR_GFM;
    } else if (ngx_http_markdown_arg_equals(&value[1], mdx_str, sizeof(mdx_str))
               || ngx_http_markdown_arg_equals(&value[1], org_str, sizeof(org_str))) {
        log_error("never had distinct conversion semantics");
        return NGX_CONF_ERROR;
    }
    return NGX_CONF_OK;
}
""",
    )
    _write(
        root,
        detector.CHART_TEMPLATE_PATH,
        "markdown_streaming {{ .Values.markdown.streaming.mode }};\n",
    )
    _write(
        root,
        detector.CHART_VALUES_PATH,
        'markdown:\n  streaming:\n    mode: "auto"\n',
    )
    _write(
        root,
        detector.CONFIGURATION_GUIDE_PATH,
        """
| `markdown_streaming_engine off;` | `markdown_streaming off;` |
| `markdown_streaming_engine auto;` | `markdown_streaming auto;` |
| `markdown_streaming_engine on;` | `markdown_streaming force;` |
""",
    )


def test_public_config_contract_accepts_canonical_fixture(tmp_path: Path) -> None:
    _write_valid_fixture(tmp_path)

    assert detector.check_public_config_contract(tmp_path) == []


def test_reject_only_directive_cannot_bind_enum_slot(tmp_path: Path) -> None:
    _write_valid_fixture(tmp_path)
    path = tmp_path / detector.DIRECTIVES_PATH
    content = path.read_text(encoding="utf-8").replace(
        "ngx_http_markdown_reject_streaming_engine,\n"
        "        NGX_HTTP_LOC_CONF_OFFSET,\n        0,\n        NULL",
        "ngx_conf_set_enum_slot,\n        NGX_HTTP_LOC_CONF_OFFSET,\n"
        "        offsetof(conf_t, stream_engine),\n        stream_engine_values",
    )
    path.write_text(content, encoding="utf-8")

    errors = detector.check_public_config_contract(tmp_path)

    assert any("must bind only" in error for error in errors)
    assert any("must not bind an enum table" in error for error in errors)


def test_removed_production_symbol_is_blocked_in_untracked_file(tmp_path: Path) -> None:
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path,
        "components/rust-converter/src/new_config.rs",
        "let selected = stream.engine;\n",
    )

    errors = detector.check_public_config_contract(tmp_path)

    assert any("new_config.rs" in error and "production symbol" in error for error in errors)


def test_chart_legacy_engine_key_is_blocked(tmp_path: Path) -> None:
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path,
        detector.CHART_TEMPLATE_PATH,
        "markdown_streaming_engine {{ .Values.markdown.streaming.engine }};\n",
    )

    errors = detector.check_public_config_contract(tmp_path)

    assert any("must emit markdown_streaming" in error for error in errors)
    assert any("legacy streaming engine key" in error for error in errors)


def test_active_example_cannot_use_removed_directive(tmp_path: Path) -> None:
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path,
        "examples/production/new.conf",
        "markdown_streaming_engine auto;\n",
    )

    errors = detector.check_public_config_contract(tmp_path)

    assert any("new.conf" in error and "active config surface" in error for error in errors)


def test_flavor_handler_cannot_reactivate_mdx(tmp_path: Path) -> None:
    _write_valid_fixture(tmp_path)
    path = tmp_path / detector.HANDLERS_PATH
    content = path.read_text(encoding="utf-8").replace(
        "return NGX_CONF_ERROR;",
        "mcf->flavor = NGX_HTTP_MARKDOWN_FLAVOR_MDX;",
    )
    path.write_text(content, encoding="utf-8")

    errors = detector.check_public_config_contract(tmp_path)

    assert any("active assignments must be exactly" in error for error in errors)
    assert any("explicitly reject both mdx" in error for error in errors)


def test_configuration_guide_requires_exact_on_to_force_mapping(tmp_path: Path) -> None:
    _write_valid_fixture(tmp_path)
    path = tmp_path / detector.CONFIGURATION_GUIDE_PATH
    content = path.read_text(encoding="utf-8").replace(
        "markdown_streaming force;", "markdown_streaming on;"
    )
    path.write_text(content, encoding="utf-8")

    errors = detector.check_public_config_contract(tmp_path)

    assert any("on -> markdown_streaming force" in error for error in errors)
