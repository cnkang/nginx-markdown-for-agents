# nginx-markdown-for-agents v0.6.1

v0.6.1 is a hardening release focused on harness governance, output safety,
and dynamic-configuration safety semantics.

## Highlights

- Added defect-prevention Rules 27–31 in `AGENTS.md` from recent fix-pattern
  analysis.
- Added output-safety routing pack:
  `docs/harness/risk-packs/output-safety.md`.
- Hardened dynamic-config behavior with two-phase snapshot semantics and
  request-bound snapshot binding.
- Tightened forwarded-host normalization and validation to reduce header
  injection risk.

## Version Alignment

- Rust converter crate remains at `0.6.1`.
- Corpus conversion helper crate now aligned to `0.6.1`:
  `tools/corpus/test-corpus-conversion/Cargo.toml`.
- Updated release-tag examples to `v0.6.1` in:
  - `.github/workflows/homebrew-tap-publish.yml`
  - `docs/guides/HOMEBREW_TAP_RELEASE.md`

## Validation

- `make release-gates-check-060`
- `cargo check --locked` (components/rust-converter)
- `cargo check --offline` (tools/corpus/test-corpus-conversion)
