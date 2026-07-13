---
domain: fuzz-infrastructure
rules: [FUZZ-001, FUZZ-002, FUZZ-003, FUZZ-004, FUZZ-005, FUZZ-006, FUZZ-007]
paths:
  - "fuzz/**"
  - "components/rust-converter/fuzz/**"
  - ".clusterfuzzlite/**"
  - ".github/workflows/cflite_*.yml"
---

## Fuzz Infrastructure Rules

### FUZZ-001: Converter Adjacent-Change Impact Assessment

| Attribute | Value |
|-----------|-------|
| Rule ID | FUZZ-001 |
| Name | Converter Adjacent-Change Impact Assessment |
| Enforcement Level | Advisory (PR review) |
| Verification Method | Manual review |

**Description:**

When a developer modifies parser, sanitizer, or emitter modules under
`components/rust-converter/src/`, they must assess whether the change affects
the coverage scope of existing fuzz targets. If the change introduces new
parsing paths, new input-handling branches, or modifies boundary conditions of
existing behavior, the corresponding fuzz target should be updated to cover the
new behavior.

**Applicable scenarios:**
- Modifying HTML parser tag-handling logic
- Modifying sanitizer filtering rules
- Modifying emitter output formatting logic
- Modifying streaming chunk processing boundaries

**Requirements:**
- PR authors must evaluate whether fuzz targets need updating to cover the change.

**PR Review Checklist item:**
- [ ] Assessed whether fuzz targets need updating to cover this change

---

### FUZZ-002: New Logic Must Have Fuzz Coverage

| Attribute | Value |
|-----------|-------|
| Rule ID | FUZZ-002 |
| Name | New Logic Must Have Fuzz Coverage |
| Enforcement Level | Advisory (PR review) |
| Verification Method | Manual review |

**Description:**

When adding new parser, header parsing, negotiation, or streaming logic,
developers must ensure the corresponding fuzz target covers the new logic, or
create a new fuzz target. This ensures all code paths handling untrusted input
have continuous fuzzing coverage.

**Applicable scenarios:**
- Adding support for new HTML tags or attributes
- Adding Accept header negotiation logic
- Adding streaming chunk processing paths
- Adding input-handling functions at the FFI boundary

**Requirements:**
- PRs introducing new logic must include fuzz target updates or additions.
- If new logic cannot be covered by existing targets, create a new target with
  a seed corpus.

---

### FUZZ-003: Target Deterministic Execution

| Attribute | Value |
|-----------|-------|
| Rule ID | FUZZ-003 |
| Name | Target Deterministic Execution |
| Enforcement Level | Mandatory |
| Verification Method | Code review + harness-check |

**Description:**

All fuzz targets must satisfy deterministic execution and side-effect
constraints: the same input must produce the same behavior. This is a
prerequisite for the fuzzing engine to work correctly — non-deterministic
behavior causes coverage guidance to fail and crashes to become unreproducible.

**Prohibited operations:**
- Network I/O (TCP/UDP connections, HTTP requests)
- Filesystem writes (creating/modifying/deleting files)
- Depending on system time (`SystemTime::now()`, `Instant::now()` for logic branching)
- External random sources (non-deterministic RNG from the `rand` crate)
- Reading environment variables for logic branching
- Process/thread creation

**Permitted operations:**
- Pure in-memory computation
- Heap allocation (monitored by the fuzzer)
- Reading compile-time constants
- Using deterministic APIs provided by `libFuzzer`

**Verification method:**
- Code review confirms no prohibited operations are present.
- `harness-check` static scan of fuzz target source for prohibited API calls
  (future extension).

---

### FUZZ-004: Crash Minimization Workflow

| Attribute | Value |
|-----------|-------|
| Rule ID | FUZZ-004 |
| Name | Crash Minimization Workflow |
| Enforcement Level | Mandatory (before issue closure) |
| Verification Method | Manual verification |

**Description:**

When fuzzing discovers a crash, the following workflow must be completed before
closing the related issue:

1. **Minimize**: Use `cargo fuzz tmin <target> <crash_file>` to reduce the
   crash input to the minimal reproducing set.
2. **Archive**: Add the minimized input to `fuzz/corpus/<target>/` as a
   regression test input.
3. **Verify fix**: Confirm the target no longer crashes on the minimized input
   after the fix.
4. **Regression protection**: Commit the minimized input to the main repository
   so future CI fuzzing continuously verifies it.

**Workflow:**
```bash
# 1. Download crash artifact
# 2. Minimize
cargo +nightly fuzz tmin <target> <crash_file>

# 3. Copy minimized input to regression corpus
cp minimized_input fuzz/corpus/<target>/regression_<issue_id>

# 4. Verify fix
cargo +nightly fuzz run <target> fuzz/corpus/<target>/regression_<issue_id>

# 5. Commit to main repository
git add fuzz/corpus/<target>/regression_<issue_id>
```

**Issue closure conditions:**
- Minimized regression input is committed to the main repository.
- The target no longer crashes on the input after the fix.
- CI fuzzing includes the regression input.

---

### FUZZ-005: Batch Requires Paired Pruning

| Attribute | Value |
|-----------|-------|
| Rule ID | FUZZ-005 |
| Name | Batch Requires Paired Pruning |
| Enforcement Level | Mandatory |
| Verification Method | harness-check automated verification |

**Description:**

When a batch fuzzing workflow (`cflite_batch.yml`) exists, a corresponding
corpus pruning mechanism (`cflite_cron.yml` or equivalent workflow) must also
exist. This prevents the generated corpus from growing unboundedly, which
causes:
- Excessive storage consumption
- Slow batch fuzzing startup times
- Redundant inputs reducing fuzzing efficiency

**Verification method:**
- `make harness-check` automatically checks: if `cflite_batch.yml` exists,
  then `cflite_cron.yml` (or a workflow containing prune mode) must also exist.
- Check failure results in a FAIL (not WARNING) from harness-check.

**Pruning requirements:**
- Pruning frequency must be at least weekly.
- Pruning must use the same corpus storage repository/branch as batch fuzzing.
- Pruning failures must mark the workflow as failed (triggering notifications).

---

### FUZZ-006: Doc-Only Changes Skip Fuzz

| Attribute | Value |
|-----------|-------|
| Rule ID | FUZZ-006 |
| Name | Doc-Only Changes Skip Fuzz |
| Enforcement Level | Mandatory |
| Verification Method | Path filters |

**Description:**

Fuzz CI workflows must not run for unrelated documentation-only changes (unless
explicitly requested). This is implemented via GitHub Actions path filters
(`paths` trigger conditions), ensuring PR fuzzing is only triggered when the
following paths change:

- `components/rust-converter/**`
- `fuzz/**`
- `.clusterfuzzlite/**`
- `.github/workflows/cflite_*.yml`
- `Makefile`
- `Cargo.lock`
- `components/rust-converter/Cargo.toml`
- `docs/harness/**`
- `tools/release/gates/**`

**Excluded paths (do not trigger fuzz):**
- `docs/guides/**`, `docs/features/**`, `docs/architecture/**` and other
  documentation-only directories
- `README.md`, `CHANGELOG.md` and other top-level documents
- `.kiro/`, `.codeartsdoer/` and other tool configurations

**Verification method:**
- The PR workflow (`cflite_pr.yml`) `on.pull_request.paths` configuration
  implements this filtering.
- `harness-check` verifies the PR workflow contains path filters.

---

### FUZZ-007: Infrastructure Passes harness-check

| Attribute | Value |
|-----------|-------|
| Rule ID | FUZZ-007 |
| Name | Infrastructure Passes harness-check |
| Enforcement Level | Mandatory |
| Verification Method | `make harness-check` |

**Description:**

All fuzz infrastructure must pass `make harness-check` validation. This
includes:

1. **Build configuration completeness**: `project.yaml`, `Dockerfile`, and
   `build.sh` exist and are correctly configured under `.clusterfuzzlite/`.
2. **Workflow completeness**: PR/batch/prune workflow files exist.
3. **Sanitizer configuration**: Workflows use address sanitizer.
4. **Path filters**: PR workflow has path filters (FUZZ-006).
5. **Batch-Prune pairing**: When batch workflow exists, prune workflow must
   also exist (FUZZ-005).
6. **Documentation completeness**: `fuzz/README.md` exists.
7. **Gitignore configuration**: `.gitignore` excludes generated corpus paths.
8. **Container privilege**: the ClusterFuzzLite Dockerfile ends with a
   non-root user while keeping the Rust toolchain readable and the
   source/build/output paths writable for the action's mounted directories.

**Verification command:**
```bash
make harness-check
```

**Failure handling:**
- Any check item failure causes harness-check to return a non-zero exit code.
- Failure messages clearly indicate the missing or incorrect configuration.
- harness-check failure in CI blocks merge.
