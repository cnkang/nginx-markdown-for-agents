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

## Integration
This detector is integrated into:
- `make harness-security-checks`: Runs as part of the standard harness check suite
- `make harness-check`: Validates harness synchronization (includes version consistency)

## Example Output
```
INFO: Checking version consistency...
PASS: Main Cargo.toml version: 0.8.3
PASS: Chart.yaml version: 0.8.3
PASS: Chart.yaml appVersion: 0.8.3
PASS: fuzz/Cargo.toml dep version: 0.8.3
PASS: corpus/Cargo.toml dep version: 0.8.3
INFO: Homebrew formula: 0.8.2 (intentionally previous; updated by publish workflow)

PASS: All version checks passed
```

## Related Rules
- **Rule 25**: Release gate validation (includes version consistency as part of release readiness)
- **Rule 31**: Documentation accuracy (version examples must be current)
- **AGENTS.md**: Version bump procedures

## History
- **2026-06-27**: Added Rule 55 after discovering multiple version inconsistencies in 0.8.3 release preparation
  - Helm Chart was at 0.8.2 while main version was 0.8.3
  - Internal dependencies were at 0.8.0
  - Documentation examples referenced v0.8.2
