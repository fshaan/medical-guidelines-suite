# generate 子命令迁移设计：三格式 → 单一 Markdown

## 背景

当前 `batch_pipeline.py` 的 `generate` 子命令输出三种格式（xlsx/docx/pptx），依赖 `openpyxl`、`python-docx`、`python-pptx` 三个库的模板渲染逻辑。多格式维护成本高，多文件输出增加用户查阅复杂度。

## 目标

- `generate` 子命令输出**单一 Markdown 文件**，替代现有三格式
- MD 文件结构清晰，便于快速查找和阅读
- 保留跨指南证据等级对照、共识/差异分析能力
- 患者按中文姓名音序排序（继承现有 `_sort_results_by_name()` 逻辑）

## 约束

- 不引入新的外部依赖（Markdown 为纯文本格式）
- 保持子命令接口兼容（命令名不变）
- 旧格式值（xlsx/docx/pptx）映射到 md + 打印废弃警告到 stderr
- 现有测试套件同步更新

## 影响范围

- `scripts/batch_pipeline.py` — generate 子命令逻辑重写
- `tests/test_generate_enhance.py` — 测试用例重写
- `SKILL.md` / `README.md` — 文档更新
- `templates/report_template.pptx` — 可选移除

## CLI 接口变更

```python
# 旧:
p_gen.add_argument("--format", choices=["all", "xlsx", "docx", "pptx"], default="all")
# 新:
p_gen.add_argument("--format", choices=["all", "md", "xlsx", "docx", "pptx"], default="md")
```

- `"all"` 和 `"md"` 走新路径（`generate_md`）
- `"xlsx"` / `"docx"` / `"pptx"` 打印废弃警告到 stderr，降级为 md

## 函数签名

```python
def _prepare_patient_rows(data: dict) -> list[dict]:
    """从 rag_results 中提取患者行数据，供 generate_md 消费。"""

def generate_md(data: dict, output_path: Path):
    """生成单一 Markdown 报告文件。"""
```

## MD 文件结构

```markdown
# 批量指南推荐报告

> 生成日期: YYYY-MM-DD | 患者数: N

## 目录
- 患者 P001 姓名
- 患者 P002 姓名
...

---

## 患者 P001 姓名

### 基本信息
| 字段 | 内容 |
|------|------|
| 患者ID | P001 |
| 肿瘤部位 | 胃体 |
| 病种诊断 | 胃癌 |
| 诊断摘要 | ... |

### 临床问题 1: {问题文本}

#### NCCN (v2026)

| 属性 | 内容 |
|------|------|
| 推荐意见 | （自由文本，不限制列宽） |
| 证据等级 | Category 1 |
| 来源 | test.txt L1-10 |

#### ESMO (v2026)

| 属性 | 内容 |
|------|------|
| 推荐意见 | （自由文本） |
| 证据等级 | Level I |
| 来源 | test.txt L20-30 |

#### 证据等级对照
| 指南 | 证据等级 | 含义 |
|------|----------|------|
| NCCN | Category 1 | ... |
| ESMO | Level I | ... |

#### 共识与差异
**共识点:**
- 各指南均提及: 关键词1、关键词2

**主要差异:**
- NCCN 独有: 关键词A、关键词B

---

## 患者 P002 姓名
...

---

*本文档由医学指南RAG系统自动生成，仅供临床参考，不替代专业医学判断。*
```

### 设计决策

| 项目 | 决策 | 理由 |
|------|------|------|
| 输出粒度 | 单文件全量 | 最易查阅和传递 |
| 推荐对比表 | 卡片式展开（每个指南一个子节） | 长文本换行问题 |
| 目录导航 | 纯文本列表，无锚点链接 | 渲染器兼容性 |
| 证据等级对照 | 问题内嵌 | 边看推荐边查等级 |
| 共识/差异 | 无序列表 | 简洁清晰 |
| 患者排序 | 中文姓名音序 | 继承现有逻辑 |

## 删除清单

- `generate_xlsx()` (~94 行)
- `generate_docx()` (~118 行)
- `generate_pptx()` (~279 行)
- `templates/report_template.pptx`（可选）

## 新增清单

- `generate_md()` (~120 行)
- `_prepare_patient_rows()` (~30 行，从旧函数中抽取共享遍历逻辑）
- 12 个测试用例（`tests/test_generate_enhance.py` 重写）

## 测试覆盖

12 个测试路径：

1. happy path — 正常数据生成完整 MD
2. legacy format 降级 — xlsx/docx/pptx 参数触发废弃警告
3. 空结果 — results 为空列表
4. 无推荐 — 患者无 guideline_results
5. 共识差异 — consensus/differences 字段正确渲染
6. 目录生成 — 纯文本目录与患者列表一致
7. 卡片式布局 — 多指南展开为独立子节
8. 证据等级对照 — 内嵌表格正确
9. 患者排序 — 按姓名音序
10. 特殊字符 — 姓名/推荐文本含 Markdown 特殊字符转义
11. 自定义输出目录 — --output-dir 参数
12. 单患者 — 仅 1 位患者时不崩溃

## Eng Review 决策记录

### Issue #1: --format 参数兼容性
**决策**: 旧格式值映射到 md + 打印废弃警告到 stderr
**理由**: 约束要求"子命令接口兼容"（命令名不变），不是参数值兼容。

### Issue #2: 共享数据准备逻辑
**决策**: 抽取 `_prepare_patient_rows()` 共享函数
**理由**: 三个旧 generate 函数各自遍历 results → questions → guideline_results，存在 DRY 违规。

### Issue #3: 测试覆盖范围
**决策**: 完整覆盖 12 个路径
**理由**: Markdown 生成是纯文本操作，测试成本低。
