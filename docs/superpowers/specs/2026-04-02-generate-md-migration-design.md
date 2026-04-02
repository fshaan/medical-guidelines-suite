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
- 旧格式值（xlsx/docx/pptx）映射到 md + `warnings.warn(FutureWarning)` 废弃警告
- 现有测试套件同步更新

## 影响范围

- `scripts/batch_pipeline.py` — generate 子命令逻辑重写
- `tests/test_generate_enhance.py` — 测试用例重写
- `SKILL.md` / `README.md` — 文档更新
- `templates/report_template.pptx` — 移除
- 依赖清理 — 移除 `python-docx`、`python-pptx`（`openpyxl` 保留，`cmd_parse` 使用）

## Out of Scope

- **图片/图表处理**：当前 RAG 结果数据模型无图片字段。若未来扩展，在 `generate_md` 中增加 `![]()` 语法支持即可，当前版本仅处理纯文本。

## CLI 接口变更

```python
# 旧:
p_gen.add_argument("--format", choices=["all", "xlsx", "docx", "pptx"], default="all")
# 新:
p_gen.add_argument("--format", choices=["all", "md", "xlsx", "docx", "pptx"], default="md")
```

- `"all"` 和 `"md"` 走新路径（`generate_md`）
- `"xlsx"` / `"docx"` / `"pptx"` 通过 `warnings.warn("... 格式已废弃，已降级为 Markdown 输出", FutureWarning)` 降级为 md

### 输出文件命名

默认输出文件名：`批量指南推荐报告_YYYYMMDD.md`（基于 `data["generated_at"]` 日期）

## 函数签名

```python
def md_escape(text: str) -> str:
    """转义 Markdown 特殊字符（|, *, [, ], \, `）。医疗文本中这些字符频繁出现。"""

def _prepare_patient_rows(data: dict) -> list[dict]:
    """从 rag_results 中提取患者行数据，返回纯 POD 结构（list of dict）。
    不依赖任何渲染器，为未来 JSON/HTML 输出提供复用基础。
    返回格式：
    [
        {
            "patient_id": str,
            "patient_name": str,
            "primary_site": str,
            "disease_type": str,
            "diagnosis_summary": str,
            "questions": [
                {
                    "question": str,
                    "guidelines": [
                        {
                            "name": str, "version": str,
                            "recommendation": str, "evidence_level": str,
                            "source_file": str, "source_lines": str,
                        }
                    ],
                    "evidence_table": [{"guideline": str, "level": str, "meaning": str}],
                    "consensus": [str],
                    "differences": [str],
                }
            ]
        }
    ]
    """

def generate_md(data: dict, output_path: Path):
    """生成单一 Markdown 报告文件。"""
```

## MD 文件结构

```markdown
# 批量指南推荐报告

> 生成日期: YYYY-MM-DD | 患者数: N

## 目录
- [患者 P001 姓名](#患者-p001-姓名)
- [患者 P002 姓名](#患者-p002-姓名)
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

## 附录：证据等级参考

以下汇总本报告中出现的所有证据等级体系及其含义。

| 体系 | 等级 | 含义 |
|------|------|------|
| NCCN | Category 1 | ... |
| NCCN | Category 2A | ... |
| ESMO | Level I | ... |
| ... | ... | ... |

---

*本文档由医学指南RAG系统自动生成，仅供临床参考，不替代专业医学判断。*
```

### 设计决策

| 项目 | 决策 | 理由 |
|------|------|------|
| 输出粒度 | 单文件全量 | 最易查阅和传递 |
| 推荐对比表 | 卡片式展开（每个指南一个子节） | 长文本换行问题 |
| 目录导航 | Markdown 锚点链接 | 不支持的环境退化为纯文本，不破坏排版；批量报告中跳转功能关键 |
| 证据等级对照 | 问题内嵌 + 末尾全局附录 | 内嵌便于对比，附录作为快速查询手册 |
| 共识/差异 | 无序列表 | 简洁清晰 |
| 患者排序 | 中文姓名音序 | 继承现有逻辑 |
| 特殊字符 | `md_escape()` 工具函数 | 医疗文本中 `\|`、`*`、`[` 常见，必须显式转义 |
| 废弃警告 | `warnings.warn(..., FutureWarning)` | `DeprecationWarning` 默认被 Python 静默，`FutureWarning` 可见 |
| 数据准备 | `_prepare_patient_rows()` 返回纯 POD | 数据获取与展现解耦，为未来 JSON/HTML 输出复用 |
| 文件命名 | `批量指南推荐报告_YYYYMMDD.md` | 统一命名，便于管理多次运行结果 |

## 删除清单

- `generate_xlsx()` (~94 行)
- `generate_docx()` (~118 行)
- `generate_pptx()` (~279 行)
- `templates/report_template.pptx`

## 新增清单

- `md_escape()` (~10 行，Markdown 特殊字符转义）
- `generate_md()` (~130 行)
- `_prepare_patient_rows()` (~35 行，从旧函数中抽取共享遍历逻辑，返回纯 POD）
- 12 个测试用例（`tests/test_generate_enhance.py` 重写）

## 测试覆盖

12 个测试路径：

1. happy path — 正常数据生成完整 MD
2. legacy format 降级 — xlsx/docx/pptx 参数触发 FutureWarning
3. 空结果 — results 为空列表
4. 无推荐 — 患者无 guideline_results
5. 共识差异 — consensus/differences 字段正确渲染
6. 目录生成 — 锚点链接格式正确，slug 与标题一致
7. 卡片式布局 — 多指南展开为独立子节
8. 证据等级对照 — 内嵌表格 + 末尾附录均正确
9. 患者排序 — 按姓名音序
10. 特殊字符 — md_escape 正确转义 `|`、`*`、`[`、`]`、`` ` ``、`\`
11. 自定义输出目录 — --output-dir 参数
12. 单患者 — 仅 1 位患者时不崩溃

## Eng Review 决策记录

### Issue #1: --format 参数兼容性
**决策**: 旧格式值映射到 md + `warnings.warn(FutureWarning)`
**理由**: 约束要求"子命令接口兼容"（命令名不变），不是参数值兼容。使用 `FutureWarning` 而非 `DeprecationWarning` 确保警告可见。

### Issue #2: 共享数据准备逻辑
**决策**: 抽取 `_prepare_patient_rows()` 共享函数，返回纯 POD 结构
**理由**: 三个旧 generate 函数各自遍历 results → questions → guideline_results，存在 DRY 违规。POD 返回值为未来 JSON/HTML 输出提供复用基础。

### Issue #3: 测试覆盖范围
**决策**: 完整覆盖 12 个路径
**理由**: Markdown 生成是纯文本操作，测试成本低。

### Issue #4: 目录导航
**决策**: 使用 Markdown 锚点链接
**理由**: 不支持的环境退化为纯文本无副作用；批量报告中跳转功能是关键体验。

### Issue #5: 证据等级全局附录
**决策**: 问题内嵌 + 末尾附录
**理由**: 内嵌便于即时对比，附录聚合全报告所有等级体系，避免跨问题滚动。

### Issue #6: 依赖清理
**决策**: 移除 python-docx、python-pptx；保留 openpyxl（cmd_parse 使用）
**理由**: 迁移后这两个库不再有任何消费者。
