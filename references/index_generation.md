# Knowledge Base Index Generation

## Overview

This document describes how to generate `data_structure.md` index files for the knowledge base.

---

## Index File Types

| Index Type | Location | Purpose |
|------------|----------|---------|
| Root Index | `$KB_ROOT/data_structure.md` | Overview of all guidelines |
| Organization Index | `$KB_ROOT/<org>/data_structure.md` | Chapter structure for one guideline |

---

## Root Index Template

```markdown
# 临床指南知识库总览

本目录包含 X 个组织的 Y 份临床指南文件。

---

## 指南目录

### <Organization1>/

- **机构**: <Full Name> (<Abbreviation>)
- **版本**: <Version>
- **语言**: <Language>
- **证据分级**: <Grading System Description>
- **特点**: <Key Features>
- **适用场景**: <When to use this guideline>

**文件清单**:
- `*.pdf` / `*.docx` — 原始指南文件
- `extracted/*.txt` — 预提取纯文本（推荐优先使用）
- `data_structure.md` — 本指南章节索引

---

## 证据等级跨指南对照表

| 指南 | 最高推荐 | 高推荐 | 中等推荐 | 低推荐/可选 |
|------|---------|--------|---------|------------|
| NCCN | Category 1 | Category 2A | Category 2B | Category 3 |
| ESMO | I,A | II,B | III,C | IV,D |
| CSCO | I级(1A) | I级(1B/2A) | II级 | III级 |
| Japanese | Strong | Weak | — | — |

---

## 临床问题 → 指南映射

| 临床问题类别 | 首选指南 | 补充参考 |
|-------------|---------|---------|
| 手术术式选择 | Japanese, NCCN | Korean, CSCO |
| 围手术期化疗 | NCCN, ESMO | CSCO, CACA |
| 晚期一线治疗 | NCCN, ESMO | CSCO |
| 靶向治疗 | NCCN, ESMO | CSCO |
| 免疫治疗 | NCCN, CSCO | ESMO |
| 姑息治疗 | ESMO, NCCN | CACA |

---

## 检索关键词建议

### 诊断相关
- diagnosis, 诊断
- staging, 分期
- biomarker, 生物标志物
- HER2, PD-L1, MSI-H, dMMR

### 治疗相关
- surgery, 手术
- chemotherapy, 化疗
- targeted therapy, 靶向治疗
- immunotherapy, 免疫治疗

---

*最后更新: <DATE>*
```

---

## Organization Index Template

```markdown
# <Organization> <Disease> 指南

## 指南基本信息

- **机构**: <Full Name>
- **版本**: <Version>
- **发布日期**: <Date>
- **语言**: <Language>
- **总行数**: <N> 行
- **证据分级**: <Grading System>

## 文件清单

| 文件 | 类型 | 行数 | 说明 |
|------|------|------|------|
| extracted/<name>.txt | 纯文本 | N | 预提取文本（首选） |
| <name>.pdf | PDF | — | 原始文件 |
| <name>.docx | DOCX | — | 原始文件 |

---

## 章节结构

### 1. 诊断与分期

| 章节 | 行数范围 | 内容摘要 |
|------|---------|---------|
| 诊断流程 | 1-50 | 诊断标准、检查项目 |
| 分期标准 | 51-120 | TNM分期定义 |
| 病理评估 | 121-200 | 病理报告要求、分子检测 |

### 2. 治疗

| 章节 | 行数范围 | 内容摘要 |
|------|---------|---------|
| 早期治疗 | 201-300 | 内镜治疗、手术治疗 |
| 局部进展期 | 301-450 | 围手术期治疗、新辅助治疗 |
| 晚期一线 | 451-600 | 一线化疗、靶向治疗、免疫治疗 |
| 晚期二线 | 601-700 | 二线治疗选择 |
| 姑息治疗 | 701-800 | 支持治疗、症状管理 |

### 3. 随访

| 章节 | 行数范围 | 内容摘要 |
|------|---------|---------|
| 随访策略 | 801-850 | 随访频率、检查项目 |

---

## 证据等级体系

<Describe the specific grading system used by this organization>

**示例 (CSCO)**:
- I级推荐: 高证据级别 + 高可及性
- II级推荐: 中等证据或可及性较低
- III级推荐: 临床实用但证据有限
- 证据类别: 1A/1B/2A/2B/3

---

## 常用检索关键词

### 诊断相关
- diagnosis, 诊断
- staging, 分期
- HER2, HER2阳性
- PD-L1, CPS
- MSI-H, dMMR

### 治疗相关
- surgery, 手术, gastrectomy, 胃切除术
- chemotherapy, 化疗, FLOT, CAPOX, SOX
- targeted therapy, 靶向治疗, trastuzumab, 曲妥珠单抗
- immunotherapy, 免疫治疗, pembrolizumab, nivolumab

### 药物相关
- trastuzumab, 曲妥珠单抗
- pertuzumab, 帕妥珠单抗
- pembrolizumab, 帕博利珠单抗
- nivolumab, 纳武利尤单抗

---

*最后更新: <DATE>*
```

---

## Automatic Index Generation Script

```python
#!/usr/bin/env python3
"""Auto-generate data_structure.md from extracted text."""

import re
from pathlib import Path
from datetime import date

def analyze_extracted_text(txt_path: Path) -> dict:
    """Analyze extracted text to identify chapters."""
    with open(txt_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    total_lines = len(lines)
    chapters = []

    # Find heading patterns
    heading_patterns = [
        r"^#\s+(.+)$",          # Markdown H1
        r"^##\s+(.+)$",         # Markdown H2
        r"^第[一二三四五六七八九十]+章",  # Chinese chapter
        r"^Chapter\s+\d+",      # English chapter
        r"^\d+\.\s+[A-Z]",      # Numbered section
    ]

    for i, line in enumerate(lines):
        for pattern in heading_patterns:
            if re.match(pattern, line):
                chapters.append({
                    "line": i + 1,
                    "title": line.strip()
                })
                break

    return {
        "total_lines": total_lines,
        "chapters": chapters
    }

def generate_org_index(org_dir: Path, org_name: str) -> str:
    """Generate organization index file."""
    extracted_dir = org_dir / "extracted"
    txt_files = list(extracted_dir.glob("*.txt"))

    if not txt_files:
        return None

    # Analyze first file
    analysis = analyze_extracted_text(txt_files[0])

    today = date.today().strftime("%Y-%m-%d")

    index_content = f"""# {org_name} 指南

## 指南基本信息

- **机构**: {org_name}
- **总行数**: {analysis['total_lines']} 行
- **提取文件**: {len(txt_files)} 个

## 文件清单

| 文件 | 行数 |
|------|------|
"""

    for txt in txt_files:
        with open(txt, "r", encoding="utf-8") as f:
            lines = len(f.readlines())
        index_content += f"| extracted/{txt.name} | {lines} |\n"

    index_content += f"""
## 章节结构

| 章节 | 行号 | 标题 |
|------|------|------|
"""

    for ch in analysis["chapters"][:20]:  # First 20 chapters
        index_content += f"| {ch['line']} | {ch['title'][:50]} |\n"

    index_content += f"""
---

*最后更新: {today}*
"""

    return index_content

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python generate_index.py <org_directory>")
        sys.exit(1)

    org_dir = Path(sys.argv[1])
    org_name = org_dir.name

    index_content = generate_org_index(org_dir, org_name)

    if index_content:
        output_file = org_dir / "data_structure.md"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(index_content)
        print(f"Generated: {output_file}")
    else:
        print("No extracted text files found")
```

---

## Manual Index Generation Checklist

When generating index manually:

1. **Read entire extracted text** — Understand structure
2. **Identify major sections** — Treatment, diagnosis, follow-up
3. **Record line ranges** — Accurate line numbers
4. **Extract keywords** — Both Chinese and English
5. **Document evidence grading** — Specific to this organization
6. **Note special features** — Tables, flowcharts, algorithms

---

## Index Quality Checklist

- [ ] Organization name and version correct
- [ ] Total line count matches actual file
- [ ] Chapter line ranges are accurate (±5 lines)
- [ ] Keywords cover both languages
- [ ] Evidence grading system described
- [ ] Last updated date included

---

*Last Updated: 2026-03-04*
