# Batch Pipeline 三项 Bug 修复设计

**日期**: 2026-04-01
**分支**: fix/batch-schema-drift
**状态**: 已批准

---

## 问题概述

在小模型（Qwen 27B、glm-5.1 等）实际运行 batch pipeline 时发现 3 个 bug：

| Bug | 现象 | 根因 | 严重性 |
|-----|------|------|--------|
| #1 跨癌种污染 | 所有患者的 grep 命令都包含胃癌关键词 | `_extract_from_structured()` L646 硬编码 `"gastric"/"胃癌"` | 高 — 搜索结果不准确 |
| #2 输出字段丢失 | 生成的报告中姓名/部位/诊断等字段为空 | LLM 输出不含原始患者元数据，pipeline 无回注机制 | 高 — 报告不可用 |
| #3 单例循环输出 | 单患者报告中同一推荐重复几十次 | 小模型缺乏终止信号，重复输出被忠实渲染 | 中 — 报告可读性差 |

---

## 修复设计

### Bug #1: 跨癌种污染 — 动态关键词映射

**改动文件**: `scripts/batch_pipeline.py`

**改动函数**: `_extract_from_structured()`

**现状** (L646):
```python
features["diagnosis_keywords"].extend([p["primary_site"], "gastric", "胃癌"])
```

**修复**:
1. 先将 `p["primary_site"]` 原值加入 `diagnosis_keywords`（保留原始值，如 "Gastric Signet Ring Cell Carcinoma"）
2. 调用已有的 `_extract_disease_keywords(p["primary_site"])` 获取映射后的中英文关键词列表，追加到 `diagnosis_keywords`
3. 删除硬编码的 `"gastric"/"胃癌"`

> **审阅建议采纳**: 复用已有的 `_extract_disease_keywords()`（L455），不重新实现映射逻辑。同时保留 `primary_site` 原值确保特殊病理描述不丢失。

**`_extract_from_narrative()` (L722)**: 无需修改。`site_patterns` 硬编码列表用于从叙述文本中扫描识别癌种，仅在文本中匹配时才 append，逻辑正确。

**测试**:
- 肺癌患者不应出现 "gastric" 关键词
- 胃癌患者应正确映射出 "gastric"/"stomach"/"胃"
- `_DISEASE_KEYWORD_MAP` 中未覆盖的癌种，仅注入 `primary_site` 原值
- 特殊病理描述（如 "Gastric Signet Ring Cell Carcinoma"）作为完整关键词保留

---

### Bug #2: 输出字段丢失 — merge 阶段元数据回注

**改动文件**: `scripts/batch_pipeline.py`

**改动函数**: `cmd_merge()`

**新增 CLI 参数**:
```python
p_merge.add_argument("--patients", help="patients.json 路径（可选，用于回注患者元数据）")
```

**修复逻辑**:
1. 当提供 `--patients` 时：
   - 加载 patients.json，按 `patient_id` 建立 lookup dict
   - 合并每个患者后，从 lookup 中回注缺失字段（不覆盖 LLM 已输出的值）：
     - `patient_name`
     - `primary_site`
     - `disease_type`
     - `diagnosis_summary`
     - `clinical_questions[].question`（若 LLM 输出中无 question 文本）
2. 不提供 `--patients` 时行为不变（向后兼容）

**SKILL.md 更新**: Step 4 merge 命令加 `--patients` 参数:
```bash
python scripts/batch_pipeline.py merge \
  --input-dir Output/batches/ \
  --output Output/rag_results.json \
  --patients Output/patients.json
```

> **审阅建议采纳**: 当 `--patients` 提供时，若 batch 结果中某个 `patient_id` 在 patients.json 中找不到，打印警告 `⚠ 患者 {pid} 未在 patients.json 中找到，跳过元数据回注`。

**测试**:
- merge 带 `--patients` 时，输出结果包含 `patient_name`/`primary_site` 等
- merge 不带 `--patients` 时，行为与现有一致
- LLM 已输出的字段不被覆盖
- batch 结果中存在 patients.json 中没有的 patient_id 时，打印警告但不中断

---

### Bug #3: 单例循环输出 — guideline_results 去重

**改动文件**: `scripts/batch_pipeline.py`

**新增函数**: `_deduplicate_guideline_results(patient: dict) -> dict`

**修复逻辑**:
1. 对每个患者的 `clinical_questions[].guideline_results` 列表：
   - 以 `(guideline, recommendation)` 元组为去重 key
   - 保留首次出现的条目，丢弃后续重复
   - 去重后若有删除，打印警告：`⚠ 患者 {pid}: 去除 {n} 条重复推荐`
2. 对 `consensus` 和 `differences` 列表用 `list(dict.fromkeys(list))` 做字符串去重（保序高效）
3. 调用时机：在 `_extract_patient_list()` 返回前，对每个患者执行

**设计考量**:
- 去重 key 用 `(guideline, recommendation)` 而非完整对象，因为同一推荐的 `source_lines` 等字段可能略有不同
- 保序（不用 set）确保首次出现的完整条目被保留
- Python 层面确定性修复，不依赖 LLM 行为

**测试**:
- 5 条重复推荐去重后只保留 1 条
- 不同指南的相同推荐文本不被误去重
- 无重复时输出不变

---

## 约束条件

- 所有现有 107 个测试零修改通过
- `--patients` 参数可选，不提供时 merge 行为不变
- `generate` 子命令不改动
- `orchestrate`/`validate`/`verify-batch` 子命令不改动

## 影响范围

- `scripts/batch_pipeline.py` — 修改 `_extract_from_structured()`、`cmd_merge()`、`_extract_patient_list()`，新增 `_deduplicate_guideline_results()`
- `tests/` — 新增 ~10 个测试
- `SKILL.md` — 更新 merge 命令示例
