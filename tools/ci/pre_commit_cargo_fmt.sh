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

# Every crate the Make target checks, formatted the same way.
cargo fmt --manifest-path components/rust-converter/Cargo.toml --all
cargo fmt --manifest-path tools/corpus/test-corpus-conversion/Cargo.toml --all
cargo fmt --manifest-path tools/e2e-harness/Cargo.toml --all

# Convergence check: a crate whose sources cannot be formatted must fail here
# rather than silently ship unformatted.
make rust-fmt-check

# Re-stage what was just reformatted, then report.  `--` separates paths from
# revisions, so a path beginning with a dash cannot be read as an option.
changed=0
while IFS= read -r -d '' path; do
    git add -- "${path}"
    changed=1
done < <(git diff --name-only --diff-filter=M -z -- '*.rs')

if [[ "${changed}" -eq 1 ]]; then
    echo "cargo-fmt reformatted the files above; they have been re-staged." >&2
    echo "Review them, then run 'git commit' again." >&2
    exit 1
fi
