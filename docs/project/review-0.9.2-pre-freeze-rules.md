# Review Report: 0.9.2 Pre-Code-Freeze — Rules & Harness Configuration

**Date**: 2026-08-07
**Branch**: `dev/wip-0.9.2-harness` (base: `dev/wip-0.9.2`)
**Scope**: `AGENTS.md` Rule 1–62 vs `docs/harness/rules/*.md`, `tools/harness/`, `Makefile`, `.github/workflows/`; branch diff vs `main` (171 commits, 456 files).
**Method**: Programmatic 3-way rule index comparison; script existence checks (37 rule-referenced + 39 Makefile/workflow-referenced); Makefile target existence; verification-command spot runs. Read-only.

## Summary

- Findings: 15 (High 3 / Medium 5 / Low 7)
- Verified-clean: Rule 1–62 index ↔ frontmatter ↔ rules/README 3-way consistent; all referenced scripts exist; all 25 AGENTS.md verification targets exist in Makefile; frontmatter YAML parseable.

## Branch-diff overview (main..HEAD)

171 commits (2026-07-30 → 08-07), 456 files, +280,188/−23,231. Themes: 82 fix, 22 docs, 16 feat, 14 refactor, 12 test, 10 build, 7 chore, 8 ci/perf/style. Surfaces: nginx-module 0.9.2 breaking removals + limits/dynconf/reason/v1-metrics; rust-converter streaming lifecycle + ABI freeze; release wave5 generic pre-freeze gates; release matrix canonicalization; perf baselines; tools/harness public-surface tooling; CI nightly/weekly observation.

## Findings

### F1 (High) — `AGENTS.md:453-465` — `make release-gates-check-092` missing from "Before declaring completion"
- **Evidence**: Makefile:900 defines `release-gates-check-092` (blocking gate, used by ci.yml 092 job + nightly-perf.yml:369); routing-manifest v092-gates family exists; AGENTS.md has zero "092" references.
- **Fix**: Add entry after the 0.9.1 line, mirroring the `RELEASE_GATE_ALLOW_SKIP_*` contract notes.

### F2 (High) — Rule 41 verification command fails against current code — `docs/harness/rules/shell.md:93` + 4 detector scripts
- **Evidence**: `grep -rn '\s\|\d\|\w' tools/harness/detect_*.sh` (the rule's own verification) matches 13 spots: `detect_shell_hygiene.sh:223,276,280,376`, `detect_live_conf_reads.sh:100,211,259,301,302,309`, `detect_finalize_return.sh:75`, `detect_cwe190_casts.sh:315` — all `\s`/`\w` inside `grep -E` patterns, exactly the BSD-grep-incompatible form the rule prohibits.
- **Fix**: Replace `\s` → `[[:space:]]` and `\w` → `[[:alnum:]_]` in those four scripts (behavior-preserving), then re-run the verification command → zero hits.

### F3 (High) — 0.9.2 new gates not indexed in AGENTS.md / rules
- **Evidence**: `public-surface-drift-check` (Makefile:255; ci.yml:382,422; 092 gate step 2/7), `schema-drift-check` (Makefile:258), `reason-codegen-check` (Makefile:290; ci.yml:424) — none appear in AGENTS.md verification list; no rule file names the public-surface detector (observability-metrics.md:39 has unnamed prose under Rule 7).
- **Fix**: Add the three targets to AGENTS.md verification list; add a public-surface detector reference to observability-metrics.md (Rule 23 section).

### F4 (Medium) — `AGENTS.md:208,232,234,235,279` — 5 detectors labeled "CI gate" but not wired to any workflow
- **Evidence**: `detect_ngx_log_arg_count.sh`, `detect_nosonar_discipline.sh`, `detect_orphan_comment_close.py`, `detect_ifdef_guard_visibility.sh`, `detect_workflow_input_injection.sh` run only under local `make harness-security-checks`.
- **Fix**: Relabel to "harness gate (make harness-security-checks)" — do not add CI wiring in pre-freeze.

### F5 (Medium) — `docs/harness/rules/version-consistency.md:122-123` — gate membership description stale
- **Evidence**: `make harness-check` (Makefile:250-253) does not run `detect_version_consistency.sh`; harness-security-checks is invoked only by harness-check-full (Makefile:265), release-gates-check-092, and ci.yml.
- **Fix**: Correct the membership description.

### F6 (Medium) — `AGENTS.md:579` Document Updates table diverges from `docs/harness/README.md:93-95`
- **Evidence**: AGENTS.md has one 0.9.2 log row; README has three. Missing: public-surface drift gates (12+ commits), dead-FFI classifier, complexity zero-exemption policy, 092 gate wiring, schema-drift/reason-codegen validators.
- **Fix**: Consolidate into 2-3 0.9.2 rows covering all of the above.

### F7 (Medium) — `docs/harness/routing-manifest.json` + `.md` — 3 new gates have no verification family (Rule 36)
- **Fix**: Add `public-surface-drift`, `schema-drift`, `reason-codegen` families (commands, path patterns, keywords) to json + md.

### F8 (Medium) — `tools/harness/detect_ffi_dead_exports.py` orphan
- **Evidence**: Added in this window (f2029297); no Makefile/workflow/rule reference.
- **Fix**: Wire into `make harness-security-checks` and index in ffi-crosslang.md (or delete — keep, low risk).

### F9 (Low) — `tools/harness/normalize_cbindgen_header.py` not indexed (used by Makefile:101).
- **Fix**: One-line reference in ffi-crosslang.md (Rule 15 check-headers chain).

### F10 (Low) — `docs/harness/rules/ci-gating.md:57` — example path `tools/install-verified-rustup.sh` doesn't exist; actual `packaging/scripts/install-verified-rustup.sh`.
- **Fix**: Correct path.

### F11 (Low) — `docs/harness/rules/release-integrity.md:102` — Rule 62 heading is `### 62.` (H3) instead of `## Rule 62:` (H2).
- **Fix**: Normalize heading.

### F12 (Low) — AGENTS.md rule index omits fuzz-infrastructure rules (FUZZ-001..007, fuzz-infrastructure.md).
- **Fix**: Add an index row (domain fuzz-infrastructure).

### F13 (Low) — `tools/harness/audit_reason_codes.sh` orphan (only archive docs reference); overlaps reason-codegen.
- **Fix**: Mark legacy in header; no wiring in pre-freeze.

### F14 (Low) — `.github/workflows/nightly-observation.yml` / `weekly-observation.yml` not registered in AGENTS.md/routing-manifest.
- **Fix**: Add an observation family entry (informational).

### F15 (Low) — `observability-metrics.md:39` public-surface prose unnamed; fold into F3.

## Verification method (post-fix)

- `make harness-check`
- `grep -rn '\s\|\d\|\w' tools/harness/detect_*.sh` → zero hits (Rule 41)
- `python3 -c` parse routing-manifest.json
- `make complexity-check` (if scripts edited)
- `make docs-check`

---

## Closeout (2026-08-07)

All findings remediated on `dev/wip-0.9.2-harness`:

| ID | Status |
|----|--------|
| F1 | **fixed** — `make release-gates-check-092` added to AGENTS.md verification list |
| F2 | **fixed** — `\s`→`[[:space:]]` / `\w`→`[[:alnum:]_]` in 4 detector scripts (13 spots); Rule 41 verification command scoped to grep/sed/awk patterns (Python `re` exempted) |
| F3 | **fixed** — public-surface-drift / schema-drift / reason-codegen targets added to AGENTS.md; observability-metrics.md names the drift gate |
| F4 | **fixed** — 5 detectors relabeled "harness gate (make harness-security-checks)" |
| F5 | **fixed** — version-consistency.md integration membership corrected |
| F6 | **fixed** — AGENTS.md Document Updates 0.9.2 rows consolidated |
| F7 | **fixed** — 4 new verification families (public-surface-drift, schema-drift, reason-codegen, observation) added to routing-manifest.json + .md |
| F8 | **fixed** — detect_ffi_dead_exports.py wired into `make harness-security-checks` + indexed in ffi-crosslang.md |
| F9 | **fixed** — normalize_cbindgen_header.py indexed in ffi-crosslang.md |
| F10 | **fixed** — ci-gating.md rustup installer path corrected |
| F11 | **fixed** — release-integrity.md Rule 62 heading normalized to H2 |
| F12 | **fixed** — FUZZ-001..007 index row added to AGENTS.md |
| F13 | **fixed** — audit_reason_codes.sh marked LEGACY in header |
| F14 | **fixed** — observation family registered in routing-manifest |
| F15 | **fixed** — observability-metrics.md names the public-surface drift gate |

### Pre-existing issue discovered during execution: dangling `schema-drift-check` target

`make schema-drift-check` (validate_schema_drift.py) required 3 release
artifacts (metrics-registry.json, diagnostics-field-contract.json,
dynconf-precedence-report.json) that had **no generator** anywhere in the
repo, were never produced by CI, and were not wired into any workflow —
the target failed on a clean checkout (baseline branch reproduced the
2/5-gate failure).  Remediated:

- Added `tools/release/gates/generate_schema_artifacts.py` — idempotent
  generator deriving the 3 artifacts from the v1 renderer header,
  diagnostics.schema.json, dynconf.schema.json, and the dynconf precedence
  header (reason-codegen pattern).
- `make schema-drift-check` now runs generator + validator (5/5 gates pass).
- Wired into CI `release-092-contract-gates` job and `release-gates-check-092`
  step [2/7] (matches the step's declared "schema drift checks" scope).
- Added `tools/release/gates/tests/test_generate_schema_artifacts.py`
  (7 tests, incl. end-to-end validator round-trip).
- AGENTS.md schema-drift-check entry updated to describe the generation step.

Verification (fresh runs, all green):
- `make schema-drift-check` — 5/5 gates PASSED
- `python3 -m pytest tools/release/gates/tests -q` — 406 passed
- `make harness-check` / `make harness-security-checks` — PASS
- `grep -rn '\s\|\d\|\w' tools/harness/detect_*.sh` (grep/sed/awk patterns) — 0 hits
