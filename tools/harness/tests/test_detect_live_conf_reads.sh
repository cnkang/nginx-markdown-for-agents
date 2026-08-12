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
