# Fuzz Testing Guide

本文档描述 `nginx-markdown-for-agents` 项目的 fuzz testing 基础设施、语料库管理策略和 harness 规则体系。

---

## Harness 规则

本节定义 fuzz harness 规则体系（FUZZ-001 至 FUZZ-007），为团队在开发过程中提供一致的 fuzzing 实践指导。

### 规则总览

| 规则 ID | 名称 | 强制级别 | 验证方式 |
|---------|------|----------|----------|
| FUZZ-001 | Converter 相邻变更评估 | Advisory（PR review） | 人工审查 |
| FUZZ-002 | 新逻辑必须有 fuzz 覆盖 | Advisory（PR review） | 人工审查 |
| FUZZ-003 | Target 确定性执行 | Mandatory | 代码审查 + harness-check |
| FUZZ-004 | Crash minimization 流程 | Mandatory（issue 关闭前） | 人工验证 |
| FUZZ-005 | Batch 配套 pruning | Mandatory | harness-check 自动验证 |
| FUZZ-006 | 纯文档变更跳过 fuzz | Mandatory | 路径过滤器 |
| FUZZ-007 | 基础设施通过 harness-check | Mandatory | `make harness-check` |

### FUZZ-001: Converter 相邻变更评估

| 属性 | 值 |
|------|-----|
| 规则 ID | FUZZ-001 |
| 名称 | Converter 相邻变更评估 |
| 强制级别 | Advisory（PR review） |
| 验证方式 | 人工审查 |

**详细说明：**

当开发者修改 `components/rust-converter/src/` 下的 parser、sanitizer、emitter
模块时，必须评估变更是否影响现有 fuzz target 的覆盖范围。如果变更引入了新的
解析路径、新的输入处理分支、或修改了现有行为的边界条件，应考虑更新对应的
fuzz target 以覆盖新行为。

**适用场景：**
- 修改 HTML parser 的标签处理逻辑
- 修改 sanitizer 的过滤规则
- 修改 emitter 的输出格式化逻辑
- 修改 streaming chunk 处理边界

**PR Review Checklist 项：**
- [ ] 已评估 fuzz target 是否需要更新以覆盖本次变更

---

### FUZZ-002: 新逻辑必须有 fuzz 覆盖

| 属性 | 值 |
|------|-----|
| 规则 ID | FUZZ-002 |
| 名称 | 新逻辑必须有 fuzz 覆盖 |
| 强制级别 | Advisory（PR review） |
| 验证方式 | 人工审查 |

**详细说明：**

新增 parser、header 解析、negotiation、streaming 逻辑时，开发者必须确保对应的
fuzz target 覆盖新逻辑，或创建新的 fuzz target。这确保所有面向不可信输入的
代码路径都有持续的 fuzzing 覆盖。

**适用场景：**
- 新增 HTML 标签或属性的解析支持
- 新增 Accept header negotiation 逻辑
- 新增 streaming 分块处理路径
- 新增 FFI 边界的输入处理函数

**要求：**
- 新增逻辑的 PR 必须包含 fuzz target 更新或新增
- 如果新逻辑无法被现有 target 覆盖，需创建新 target 并添加种子语料库

---

### FUZZ-003: Target 确定性执行

| 属性 | 值 |
|------|-----|
| 规则 ID | FUZZ-003 |
| 名称 | Target 确定性执行 |
| 强制级别 | Mandatory |
| 验证方式 | 代码审查 + harness-check |

**详细说明：**

所有 fuzz target 必须满足确定性执行和副作用受限约束：相同输入必须产生相同行为。
这是 fuzzing 引擎正确工作的前提——非确定性行为会导致覆盖引导失效、crash 不可复现。

**禁止的操作：**
- 网络 I/O（TCP/UDP 连接、HTTP 请求）
- 文件系统写入（创建/修改/删除文件）
- 依赖系统时间（`SystemTime::now()`、`Instant::now()` 用于逻辑分支）
- 外部随机源（`rand` crate 的非确定性 RNG）
- 环境变量读取用于逻辑分支
- 进程/线程创建

**允许的操作：**
- 纯内存计算
- 堆分配（fuzzer 会监控）
- 读取编译时常量
- 使用 `libFuzzer` 提供的确定性 API

**验证方法：**
- 代码审查确认无禁止操作
- `harness-check` 静态扫描 fuzz target 源码中的禁止 API 调用（未来扩展）

---

### FUZZ-004: Crash minimization 流程

| 属性 | 值 |
|------|-----|
| 规则 ID | FUZZ-004 |
| 名称 | Crash minimization 流程 |
| 强制级别 | Mandatory（issue 关闭前） |
| 验证方式 | 人工验证 |

**详细说明：**

当 fuzzing 发现 crash 时，在关闭相关 issue 前必须完成以下流程：

1. **Minimize**：使用 `cargo fuzz tmin <target> <crash_file>` 将 crash 输入精简到最小复现集
2. **归档**：将 minimized input 添加到 `fuzz/corpus/<target>/` 作为回归测试输入
3. **修复验证**：确认修复后 target 对该输入不再 crash
4. **回归保护**：minimized input 签入主仓库，确保未来 CI fuzzing 持续验证

**流程：**
```bash
# 1. 下载 crash artifact
# 2. Minimize
cargo +nightly fuzz tmin <target> <crash_file>

# 3. 复制 minimized input 到 regression corpus
cp minimized_input fuzz/corpus/<target>/regression_<issue_id>

# 4. 验证修复
cargo +nightly fuzz run <target> fuzz/corpus/<target>/regression_<issue_id>

# 5. 签入主仓库
git add fuzz/corpus/<target>/regression_<issue_id>
```

**Issue 关闭条件：**
- Minimized regression input 已签入主仓库
- 修复后 target 对该输入不再 crash
- CI fuzzing 包含该 regression input

---

### FUZZ-005: Batch 配套 pruning

| 属性 | 值 |
|------|-----|
| 规则 ID | FUZZ-005 |
| 名称 | Batch 配套 pruning |
| 强制级别 | Mandatory |
| 验证方式 | harness-check 自动验证 |

**详细说明：**

当 batch fuzzing 工作流（`cflite_batch.yml`）存在时，必须有对应的 corpus pruning
机制（`cflite_cron.yml` 或等效工作流）。这防止生成语料库无限增长导致：
- 存储空间消耗过大
- Batch fuzzing 启动时间过长
- 冗余输入降低 fuzzing 效率

**验证方法：**
- `make harness-check` 自动检查：如果 `cflite_batch.yml` 存在，则 `cflite_cron.yml`
  （或包含 prune mode 的工作流）必须存在
- 检查失败时 harness-check 报告 FAIL（非 WARNING）

**Pruning 要求：**
- Pruning 频率不低于每周一次
- Pruning 使用与 batch 相同的 corpus 存储仓库/分支
- Pruning 失败时工作流标记为 failure（触发通知）

---

### FUZZ-006: 纯文档变更跳过 fuzz

| 属性 | 值 |
|------|-----|
| 规则 ID | FUZZ-006 |
| 名称 | 纯文档变更跳过 fuzz |
| 强制级别 | Mandatory |
| 验证方式 | 路径过滤器 |

**详细说明：**

Fuzz CI 工作流不得为无关的纯文档变更运行（除非显式请求）。这通过 GitHub Actions
的路径过滤器（`paths` 触发条件）实现，确保仅在以下路径变更时触发 PR fuzzing：

- `components/rust-converter/**`
- `fuzz/**`
- `.clusterfuzzlite/**`
- `.github/workflows/cflite_*.yml`
- `Makefile`
- `Cargo.lock`
- `components/rust-converter/Cargo.toml`
- `docs/harness/**`
- `tools/release/gates/**`

**排除的路径（不触发 fuzz）：**
- `docs/guides/**`、`docs/features/**`、`docs/architecture/**` 等纯文档目录
- `README.md`、`CHANGELOG.md` 等顶层文档
- `.kiro/`、`.codeartsdoer/` 等工具配置

**验证方法：**
- PR 工作流（`cflite_pr.yml`）的 `on.pull_request.paths` 配置实现此过滤
- `harness-check` 验证 PR 工作流包含路径过滤器

---

### FUZZ-007: 基础设施通过 harness-check

| 属性 | 值 |
|------|-----|
| 规则 ID | FUZZ-007 |
| 名称 | 基础设施通过 harness-check |
| 强制级别 | Mandatory |
| 验证方式 | `make harness-check` |

**详细说明：**

所有 fuzz 基础设施必须通过 `make harness-check` 验证。这包括：

1. **构建配置完整性**：`.clusterfuzzlite/` 目录下 project.yaml、Dockerfile、build.sh 存在且配置正确
2. **工作流完整性**：PR/batch/prune 工作流文件存在
3. **Sanitizer 配置**：工作流使用 address sanitizer
4. **路径过滤器**：PR 工作流有路径过滤器（FUZZ-006）
5. **Batch-Prune 配对**：batch 工作流存在时 prune 工作流必须存在（FUZZ-005）
6. **文档完整性**：`fuzz/README.md` 存在
7. **Gitignore 配置**：`.gitignore` 排除生成语料库路径

**验证命令：**
```bash
make harness-check
```

**失败处理：**
- 任一检查项失败时 harness-check 返回非零退出码
- 失败信息明确指出缺失或错误的配置项
- CI 中 harness-check 失败阻塞合并

---

## 参考

- [AGENTS.md](../AGENTS.md) — 仓库工程规则
- [docs/harness/rules/testing-coverage.md](../docs/harness/rules/testing-coverage.md) — 测试覆盖规则索引
- [ClusterFuzzLite 文档](https://google.github.io/clusterfuzzlite/) — 官方文档
