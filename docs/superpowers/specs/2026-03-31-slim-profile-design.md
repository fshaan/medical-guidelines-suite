# --profile slim 设计规格

> 为小模型（Qwen 27B 等）优化的批处理 profile，降低 prompt 复杂度同时保持输出质量。

## 1. 问题背景

本地部署的 27B 级别模型运行 batch pipeline 时出现：
- 130+ grep 命令被跳过（"太多了，用更高效方式"）
- 4 层嵌套 21 字段 JSON 输出畸形
- v2.3 大模型"防偷懒"约束对小模型无效且反生产
- 模型尝试调用不可用的 subagent/tool

## 2. 设计决策记录

| 决策点 | 选择 | 备选 |
|--------|------|------|
| JSON 输出格式 | **方案 B：完全扁平化**，每个 guideline 一条 entry | A：保留 per-guideline 嵌套 |
| 维度合并 | **4 组方案** | 3 组（命中率风险） |
| 集成架构 | **ProfileConfig dataclass** | 条件分支 / 平行函数 |
| 微检查点 | **prompt 内引导，单次 JSON 输出** | 分段输出 + Python 拼装 |
| consensus/differences | **merge 阶段 Python 自动生成** | 省略 / generate 阶段生成 |

## 3. ProfileConfig 数据模型

```python
from dataclasses import dataclass

SLIM_DIMENSION_GROUPS = [
    ["diagnosis_keywords", "staging_keywords", "metastasis_keywords"],
    ["molecular_keywords", "marker_keywords"],
    ["treatment_keywords", "event_keywords"],
    ["comorbidity_keywords", "special_keywords"],
]

@dataclass
class ProfileConfig:
    name: str = "full"
    dimension_groups: list[list[str]] | None = None  # None = 原始 9 维
    min_rec_length: int = 50
    skip_anti_laziness: bool = False
    skip_snippet_verify: bool = False
    micro_checkpoints: bool = False
    flat_json: bool = False
    org_filter_by_disease: bool = False

PROFILE_FULL = ProfileConfig()

PROFILE_SLIM = ProfileConfig(
    name="slim",
    dimension_groups=SLIM_DIMENSION_GROUPS,
    min_rec_length=20,
    skip_anti_laziness=True,
    skip_snippet_verify=True,
    micro_checkpoints=True,
    flat_json=True,
    org_filter_by_disease=True,
)

def get_profile(name: str) -> ProfileConfig:
    return {"full": PROFILE_FULL, "slim": PROFILE_SLIM}[name]
```

CLI 集成：`orchestrate` 和 `validate` 子命令新增 `--profile {full,slim}` 参数，默认 `full`。

## 4. Grep 命令生成

### 4.1 维度合并

4 个搜索组替代原始 9 个独立维度：

| 组 | 合并维度 |
|----|----------|
| 1 | diagnosis + staging + metastasis |
| 2 | molecular + marker |
| 3 | treatment + event |
| 4 | comorbidity + special |

每组关键词去重后截断至 **15 个**上限。

### 4.2 Org 动态过滤

`filter_orgs_by_disease()` 根据患者 disease_type 匹配 org 目录下文件名，过滤无关 org。

匹配逻辑：从 disease_type 提取疾病关键词（中英文），检查 org_files 中是否有文件名包含这些关键词。无匹配时 fallback 到全部 org。

### 4.3 预期效果

- Full：9 dims x 4 orgs = **36 条/患者**
- Slim：4 groups x 3 orgs(avg) = **12 条/患者**

## 5. Slim JSON 输出格式

扁平化结构，每个 patient-guideline 组合一条 entry：

```json
{
  "batch_id": "batch_001",
  "processed_at": "2026-03-31T10:00:00",
  "results": [
    {
      "patient_id": "T002690492",
      "patient_name": "张三",
      "clinical_question": "一句话临床问题",
      "guideline": "NCCN",
      "recommendation": ">=20字推荐内容",
      "evidence_level": "Category 1",
      "source_file": "NCCN_GastricCancer_2026.V2_EN.txt"
    }
  ]
}
```

对比 full 模式：5 层嵌套 21 字段 → **2 层 7 字段**。

移除字段：`execution_log`、`execution_summary`、`source_lines`、`consensus`、`differences`。

## 6. Slim Prompt 模板

结构：`步骤 1 执行 grep` → `自检计数` → `步骤 2 输出 JSON` → `最终自检`

| 维度 | Full | Slim |
|------|------|------|
| Prompt 长度 | ~750 行 | ~200 行 |
| JSON 嵌套 | 5 层 | 2 层 |
| 字段数/条目 | 21 | 7 |
| execution_log | 必须 | 省略 |
| 自检点 | 0 | 2 |
| 反偷懒声明 | ~200 字 | 无 |

微检查点格式：每批 grep 后要求填写匹配数计数表，JSON 输出前要求确认条目总数。

## 7. Merge 管道改造

### 7.1 格式自动检测

`_is_flat_format(results)` 检查首条 result 是否含 `guideline` 键且无 `guideline_results` 键。

### 7.2 扁平→聚合

`_aggregate_flat_results(flat_results)` 按 patient_id 分组，重建 `guideline_results` 数组，输出 full 兼容的嵌套结构。

### 7.3 Consensus/Differences 生成

`_generate_consensus(patient)` 基于多 guideline 推荐文本的中文关键词重叠分析：
- 所有 guideline 共有关键词 → consensus
- 各 guideline 独有关键词 → differences

### 7.4 透明性

聚合后输出与 full 格式一致，`generate` 子命令（xlsx/docx/pptx）**零修改**。

## 8. 验证策略

### 8.1 Validate (cmd_validate)

| 检查项 | Full | Slim |
|--------|------|------|
| 患者完整性 | ERROR | ERROR |
| 推荐非空 | >=50字 | >=20字 |
| Org 覆盖率 | ERROR | ERROR |
| 跨批次相似度 | ERROR | **跳过** |
| 批次深度衰减 | WARNING | **跳过** |
| 跨患者质量一致性 | WARNING | **跳过** |
| evidence_level 缺失 | ERROR | **WARNING** |
| source_file 缺失 | ERROR | **WARNING** |

### 8.2 Verify-batch (cmd_verify_batch)

| 检查项 | Full | Slim |
|--------|------|------|
| V1 命令覆盖率 | ERROR | ERROR |
| V2 计数一致性 | ERROR | ERROR |
| V3 Snippet 真实性 | ERROR | **跳过** |
| V4 空匹配矛盾 | WARNING | **WARNING** |

## 9. 不变量

- `parse`、`merge`（聚合后）、`generate` 子命令 **零修改**
- 所有现有 80 个测试 **零修改通过**
- `--profile full` 为默认值，现有行为完全不变
- 函数签名变更使用 `config: ProfileConfig = None` 保持向后兼容

## 10. 影响范围

- `scripts/batch_pipeline.py` — 新增 ~200 行，修改 ~50 行
- `tests/test_slim_profile.py` — 新增 ~25 个测试
- `SKILL.md`、`README.md`、`CHANGELOG.md` — 文档更新
