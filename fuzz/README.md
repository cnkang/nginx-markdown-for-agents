# Fuzz Testing Guide

This document describes the fuzz testing infrastructure, corpus management
strategy, and harness rules for the `nginx-markdown-for-agents` project.

---

## Local Fuzz Running

### Prerequisites

1. **Rust nightly toolchain** (required by cargo-fuzz / libFuzzer):

   ```bash
   rustup toolchain install nightly
   ```

2. **cargo-fuzz CLI**:

   ```bash
   cargo +nightly install cargo-fuzz
   ```

3. Verify installation:

   ```bash
   cargo +nightly fuzz --version
   ```

### Build all targets

From the `components/rust-converter` directory:

```bash
cargo +nightly fuzz build
```

### Smoke run (30 seconds per target)

```bash
cargo +nightly fuzz run convert_html            -- -max_total_time=30
cargo +nightly fuzz run streaming_chunks        -- -max_total_time=30
cargo +nightly fuzz run negotiation_and_headers -- -max_total_time=30
```

### Deep run (5 minutes)

```bash
cargo +nightly fuzz run convert_html            -- -max_total_time=300
cargo +nightly fuzz run streaming_chunks        -- -max_total_time=300
cargo +nightly fuzz run negotiation_and_headers -- -max_total_time=300
```

### Recommended parameters

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `-max_total_time=N` | unlimited | Wall-clock budget in seconds |
| `-max_len=65536` | 4096 | Maximum input size (bytes) |
| `-timeout=60` | 1200 | Per-input timeout (seconds) |
| `-jobs=N` | 1 | Parallel fuzzer processes |
| `-workers=N` | 1 | Parallel workers per job |

Example with all recommended settings:

```bash
cargo +nightly fuzz run convert_html -- \
  -max_total_time=300 \
  -max_len=65536 \
  -timeout=60 \
  -jobs=4 \
  -workers=4
```

### Makefile shortcuts

From the repository root:

```bash
make fuzz-smoke              # 30s smoke for new targets
make test-rust-fuzz-smoke    # 5s quick check for all targets
```

---

## Crash Analysis

When a target crashes, cargo-fuzz saves the triggering input to
`components/rust-converter/fuzz/artifacts/<target>/crash-<hash>`.

### Reproduce a crash

```bash
cargo +nightly fuzz run <target> components/rust-converter/fuzz/artifacts/<target>/crash-<hash>
```

### View backtrace

```bash
RUST_BACKTRACE=1 cargo +nightly fuzz run <target> components/rust-converter/fuzz/artifacts/<target>/crash-<hash>
```

For full symbol information:

```bash
RUST_BACKTRACE=full cargo +nightly fuzz run <target> components/rust-converter/fuzz/artifacts/<target>/crash-<hash>
```

### Artifact types

| File prefix | Meaning |
|-------------|---------|
| `crash-*` | Input triggered a panic or sanitizer report |
| `timeout-*` | Input exceeded the per-input timeout |
| `oom-*` | Input caused out-of-memory |
| `leak-*` | Input triggered a memory leak (LSan) |

### Minimization

Use `cargo fuzz tmin` to reduce a crash artifact to the smallest input that
still triggers the same failure:

```bash
cargo +nightly fuzz tmin <target> components/rust-converter/fuzz/artifacts/<target>/crash-<hash>
```

The minimized input is written to `components/rust-converter/fuzz/artifacts/<target>/minimized-from-<hash>`.

### Security classification

Any finding reported by a sanitizer (ASan, UBSan, MSan) is classified as a
**security defect** and must follow the project's security vulnerability
reporting process. Do NOT commit crash artifacts that trigger sanitizer reports
to public branches. Report via the project's private security channel or GitHub
Security Advisory.

---

## Contribution Workflow

### Adding a new fuzz target

1. Create a new target file in `components/rust-converter/fuzz/fuzz_targets/<target_name>.rs`
2. Add the target to `components/rust-converter/fuzz/Cargo.toml` under `[[bin]]`
3. Create a seed corpus directory at `components/rust-converter/fuzz/corpus/<target_name>/`
4. Add representative seed inputs (valid and edge-case samples)
5. Verify the target builds: `cargo +nightly fuzz build`
6. Run a smoke test: `cargo +nightly fuzz run <target_name> -- -max_total_time=30`
7. Update this README's Target Summary table

### Updating an existing target

When modifying converter logic that affects a fuzz target:

1. Verify the target still builds: `cargo +nightly fuzz build`
2. Run a smoke test to confirm no regressions: `make test-rust-fuzz-smoke`
3. If new code paths are introduced, add seed corpus inputs that exercise them
4. Update the target's input model documentation if the byte layout changes

### Handling crashes found by CI

1. Download the crash artifact from the GitHub Actions workflow run
2. Reproduce locally: `cargo +nightly fuzz run <target> <crash_file>`
3. Minimize: `cargo +nightly fuzz tmin <target> <crash_file>`
4. Assess security impact (sanitizer report = security defect)
5. Fix the underlying bug
6. Add minimized input to regression corpus:
   ```bash
   cp components/rust-converter/fuzz/artifacts/<target>/minimized-from-<hash> \
      components/rust-converter/fuzz/corpus/<target>/regression_<descriptive_name>.bin
   git add -f components/rust-converter/fuzz/corpus/<target>/regression_<descriptive_name>.bin
   ```
7. Verify the fix: `cargo +nightly fuzz run <target> -- -max_total_time=30`
8. Close the issue only after regression input is committed and CI passes

### PR checklist for fuzz-related changes

- [ ] `cargo +nightly fuzz build` succeeds
- [ ] `make test-rust-fuzz-smoke` passes
- [ ] New code paths have corresponding seed corpus inputs
- [ ] Fuzz target documentation updated if input model changed
- [ ] No crash artifacts committed to public branches

---

## Seed Corpus Structure

The seed corpus provides meaningful starting inputs for the fuzzer. Each target
has its own corpus directory under `components/rust-converter/fuzz/corpus/<target>/`.

### Directory layout

```
components/rust-converter/fuzz/corpus/
├── convert_html/
│   ├── basic_heading.html          # Simple heading
│   ├── nested_lists.html           # Nested list structures
│   ├── code_blocks.html            # Code block variations
│   ├── mixed_content.html          # Mixed inline/block elements
│   └── regression_*.bin            # Minimized crash regressions
├── streaming_chunks/
│   ├── single_chunk.bin            # Complete input in one chunk
│   ├── multi_chunk.bin             # Input split across chunks
│   ├── empty_chunks.bin            # Zero-length chunk boundaries
│   └── regression_*.bin            # Minimized crash regressions
└── negotiation_and_headers/
    ├── accept_markdown.bin         # Valid Accept: text/markdown
    ├── accept_html.bin             # Valid Accept: text/html
    ├── malformed_accept.bin        # Malformed Accept header
    └── regression_*.bin            # Minimized crash regressions
```

### Seed corpus guidelines

- Include both valid and edge-case inputs
- Keep individual files small (under 1KB preferred for fast iteration)
- Use descriptive filenames that indicate the input's purpose
- Regression files use the `regression_<description>.bin` naming convention
- Seed corpus files are committed via `git add -f` (the directory may be
  partially gitignored for generated content)

> For detailed corpus classification (seed, generated, crash, regression,
> coverage), see the [Corpus Classification](#corpus-classification) section
> below.

---

## Corpus Classification

The project's fuzz corpus is divided into five categories, each with different
storage locations, version control strategies, and lifecycle management.

### Classification Overview

| Category | Storage Location | Committed to Main Repo | Lifecycle |
|----------|-----------------|------------------------|-----------|
| Seed Corpus | `components/rust-converter/fuzz/corpus/<target>/` | ✅ | Permanent (manually maintained) |
| Generated Corpus | `<repo>-corpus` repo main branch | ❌ | Auto-managed (pruned periodically) |
| Crash Artifacts | Workflow Artifacts | ❌ | 90 days (GitHub default) |
| Minimized Regression Corpus | `components/rust-converter/fuzz/corpus/<target>/` | ✅ | Permanent (fix verification) |
| Coverage Corpus | `<repo>-corpus` repo coverage branch | ❌ | Auto-managed |

### Category Descriptions

- **Seed Corpus**: Manually written initial input sets that provide meaningful
  starting coverage for the fuzzer. Stored in `components/rust-converter/fuzz/corpus/<target>/`, committed
  to the main repository, and maintained by developers.
- **Generated Corpus**: Coverage-expanding inputs automatically generated by CI
  batch fuzzing. Stored in the independent `<repo>-corpus` repository on the
  main branch, periodically pruned by the corpus pruning workflow.
- **Crash Artifacts**: Input files that triggered crashes, automatically
  uploaded by CI workflows as GitHub Actions Workflow Artifacts. Retained for
  90 days.
- **Minimized Regression Corpus**: Crash inputs reduced via `cargo fuzz tmin`,
  committed to the main repository as permanent regression tests. Named using
  the `regression_<description>.bin` convention.
- **Coverage Corpus**: Coverage-guided corpus generated by CI, stored in the
  `<repo>-corpus` repository on the coverage branch, automatically maintained
  by ClusterFuzzLite.

> For component-specific details, see
> [components/rust-converter/fuzz/README.md](../components/rust-converter/fuzz/README.md#corpus-classification).

---

## Applicable Harness Rules

Canonical rule definitions live in
[`docs/harness/rules/fuzz-infrastructure.md`](../docs/harness/rules/fuzz-infrastructure.md).

| Rule ID | Name | Level | Summary |
|---------|------|-------|---------|
| FUZZ-001 | Converter Adjacent-Change Impact Assessment | Advisory | Assess fuzz target coverage when modifying converter modules |
| FUZZ-002 | New Logic Must Have Fuzz Coverage | Advisory | New untrusted-input code paths require fuzz target coverage |
| FUZZ-003 | Target Deterministic Execution | Mandatory | Fuzz targets must be deterministic with no side effects |
| FUZZ-004 | Crash Minimization Workflow | Mandatory | Minimize, archive, and verify before closing crash issues |
| FUZZ-005 | Batch Requires Paired Pruning | Mandatory | Batch fuzzing workflow requires a paired pruning workflow |
| FUZZ-006 | Doc-Only Changes Skip Fuzz | Mandatory | Path filters prevent fuzz CI on documentation-only changes |
| FUZZ-007 | Infrastructure Passes harness-check | Mandatory | All fuzz infrastructure must pass `make harness-check` |

---

## References

- [AGENTS.md](../AGENTS.md) — Repository engineering rules
- [docs/harness/rules/testing-coverage.md](../docs/harness/rules/testing-coverage.md) — Testing coverage rule index
- [docs/harness/rules/fuzz-infrastructure.md](../docs/harness/rules/fuzz-infrastructure.md) — Full fuzz rule definitions
- [ClusterFuzzLite documentation](https://google.github.io/clusterfuzzlite/) — Official docs
