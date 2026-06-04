# Release Notes Generation

This document describes how to generate platform support matrix release notes
from the authoritative source (`tools/release-matrix.json`).

## Quick Start

Generate current release notes for stdout:

```sh
make release-notes
```

Or invoke the tool directly:

```sh
python3 tools/render_release_matrix_docs.py --release-notes
```

## Modes

### Current Matrix Summary

Generates a release-ready platform support section from the current matrix:

```sh
python3 tools/render_release_matrix_docs.py --release-notes
```

Output includes:
- Supported NGINX versions with channel classification
- Artifact availability table (type, tier, platforms)
- Coverage summary (tier counts and blocking status)

### Diff Against Previous Release

Compare the current matrix against a saved previous version to generate a
changes section:

```sh
python3 tools/render_release_matrix_docs.py --release-notes --previous path/to/old-matrix.json
```

Additional output when `--previous` is provided:
- Added/removed NGINX versions
- Added/removed platform entries
- Tier changes (e.g., experimental → supported)

## Storing Previous Matrices

Before each release, archive the current matrix for future comparison:

```sh
cp tools/release-matrix.json docs/project/release-gates/release-matrix-v<VERSION>.json
```

Example workflow for v0.8.0 release:

```sh
# Archive the pre-release matrix
cp tools/release-matrix.json docs/project/release-gates/release-matrix-v0.7.0.json

# After matrix updates for 0.8.0, generate notes with diff
python3 tools/render_release_matrix_docs.py \
    --release-notes \
    --previous docs/project/release-gates/release-matrix-v0.7.0.json
```

## Example Output

```markdown
## Platform Support Matrix

### Supported NGINX Versions

- 1.31.1 (mainline)
- 1.30.2 (stable)
- 1.28.3 (stable)
- 1.26.3 (stable)
- 1.24.0 (oldstable)

### Artifacts

| Type | Tier | Platforms |
|------|------|-----------|
| Dynamic module (binary) | supported | linux glibc amd64/arm64, linux musl amd64/arm64 |
| Docker image | supported | Official nginx Docker images (stable/mainline) |
| DEB package | supported | DEB packages for Ubuntu/Debian (glibc amd64/arm64) |
| RPM package | supported | RPM packages for AlmaLinux/Amazon Linux (glibc amd64/arm64) |
| Homebrew formula | experimental | macOS Homebrew formula (arm64) |
| Source | best-effort | Source build from repository (any platform) |

### Coverage Summary

- **supported**: 24 entries (release blocking)
- **experimental**: 1 entries (non-blocking)
- **best-effort**: 1 entries
```

## Integration with Release Checklist

When preparing a release:

1. Update `tools/release-matrix.json` with any new/changed platforms.
2. Run `python3 tools/render_release_matrix_docs.py --check` to validate docs.
3. Run `python3 tools/render_release_matrix_docs.py --write` to update generated sections.
4. Run `make release-notes` to generate the platform support section.
5. Copy the output into the release notes document (`docs/project/release-notes-<version>.md`).

## Validation

Verify the matrix is valid and all generated doc sections are consistent:

```sh
python3 tools/render_release_matrix_docs.py --check
```

This is also run as part of `make docs-check` in CI.
