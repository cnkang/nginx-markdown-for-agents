#!/usr/bin/env bash
# Regression test for helper-function matching in detect_live_conf_reads.sh.
#
# The helper alternation must stay inside the anchored function-definition
# expression.  An ungrouped final alternative can match a call site after a
# semicolon and incorrectly treat the caller's live-conf read as allowlisted.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_live_conf_reads.sh"
fixture_dir="$(mktemp -d "${TMPDIR:-/tmp}/live-conf-reads.XXXXXX")" || exit 1
trap 'rm -rf "${fixture_dir}"' EXIT

mkdir -p "${fixture_dir}"
cat >"${fixture_dir}/fixture.c" <<'SOURCE'
typedef struct {
    int enabled;
} ngx_http_markdown_conf_t;

static int
ngx_http_markdown_manifest_append_trusted_proxies(
    int *builder, const ngx_http_markdown_conf_t *conf)
{
    (void) builder;
    return conf->enabled;
}

static void
caller(int *builder, const ngx_http_markdown_conf_t *conf) { (void) builder;
    ngx_http_markdown_manifest_append_trusted_proxies(builder, conf);
    (void) conf->enabled;
}
SOURCE

if output="$(bash "${DETECTOR}" "${fixture_dir}" 2>&1)"; then
    exit_code=0
else
    exit_code=$?
fi

comment_dir="${fixture_dir}/comments"
mkdir -p "${comment_dir}"
cat >"${comment_dir}/comment_only.c" <<'SOURCE'
/*
conf->enabled appears in documentation, not in executable code.
*/
static int clean(void)
{
    return 0;
}
SOURCE

if comment_output="$(bash "${DETECTOR}" "${comment_dir}" 2>&1)"; then
    comment_exit_code=0
else
    comment_exit_code=$?
fi

if [[ "${comment_exit_code}" -ne 0 ]]; then
    printf 'FAIL: multiline comment was treated as a live conf read\n' >&2
    printf '%s\n' "${comment_output}" >&2
    exit 1
fi

if [[ "${exit_code}" -eq 1 ]] \
    && [[ "${output}" == *"request-path reads live conf->"* ]] \
    && [[ "${output}" == *"fixture.c"* ]]; then
    printf 'PASS: call-site live conf read is not treated as helper definition\n'
    exit 0
fi

printf 'FAIL: call-site live conf read was not rejected (exit=%s)\n' \
    "${exit_code}" >&2
printf '%s\n' "${output}" >&2
exit 1
