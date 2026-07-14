---
domain: version-consistency
rules: [55]
paths:
  - "rust-toolchain.toml"
  - "components/rust-converter/Cargo.toml"
  - "components/rust-converter/fuzz/Cargo.toml"
  - "tools/e2e-harness/Cargo.toml"
  - "tools/corpus/test-corpus-conversion/Cargo.toml"
  - ".github/workflows/**"
  - "packaging/debian/control"
  - "charts/nginx-markdown/Chart.yaml"
  - "AGENTS.md"
  - "CONTRIBUTING.md"
  - "SECURITY.md"
  - "README.md"
  - "README_zh-CN.md"
  - "packaging/README.md"
  - "docs/**"
  - "tools/harness/check_rust_baseline.py"
  - "tools/harness/detect_version_consistency.sh"
---

# Version Consistency Rule

## Rule ID
**Rule 55**: Version Consistency Across All Artifacts

## Severity
**P1 - Important**: Version mismatches can cause user confusion, deployment failures, and release inconsistencies.

## Description
All version numbers across the project must be synchronized to reflect the current release version. This includes:
- Main `Cargo.toml` (source of truth)
- Helm Chart `version` and `appVersion` fields
- Internal Cargo.toml dependencies (fuzz targets, corpus tools)
- Documentation examples (INSTALLATION.md, etc.)

The Rust build contract is a second, independently synchronized baseline:

- `rust-toolchain.toml` owns the exact repository/release compiler.
- Every first-party Cargo manifest declares the matching major/minor MSRV.
- Blocking CI and release workflows use the exact compiler; nightly fuzzing is
  the only intentionally nightly lane.
- Release Dockerfiles consume `rust-toolchain.toml` instead of embedding an
  independent compiler version.
- Source-build packaging and current build documentation declare the same
  MSRV.

## Rationale
Version inconsistencies have been a recurring issue in past releases:
- Users following documentation examples with outdated versions
- Helm charts deploying wrong application versions
- Internal tools depending on stale library versions
- Confusion about which version is actually current

Automated detection prevents these issues from reaching release.

## Detection
The harness detector `tools/harness/detect_version_consistency.sh` performs the following checks:

1. **Source of Truth**: Reads version from `components/rust-converter/Cargo.toml`
2. **Helm Chart**: Validates `charts/nginx-markdown/Chart.yaml`:
   - `version` field matches source of truth
   - `appVersion` field matches source of truth
3. **Internal Dependencies**: Checks path dependencies in:
   - `components/rust-converter/fuzz/Cargo.toml`
   - `tools/corpus/test-corpus-conversion/Cargo.toml`
4. **Homebrew Formula**: Reports version (informational only - intentionally kept at previous release, updated by publish workflow)
5. **Rust Baseline**: Calls the blocking
   `tools/harness/check_rust_baseline.py` validator, which checks:
   - exact `MAJOR.MINOR.PATCH` toolchain identity;
   - all four first-party manifest MSRVs;
   - classified blocking, release, and nightly workflows;
   - release Dockerfile consumption of the canonical toolchain;
   - Debian source-build compiler floor; and
   - current contributor, install, compatibility, and operations docs.

An unclassified workflow that installs Rust is a failure so a new workflow
cannot silently introduce a floating compiler.

## Exclusions
The following are intentionally NOT checked:
- **Homebrew Formula** (`packaging/homebrew/nginx-markdown-module.rb`): The `url` and `sha256` fields are intentionally kept at the previous release version. The `homebrew-tap-publish.yml` workflow automatically rewrites these fields during release.
- **Historical CHANGELOG entries**: Version numbers in changelog history are expected to reference past versions.
- **Documentation historical references**: Mentions of past versions in migration guides, compatibility notes, etc.

## Remediation
When version inconsistency is detected:

1. **Identify the source of truth**: Check `components/rust-converter/Cargo.toml` for the current version
2. **Update Helm Chart**:
   ```yaml
   # charts/nginx-markdown/Chart.yaml
   version: <current-version>
   appVersion: "<current-version>"
   ```
3. **Update internal dependencies**:
   ```toml
   # components/rust-converter/fuzz/Cargo.toml
   # tools/corpus/test-corpus-conversion/Cargo.toml
   nginx-markdown-converter = { version = "<current-version>", path = "..." }
   ```
4. **Update documentation examples**: Replace outdated version references in INSTALLATION.md and other guides
5. **Update the Rust baseline atomically**: Change `rust-toolchain.toml`, all
   first-party `rust-version` fields, classified workflows, source-build
   packaging, and current build documentation in the same change set. Do not
   use floating `stable` for blocking or release builds.

## Integration
This detector is integrated into:
- `make harness-security-checks`: Runs as part of the standard harness check suite
- `make harness-check`: Validates harness synchronization (includes version consistency)

## Example Output
```text
INFO: Checking version consistency...
PASS: Main Cargo.toml version: 0.9.1
PASS: Chart.yaml version: 0.9.1
PASS: Chart.yaml appVersion: 0.9.1
PASS: fuzz/Cargo.toml dep version: 0.9.1
PASS: corpus/Cargo.toml dep version: 0.9.1
Rust baseline consistency check PASSED: toolchain=1.97.0, MSRV=1.97
INFO: Homebrew formula: 0.8.3 (intentionally previous; updated by publish workflow)

PASS: All version checks passed
```

## Related Rules
- **Rule 9**: Documentation tooling accuracy (version examples must match the active release)
- **Rule 13**: CI and release gate validation (release readiness includes version consistency)
- **AGENTS.md**: Version bump procedures

## History
- **2026-06-27**: Added Rule 55 after discovering multiple version inconsistencies in 0.8.3 release preparation
  - Helm Chart was at 0.8.2 while main version was 0.8.3
  - Internal dependencies were at 0.8.0
  - Documentation examples referenced v0.8.2
- **2026-07-13**: Updated example output to 0.9.1 (Rule 55 detector output)
- **2026-07-14**: Added blocking exact Rust compiler/MSRV consistency checks
  for manifests, workflows, release Dockerfiles, source packaging, and current
  build documentation
