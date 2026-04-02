# Medical Guidelines Suite — 项目指令

本文件为 OpenCode 平台提供项目指令。完整技能定义见 `SKILL.md`。

---

## 项目概述

临床指南 RAG 检索系统。输入患者临床数据，跨多个国际/国内指南组织检索推荐意见，输出结构化对比结果。

**核心功能**：
1. **单患者检索**：输入临床问题 → 跨指南推荐对比表
2. **批量处理**：输入患者 Excel → orchestrate 编排 → 生成推荐报告（xlsx + docx + pptx）

**技术特点**：
- 无向量数据库 — 使用 grep + 分层关键词搜索
- 预提取纯文本文件 — 优先搜索 `extracted/*.txt`
- 领域无关设计 — 适用于任何医学专科

---

## 输出语言

<HARD_CONSTRAINT>

**所有输出必须为简体中文（简体中文）**，无论源指南语言。

</HARD_CONSTRAINT>

---

## 知识库配置

知识库通过环境变量配置，**不在项目目录内**：

```
MEDICAL_GUIDELINES_DIR=/Users/f.sh/MyDocuments/RAG/guidelines
```

知识库结构：
```
$MEDICAL_GUIDELINES_DIR/
├── data_structure.md          # 根索引
├── NCCN/
│   ├── data_structure.md      # 组织索引
│   └── extracted/*.txt        # 预提取文本
├── ESMO/、CSCO/、JGCA/、CACA/
```

`resolve_kb_root()` 按优先级查找：`--kb-root` 参数 > `MEDICAL_GUIDELINES_DIR` 环境变量 > `./guidelines/`

---

## 批处理工作流（v2.2 orchestrate 驱动）

```
parse → orchestrate → [LLM 按 prompt 执行] → merge → validate → generate
```

### 命令参考

```bash
# 1. 解析患者 Excel
python3 scripts/batch_pipeline.py parse --input Input/2026-3-25.xlsx --output Output/patients.json

# 2. 编排：自动扫描知识库 + 提取特征 + 生成 batch prompt
python3 scripts/batch_pipeline.py orchestrate \
  --patients Output/patients.json \
  --output-dir Output/batches \
  --batch-size 5

# 3. 读取 Output/batches/orchestration_plan.json
#    对每个 pending 批次：读取 batch_NNN_prompt.md，执行 grep，写入 rag_batch_NNN.json

# 4. 合并 + 验证 + 生成
python3 scripts/batch_pipeline.py merge --input-dir Output/batches/ --output Output/rag_results.json
python3 scripts/batch_pipeline.py validate --input Output/rag_results.json --patients Output/patients.json
python3 scripts/batch_pipeline.py generate --input Output/rag_results.json --format md
```

### 批次 prompt 执行规则

每个 `batch_NNN_prompt.md` 开头包含 `<CONTEXT_RESET>` 和 `<MANDATORY_RULES>`：
- **必须逐条执行所有 grep 命令**，不得跳过任何组织
- **不得引用前批结果**（每个批次从零开始）
- **可以补充 grep 命令**，但不得删减已有的
- 输出格式见 prompt 末尾的 JSON Schema

---

## 关键约束

1. **禁止并行代理**：所有批处理步骤在当前会话中顺序执行
2. **禁止自行编码替代脚本**：必须使用 `scripts/batch_pipeline.py` 的子命令
3. **所有输出路径以 orchestration_plan.json 为准**
4. **所有 org 必须检索**：不得因"已找到足够信息"提前停止
5. **检索深度一致**：第 1 位和最后 1 位患者深度相同

完整约束和工作流细节见 `SKILL.md`。

---

## 文件结构

```
medical-guidelines-suite/
├── SKILL.md                    # 完整技能定义
├── AGENTS.md                   # 本文件（OpenCode 指令）
├── CLAUDE.md                   # Claude Code 指令
├── scripts/batch_pipeline.py   # 8 个子命令: parse/split/orchestrate/merge/validate/verify-batch/generate
├── references/                 # 文件处理指南 + 输入格式规范
├── templates/                  # 索引模板 + PPTX 模板
├── tests/                      # pytest 测试 (118 tests)
├── Input/                      # 用户输入文件
└── Output/                     # 生成输出（自动创建）
```

---

*Last Updated: 2026-03-27*
