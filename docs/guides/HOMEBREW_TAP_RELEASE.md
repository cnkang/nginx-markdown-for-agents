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

1. Push your release tag (for example `vX.Y.Z`).
2. Publish a GitHub release for that tag.
3. Workflow `Publish Homebrew Formula to Tap` will:
   - Resolve the release tag to one immutable commit and verify its package
     version.
   - Read the Formula source from that exact tag commit, even for a manual
     recovery run.
   - Download `https://github.com/<owner>/<repo>/archive/refs/tags/<tag>.tar.gz`
     and compare its normalized tracked-file content with a local `git archive`
     of the resolved tag commit.
   - Compute SHA-256 from those exact verified downloaded bytes.
   - Update only the Formula's class-level `url`, `version`, and `sha256`
     fields, preserving nested resource identities.
   - Run `brew audit --strict` on the exact rendered Formula before the tap
     credential is introduced.
   - Copy formula into your tap repository and push

Manual publication is accepted only when the workflow itself runs from the
repository's default branch. The Formula program, verified source tree,
archive URL, version, and checksum therefore all derive from one reviewed
release tag commit.

## Post-Release macOS Verification

Workflow `Homebrew Post-Release Verify` runs on GitHub macOS and executes:

1. `brew tap cnkang/nginx-markdown`
2. `brew audit --strict cnkang/nginx-markdown/nginx-markdown-module`
3. `brew install --build-from-source cnkang/nginx-markdown/nginx-markdown-module`
4. `brew test cnkang/nginx-markdown/nginx-markdown-module`

If you use your own tap repository, replace `cnkang/nginx-markdown` with your
tap name.

You can run it manually through `workflow_dispatch` if needed.
