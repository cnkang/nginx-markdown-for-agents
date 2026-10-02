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
# one command, and the reformat lands in the staged tree pre-commit is about
# to commit.
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
