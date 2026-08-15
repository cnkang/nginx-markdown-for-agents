"""Pytest tests for detect_elts_null_guard.py.

Validates the ngx_list_part_t elts NULL-guard audit:
- chain loop indexing elts without a guard is flagged
- chain loop with a NULL guard passes
- non-chain functions are skipped
- elts accessed without indexing is not flagged
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import detect_elts_null_guard as module


def _audit_text(content: str):
    path = (
        Path(module.__file__).resolve().parents[1]
        / "_fixture_elts_null_guard.c"
    )
    path.write_text(content, encoding="utf-8")
    try:
        return module.audit_file(path)
    finally:
        path.unlink()


UNGUARDED_LOOP = """\
ngx_table_elt_t *
ngx_http_markdown_find_header(ngx_http_request_t *r, ngx_str_t *name)
{
    ngx_list_part_t  *part;
    ngx_table_elt_t  *headers;
    ngx_uint_t        i;

    part = &r->headers_in.headers.part;
    headers = part->elts;
    for ( ;; ) {
        /* DANGEROUS: indexes elts without a nelts bound or NULL guard. */
        for (i = 0; headers[i].hash != 0; i++) {
            if (headers[i].key.len == name_len
                && ngx_strncasecmp(headers[i].key.data, name, name_len) == 0)
            {
                return &headers[i];
            }
        }
        if (part->next == NULL) {
            break;
        }
        part = part->next;
        headers = part->elts;
    }
    return NULL;
}
"""

GUARDED_LOOP = """\
void
ngx_http_markdown_copy_content_encoding(ngx_http_request_t *r, u_char *data)
{
    ngx_list_part_t        *part;
    const ngx_table_elt_t  *headers;
    ngx_uint_t              i;

    for (part = &r->headers_in.headers.part;
         part != NULL;
         part = part->next)
    {
        headers = part->elts;
        if (headers == NULL && part->nelts != 0) {
            return;
        }
        for (i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0) {
                continue;
            }
        }
    }
}
"""

NO_INDEX = """\
void
ngx_http_markdown_count(ngx_http_request_t *r)
{
    ngx_list_part_t *part;
    ngx_uint_t       total = 0;

    for (part = &r->headers_in.headers.part;
         part != NULL;
         part = part->next)
    {
        total += part->nelts;
    }
}
"""

NON_CHAIN = """\
void
ngx_http_markdown_parse(ngx_str_t *s)
{
    ngx_uint_t i;
    for (i = 0; i < s->len; i++) {
        s->data[i] = 0;
    }
}
"""


def test_unguarded_loop_flagged() -> None:
    findings = _audit_text(UNGUARDED_LOOP)
    assert len(findings) >= 1, f"expected findings, got {findings}"
    assert "NULL" in findings[0]


def test_guarded_loop_passes() -> None:
    findings = _audit_text(GUARDED_LOOP)
    assert findings == []


def test_no_index_not_flagged() -> None:
    findings = _audit_text(NO_INDEX)
    assert findings == []


def test_non_chain_skipped() -> None:
    findings = _audit_text(NON_CHAIN)
    assert findings == []
