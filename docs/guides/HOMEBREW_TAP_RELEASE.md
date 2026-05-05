# Homebrew Tap Release

This repository ships the Homebrew formula through a dedicated tap repository.

## Required Repository Settings

Set these in this repository before running the workflows:

1. Repository variable `HOMEBREW_TAP_REPO`
   - Format: `owner/repo`
   - Example: `cnkang/homebrew-nginx-markdown`
2. Optional repository variable `HOMEBREW_TAP_BRANCH`
   - Default: `main`
3. Optional repository variable `HOMEBREW_FORMULA_PATH`
   - Default: `Formula/nginx-markdown-module.rb`
4. Optional repository variable `HOMEBREW_FORMULA_TOKEN`
   - Default: `nginx-markdown-module`
5. Repository secret `HOMEBREW_TAP_TOKEN`
   - PAT with write access to the tap repository.

## Publish Flow

1. Push your release tag (for example `v0.6.0`).
2. Publish a GitHub release for that tag.
3. Workflow `Publish Homebrew Formula to Tap` will:
   - Download `https://github.com/<owner>/<repo>/archive/refs/tags/<tag>.tar.gz`
   - Compute SHA-256 from the downloadable artifact
   - Update `url` and `sha256` in `packaging/homebrew/nginx-markdown-module.rb`
   - Copy formula into your tap repository and push

## Post-Release macOS Verification

Workflow `Homebrew Post-Release Verify` runs on GitHub macOS and executes:

1. `brew tap <owner>/<tap>`
2. `brew audit --strict <tap>/<formula>`
3. `brew install --build-from-source <tap>/<formula>`
4. `brew test <tap>/<formula>`

You can run it manually through `workflow_dispatch` if needed.
