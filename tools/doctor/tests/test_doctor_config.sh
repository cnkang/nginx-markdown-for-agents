#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "$0")/../../.." && pwd)
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/doctor-config-test.XXXXXX")
cleanup() {
    rm -f "$tmpdir/nginx" "$tmpdir/uname" "$tmpdir/ldd" "$tmpdir/nm" "$tmpdir/rustc"
    rm -f "$tmpdir/ngx_http_markdown_filter_module.so"
    rm -f "$tmpdir/captured.conf" "$tmpdir/output.json"
    rmdir "$tmpdir"
    return 0
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
    *)
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
cat > "$tmpdir/nm" <<'STUB'
#!/usr/bin/env bash
cat <<'SYMBOLS'
0000000000000000 T ngx_http_markdown_filter_module
0000000000000010 T markdown_abi_version
0000000000000020 T markdown_convert
0000000000000030 T markdown_converter_new
SYMBOLS
STUB
chmod +x "$tmpdir/uname" "$tmpdir/ldd" "$tmpdir/nm"
: > "$tmpdir/ngx_http_markdown_filter_module.so"
cat > "$tmpdir/rustc" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' 'rustc 1.97.1 (contract toolchain)'
STUB
chmod +x "$tmpdir/rustc"

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
grep -Fq '"name":"rust_linkage","status":"pass"' "$tmpdir/output.json"
grep -Fq '"markdown_abi_version"' "$tmpdir/output.json"
git_root=""
if command -v git >/dev/null 2>&1 \
    && git_root=$(git -C "$repo_root" rev-parse --show-toplevel 2>/dev/null || true) \
    && [[ -n "$git_root" && "$git_root" == "$repo_root" ]]; then
    grep -Fq '"name":"rust_toolchain","status":"pass"' "$tmpdir/output.json"
    grep -Fq '"pinned_channel":"1.97.1"' "$tmpdir/output.json"
    grep -Fq '"pinned_channel_expected":"1.97.1"' "$tmpdir/output.json"
    grep -Fq '"msrv":"1.97"' "$tmpdir/output.json"
else
    # Without a usable git checkout (git missing, or the resolved toplevel
    # differs from the doctor root) the doctor cannot validate the pinned
    # toolchain channel; it reports a warn with repository_checkout:false.
    grep -Fq '"name":"rust_toolchain","status":"warn"' "$tmpdir/output.json"
    grep -Fq '"repository_checkout":false' "$tmpdir/output.json"
fi

printf '%s\n' 'doctor config test passed'
