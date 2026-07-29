---
domain: release-integrity
rules: [61, 62]
paths:
  - "tools/perf/**"
  - "tools/release/matrix/**"
  - "perf/reports/**"
  - ".github/workflows/**"
---

# Release Integrity Rules

## Rule 61: Performance Evidence Provenance Invariant

Historical issue chain (performance evidence provenance):
`f14ef413`, `8d8fbf6a`, `b7a7e5b2`, `f2f93980`, `dba7fd1c`, `cab92df2`,
`f5df095b`.

**Principle**: Every piece of performance evidence accepted by the release
gate must be *attributable to an exact, auditable source*. Evidence whose
provenance is ambiguous, mixed-environment, or unbound is not release
evidence — the gate must reject it (fail closed). This invariant collapsed
from a seven-commit incremental design sequence and must be stated as one
contract so future gate work starts from the complete set of fields.

**Required evidence provenance fields.** Each scenario record in the
evidence pack (written by `tools/perf/run_module_benchmark.sh` and
ingested by `tools/perf/evidence_gate.py`) must carry all of the following:

| Field | Meaning | Constraint |
|-------|---------|------------|
| `source_git_commit` | Exact source commit the benchmark ran against | Full 40-hex object ID; must not be abbreviated |
| `source_workflow_run` | GitHub Actions workflow run identifier | Must identify the CI run that produced the evidence |
| `source_workflow_attempt` | Workflow run attempt number | Required; distinguishes re-runs from original runs |
| `source_artifact` | Retained raw benchmark artifact | Must be a retained file/digest, not a live re-computation |
| `source_artifact_digest` | SHA-256 of the raw artifact | Verbatim; proves the artifact was not altered after the run |
| `measurement_timestamp` | UTC timestamp of the measurement | ISO-8601 with timezone; not a normalized or imputed value |
| `source_environment` | Comparison environment | Must match the baseline environment (OS, CPU, NGINX version, build flags); mixed-environment baselines are split or rejected |

**Additional contracts:**

1. **Bounded metrics only.** Every numeric metric emitted into the evidence
   pack must use a bounded category (e.g. `conversion_error`, `resource_limit`,
   `system_error`) — no unbounded gauge-like constructs that can silently
   absorb regressions.
2. **Per-codec path evidence.** Each streaming decompression codec (gzip,
   deflate, Brotli) must record a positive `decompression_streaming_total > 0`
   counter from a real module-enabled NGINX path, not a mock or fallback.
3. **Fallback-rate truth.** The streaming fallback rate must be computed from
   actual `fallback` vs `streaming` counter values and must not be normalized
   or split by environment after collection.
4. **Immutable baseline retention.** Once a baseline evidence pack is
   generated and used by a release gate, it becomes an immutable audit record.
   Subsequent regeneration does not overwrite it; the old pack is preserved
   with its own digest.
5. **Fail closed on missing provenance.** The blocking gate
   (`make release-gates-check-091`) must reject any scenario record that is
   missing any required provenance field, rather than skipping it silently.

**Verification:**
- `make release-gates-check-091` — blocking gate (requires `NGINX_BIN` or
  `--allow-skip-module`); every scenario record must include all provenance
  fields above.
- `make perf-evidence-check` — non-blocking report-only mode; validates the
  same invariant for PR visibility.
- `python3 -m pytest tools/perf/tests/` — perf tooling test suite
  (686 tests); must pass.
- Inspect `perf/reports/evidence-091.json` and confirm each scenario record
  carries all seven provenance fields.

**Why this rule.** Without a single contract, evidence provenance fields were
added one at a time as blockers were discovered (seven commits). The result
was working but undocumented as a unit — the next person to extend the gate
had no way to know which fields were required vs. optional. This rule captures
the invariant the seven commits collectively established.

---

### 62. Release matrix key normalization invariant

Historical issue chain (release-matrix key normalization): `59fbc06e`
(`entries` key support), `018e3483` (required keys check), `9c74af50`
(`_validate_manual_entries` keys), `2d7887c0` (loader and diffing keys),
`25c5d8c6` (fix matrix ghost updates and out-of-sync entries), `19d769a7`
(SonarCloud/CodeQL review findings).

**Principle**: All release-matrix code paths must resolve an entry through
*one* normalization function and use *one* canonical key set, not five
independent ad-hoc key lookups that silently disagree. The matrix loader
(`tools/release/matrix/update_matrix.py`) evolved through a five-commit
normalization sequence because key aliases (`nginx` / `nginx_version`,
`os_type` / `os` / `libc`) were handled independently in each function.

**Required:**

1. **Single alias-resolution path.** Every function that reads a matrix entry
   dict (`load_matrix`, `_validate_matrix_entry`, `_validate_manual_entries`,
   `_entry_sort_key`, `compute_matrix`, `diff_matrix`) must resolve aliased
   keys through the same normalization entry point, so an entry written with
   legacy keys (`nginx`, `os`) is interpreted identically to one written with
   canonical keys (`nginx_version`, `os_type`).

2. **Stable composite sort key.** `_entry_sort_key` and `_entry_key` in
   `diff_matrix` must sort on the *normalized* tuple
   `(version_tuple(nginx_version), os_type, arch)` so that normalization
   and diff ordering never disagree — disagreement caused the ghost-update
   regressions fixed in `25c5d8c6`.

3. **Canonical key set for required-key validation.** The required-key set
   (`REQUIRED_MATRIX_ENTRY_KEYS`) used by `_validate_matrix_entry` and the
   key set used by `_validate_manual_entries` must be identical in their
   interpretation of aliases, so adding a new required key does not create
   a second, inconsistent lookup.

**Verification:**
- `python3 -m pytest tools/release/matrix/tests/ -v --tb=short` — 114 tests;
  all must pass.
- `python3 -c "from tools.release.matrix.update_matrix import REQUIRED_MATRIX_ENTRY_KEYS, load_matrix; print('loader loads canonical schema')" --allow-skip-module`
  — loader must accept both the canonical `nginx_version`/`os_type`/`arch`
  keys and the legacy aliases.

**Why this rule.** The five-commit normalization chain is now internally
consistent, but the *reason* each function has its own key lookup is not
recorded anywhere. This rule prevents the next schema change from regressing
normalization across the multiple matrix consumers (loader, diff, validation,
sort) in the same way.

---

## Related Rules
- **Rule 13**: CI and release gate validation
- **Rule 54**: Release artifact path traversal protection
- **Rule 55**: Version consistency
- **ADR-0022**: 0.9.1 Performance Evidence Release Gate
- **`tools/perf/evidence_gate.py`**: Blocking evidence gate implementation

## History
- **2026-07-29**: Added Rules 61–62 after v0.9.0→HEAD fix-commit recurrence
  analysis identified two incremental-design gaps: the 7-commit performance
  evidence provenance chain and the 5-commit release-matrix key
  normalization chain, neither of which had a single stated contract.
