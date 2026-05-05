# nginx-markdown-for-agents v0.6.0

v0.6.0 is the production-readiness release for streaming defaults, noise
pruning, release gates, and Homebrew-based distribution.

## Highlights

- Streaming conversion now defaults to `auto` instead of `off`.
- Noise pruning is enabled by default through the Rust converter's default
  feature set.
- New v0.6.0 release gates validate streaming-default behavior, noise pruning,
  memory-budget configuration, reason-code coverage, documentation alignment,
  and Homebrew formula readiness.
- Homebrew tap publication is automated after the release is published: the
  workflow computes the GitHub tag archive SHA-256 and copies the updated
  formula into the configured tap repository.
- macOS post-release verification can install the tap formula and run a source
  build check on GitHub-hosted macOS runners.

## Upgrade Notes

- Operators who need 0.5.x-compatible routing must set
  `markdown_streaming_engine off` explicitly.
- Operators who need 0.5.x-compatible pruning behavior must set
  `markdown_prune_noise off` explicitly.
- `markdown_streaming_auto_threshold` now controls the auto-mode streaming
  threshold. `markdown_large_body_threshold` keeps its full-buffer routing
  semantics.
- For Homebrew, do not manually publish the repository formula before the
  release tag exists. The tag archive SHA-256 is generated from GitHub's
  downloadable archive after `v0.6.0` is pushed.

## Validation

- `make release-gates-check-060`
- `make docs-check`
- `make license-check`
- `make test-all`

See `CHANGELOG.md` for the full change list.
