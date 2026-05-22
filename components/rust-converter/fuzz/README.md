# Fuzz Testing Guide

This directory contains [cargo-fuzz](https://github.com/rust-fuzz/cargo-fuzz) targets for the
`nginx-markdown-converter` crate. Each target exercises a different subsystem with arbitrary
byte inputs, checking for panics, memory-safety violations, timeouts, and logic errors.

---

## Prerequisites

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

---

## Running Targets

All commands assume the working directory is `components/rust-converter`.

### Build all targets

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
`fuzz/artifacts/<target>/crash-<hash>`.

### Reproduce a crash

```bash
cargo +nightly fuzz run <target> fuzz/artifacts/<target>/crash-<hash>
```

### View backtrace

```bash
RUST_BACKTRACE=1 cargo +nightly fuzz run <target> fuzz/artifacts/<target>/crash-<hash>
```

For full symbol information:

```bash
RUST_BACKTRACE=full cargo +nightly fuzz run <target> fuzz/artifacts/<target>/crash-<hash>
```

### Artifact types

| File prefix | Meaning |
|-------------|---------|
| `crash-*` | Input triggered a panic or sanitizer report |
| `timeout-*` | Input exceeded the per-input timeout |
| `oom-*` | Input caused out-of-memory |
| `leak-*` | Input triggered a memory leak (LSan) |

---

## Minimization

Use `cargo fuzz tmin` to reduce a crash artifact to the smallest input that
still triggers the same failure:

```bash
cargo +nightly fuzz tmin <target> fuzz/artifacts/<target>/crash-<hash>
```

The minimized input is written to `fuzz/artifacts/<target>/minimized-from-<hash>`.

### Tips

- Minimization can take several minutes for complex crashes.
- Use `-- -timeout=60` if the original crash was a timeout.
- The minimized file is deterministic — re-running produces the same result.

---

## Regression Corpus

After fixing a crash, add the minimized input to the seed corpus so the fuzzer
continuously re-tests the fixed path:

```bash
# 1. Minimize the crash
cargo +nightly fuzz tmin <target> fuzz/artifacts/<target>/crash-<hash>

# 2. Copy minimized input to the seed corpus
cp fuzz/artifacts/<target>/minimized-from-<hash> \
   fuzz/corpus/<target>/regression_<descriptive_name>.bin

# 3. Force-add to version control (corpus dir is gitignored by default)
git add -f fuzz/corpus/<target>/regression_<descriptive_name>.bin

# 4. Verify the fix holds
cargo +nightly fuzz run <target> -- -max_total_time=30
```

### Naming convention

Use descriptive names for regression files:

- `regression_empty_input_panic.bin`
- `regression_deep_nesting_overflow.bin`
- `regression_malformed_utf8_boundary.bin`

---

## 语料库分类 (Corpus Classification)

本项目的 fuzz 语料库分为五类，各有不同的存储位置、版本控制策略和生命周期管理。

### 分类总览

| 类别 | 存储位置 | 签入主仓库 | 生命周期 | 访问权限 |
|------|----------|-----------|----------|----------|
| Seed Corpus | `fuzz/corpus/<target>/` | ✅ | 永久（手工维护） | 所有开发者 |
| Generated Corpus | `<repo>-corpus` 仓库 main 分支 | ❌ | 自动管理（prune 精简） | CI 系统 |
| Crash Artifacts | Workflow Artifacts | ❌ | 90 天（GitHub 默认） | 仓库成员 |
| Minimized Regression Corpus | `fuzz/corpus/<target>/` | ✅ | 永久（修复验证） | 所有开发者 |
| Coverage Corpus | `<repo>-corpus` 仓库 coverage 分支 | ❌ | 自动管理 | CI 系统 |

### 各类详细说明

#### Seed Corpus

手工编写的初始输入集合，为 fuzzer 提供有意义的起始覆盖。

- **存储位置**：`fuzz/corpus/<target>/`（例如 `fuzz/corpus/convert_html/`）
- **签入主仓库**：✅ 是（通过 `git add -f` 追踪）
- **生命周期**：永久保留，由开发者手工维护和更新
- **访问权限**：所有开发者可读写
- **维护策略**：新增 fuzz target 时应同步创建对应的种子语料库目录，包含代表性输入样本

#### Generated Corpus

CI fuzzing 运行时自动生成的覆盖扩展输入，由 ClusterFuzzLite batch fuzzing 持续积累。

- **存储位置**：独立存储仓库 `<repo>-corpus` 的 main 分支
- **签入主仓库**：❌ 否（防止主仓库 git 历史膨胀）
- **生命周期**：自动管理，由 corpus pruning 工作流定期精简冗余输入
- **访问权限**：CI 系统（通过 `CORPUS_REPO_TOKEN` secret 访问）
- **维护策略**：无需人工干预，prune 工作流自动保持最小覆盖集

#### Crash Artifacts

触发 crash 的输入文件，由 CI fuzzing 工作流在发现问题时自动上传。

- **存储位置**：GitHub Actions Workflow Artifacts
- **签入主仓库**：❌ 否
- **生命周期**：90 天（GitHub Actions artifacts 默认保留期限）
- **访问权限**：仓库成员可下载
- **维护策略**：发现 crash 后应及时下载、分析、minimization，转为 regression corpus

#### Minimized Regression Corpus

经过 `cargo fuzz tmin` 精简和人工审查的 crash 输入，作为回归测试永久保留。

- **存储位置**：`fuzz/corpus/<target>/`（与 seed corpus 同目录）
- **签入主仓库**：✅ 是（通过 `git add -f` 追踪）
- **生命周期**：永久保留，确保修复后的路径持续被 fuzzer 验证
- **访问权限**：所有开发者可读写
- **维护策略**：每次修复 crash 后，将 minimized input 添加到对应 target 的 corpus 目录，命名遵循 `regression_<描述>.bin` 约定

#### Coverage Corpus

CI 生成的覆盖引导语料库，用于跟踪代码覆盖率进展。

- **存储位置**：独立存储仓库 `<repo>-corpus` 的 coverage 分支
- **签入主仓库**：❌ 否
- **生命周期**：自动管理，随 batch fuzzing 运行持续更新
- **访问权限**：CI 系统
- **维护策略**：无需人工干预，由 ClusterFuzzLite 自动维护

---

## Target Summary

### New targets (v0.7.0)

| Target | Purpose | Input Model | Key Invariants |
|--------|---------|-------------|----------------|
| `convert_html` | Full HTML→Markdown pipeline | First 4 bytes → option derivation (front_matter, estimate_tokens, flavor, sanitize_mode, prune_noise); remaining bytes → HTML via `from_utf8_lossy` | No panic; no sanitizer report; completes in 60s; output ≤ input×10 + 4096 |
| `streaming_chunks` | Streaming chunk boundary testing | Byte 0 → mode flags (EOF, partial); byte 1 → chunk count seed; bytes 2..N → split points; rest → HTML body | No panic; arbitrary splits stable (zero-len, 1-byte, >1000 chunks); consistency: single-pass == chunked (whitespace-normalized) |
| `negotiation_and_headers` | Content negotiation + decision + header plan | Fixed regions: 0..64 Accept, 64..96 Content-Type, 96..98 status code, 98..102 config flags, 102..166 User-Agent, 166.. extensions | No panic; deterministic decisions (same input → same output); malformed headers stable |

### Existing targets

| Target | Purpose | Input Model | Key Invariants |
|--------|---------|-------------|----------------|
| `parser_html` | HTML parsing stage | Raw bytes → `parse_html` | No panic; no sanitizer report |
| `ffi_convert` | FFI conversion entry point | Raw bytes through FFI boundary | No panic; no memory safety violations |
| `security_validator` | Security validation logic | Raw bytes → security validator | No panic; no bypass on malformed input |
| `fuzz_streaming_no_panic` | Streaming converter no-panic | Raw bytes → streaming converter | No panic on any input |
| `fuzz_streaming_chunk_split` | Streaming chunk splitting | Raw bytes with split points | No panic on arbitrary splits |
| `fuzz_streaming_malformed` | Streaming with malformed input | Malformed/invalid bytes → streaming | No panic; graceful error handling |
| `fuzz_decompression` | Decompression logic | Compressed/malformed bytes → decompressor | No panic; no buffer overflow |
| `fuzz_url_validation` | URL validation | Arbitrary strings → URL validator | No panic; no bypass on crafted URLs |

---

## Security Classification

### Sanitizer reports are security defects

Any finding reported by a sanitizer is classified as a **security defect** and
must follow the project's security vulnerability reporting process:

| Report Source | Classification | Action |
|---------------|---------------|--------|
| AddressSanitizer (ASan) | **Security defect** | File security advisory; do NOT disclose publicly |
| UndefinedBehaviorSanitizer (UBSan) | **Security defect** | File security advisory; do NOT disclose publicly |
| MemorySanitizer (MSan) | **Security defect** | File security advisory; do NOT disclose publicly |
| Panic (unwrap/expect/assert) | Logic defect | Create issue, priority P1 |
| Timeout (>60s) | Performance defect | Create issue, priority P2 |
| Output size exceeds bound | Needs investigation | Create issue, triage required |

### Reporting process

1. **Do NOT** commit crash artifacts that trigger sanitizer reports to public branches.
2. Report via the project's private security channel or GitHub Security Advisory.
3. Include: target name, minimized input (base64-encoded), sanitizer output, backtrace.
4. Assign severity based on exploitability assessment.

---

## API Exposure Strategy (`cfg(fuzzing)`)

### Mechanism

Fuzz targets primarily use the crate's **public API**:

- `converter::MarkdownConverter::convert()` / `convert_with_context()`
- `parser::parse_html()` / `parse_html_with_charset()`
- `streaming::converter::StreamingConverter`
- `negotiator::negotiate()`
- `decision::make_decision()`
- `header_plan::HeaderPlan::for_markdown_conversion()`

When a target needs access to internal functions (e.g., budget checks,
internal conversion context), the crate exposes them via `#[cfg(fuzzing)]`:

```rust
// In src/converter.rs
#[cfg(fuzzing)]
pub fn convert_with_raw_bytes(html: &[u8], options: &ConversionOptions) -> Result<String, ConversionError> {
    // Internal entry point, only visible in fuzzing builds
}
```

### Why `cfg(fuzzing)`?

- **Automatic activation**: cargo-fuzz passes `--cfg fuzzing` to rustc — no
  manual feature flags needed.
- **Zero production impact**: `cargo build --release` never includes these
  functions. The public API surface is unchanged.
- **No feature gate pollution**: Unlike `#[cfg(feature = "fuzzing")]`, this
  approach doesn't require adding a feature to `Cargo.toml` that downstream
  consumers might accidentally enable.
- **Consistent with ecosystem**: This is the standard pattern used by the Rust
  fuzzing ecosystem (libfuzzer-sys, afl.rs, etc.).

### Verification

Confirm that fuzz-only functions are absent from release builds:

```bash
cargo build --release
# Inspect exported symbols (should not contain fuzz-only functions)
nm target/release/libnginx_markdown_converter.rlib 2>/dev/null | grep -c "fuzzing" || true
```

---

## Directory Structure

```
fuzz/
├── Cargo.toml                          # Fuzz crate manifest
├── Cargo.lock                          # Locked dependencies
├── README.md                           # This file
├── fuzz_targets/
│   ├── convert_html.rs                 # Full pipeline target
│   ├── streaming_chunks.rs             # Chunk boundary target
│   ├── negotiation_and_headers.rs      # Negotiation target
│   ├── parser_html.rs                  # HTML parser target
│   ├── ffi_convert.rs                  # FFI entry target
│   ├── security_validator.rs           # Security validation target
│   ├── fuzz_streaming_no_panic.rs      # Streaming no-panic target
│   ├── fuzz_streaming_chunk_split.rs   # Streaming chunk split target
│   ├── fuzz_streaming_malformed.rs     # Streaming malformed target
│   ├── fuzz_decompression.rs           # Decompression target
│   └── fuzz_url_validation.rs          # URL validation target
├── corpus/
│   ├── convert_html/                   # Seed corpus (version-controlled)
│   ├── streaming_chunks/               # Seed corpus (version-controlled)
│   └── negotiation_and_headers/        # Seed corpus (version-controlled)
├── artifacts/
│   └── .gitkeep                        # Crash artifacts (gitignored)
└── target/                             # Build output (gitignored)
```

---

## Troubleshooting

### "error: toolchain 'nightly' is not installed"

```bash
rustup toolchain install nightly
```

### "error: no such subcommand: fuzz"

```bash
cargo +nightly install cargo-fuzz
```

### Target won't compile after upstream changes

Fuzz targets depend on the converter's API. If a refactor changes function
signatures, update the affected target file and re-run `cargo +nightly fuzz build`.

### Fuzzer runs but finds no new coverage

- Ensure seed corpus files exist in `fuzz/corpus/<target>/`.
- Try increasing `max_len` if the target processes structured input.
- Use `-print_final_stats=1` to see coverage metrics.

---

## 存储策略

This section documents the corpus storage strategy for CI-generated fuzzing
artifacts. The goal is to prevent uncontrolled generated corpus from polluting
the main repository's git history while maintaining persistent coverage data
across fuzzing runs.

### 独立存储仓库方案

CI-generated corpus (from batch fuzzing and coverage runs) is stored in a
**separate repository** named `<repo>-corpus` (e.g.,
`nginx-markdown-for-agents-corpus`).

#### 选择理由

| 方案 | 优点 | 缺点 | 选择 |
|------|------|------|------|
| 独立存储仓库 | 权限隔离；无大小限制；ClusterFuzzLite 原生支持 | 需创建额外仓库；需配置访问 token | ✅ |
| 独立分支 | 无需额外仓库 | 污染主仓库 git 历史；大小受限 | ❌ |
| GitHub Actions cache | 简单 | 7 天过期；大小受限（10GB）；无法跨 workflow 共享 | ❌ |

The independent repository approach was chosen because:

1. **ClusterFuzzLite native support** — the `storage-repo` parameter directly
   accepts an external repository, requiring no custom scripting.
2. **Permission isolation** — the corpus repo can have narrower write access
   than the main repo, reducing blast radius of token compromise.
3. **No size limits** — generated corpus can grow freely without bloating the
   main repo's clone size or triggering GitHub's repository size warnings.

### 访问方式

Batch and prune workflows access the corpus repository using a **Personal
Access Token (PAT)** or **GitHub App token** stored as a repository secret:

- **Secret name**: `CORPUS_REPO_TOKEN`
- **Required permissions**: `contents: write` on the corpus repository
- **Usage**: Passed to ClusterFuzzLite via the `storage-repo` workflow parameter

```yaml
# Example workflow configuration
- uses: google/clusterfuzzlite/actions/run_fuzzers@v1
  with:
    mode: batch
    storage-repo: ${{ github.repository }}-corpus
    storage-repo-branch: main
    storage-repo-branch-coverage: coverage
  env:
    CORPUS_REPO_TOKEN: ${{ secrets.CORPUS_REPO_TOKEN }}
```

**Security notes**:
- The PR fuzz workflow (`cflite_pr.yml`) does **not** use `CORPUS_REPO_TOKEN`
  because it runs in `code-change` mode which is self-contained.
- Fork PRs cannot access repository secrets, so corpus storage is only
  available to trusted workflows (batch/prune).

### 清理策略

Corpus pruning is handled automatically by the `cflite_cron.yml` workflow:

- **Frequency**: Weekly (every Sunday at UTC 06:00)
- **Mode**: ClusterFuzzLite `prune` mode
- **Effect**: Removes redundant inputs that do not contribute unique coverage,
  keeping only the minimal set that achieves the same code coverage.
- **Storage target**: Same `<repo>-corpus` repository and branch used by batch
  fuzzing, ensuring the pruned corpus is what batch fuzzing consumes.

If pruning fails, the workflow is marked as failed and triggers GitHub's
default notification mechanism. Persistent pruning failures lead to corpus
bloat, which increases batch fuzzing startup time.

### .gitignore 排除策略

The main repository's `.gitignore` excludes all generated corpus and artifact
paths to prevent accidental commits:

```gitignore
# Fuzz artifacts and generated corpus (do NOT commit)
fuzz/artifacts/
fuzz/target/
```

**What IS committed to the main repo** (via `git add -f`):
- `fuzz/corpus/<target>/` — hand-written seed corpus files
- `fuzz/corpus/<target>/regression_*.bin` — minimized regression inputs after
  review

**What is NOT committed**:
- Generated corpus from CI batch fuzzing → stored in `<repo>-corpus`
- Coverage corpus → stored in `<repo>-corpus` (coverage branch)
- Crash artifacts → uploaded as workflow artifacts (90-day retention)
- Local fuzzing output (`fuzz/target/`, `fuzz/artifacts/`) → gitignored

This separation ensures the main repository stays lean while CI-generated
coverage data persists across runs in the dedicated corpus repository.
