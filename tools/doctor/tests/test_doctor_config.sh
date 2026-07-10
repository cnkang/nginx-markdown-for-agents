#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "$0")/../../.." && pwd)
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/doctor-config-test.XXXXXX")
cleanup() {
    rm -f "$tmpdir/nginx" "$tmpdir/uname" "$tmpdir/ldd"
    rm -f "$tmpdir/ngx_http_markdown_filter_module.so"
    rm -f "$tmpdir/captured.conf" "$tmpdir/output.json"
    rmdir "$tmpdir"
}
trap cleanup EXIT

capture_path="$tmpdir/captured.conf"
cat > "$tmpdir/nginx" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
    -v)
        printf '%s\n' 'nginx version: nginx/1.26.3' >&2
        ;;
    -V)
        printf '%s\n' 'configure arguments: --with-compat' >&2
        ;;
    -t)
        shift
        while [[ $# -gt 0 ]]; do
            if [[ "$1" == "-c" ]]; then
                cp "$2" "$DOCTOR_CAPTURE_PATH"
                exit 0
            fi
            shift
        done
        exit 2
        ;;
esac
STUB
chmod +x "$tmpdir/nginx"
cat > "$tmpdir/uname" <<'STUB'
#!/usr/bin/env bash
case "${1:-}" in
    -s) printf '%s\n' Linux ;;
    -m) printf '%s\n' x86_64 ;;
    *) printf '%s\n' Linux ;;
esac
STUB
cat > "$tmpdir/ldd" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' 'ldd (GNU libc) 2.31'
STUB
chmod +x "$tmpdir/uname" "$tmpdir/ldd"
: > "$tmpdir/ngx_http_markdown_filter_module.so"

PATH="$tmpdir:$PATH" DOCTOR_CAPTURE_PATH="$capture_path" \
    bash "$repo_root/tools/doctor/nginx-markdown-doctor.sh" \
    --json --nginx-bin "$tmpdir/nginx" --module-path "$tmpdir" \
    > "$tmpdir/output.json" || true

[[ -s "$capture_path" ]]
load_line=$(grep -n '^load_module ' "$capture_path" | cut -d: -f1)
events_line=$(grep -n '^events ' "$capture_path" | cut -d: -f1)
http_line=$(grep -n '^http ' "$capture_path" | cut -d: -f1)

[[ -n "$load_line" ]]
[[ "$load_line" -lt "$events_line" ]]
[[ "$load_line" -lt "$http_line" ]]
grep -Fq "load_module \"$tmpdir/ngx_http_markdown_filter_module.so\";" \
    "$capture_path"
grep -Fq 'markdown_filter on;' "$capture_path"
grep -Fq '"artifact":"ngx_http_markdown_filter_module-1.26.3-glibc-x86_64.tar.gz"' \
    "$tmpdir/output.json"

printf '%s\n' 'doctor config test passed'
