# v2.2 工程评审报告：架构分析与改进决策

> 评审日期: 2026-03-25
> 评审对象: `docs/v2.2-fix-plan.md`
> 分支: main
> 仓库模式: solo

---

## Step 0: 范围挑战

### 复杂度评估

| 指标 | 值 | 阈值 | 状态 |
|------|-----|------|------|
| 修改/新建文件数 | 6 | 8 | ✓ |
| 新增类/服务 | 0 | 2 | ✓ |
| 新增子命令 | 1 (orchestrate) | — | ✓ |
| 新增函数 | 5 | — | 可接受 |
| batch_pipeline.py 新增行数 | ~350 | 现有 947 行 (+37%) | ⚠️ 偏大 |

**结论**: 复杂度在可控范围内，但 batch_pipeline.py 的新增比例较高，需注意函数粒度。

### 现有代码复用分析

```
┌─────────────────────────────────────────────────────────────┐
│                    现有代码复用情况                           │
├──────────────┬──────────────┬────────────┬──────────────────┤
│ 子问题       │ 现有代码      │ 计划复用？  │ 评审决策         │
├──────────────┼──────────────┼────────────┼──────────────────┤
│ 患者解析     │ cmd_parse()   │ ✓ 复用输出 │ OK               │
│ 分批         │ cmd_split()   │ ✗ 重写     │ → 提取复用 [5A]  │
│ 合并         │ cmd_merge()   │ ✓ 原样复用 │ OK               │
│ 验证         │ cmd_validate()│ ✓ 增强     │ OK               │
│ 生成         │ cmd_generate()│ ✓ 增强参数 │ OK               │
│ 批次隔离规则 │ HARD_CONSTR.  │ ✓ 扩展     │ OK               │
└──────────────┴──────────────┴────────────┴──────────────────┘
```

### 可延期工作

| 内容 | 延期影响 | 建议 |
|------|---------|------|
| Phase 3: 多平台适配 (AGENTS.md/GEMINI.md/skill.json) | 低——不影响核心功能 | 可拆为独立 PR |
| 拼音排序 (pypinyin) | 低——仅影响输出美观 | 可延期，用 `sorted()` 临时替代 |

---

## 1. 架构评审

### 1.1 数据流全景

```
                         orchestrate 子命令
                    ┌────────────────────────────┐
                    │                            │
  patients.json ───►│  resolve_kb_root()         │
                    │       │                    │
                    │       ▼                    │
  KB/data_structure │  scan_knowledge_base()     │
  .md (根+各org) ──►│   │ 解析 markdown 表格     │
                    │   │ fallback → 目录枚举    │
                    │   ▼                        │
                    │  kb_profile (内存字典)      │
                    │       │                    │
                    │       ▼                    │
                    │  extract_patient_features()│◄── 26 字段 × 9 维度
                    │       │                    │
                    │       ▼                    │
                    │  generate_grep_commands()  │
                    │       │                    │
                    │       ▼                    │
                    │  _split_patients()         │◄── 复用现有 split 逻辑
                    │       │                    │
                    │       ▼                    │
                    │  generate_batch_prompt()   │
                    │   × N batches              │
                    └───────┬────────────────────┘
                            │
                            ▼
                    Output/batches/
                    ├── orchestration_plan.json
                    ├── batch_001_prompt.md ──► LLM 逐批执行
                    ├── batch_002_prompt.md     │
                    └── ...                     │
                                                ▼
                    Output/batches/
                    ├── rag_batch_001.json
                    ├── rag_batch_002.json
                    └── ...
                            │
                            ▼
                    ┌──────────────────┐
                    │  merge           │ → Output/rag_results.json
                    │  validate        │ → 覆盖率检查 + 跨批次相似度检测
                    │  generate        │ → xlsx / docx / pptx
                    └──────────────────┘
```

### 1.2 评审决策汇总

| # | 问题 | 决策 | 理由 |
|---|------|------|------|
| 1 | Markdown 解析脆弱性 | **1A: 严格解析 + 友好降级** | 锚定模板格式解析，失败时 fallback 到目录枚举。单点故障需要后备。 |
| 2 | Prompt 大小爆炸 | **2A: 根索引嵌入 + org 索引按需读取** | 只嵌入根 data_structure.md。org 级索引用路径引用，LLM 按需 Read。合并同维度关键词减少命令数。 |
| 3 | `<CONTEXT_RESET>` 不可靠 | **3A: CONTEXT_RESET + validate 后备拦截** | 保留文本约束作为第一层。增强 validate 做跨批次相似度检测——如果后批与前批产出雷同内容，发出 WARNING。 |
| 4 | AGENTS.md/GEMINI.md DRY 违反 | **4A: 精简平台文件 + 引用 SKILL.md** | 平台文件只包含差异内容（安装路径、工具映射）。工作流和约束统一引用 SKILL.md。 |
| 5 | split 子命令叠床 | **5A: orchestrate 复用 split 逻辑** | 提取 `_split_patients()` 内部函数，cmd_split 和 cmd_orchestrate 共用。 |

### 1.3 生产故障场景分析

| 代码路径 | 故障场景 | 计划是否应对 | 建议 |
|---------|---------|------------|------|
| `scan_knowledge_base()` | data_structure.md 格式异常 | 决策 1A 已覆盖 | fallback 到目录枚举 |
| `extract_patient_features()` | narrative 格式缺乏关键字段 | ⚠️ 未覆盖 | 设 confidence: "low"，prompt 中提示 LLM 补充推断 |
| `generate_batch_prompt()` | prompt 超出目标模型上下文限制 | 决策 2A 部分缓解 | 增加 prompt 大小预估，超限时自动减小 batch-size |
| LLM 执行 grep | grep 命令语法错误（特殊字符未转义） | ⚠️ 未覆盖 | 关键词需转义 `[`, `]`, `(`, `)` 等 grep 特殊字符 |
| 批次间上下文污染 | LLM 忽略 CONTEXT_RESET | 决策 3A 已覆盖 | validate 跨批次相似度检测 |
| `cmd_generate()` 输出路径 | 用户同时运行两个批处理任务 | ⚠️ 未覆盖 | Output/ 路径冲突。建议 output-dir 默认包含时间戳 |

---

## 2. 代码质量评审

### 2.1 函数粒度

`extract_patient_features()` 覆盖 9 个维度 × 2 种格式（structured + narrative），预计 150+ 行。

**建议**: 拆为 `_extract_structured_features()` 和 `_extract_narrative_features()`，每个维度用独立的小函数。

### 2.2 DRY 分析

```
潜在重复:
┌──────────────────────────┬───────────────────────────┐
│ 位置 A                   │ 位置 B                     │
├──────────────────────────┼───────────────────────────┤
│ SKILL.md 的 Step 0 疾病  │ extract_patient_features() │
│ 诊断映射表               │ 中的诊断维度提取逻辑       │
│                          │ → 需要保持同步             │
├──────────────────────────┼───────────────────────────┤
│ SKILL.md 工作流描述      │ AGENTS.md/GEMINI.md       │
│                          │ → 已决策 4A 解决           │
├──────────────────────────┼───────────────────────────┤
│ cmd_split() 分批逻辑     │ cmd_orchestrate() 分批    │
│                          │ → 已决策 5A 解决           │
└──────────────────────────┴───────────────────────────┘
```

### 2.3 错误处理缺口

| 函数 | 缺失的错误处理 | 严重度 |
|------|--------------|--------|
| `scan_knowledge_base()` | org 目录无 extracted/ 子目录 | 中 — fallback 处理 |
| `extract_patient_features()` | 所有字段为 None 的患者 | 低 — 返回空关键词列表 |
| `generate_grep_commands()` | 关键词含 grep 特殊字符 | **高** — 命令执行失败 |
| `cmd_orchestrate()` | patients.json 不存在或为空 | 中 — 提前退出 |
| `generate_batch_prompt()` | prompt 超出上下文限制 | 中 — 降级 batch-size |

---

## 3. 测试评审

### 测试框架: 无（项目当前无测试目录和测试框架）

**建议**: 新增 `tests/` 目录 + pytest。orchestrate 的所有新增函数都是纯函数（输入→输出），极易测试。

### 代码路径覆盖图

```
CODE PATH COVERAGE
===========================
[+] scripts/batch_pipeline.py — orchestrate 新增函数

    resolve_kb_root()
    ├── [GAP] --kb-root 参数指定
    ├── [GAP] 环境变量 MEDICAL_GUIDELINES_DIR
    ├── [GAP] ./guidelines/ 存在
    ├── [GAP] ./knowledge/ 存在
    └── [GAP] 全部不存在 → 报错退出

    scan_knowledge_base()
    ├── [GAP] 正常解析 root data_structure.md
    ├── [GAP] root 解析失败 → fallback 目录枚举
    ├── [GAP] org 目录无 data_structure.md
    ├── [GAP] org 目录无 extracted/
    └── [GAP] 空知识库（无任何 org）

    extract_patient_features()
    ├── [GAP] 结构化格式 — 全字段填写
    ├── [GAP] 结构化格式 — 关键字段缺失
    ├── [GAP] narrative 格式 — 正常叙述
    ├── [GAP] narrative 格式 — 极短叙述
    └── [GAP] 所有字段为 None

    generate_grep_commands()
    ├── [GAP] 正常关键词生成
    ├── [GAP] 关键词含特殊字符（括号、方括号）
    ├── [GAP] 单 org 无 extracted/ 文件
    └── [GAP] 关键词为空列表

    generate_batch_prompt()
    ├── [GAP] 正常 prompt 生成
    ├── [GAP] prompt 大小超限
    └── [GAP] CONTEXT_RESET 标签正确嵌入

    cmd_orchestrate()
    ├── [GAP] 正常完整流程
    ├── [GAP] checkpoint 恢复（已有 rag_batch_*.json）
    ├── [GAP] patients.json 不存在
    └── [GAP] 知识库路径无效

    cmd_validate() — 新增检查
    ├── [GAP] 组织覆盖率检测
    ├── [GAP] 跨批次相似度检测（新增）
    └── [GAP] 特征覆盖检测

    cmd_generate() — 参数增强
    ├── [GAP] --template-dir 参数
    ├── [GAP] --output-dir 参数
    └── [GAP] 拼音排序

USER FLOW COVERAGE
===========================
[+] 端到端批处理流程
    ├── [GAP] [→E2E] parse → orchestrate → 手动grep → merge → validate → generate
    └── [GAP] [→E2E] 断点恢复：orchestrate 检测 checkpoint 跳过已完成批次

[+] LLM prompt 质量
    ├── [GAP] [→EVAL] batch_prompt.md 是否包含所有 org 的 grep 命令
    ├── [GAP] [→EVAL] CONTEXT_RESET 有效性（后批不引用前批）
    └── [GAP] [→EVAL] 全特征覆盖（所有非空字段都出现在关键词中）

─────────────────────────────────────
COVERAGE: 0/28 paths tested (0%)
  Code paths: 0/22 (0%)
  User flows: 0/6 (0%)
QUALITY:  无测试
GAPS: 28 paths need tests (2 need E2E, 3 need eval)
─────────────────────────────────────
```

### 测试优先级建议

| 优先级 | 测试 | 类型 | 理由 |
|--------|------|------|------|
| P0 | `scan_knowledge_base()` 正常+降级 | 单元 | 整个 orchestrate 的根基 |
| P0 | `extract_patient_features()` 全格式 | 单元 | 9 维度提取正确性 |
| P0 | `generate_grep_commands()` 含特殊字符 | 单元 | grep 命令执行安全 |
| P1 | `cmd_orchestrate()` 完整流程 | 集成 | checkpoint 恢复 |
| P1 | validate 跨批次相似度 | 单元 | CONTEXT_RESET 的后备防线 |
| P2 | prompt 大小预估 | 单元 | 上下文溢出预防 |
| P2 | 端到端 | E2E | 全流程回归 |

---

## 4. 性能评审

### 4.1 Grep 命令爆炸

```
27 患者 × 6 org × 3 合并维度 = ~486 条 grep 命令
每条 grep 扫描 ~79,000 行文本
总 I/O: ~38M 行文本扫描

缓解措施（已在计划中）:
- 同维度关键词用 \| 合并 → 减少 3-5x
- 实际命令数预估: ~100-150 条（可接受）
```

### 4.2 Prompt 文件 I/O

```
每个 batch_prompt.md 预估大小:
  根索引嵌入: ~170 行
  5 患者 × 完整信息: ~50 行
  5 患者 × ~20 grep 命令: ~100 行
  规则 + schema: ~50 行
  ──────────────
  总计: ~370 行（~15KB）

6 个批次 × 15KB = ~90KB 总写入
→ 性能无问题
```

### 4.3 无性能问题

该项目的瓶颈在 LLM 执行速度，不在脚本性能。无需优化。

---

## 5. 故障模式总结

| 代码路径 | 故障 | 有测试？ | 有错误处理？ | 用户看到什么？ |
|---------|------|---------|------------|-------------|
| scan_knowledge_base 解析失败 | md 格式异常 | ❌ | ✓ fallback | WARNING 信息 |
| grep 命令含特殊字符 | 命令执行错误 | ❌ | ❌ | **静默失败** ⚠️ |
| prompt 超出上下文限制 | 输出截断/质量下降 | ❌ | ❌ | **静默降质** ⚠️ |
| LLM 忽略 CONTEXT_RESET | 后批引用前批内容 | ❌ | ✓ validate | WARNING 信息 |
| 并发运行 Output/ 冲突 | 文件覆盖 | ❌ | ❌ | **数据丢失** ⚠️ |

**Critical Gaps** (无测试 + 无错误处理 + 静默失败): **3 个**
1. grep 特殊字符未转义
2. prompt 超出上下文限制
3. 并发运行 Output/ 冲突

---

## 6. NOT in scope（显式延期）

| 内容 | 延期理由 |
|------|---------|
| 自动化测试框架搭建 | 可在实施时并行完成，不阻塞计划 |
| pypinyin 依赖引入 | 低优先级美化功能，`sorted()` 可临时替代 |
| 多平台适配实际验证（OpenCode/Gemini 端到端测试） | 需要实际环境，无法在此 PR 中完成 |
| scan_knowledge_base 的 LLM 辅助模式（用 LLM 解析非标准 markdown） | 过度工程化，先用正则 + fallback |
| orchestration_plan.json 的版本兼容 | 首次引入，无历史包袱 |

## 7. What already exists（现有代码复用）

| 现有代码 | 位置 | 计划是否复用 | 评审建议 |
|---------|------|------------|---------|
| `cmd_split()` 分批逻辑 | batch_pipeline.py:170-209 | ✓ 决策 5A 复用 | 提取为 `_split_patients()` |
| `cmd_parse()` 患者解析 | batch_pipeline.py:19-168 | ✓ 使用其输出 | OK |
| `cmd_merge()` | batch_pipeline.py:211-262 | ✓ 原样使用 | OK |
| `cmd_validate()` | batch_pipeline.py:264-364 | ✓ 增强 | 新增覆盖率 + 相似度检测 |
| `cmd_generate()` | batch_pipeline.py:367-893 | ✓ 增强参数 | 新增 --template-dir/--output-dir |
| SKILL.md 批次隔离 HARD_CONSTRAINT | SKILL.md:1252-1264 | ✓ 扩展 | 新增执行模式约束 + 全覆盖约束 |
| templates/data_structure_root.md | templates/ | ✓ scan 解析参考 | 锚定此模板格式解析 |

---

## 8. 改进建议汇总（按优先级）

### P0: 必须在实施中处理

1. **grep 关键词转义**: `generate_grep_commands()` 中对 `[`, `]`, `(`, `)`, `.`, `*` 等 grep 特殊字符进行转义
2. **scan_knowledge_base fallback**: 解析失败时不静默跳过，打印 WARNING + 降级到目录枚举
3. **prompt 大小预估**: 生成 prompt 后检查行数，超出阈值时自动减小 batch-size 并重新分批

### P1: 强烈建议

4. **validate 跨批次相似度检测**: 比较后批与前批的 `recommendation` 文本相似度，超过阈值（如 Jaccard > 0.8）报 WARNING
5. **`_split_patients()` 提取**: 从 cmd_split 提取分批逻辑为内部函数，cmd_split 和 cmd_orchestrate 共用
6. **AGENTS.md/GEMINI.md 精简**: 只保留平台差异内容，工作流引用 SKILL.md

### P2: 建议但可延期

7. **拼音排序**: 用 `locale.strxfrm` 替代 pypinyin（避免新依赖），不满意时再引入
8. **Output/ 并发保护**: output-dir 默认包含日期前缀（如 `Output/2026-03-25/`）

---

## 9. 架构决策图

```
┌─────────────────────────────────────────────────────────────────┐
│                      v2.2 架构决策树                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  scan_knowledge_base() ──── 解析 markdown? ─── YES             │
│                                  │                              │
│                                  ├── 成功 → kb_profile          │
│                                  └── 失败 → fallback 目录枚举   │ [1A]
│                                                                 │
│  batch_prompt.md ──── 嵌入什么？                                │
│                          │                                      │
│                          ├── 根索引全文 ✓                       │
│                          └── org 索引 → 路径引用，按需 Read      │ [2A]
│                                                                 │
│  批次隔离 ──── 几层防御？                                       │
│                   │                                             │
│                   ├── Layer 1: CONTEXT_RESET 文本指令            │
│                   ├── Layer 2: validate 跨批次相似度检测         │ [3A]
│                   └── Layer 3: SKILL.md HARD_CONSTRAINT          │
│                                                                 │
│  平台文件 ──── 内容策略？                                       │
│                   │                                             │
│                   ├── SKILL.md: 单一事实来源                     │
│                   └── AGENTS.md/GEMINI.md: 仅差异 + 引用         │ [4A]
│                                                                 │
│  分批逻辑 ──── 复用？                                           │
│                   │                                             │
│                   └── _split_patients() 被 split + orchestrate  │
│                      共同调用                                    │ [5A]
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 10. Completion Summary

| 项目 | 结果 |
|------|------|
| Step 0: 范围挑战 | 范围接受，建议 Phase 3 可拆分独立 PR |
| 架构评审 | 5 issues found, 5 resolved |
| 代码质量评审 | 3 issues found (函数粒度、DRY、错误处理) |
| 测试评审 | 图已生成, 28 gaps identified, 0% 覆盖率 |
| 性能评审 | 0 issues (LLM 是瓶颈，脚本性能无需优化) |
| NOT in scope | 5 items 显式延期 |
| What already exists | 7 items 已识别 |
| 故障模式 | **3 critical gaps** (grep 转义、prompt 超限、并发冲突) |
| Lake Score | 5/5 recommendations chose complete option |

---

*Generated by /plan-eng-review on 2026-03-25*
