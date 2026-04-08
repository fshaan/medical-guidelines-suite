# Medical Guidelines Suite — 项目指令

本文件为 OpenCode 平台提供项目指令。完整技能定义见 `SKILL.md`。

---

## 项目概述

临床指南 RAG 检索系统。输入患者临床数据，跨多个国际/国内指南组织检索推荐意见，输出结构化对比结果。

**核心功能**：
1. **单患者检索**：输入临床问题 → 跨指南推荐对比表
2. **批量处理**：输入患者 Excel → orchestrate 编排 → 生成推荐报告（Markdown）

**技术特点**：
- QMD 混合检索 — BM25 + 向量搜索 + LLM 重排序
- Docling 提取 Markdown — 搜索 `extracted/*.md`
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
│   └── extracted/*.md         # Docling 提取的 Markdown
├── ESMO/、CSCO/、JGCA/、CACA/
```

`resolve_kb_root()` 按优先级查找：`--kb-root` 参数 > `MEDICAL_GUIDELINES_DIR` 环境变量 > `./guidelines/`

---

## 批处理工作流（v3.0 QMD 预检索驱动）

```
parse → index → orchestrate → [LLM 按 prompt 分析] → verify-batch → merge → validate → generate
```

### 命令参考

```bash
# 1. 解析患者 Excel
python3 scripts/batch_pipeline.py parse --input Input/2026-3-25.xlsx --output Output/patients.json

# 2. 构建 QMD 检索索引
python3 scripts/batch_pipeline.py index --kb-root $MEDICAL_GUIDELINES_DIR

# 3. 编排：自动扫描知识库 + QMD 预检索 + 生成 batch prompt
python3 scripts/batch_pipeline.py orchestrate \
  --patients Output/patients.json \
  --output-dir Output/batches \
  --batch-size 5

# 4. LLM 按每个 batch prompt 分析预检索结果，写入 rag_batch_NNN.json

# 5. 验证 + 合并 + 校验 + 生成
python3 scripts/batch_pipeline.py verify-batch --input-dir Output/batches/ --kb-root $MEDICAL_GUIDELINES_DIR
python3 scripts/batch_pipeline.py merge --input-dir Output/batches/ --output Output/rag_results.json
python3 scripts/batch_pipeline.py validate --input Output/rag_results.json --patients Output/patients.json
python3 scripts/batch_pipeline.py generate --input Output/rag_results.json --format md
```

### 批次 prompt 执行规则

每个 `batch_NNN_prompt.md` 包含 QMD 预检索的指南片段和 `<MANDATORY_RULES>`：
- **LLM 只分析预检索结果**，不需要自行执行检索
- **不得引用前批结果**（每个批次从零开始）
- **必须覆盖所有预检索片段中的组织**
- 输出格式见 prompt 末尾的 JSON Schema

---

## 关键约束

1. **禁止并行代理**：所有批处理步骤在当前会话中顺序执行
2. **禁止自行编码替代脚本**：必须使用 `scripts/batch_pipeline.py` 的子命令
3. **所有输出路径以 orchestration_plan.json 为准**
4. **所有预检索片段必须分析**：不得因"已找到足够信息"跳过任何组织
5. **分析深度一致**：第 1 位和最后 1 位患者深度相同

完整约束和工作流细节见 `SKILL.md`。

---

## 文件结构

```
medical-guidelines-suite/
├── SKILL.md                    # 完整技能定义
├── AGENTS.md                   # 本文件（OpenCode 指令）
├── CLAUDE.md                   # Claude Code 指令
├── scripts/batch_pipeline.py   # 9 个子命令: parse/split/orchestrate/index/merge/validate/verify-batch/generate
├── scripts/retriever.py        # QMD 服务封装（BM25 + 向量 + 重排序）
├── scripts/extract_all.py      # Docling 批量提取（PDF/DOCX → Markdown）
├── references/                 # 文件处理指南 + 输入格式规范
├── templates/                  # 索引模板
├── tests/                      # pytest 测试 (134 tests)
├── Input/                      # 用户输入文件
└── Output/                     # 生成输出（自动创建）
```

---

*Last Updated: 2026-04-08*
