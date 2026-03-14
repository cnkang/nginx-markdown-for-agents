# 优化执行路线图（Spec-Mode 版本，无固定时间线）

> 适用范围：`nginx-markdown-for-agents` 当前架构（NGINX C 模块 + Rust 转换引擎）
>
> 使用方式：本文件可直接作为 **Spec 模式编程输入**。每个工作包均包含目标、输入、输出、约束、实现提示、验收与回滚。

---

## 1. 文档目标与使用约定

## 1.1 目标

将三条核心优化方向沉淀为可执行的工程工作包：

- A：大响应性能/内存模型优化
- B：版本兼容与发布覆盖自动化
- C：性能基线门禁化

## 1.2 使用约定（给 Agent / 开发者）

- 每次只选择 **一个工作包** 实施。
- 每个工作包按“先观测、后改造、可回滚”推进。
- 默认采用小步 PR，避免跨主题超大改动。

## 1.3 工作包状态机

`draft -> in_progress -> validating -> merged -> hardened`

- `draft`：规格明确、尚未编码
- `in_progress`：实现中
- `validating`：执行测试/对比
- `merged`：已合入，默认策略可能仍受开关控制
- `hardened`：经过多轮验证，成为稳定默认行为

---

## 2. 优先级与执行顺序（无时间线）

### P0（优先落地）

1. C：性能基线门禁化（先建立可度量能力）
2. B：版本兼容与发布覆盖自动化（优先改善安装/发布可靠性）
3. A：大响应性能/内存模型优化（在可度量体系下推进）

### P1（P0 稳定后）

4. 条件请求路径与 ETag 复用优化
5. 指标维度扩展（失败分类、分桶、路径命中）
6. 安装诊断与文档体验闭环

### P2（探索）

7. 更深入的 streaming conversion 方案
8. 输出格式与 flavor 扩展

---

## 3. 跨包通用模板（Spec 输入模板）

> 以下模板可直接复制给 Agent。

```yaml
spec_id: <A-1|B-2|C-3...>
title: <工作包标题>
priority: <P0|P1|P2>
objective:
  - <业务或工程目标>
non_goals:
  - <明确不做范围>
inputs:
  code_paths:
    - <受影响目录/文件>
  docs_paths:
    - <需更新文档>
constraints:
  - <兼容性约束>
  - <性能/稳定性约束>
implementation_hints:
  - <建议改造点>
outputs:
  code_changes:
    - <预期代码产物>
  docs_changes:
    - <预期文档产物>
validation:
  commands:
    - <命令1>
    - <命令2>
  acceptance_criteria:
    - <可量化验收条件>
rollback_plan:
  - <开关回退/代码回退>
risks:
  - <主要风险>
dependencies:
  - <依赖的前置工作包>
```

---

## 4. 任务桩 C（P0）：性能基线门禁化

> 说明：先解决“如何稳定衡量”的问题，再进行性能优化，避免争议。

### C-1 工作包：指标体系定稿

- **spec_id**: `C-1`
- **objective**:
  - 统一性能指标定义，确保 PR 间可比较。
- **inputs**:
  - `docs/testing/PERFORMANCE_BASELINES.md`
  - `components/rust-converter` 现有基准入口
- **outputs**:
  - 指标清单（至少含 P50/P95、峰值内存、吞吐、阶段占比）
  - 样本分层（small/medium/large + front-matter variant）
- **constraints**:
  - 不引入破坏性变更；仅定义与补齐测量口径。
- **validation**:
  - 文档可复现：新开发者按文档可独立跑出同结构报告。
- **acceptance_criteria**:
  - 指标定义无歧义；样本集固定且版本可追踪。
- **risks**:
  - 指标过多导致维护负担上升。

### C-2 工作包：阈值策略（告警/阻断）

- **spec_id**: `C-2`
- **objective**:
  - 建立双阈值机制：告警不阻断、阻断可防明显回归。
- **inputs**:
  - `docs/testing/PERFORMANCE_BASELINES.md`
  - CI 工作流配置（`.github/workflows/`）
- **outputs**:
  - 阈值配置文档（平台分层）
  - 阈值更新流程说明
- **constraints**:
  - 阈值需考虑 CI 波动，避免误报高频触发。
- **validation**:
  - 模拟回归/正常波动两类样本，验证阈值行为。
- **acceptance_criteria**:
  - 明显回归可被阻断；轻微波动仅告警。
- **dependencies**:
  - `C-1`

### C-3 工作包：CI 集成与报告自动化

- **spec_id**: `C-3`
- **objective**:
  - 在 PR 与 nightly 提供自动性能报告与趋势对比。
- **inputs**:
  - `.github/workflows/ci.yml`
  - 现有基准脚本与输出
- **outputs**:
  - 轻量 perf smoke（PR）
  - 重型 perf 任务（nightly）
  - 可追踪 artifact 与 PR 摘要输出
- **constraints**:
  - PR 流程运行时长受控。
- **validation**:
  - 新 PR 可见相对基线变化。
- **acceptance_criteria**:
  - 报告可读、可比较、可审计。
- **dependencies**:
  - `C-1`, `C-2`

### C-4 工作包：本地复现工具统一入口

- **spec_id**: `C-4`
- **objective**:
  - 让开发者/agent 一键执行“跑样本-对比-导出报告”。
- **outputs**:
  - 统一脚本入口（如 `tools/` 下）
  - 使用文档与示例输出
- **acceptance_criteria**:
  - 本地与 CI 报告结构一致。
- **dependencies**:
  - `C-1`

---

## 5. 任务桩 B（P0）：版本兼容与发布覆盖自动化

### B-1 工作包：发布矩阵规范化

- **spec_id**: `B-1`
- **objective**:
  - 固化产物矩阵：`nginx patch × os family × arch`。
- **inputs**:
  - 发布工作流（`.github/workflows/release-binaries.yml`）
  - 安装脚本（`tools/install.sh`）
- **outputs**:
  - 矩阵清单与生成规则
  - 发布前完整性检查逻辑
- **constraints**:
  - 不改变已支持版本的安装行为语义。
- **validation**:
  - 人工构造漏发场景，验证可阻断发布。
- **acceptance_criteria**:
  - 漏发不可进入最终发布产物。

### B-2 工作包：安装脚本失败引导增强

- **spec_id**: `B-2`
- **objective**:
  - 让“版本不匹配”错误信息可直接指导下一步操作。
- **inputs**:
  - `tools/install.sh`
  - `docs/guides/INSTALLATION.md`
- **outputs**:
  - 缺失版本时输出可用版本列表
  - 清晰源码编译引导（锚点化）
  - 可选机器可读输出（JSON）
- **constraints**:
  - 保持脚本在常见 shell 环境兼容。
- **validation**:
  - 模拟 exact 版本缺失路径。
- **acceptance_criteria**:
  - 用户可依据输出完成后续安装决策。
- **dependencies**:
  - `B-1`（建议）

### B-3 工作包：兼容文档闭环

- **spec_id**: `B-3`
- **objective**:
  - 提供常见失败场景 SOP（版本/架构/libc）。
- **outputs**:
  - 安装指南新增“兼容矩阵与失败排查”章节
- **acceptance_criteria**:
  - 支持人员可直接引用章节定位问题。
- **dependencies**:
  - `B-1`, `B-2`

### B-4 工作包：多环境安装验证

- **spec_id**: `B-4`
- **objective**:
  - 在多镜像/多平台定期验证安装脚本行为一致。
- **outputs**:
  - 定期验证 job 与结果归档
- **acceptance_criteria**:
  - 主流目标环境无系统性安装偏差。
- **dependencies**:
  - `B-2`

---

## 6. 任务桩 A（P0）：大响应性能与内存模型优化

### A-1 工作包：设计规格与不可退化指标

- **spec_id**: `A-1`
- **objective**:
  - 明确大响应优化方案边界与验收口径。
- **inputs**:
  - `docs/architecture/REQUEST_LIFECYCLE.md`
  - `docs/testing/PERFORMANCE_BASELINES.md`
- **outputs**:
  - 《分段/增量处理设计说明》
  - 不可退化指标（小响应时延、功能一致性）
- **constraints**:
  - 不改变既有默认行为。
- **validation**:
  - 架构评审可通过；边界条件覆盖完整。
- **dependencies**:
  - `C-1`

### A-2 工作包：NGINX 侧阈值路径与开关

- **spec_id**: `A-2`
- **objective**:
  - 在 C 模块引入阈值分支与路径命中指标。
- **inputs**:
  - `components/nginx-module/src/` 相关 payload/request 路径
- **outputs**:
  - 请求上下文分支字段
  - 配置开关（默认 off）
  - 路径命中 metrics
- **constraints**:
  - `HEAD`、`304`、fail-open replay 语义不变。
- **validation**:
  - 开关开/关下功能一致；回退路径稳定。
- **acceptance_criteria**:
  - 无新增崩溃/挂起；关键行为与基线一致。
- **dependencies**:
  - `A-1`, `C-3`

### A-3 工作包：Rust 侧增量接口原型（feature-gated）

- **spec_id**: `A-3`
- **objective**:
  - 提供内部增量处理 API 原型，控制对外 ABI 影响。
- **inputs**:
  - `components/rust-converter/src/`（converter/ffi）
- **outputs**:
  - 分块输入 + finalize 原型
  - 一致性测试（表格、嵌套列表、实体解码）
- **constraints**:
  - 对外 C ABI 保持兼容；默认行为不变。
- **validation**:
  - 复杂样本与现有路径输出比对。
- **acceptance_criteria**:
  - 一致性通过率达标，且无明显性能负回归。
- **dependencies**:
  - `A-1`, `C-3`

### A-4 工作包：测试矩阵与内存观测补齐

- **spec_id**: `A-4`
- **objective**:
  - 完整覆盖大文档/组合场景的正确性与性能验证。
- **outputs**:
  - 样本集扩展（100KB/1MB/5MB）
  - 场景组合（chunked + gzip + auth + conditional）
  - 内存峰值采样脚本
- **acceptance_criteria**:
  - 大文档内存峰值下降（目标建议 ≥30%，按平台校准）。
- **dependencies**:
  - `A-2`, `A-3`, `C-4`

### A-5 工作包：灰度、回滚与文档收敛

- **spec_id**: `A-5`
- **objective**:
  - 提供可操作的上线策略与一键回滚路径。
- **outputs**:
  - 灰度策略文档
  - 回滚 playbook（开关级回退）
- **acceptance_criteria**:
  - 运维可按文档独立完成启停与回滚。
- **dependencies**:
  - `A-2`, `A-4`

---

## 7. 风险总表（跨任务）

- **R1：语义一致性风险**（A 相关）
  - 缓解：高价值样本快照对比 + corpus 回归 + feature flag
- **R2：CI 噪声风险**（C 相关）
  - 缓解：平台分层阈值 + nightly 重跑 + 中位数策略
- **R3：发布矩阵膨胀风险**（B 相关）
  - 缓解：矩阵分层支持策略 + 自动完整性检查
- **R4：排障复杂度上升**（A/B/C 共同）
  - 缓解：结构化日志 + 路径命中指标 + 统一 SOP

---

## 8. 验收总门槛（Definition of Done）

任一工作包完成需同时满足：

1. **功能正确**：相关测试/回归通过。
2. **可观测**：有可复用指标或日志支撑。
3. **可回滚**：具备明确回退路径。
4. **可文档化**：行为、限制、排障步骤已写入文档。
5. **可审计**：变更与结果可在 CI/PR 中追踪。

---

## 9. 建议的 PR 粒度（便于并行 Agent 编排）

- **PR-Group-1（C 基础）**：`C-1` + `C-4`
- **PR-Group-2（C 门禁）**：`C-2` + `C-3`
- **PR-Group-3（B 基础）**：`B-1` + `B-2`
- **PR-Group-4（B 收敛）**：`B-3` + `B-4`
- **PR-Group-5（A 设计/实现）**：`A-1` + `A-2` + `A-3`
- **PR-Group-6（A 验证/上线）**：`A-4` + `A-5`

---

## 10. 附录：可直接下发给 Agent 的精简指令

### 指令模板（实现）

```text
实现 spec_id=<X-Y>。
要求：
1) 严格遵守 objective/non_goals；
2) 仅修改 inputs.code_paths 相关文件；
3) 输出包含：变更摘要、validation 命令结果、acceptance_criteria 对照、rollback_plan；
4) 若出现范围蔓延，先提交“最小可合并版本”。
```

### 指令模板（验证）

```text
验证 spec_id=<X-Y>。
要求：
1) 执行 validation.commands；
2) 输出与基线的差异报告（含关键指标）；
3) 判定是否满足 acceptance_criteria；
4) 若不满足，给出最小修复建议（不直接扩改范围）。
```

