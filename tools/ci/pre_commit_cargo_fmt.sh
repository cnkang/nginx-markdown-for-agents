#!/usr/bin/env bash
# pre_commit_cargo_fmt.sh — format every Rust crate, then verify convergence.
#
# Why this exists instead of `entry: make rust-fmt-check`:
# pre-commit stashes the staged tree when a hook fails and restores its own
# patch afterwards, which silently reverts formatting the contributor just
# applied.  CONTRIBUTING.md tells contributors to run `cargo fmt` first, so a
# check-only hook made the documented workflow fail twice for the same reason
# and left the working tree looking formatted when it was not.
#
# Formatting in place and failing only when `cargo fmt` cannot converge keeps
# one command.  Note that `cargo fmt` writes the WORKING TREE, not the index:
# reformatting a file whose staged blob was unformatted leaves the commit
# content unchanged, so the formatted bytes are re-staged here and the hook
# then fails, telling the contributor to retry.  Without the re-stage the hook
# would exit 0 and commit the unformatted blob it just reformatted away from.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

# Record every Rust file this hook might rewrite, together with its content,
# BEFORE formatting runs -- so the re-stage below can tell a formatter edit from
# a contributor edit.
#
# `git diff HEAD` (not plain `git diff`) is the selector: plain `git diff` only
# sees worktree-vs-index, so a file whose UNFORMATTED blob is already staged
# would be missing from the snapshot and stay unformatted in the commit.
#
# The filter is ACM, not M: an ADDED Rust file is just as unformatted as a
# modified one, and `M` alone let a brand-new file through the snapshot while
# cargo fmt fixed only the worktree -- the commit then carried the unformatted
# blob and the hook still exited 0.
#
# A temp file, not an associative array: macOS ships bash 3.2, which has no
# `declare -A`, and this hook must behave the same on a developer Mac and on CI.
snapshot="$(mktemp)"
trap 'rm -f "${snapshot}"' EXIT
while IFS= read -r -d '' path; do
    [[ -f "${path}" ]] || continue
    printf '%s\0%s\0' "$(git hash-object -- "${path}")" "${path}" >>"${snapshot}"
done < <(git diff HEAD --name-only --diff-filter=ACM -z -- '*.rs')

# Every crate the Make target checks, formatted the same way.
cargo fmt --manifest-path components/rust-converter/Cargo.toml --all
cargo fmt --manifest-path tools/corpus/test-corpus-conversion/Cargo.toml --all
cargo fmt --manifest-path tools/e2e-harness/Cargo.toml --all

# Convergence check: a crate whose sources cannot be formatted must fail here
# rather than silently ship unformatted.
make rust-fmt-check

# Re-stage ONLY what the formatter itself rewrote, then report.
#
# Staging every modified .rs file would sweep unrelated unstaged developer work
# into the index: with nothing staged at all, a single pre-existing edit was
# enough to pull three files into the commit. Snapshot the dirty Rust files'
# checksums before formatting, then re-stage a path only when its content
# actually changed under `cargo fmt`.
#
# `--` separates paths from revisions, so a path beginning with a dash cannot be
# read as an option.
# Walk the SNAPSHOT, not the current diff: a file the formatter just repaired
# is no longer `git diff`-modified, so diffing afterwards can never see it.
changed=0
while IFS= read -r -d '' before_sum && IFS= read -r -d '' path; do
    [[ -f "${path}" ]] || continue
    [[ "${before_sum}" == "$(git hash-object -- "${path}")" ]] && continue
    # The content changed under `cargo fmt`, so this difference is the
    # formatter's, not a contributor edit that happened to be in the tree.
    git add -- "${path}"
    changed=1
    printf '  reformatted and re-staged: %s\n' "${path}" >&2
done < "${snapshot}"

if [[ "${changed}" -eq 1 ]]; then
    echo "cargo-fmt reformatted the files above; they have been re-staged." >&2
    echo "Review them, then run 'git commit' again." >&2
    exit 1
fi
