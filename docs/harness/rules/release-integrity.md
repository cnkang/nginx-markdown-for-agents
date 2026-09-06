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
from a seven-commit incremental design sequence and must read as one
contract so future gate work starts from the complete set of fields.

**Required evidence provenance fields.** The `baseline_policy` object in each
finalized baseline (written by `tools/perf/finalize_module_baseline.py` and
ingested by `tools/perf/evidence_gate.py`) must carry all of the following:

| Field | Meaning | Constraint |
|-------|---------|------------|
| `source_git_commit` | Exact source commit the benchmark ran against | Full 40-hex object ID; must not be abbreviated |
| `source_run` | GitHub Actions workflow run URL | `https://github.com/<owner>/<repo>/actions/runs/<id>/attempts/<n>`; run ID and attempt are positive integers; repo slug must match checkout |
| `source_artifact` | Retained raw benchmark artifact path | Repository-relative path; not absolute; no `..` traversal; must not be `"not-recorded"` or `"unknown"` |
| `source_artifact_sha256` | SHA-256 of the raw artifact file | 64-hex lowercase digest; recomputed and verified by the gate |
| `measurement_timestamp` | UTC timestamp of the measurement | ISO-8601 with explicit UTC offset (`Z` or `+00:00`); not a normalized or imputed value |
| `normalization` | Normalization mode applied | `"none"` for `verbatim_run`; `"conservative"` for `conservative_normalized` |

**Evidence object levels.** These fields are intentionally scoped to their
own objects. Do not copy provenance into every scenario record:

- `baseline_policy` carries `source_git_commit`, `source_run`,
  `source_artifact`, `source_artifact_sha256`, `measurement_timestamp`, and
  `normalization`.
- The top-level `module_benchmark` object carries the benchmark environment
  (`platform`, `load_generator`, `nginx_version`) and measurement identity
  (`git_commit`, `timestamp`).
- Each scenario carries its scenario evidence: scenario metadata,
  `load_integrity`, `metrics`, and `response_correctness`.
- `baseline_policy.scenario_sources` is optional. Only when it exists does
  the gate validate its per-scenario environment entries against the
  top-level `module_benchmark` environment.

**Additional contracts:**

1. **Per-codec path evidence.** Each streaming decompression codec (gzip,
   deflate, Brotli) must record a positive `decompression_streaming_total > 0`
   counter from a real module-enabled NGINX path, not a mock or fallback.
2. **Fallback-rate consistency.** The stored `fallback_rate` in each scenario
   must equal `precommit_failopen_total / streaming_requests_total` (or 0.0
   when `streaming_requests_total == 0`). The evidence gate cross-checks this
  via `_fallback_rate_consistency_violations`; a mismatch is a gate failure.
   `streaming_fallback_total` remains a separate path-routing counter. The
   module must not substitute it for the pre-commit fail-open ratio.
3. **Immutable baseline retention.** Once a release gate generates and uses a
   baseline evidence pack, it becomes an immutable audit record.
   Subsequent regeneration does not overwrite it; the old pack stays preserved
   with its own digest.
4. **Fail closed on missing provenance.** The blocking gate
   (`make release-gates-check-092`) must reject any baseline policy that is
   missing any required provenance field, rather than skipping it silently.
5. **Raw artifact binding.** For `verbatim_run` baselines, the gate
   recomputes the SHA-256 of the raw artifact file and verifies it matches
   `source_artifact_sha256`; the finalized report (minus `baseline_policy`)
   must be byte-identical to the raw report.
6. **Scenario source environment.** If `baseline_policy.scenario_sources`
   exists, each entry must declare `platform`, `load_generator`, and
   `nginx_version` consistent with the top-level `module_benchmark` fields.
   Mixed-environment baselines are split or rejected.

**Evidence binding lifecycle (2026-08-21 extension).** The 0.9.2
pre-freeze window produced ~15 commits churning candidate-bound evidence
(`36a9ad45`, `0847c287`, `f68109ac`, `2f8fc276`, `3bd22d47`, `f49920c6`,
`19340b31`, `d7012e42` reverted two minutes later by `cf6aea8e`) plus one
direct edit of a derived field (`8c899644`).  Provenance fields alone did
not prevent the churn because the *workflow* did not manage the lifecycle:

7. **Baselines are finalizer output, never hand-edited data.**
   `tools/perf/finalize_module_baseline.py` must produce every change to a
   finalized `perf/baselines/module-baseline-*.json` from retained raw inputs.
   Hand-editing a finalized baseline — including derived fields such as
   `fallback_rate` — is forbidden; regenerate instead.
8. **Raw inputs move with the finalized pack.**  A change set that touches
   a finalized baseline must touch its retained inputs
   (`*-raw.json` and/or `*-raw-probes/`) in the same commit.  A finalized
   JSON that moves alone is the signature of a hand edit.
9. **Archival imports keep their schema.**  `type: verbatim_import` packs
   are immutable audit records: they require a full 40-hex
   `source_git_commit` and an existing retained artifact, but their
   remaining provenance fields stay as imported rather than being
   retro-fitted to the finalizer schema.
10. **A durable measurement ref anchors every measurement commit.**
    Presence is not provenance.  A measurement commit always predates the
    commit that records it, because no commit can carry a baseline whose
    provenance hash is its own hash, so a finalized baseline stays
    verifiable only while a ref still reaches that older commit.  An object
    that no ref reaches survives only until the server collects it, so a
    fetch by explicit SHA must never substitute for an anchor.  Anchor the
    commit with an annotated tag named
    `refs/tags/perf-baseline/<baseline-stem>`, a namespace disjoint from
    the release tags `v<MAJOR>.<MINOR>.<PATCH>`.  The tag message must
    record the baseline stem, `source_run`, and `source_artifact_sha256`,
    so the ref audits itself.  Archival fallback: a `type:
    verbatim_import` pack exempted from those two fields under clause 9
    records the stem, the full `source_git_commit`, and the retained
    artifact path instead.  In both cases the gate enforces the anchor by
    resolving (`source_git_commit`, stem) — see `repo_commit_anchored()`
    — never by parsing the message.  A pushed anchor is immutable: never move it
    and never delete it.  Create one only when the finalizer produces a new
    canonical baseline whose measurement commit no existing ref anchors —
    a commit that an immutable ref (a release tag or an existing
    `perf-baseline/<stem>` tag) already reaches must not receive a
    redundant ref.  Reachability from the mutable `main` branch is not a
    durable anchor: it only defers anchor creation within the active
    release line, and the release freeze must anchor every measurement
    commit that no immutable ref reaches yet.  Archival `verbatim_import` packs receive no
    exemption from this clause.  Naming and lifecycle policy live in
    `perf/baselines/README.md`, section `Measurement Commit Durability`.

Verification:
- `python3 tools/harness/detect_baseline_hand_edit.py` — full audit over
  `perf/baselines/`: provenance field presence, commit identity binding,
  SHA format/history existence, artifact digest recomputation, timestamp
  format. Blocking harness gate.
- `python3 tools/harness/detect_baseline_hand_edit.py --changed <paths>`
  — changed-file mode for pre-commit use: flags finalized baselines that
  changed without their raw inputs.
- The same full audit resolves the durable measurement ref through
  `repo_commit_anchored(sha, stem)`: the canonical
  `refs/tags/perf-baseline/<stem>` fast path first, then any ref that
  reaches the commit, and an explicit SKIP plus a finding when a shallow or
  tagless checkout cannot decide. A present but unanchored commit fails
  the gate and the finding names the ref to create. The `harness-tooling`
  CI job first fetches the anchor refspec
  `+refs/tags/perf-baseline/*:refs/tags/perf-baseline/*`, then proves each
  commit present with `git cat-file -e` before the gate runs. An
  object-only fetch by explicit SHA does not satisfy this contract: it
  lands the commit as a second shallow root that no ref reaches.

**Verification:**
- `RELEASE_GATE_ALLOW_SKIP_MODULE=1 make release-gates-check-092` — blocking
  gate (requires `NGINX_BIN` or `RELEASE_GATE_ALLOW_SKIP_MODULE=1`), validates
  `baseline_policy`, top-level `module_benchmark` environment/identity,
  and each scenario's evidence objects at their respective levels.
- `make perf-evidence-check` — non-blocking report-only mode, validates the
  same invariant for PR visibility.
- **Evidence-pack regeneration check (both gates above):** the gate must
  regenerate the evidence pack twice from its retained inputs (raw report,
  raw probe artifacts) and compare the regenerated pack and its digest with
  the original values. `finalize_module_baseline.py` is the single
  regeneration path; `evidence_gate.py` recomputes the raw artifact SHA-256
  (`_verify_raw_digest`) and requires the finalized report to be
  byte-identical to the raw report (minus `baseline_policy`), so running the
  finalizer and the gate on the same retained inputs twice must yield the
  same pack and the same digest. A mismatch indicates nondeterministic
  generation or an unbound artifact and fails the gate (fail closed).
- `python3 -m pytest tools/perf/tests/` — perf tooling test suite
  (692 tests), must pass.
- Inspect `perf/baselines/module-baseline-092.json` `baseline_policy` and
  confirm it carries the six policy provenance fields above, inspect
  `module_benchmark` and scenario records for their separate schemas.

**Why this rule.** Without a single contract, evidence provenance fields
appeared one at a time as blockers surfaced (seven commits). The result
was working but undocumented as a unit — the next person to extend the gate
had no way to tell required from optional fields. This rule captures
the invariant the seven commits collectively established.

---

## Rule 62: Release matrix key normalization invariant

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
`os_type` / `os` / `libc`) stayed independent in each function.

**Required:**

1. **Single alias-resolution path.** Every function that reads a matrix entry
   dict (`load_matrix`, `_validate_matrix_entry`, `_validate_manual_entries`,
   `_entry_sort_key`, `compute_matrix`, `diff_matrix`) must resolve aliased
   keys through the same normalization entry point, so the normalization layer
   interprets an entry written with legacy keys (`nginx`, `os`) the same way as
   one written with canonical keys (`nginx_version`, `os_type`).

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
- `python3 -m pytest tools/release/matrix/tests/ -v --tb=short` — 114 tests,
  all must pass. These tests exercise both canonical (`nginx_version`/`os_type`)
  and legacy (`nginx`/`os`) key entries through the shared normalization path.

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
- **2026-08-22**: Added Rule 61 clause 10, the durable measurement ref.
  Two measurement commits behind the checked-in 0.9.1 and 0.9.2 baselines
  had fallen out of every branch and tag during the 2026-08-20/21 evidence
  churn, so the blocking provenance gate failed on any clean checkout, full
  clone included. Clauses 1–9 bound the evidence but never kept the
  measurement commit reachable. The clause records the anchor namespace,
  the annotated-tag and immutability requirements, and the rule that a
  commit an existing ref already reaches must not receive a redundant ref.
  `detect_baseline_hand_edit.py` gained `repo_commit_anchored()` and the
  `harness-tooling` CI job gained a provenance preparation step.
- **2026-07-29**: Corrected Rule 61 provenance field table to match real
  `baseline_policy` schema: `source_run` (not `source_workflow_run` +
  `source_workflow_attempt`), `source_artifact_sha256` (not
  `source_artifact_digest`), `normalization` added, `source_environment`
  moved to `scenario_sources`. Replaced "bounded metrics only" and
  "fallback-rate truth" with accurate fallback-rate consistency contract
  (stored versus counter-derived) and raw-artifact binding. Added
  `_fallback_rate_consistency_violations` to evidence gate.
- **2026-07-29**: Added Rules 61–62 after v0.9.0→HEAD fix-commit recurrence
  analysis identified two incremental-design gaps: the 7-commit performance
  evidence provenance chain and the 5-commit release-matrix key
  normalization chain, neither of which had a single stated contract.
