#!/usr/bin/env bash
# Verify the gitleaks wrapper scans tracked worktree files and excludes ignored state.

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/nginx-markdown-gitleaks-test.XXXXXX")"
ignored_dir="$repo_root/.codeartsdoer/gitleaks-scope-test"

cleanup() {
    rm -rf "$tmp_dir"
    rm -rf "$ignored_dir"
    return 0
}

trap cleanup EXIT
mkdir -p "$tmp_dir/bin" "$ignored_dir"
printf '%s\n' 'ignored-local-secret-marker' > "$ignored_dir/fixture.txt"

cat > "$tmp_dir/bin/gitleaks" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

source_dir=""
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --source)
            source_dir="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

[[ -n "$source_dir" ]]
[[ -f "$source_dir/AGENTS.md" ]]
[[ ! -e "$source_dir/.codeartsdoer/gitleaks-scope-test/fixture.txt" ]]
EOF
chmod +x "$tmp_dir/bin/gitleaks"

PATH="$tmp_dir/bin:$PATH" bash "$repo_root/tools/security/run_gitleaks_tracked.sh"

deleted_repo="$tmp_dir/deleted-repo"
mkdir -p "$deleted_repo"
git -C "$deleted_repo" init -q
printf '%s\n' 'tracked-live-content' > "$deleted_repo/AGENTS.md"
printf '%s\n' 'tracked-before-delete' > "$deleted_repo/removed.txt"
git -C "$deleted_repo" add AGENTS.md removed.txt
git -C "$deleted_repo" \
    -c user.name='Harness Test' \
    -c user.email='harness@example.invalid' \
    commit -qm 'test fixture'
rm -f "$deleted_repo/removed.txt"
if [[ -e "$deleted_repo/removed.txt" ]]; then
    echo "failed to delete removed.txt fixture" >&2
    exit 1
fi

(
    cd "$deleted_repo"
    PATH="$tmp_dir/bin:$PATH" \
        bash "$repo_root/tools/security/run_gitleaks_tracked.sh"
)
echo "gitleaks tracked-worktree scope test passed"
