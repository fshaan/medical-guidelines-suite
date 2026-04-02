# generate MD 迁移实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `batch_pipeline.py` 的 `generate` 子命令从三格式输出（xlsx/docx/pptx）迁移到单一 Markdown 输出。

**Architecture:** 新增 `md_escape()`、`_prepare_patient_rows()`、`generate_md()` 三个函数，删除旧的三格式 generate 函数。`_prepare_patient_rows()` 返回纯 POD 结构，与渲染逻辑解耦。CLI 层面对旧 `--format` 值发出 `FutureWarning` 并降级为 md。

**Tech Stack:** Python 3.9+，标准库 only（无新依赖）

**Spec:** `docs/superpowers/specs/2026-04-02-generate-md-migration-design.md`

---

## File Map

| 文件 | 操作 | 职责 |
|------|------|------|
| `scripts/batch_pipeline.py:1829-2048` | 删除 | `generate_xlsx()`, `generate_docx()`, `generate_pptx()` |
| `scripts/batch_pipeline.py:1829` | 新增（同位置） | `md_escape()`, `_prepare_patient_rows()`, `generate_md()` |
| `scripts/batch_pipeline.py:2324-2411` | 修改 | `cmd_generate()` 路由 + CLI 参数 |
| `tests/test_generate_enhance.py` | 重写 | 12 个测试用例 |
| `templates/report_template.pptx` | 删除 | PPTX 模板不再需要 |

---

### Task 1: md_escape 工具函数

**Files:**
- Modify: `scripts/batch_pipeline.py` (在 `load_rag_results` 之后、`generate_xlsx` 之前插入)
- Test: `tests/test_generate_enhance.py`

- [ ] **Step 1: 在 tests/test_generate_enhance.py 中写 md_escape 的测试**

```python
import json
import pytest
import warnings
from pathlib import Path


def _make_rag_results(tmp_path, names=None):
    names = names or [("P001", "张三"), ("P002", "李四"), ("P003", "王五")]
    results = []
    for pid, name in names:
        results.append({
            "patient_id": pid, "patient_name": name,
            "primary_site": "胃体", "disease_type": "胃癌",
            "diagnosis_summary": "测试诊断",
            "clinical_questions": [{
                "question": "测试问题",
                "guideline_results": [{"guideline": "NCCN", "version": "2026",
                    "recommendation": "测试推荐", "evidence_level": "Category 1",
                    "source_file": "test.txt", "source_lines": "1-10"}],
                "consensus": ["共识1"], "differences": ["差异1"],
            }],
        })
    data = {"generated_at": "2026-04-02", "patient_count": len(results), "results": results}
    p = tmp_path / "rag_results.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def test_md_escape_pipe():
    from scripts.batch_pipeline import md_escape
    assert md_escape("A | B") == "A \\| B"


def test_md_escape_star():
    from scripts.batch_pipeline import md_escape
    assert md_escape("*emphasis*") == "\\*emphasis\\*"


def test_md_escape_brackets():
    from scripts.batch_pipeline import md_escape
    assert md_escape("[ref]") == "\\[ref\\]"


def test_md_escape_backtick():
    from scripts.batch_pipeline import md_escape
    assert md_escape("`code`") == "\\`code\\`"


def test_md_escape_backslash():
    from scripts.batch_pipeline import md_escape
    assert md_escape("A\\B") == "A\\\\B"


def test_md_escape_no_escape_needed():
    from scripts.batch_pipeline import md_escape
    assert md_escape("普通文本") == "普通文本"


def test_md_escape_empty():
    from scripts.batch_pipeline import md_escape
    assert md_escape("") == ""


def test_md_escape_combined():
    from scripts.batch_pipeline import md_escape
    assert md_escape("A*B|C[D") == "A\\*B\\|C\\[D"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_generate_enhance.py::test_md_escape_pipe -v`
Expected: FAIL — `ImportError: cannot import name 'md_escape'`

- [ ] **Step 3: 在 scripts/batch_pipeline.py 的 `load_rag_results()` 函数之后（第 1827 行后）插入 md_escape**

```python
def md_escape(text: str) -> str:
    """转义 Markdown 特殊字符"""
    if not text:
        return ""
    text = text.replace("\\", "\\\\")
    text = text.replace("|", "\\|")
    text = text.replace("*", "\\*")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace("`", "\\`")
    return text
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_generate_enhance.py -k "md_escape" -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add scripts/batch_pipeline.py tests/test_generate_enhance.py
git commit -m "feat: add md_escape utility function with tests"
```

---

### Task 2: _prepare_patient_rows 数据准备函数

**Files:**
- Modify: `scripts/batch_pipeline.py` (在 `md_escape` 之后插入)
- Test: `tests/test_generate_enhance.py`

- [ ] **Step 1: 在 tests/test_generate_enhance.py 末尾追加测试**

```python
def test_prepare_patient_rows_basic():
    from scripts.batch_pipeline import _prepare_patient_rows
    data = {
        "results": [{
            "patient_id": "P001", "patient_name": "张三",
            "primary_site": "胃体", "disease_type": "胃癌",
            "diagnosis_summary": "摘要",
            "clinical_questions": [{
                "question": "治疗方案",
                "guideline_results": [
                    {"guideline": "NCCN", "version": "2026",
                     "recommendation": "手术", "evidence_level": "Category 1",
                     "source_file": "test.txt", "source_lines": "1-10"},
                    {"guideline": "ESMO", "version": "2026",
                     "recommendation": "化疗", "evidence_level": "Level I",
                     "source_file": "test2.txt", "source_lines": "20-30"},
                ],
                "consensus": ["手术为主"], "differences": ["NCCN 独有: 术后辅助"],
            }],
        }],
    }
    rows = _prepare_patient_rows(data)
    assert len(rows) == 1
    row = rows[0]
    assert row["patient_id"] == "P001"
    assert row["patient_name"] == "张三"
    assert row["primary_site"] == "胃体"
    assert row["disease_type"] == "胃癌"
    assert row["diagnosis_summary"] == "摘要"
    assert len(row["questions"]) == 1
    q = row["questions"][0]
    assert q["question"] == "治疗方案"
    assert len(q["guidelines"]) == 2
    assert q["guidelines"][0]["name"] == "NCCN"
    assert q["guidelines"][0]["recommendation"] == "手术"
    assert q["consensus"] == ["手术为主"]
    assert q["differences"] == ["NCCN 独有: 术后辅助"]


def test_prepare_patient_rows_empty():
    from scripts.batch_pipeline import _prepare_patient_rows
    rows = _prepare_patient_rows({"results": []})
    assert rows == []


def test_prepare_patient_rows_no_questions():
    from scripts.batch_pipeline import _prepare_patient_rows
    data = {"results": [{
        "patient_id": "P001", "patient_name": "张三",
        "primary_site": "胃体", "disease_type": "胃癌",
        "diagnosis_summary": "摘要",
        "clinical_questions": [],
    }]}
    rows = _prepare_patient_rows(data)
    assert len(rows) == 1
    assert rows[0]["questions"] == []


def test_prepare_patient_rows_no_guidelines():
    from scripts.batch_pipeline import _prepare_patient_rows
    data = {"results": [{
        "patient_id": "P001", "patient_name": "张三",
        "primary_site": "胃体", "disease_type": "胃癌",
        "diagnosis_summary": "摘要",
        "clinical_questions": [{
            "question": "Q1",
            "guideline_results": [],
            "consensus": [], "differences": [],
        }],
    }]}
    rows = _prepare_patient_rows(data)
    assert len(rows) == 1
    assert rows[0]["questions"][0]["guidelines"] == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_generate_enhance.py::test_prepare_patient_rows_basic -v`
Expected: FAIL — `ImportError: cannot import name '_prepare_patient_rows'`

- [ ] **Step 3: 在 scripts/batch_pipeline.py 的 `md_escape()` 之后插入 _prepare_patient_rows**

```python
def _prepare_patient_rows(data: dict) -> list[dict]:
    """从 rag_results 中提取患者行数据，返回纯 POD 结构。"""
    rows = []
    for result in data.get("results", []):
        questions = []
        for q in result.get("clinical_questions", []):
            guidelines = []
            for gr in q.get("guideline_results", []):
                guidelines.append({
                    "name": gr.get("guideline", ""),
                    "version": gr.get("version", ""),
                    "recommendation": gr.get("recommendation", ""),
                    "evidence_level": gr.get("evidence_level", ""),
                    "source_file": gr.get("source_file", ""),
                    "source_lines": gr.get("source_lines", ""),
                })
            questions.append({
                "question": q.get("question", ""),
                "guidelines": guidelines,
                "evidence_table": [],
                "consensus": q.get("consensus", []),
                "differences": q.get("differences", []),
            })
        rows.append({
            "patient_id": result.get("patient_id", ""),
            "patient_name": result.get("patient_name", ""),
            "primary_site": result.get("primary_site", ""),
            "disease_type": result.get("disease_type", ""),
            "diagnosis_summary": result.get("diagnosis_summary", ""),
            "questions": questions,
        })
    return rows
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_generate_enhance.py -k "prepare_patient_rows" -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add scripts/batch_pipeline.py tests/test_generate_enhance.py
git commit -m "feat: add _prepare_patient_rows with POD structure and tests"
```

---

### Task 3: generate_md 核心函数

**Files:**
- Modify: `scripts/batch_pipeline.py` (在 `_prepare_patient_rows` 之后插入)
- Test: `tests/test_generate_enhance.py`

- [ ] **Step 1: 在 tests/test_generate_enhance.py 末尾追加测试**

```python
def _read_output(tmp_path, filename="批量指南推荐报告_20260402.md"):
    p = tmp_path / "Output" / filename
    if not p.exists():
        for f in (tmp_path / "Output").iterdir():
            if f.suffix == ".md":
                return f.read_text(encoding="utf-8")
        return None
    return p.read_text(encoding="utf-8")


def test_generate_md_happy_path(tmp_path):
    from scripts.batch_pipeline import generate_md
    rag_path = _make_rag_results(tmp_path, names=[("P001", "张三")])
    data = json.loads(rag_path.read_text(encoding="utf-8"))
    output_path = tmp_path / "Output" / "test_report.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    assert "# 批量指南推荐报告" in content
    assert "生成日期: 2026-04-02" in content
    assert "患者数: 1" in content
    assert "## 目录" in content
    assert "张三" in content
    assert "### 基本信息" in content
    assert "P001" in content
    assert "胃体" in content
    assert "测试问题" in content
    assert "NCCN" in content
    assert "Category 1" in content
    assert "共识1" in content
    assert "差异1" in content
    assert "本文档由医学指南RAG系统自动生成" in content


def test_generate_md_card_layout(tmp_path):
    from scripts.batch_pipeline import generate_md
    data = {
        "generated_at": "2026-04-02", "patient_count": 1, "results": [{
            "patient_id": "P001", "patient_name": "张三",
            "primary_site": "胃体", "disease_type": "胃癌",
            "diagnosis_summary": "摘要",
            "clinical_questions": [{
                "question": "治疗方案",
                "guideline_results": [
                    {"guideline": "NCCN", "version": "2026",
                     "recommendation": "推荐手术切除", "evidence_level": "Category 1",
                     "source_file": "a.txt", "source_lines": "1-10"},
                    {"guideline": "ESMO", "version": "2026",
                     "recommendation": "推荐围手术期化疗", "evidence_level": "Level I",
                     "source_file": "b.txt", "source_lines": "20-30"},
                ],
                "consensus": ["手术为主"], "differences": ["NCCN 独有: D2清扫"],
            }],
        }],
    }
    output_path = tmp_path / "report.md"
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    assert "#### NCCN (v2026)" in content
    assert "#### ESMO (v2026)" in content
    assert "推荐手术切除" in content
    assert "围手术期化疗" in content


def test_generate_md_toc_anchors(tmp_path):
    from scripts.batch_pipeline import generate_md
    data = {
        "generated_at": "2026-04-02", "patient_count": 2, "results": [
            {"patient_id": "P001", "patient_name": "张三",
             "primary_site": "胃体", "disease_type": "胃癌",
             "diagnosis_summary": "摘要", "clinical_questions": []},
            {"patient_id": "P002", "patient_name": "李四",
             "primary_site": "结肠", "disease_type": "结肠癌",
             "diagnosis_summary": "摘要", "clinical_questions": []},
        ],
    }
    output_path = tmp_path / "report.md"
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    assert "[张三](#" in content
    assert "[李四](#" in content


def test_generate_md_empty_results(tmp_path):
    from scripts.batch_pipeline import generate_md
    data = {"generated_at": "2026-04-02", "patient_count": 0, "results": []}
    output_path = tmp_path / "report.md"
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    assert "患者数: 0" in content
    assert "## 目录" in content
    assert "本文档由医学指南RAG系统自动生成" in content


def test_generate_md_no_guidelines(tmp_path):
    from scripts.batch_pipeline import generate_md
    data = {
        "generated_at": "2026-04-02", "patient_count": 1, "results": [{
            "patient_id": "P001", "patient_name": "张三",
            "primary_site": "胃体", "disease_type": "胃癌",
            "diagnosis_summary": "摘要",
            "clinical_questions": [{
                "question": "Q1", "guideline_results": [],
                "consensus": [], "differences": [],
            }],
        }],
    }
    output_path = tmp_path / "report.md"
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    assert "Q1" in content


def test_generate_md_consensus_differences(tmp_path):
    from scripts.batch_pipeline import generate_md
    data = {
        "generated_at": "2026-04-02", "patient_count": 1, "results": [{
            "patient_id": "P001", "patient_name": "张三",
            "primary_site": "胃体", "disease_type": "胃癌",
            "diagnosis_summary": "摘要",
            "clinical_questions": [{
                "question": "Q1",
                "guideline_results": [{"guideline": "NCCN", "version": "2026",
                    "recommendation": "R", "evidence_level": "C1",
                    "source_file": "a.txt", "source_lines": "1"}],
                "consensus": ["共识A", "共识B"],
                "differences": ["差异X"],
            }],
        }],
    }
    output_path = tmp_path / "report.md"
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    assert "共识A" in content
    assert "共识B" in content
    assert "差异X" in content
    assert "**共识点:**" in content
    assert "**主要差异:**" in content


def test_generate_md_evidence_level_table(tmp_path):
    from scripts.batch_pipeline import generate_md
    data = {
        "generated_at": "2026-04-02", "patient_count": 1, "results": [{
            "patient_id": "P001", "patient_name": "张三",
            "primary_site": "胃体", "disease_type": "胃癌",
            "diagnosis_summary": "摘要",
            "clinical_questions": [{
                "question": "Q1",
                "guideline_results": [
                    {"guideline": "NCCN", "version": "2026",
                     "recommendation": "R", "evidence_level": "Category 1",
                     "source_file": "a.txt", "source_lines": "1"},
                ],
                "consensus": [], "differences": [],
            }],
        }],
    }
    output_path = tmp_path / "report.md"
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    assert "#### 证据等级对照" in content


def test_generate_md_evidence_appendix(tmp_path):
    from scripts.batch_pipeline import generate_md
    data = {
        "generated_at": "2026-04-02", "patient_count": 1, "results": [{
            "patient_id": "P001", "patient_name": "张三",
            "primary_site": "胃体", "disease_type": "胃癌",
            "diagnosis_summary": "摘要",
            "clinical_questions": [{
                "question": "Q1",
                "guideline_results": [
                    {"guideline": "NCCN", "version": "2026",
                     "recommendation": "R", "evidence_level": "Category 1",
                     "source_file": "a.txt", "source_lines": "1"},
                    {"guideline": "ESMO", "version": "2026",
                     "recommendation": "R2", "evidence_level": "Level I",
                     "source_file": "b.txt", "source_lines": "2"},
                ],
                "consensus": [], "differences": [],
            }],
        }],
    }
    output_path = tmp_path / "report.md"
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    assert "## 附录：证据等级参考" in content
    assert "NCCN" in content.split("## 附录：证据等级参考")[1]
    assert "ESMO" in content.split("## 附录：证据等级参考")[1]


def test_generate_md_sort_by_name(tmp_path):
    from scripts.batch_pipeline import generate_md
    data = {
        "generated_at": "2026-04-02", "patient_count": 3, "results": [
            {"patient_id": "P003", "patient_name": "王五",
             "primary_site": "胃体", "disease_type": "胃癌",
             "diagnosis_summary": "摘要", "clinical_questions": []},
            {"patient_id": "P001", "patient_name": "张三",
             "primary_site": "胃体", "disease_type": "胃癌",
             "diagnosis_summary": "摘要", "clinical_questions": []},
            {"patient_id": "P002", "patient_name": "李四",
             "primary_site": "胃体", "disease_type": "胃癌",
             "diagnosis_summary": "摘要", "clinical_questions": []},
        ],
    }
    output_path = tmp_path / "report.md"
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    pos_li = content.index("李四")
    pos_wang = content.index("王五")
    pos_zhang = content.index("张三")
    assert pos_li < pos_wang < pos_zhang


def test_generate_md_special_chars(tmp_path):
    from scripts.batch_pipeline import generate_md
    data = {
        "generated_at": "2026-04-02", "patient_count": 1, "results": [{
            "patient_id": "P001", "patient_name": "张三",
            "primary_site": "胃*体", "disease_type": "胃|癌",
            "diagnosis_summary": "摘要[1]",
            "clinical_questions": [{
                "question": "Q1",
                "guideline_results": [{"guideline": "NCCN", "version": "2026",
                    "recommendation": "用*药|方案[2]", "evidence_level": "C1",
                    "source_file": "a.txt", "source_lines": "1"}],
                "consensus": [], "differences": [],
            }],
        }],
    }
    output_path = tmp_path / "report.md"
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    lines_with_pipe = [l for l in content.split("\n") if "|" in l and "---" not in l]
    table_lines = [l for l in lines_with_pipe if l.strip().startswith("|")]
    for tl in table_lines:
        inner = tl.strip("|")
        assert "\\|" in inner or "|" not in inner.replace("\\|", ""), f"Unescaped pipe: {tl}"


def test_generate_md_custom_output_dir(tmp_path):
    import argparse
    from scripts.batch_pipeline import cmd_generate
    rag_path = _make_rag_results(tmp_path)
    custom_dir = tmp_path / "custom_output"
    args = argparse.Namespace(input=str(rag_path), output_dir=str(custom_dir), format="md")
    cmd_generate(args)
    md_files = list(custom_dir.glob("*.md"))
    assert len(md_files) == 1


def test_generate_md_single_patient(tmp_path):
    from scripts.batch_pipeline import generate_md
    data = {
        "generated_at": "2026-04-02", "patient_count": 1, "results": [{
            "patient_id": "P001", "patient_name": "独一",
            "primary_site": "胃体", "disease_type": "胃癌",
            "diagnosis_summary": "摘要", "clinical_questions": [{
                "question": "Q1",
                "guideline_results": [{"guideline": "NCCN", "version": "2026",
                    "recommendation": "R", "evidence_level": "C1",
                    "source_file": "a.txt", "source_lines": "1"}],
                "consensus": [], "differences": [],
            }],
        }],
    }
    output_path = tmp_path / "report.md"
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    assert "独一" in content
    assert "患者数: 1" in content
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_generate_enhance.py::test_generate_md_happy_path -v`
Expected: FAIL — `ImportError: cannot import name 'generate_md'`

- [ ] **Step 3: 在 scripts/batch_pipeline.py 的 `_prepare_patient_rows()` 之后插入 generate_md**

```python
def _slugify(text: str) -> str:
    """生成 Markdown 锚点 slug（中文保留，空格转 -，去掉特殊字符）"""
    slug = text.strip().lower()
    slug = re.sub(r"[^\w\u4e00-\u9fff\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    return slug


def generate_md(data: dict, output_path: Path):
    """生成单一 Markdown 报告文件。"""
    rows = _prepare_patient_rows(data)
    generated_at = data.get("generated_at", str(date.today()))
    patient_count = data.get("patient_count", len(rows))
    date_compact = generated_at.replace("-", "")

    lines = []
    lines.append("# 批量指南推荐报告")
    lines.append("")
    lines.append(f"> 生成日期: {md_escape(generated_at)} | 患者数: {patient_count}")
    lines.append("")
    lines.append("## 目录")

    for row in rows:
        pid = row["patient_id"]
        name = md_escape(row["patient_name"])
        slug = _slugify(f"{pid} {row['patient_name']}")
        lines.append(f"- [{name}](#{slug})")

    lines.append("")
    lines.append("---")
    lines.append("")

    all_evidence_entries = []

    for row in rows:
        pid = row["patient_id"]
        name = md_escape(row["patient_name"])
        lines.append(f"## {pid} {name}")
        lines.append("")
        lines.append("### 基本信息")
        lines.append("")
        lines.append("| 字段 | 内容 |")
        lines.append("|------|------|")
        info_fields = [
            ("患者ID", pid),
            ("肿瘤部位", md_escape(row["primary_site"])),
            ("病种诊断", md_escape(row["disease_type"])),
            ("诊断摘要", md_escape(row["diagnosis_summary"])),
        ]
        for label, value in info_fields:
            lines.append(f"| {label} | {value or '—'} |")
        lines.append("")

        for qi, q in enumerate(row["questions"], 1):
            question_text = md_escape(q["question"])
            lines.append(f"### 临床问题 {qi}: {question_text}")
            lines.append("")

            for g in q["guidelines"]:
                gname = md_escape(g["name"])
                gver = md_escape(g["version"])
                lines.append(f"#### {gname} ({gver})")
                lines.append("")
                lines.append("| 属性 | 内容 |")
                lines.append("|------|------|")
                lines.append(f"| 推荐意见 | {md_escape(g['recommendation'])} |")
                lines.append(f"| 证据等级 | {md_escape(g['evidence_level'])} |")
                source = g["source_file"]
                slines = g["source_lines"]
                source_display = f"{md_escape(source)} L{slines}" if slines else md_escape(source)
                lines.append(f"| 来源 | {source_display} |")
                lines.append("")

                if g["evidence_level"]:
                    all_evidence_entries.append((g["name"], g["evidence_level"]))

            if any(g["evidence_level"] for g in q["guidelines"]):
                lines.append("#### 证据等级对照")
                lines.append("")
                lines.append("| 指南 | 证据等级 | 含义 |")
                lines.append("|------|----------|------|")
                for g in q["guidelines"]:
                    if g["evidence_level"]:
                        lines.append(f"| {md_escape(g['name'])} | {md_escape(g['evidence_level'])} | — |")
                lines.append("")

            lines.append("#### 共识与差异")
            lines.append("")
            if q["consensus"]:
                lines.append("**共识点:**")
                for c in q["consensus"]:
                    lines.append(f"- {md_escape(c)}")
                lines.append("")
            if q["differences"]:
                lines.append("**主要差异:**")
                for d in q["differences"]:
                    lines.append(f"- {md_escape(d)}")
                lines.append("")

        lines.append("---")
        lines.append("")

    if all_evidence_entries:
        lines.append("## 附录：证据等级参考")
        lines.append("")
        lines.append("以下汇总本报告中出现的所有证据等级体系及其含义。")
        lines.append("")
        lines.append("| 体系 | 等级 | 含义 |")
        lines.append("|------|------|------|")
        seen = set()
        for gname, level in all_evidence_entries:
            key = (gname, level)
            if key not in seen:
                seen.add(key)
                lines.append(f"| {md_escape(gname)} | {md_escape(level)} | — |")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("*本文档由医学指南RAG系统自动生成，仅供临床参考，不替代专业医学判断。*")
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  ✓ Markdown 报告: {output_path}")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_generate_enhance.py -k "generate_md" -v`
Expected: 12 passed

- [ ] **Step 5: 提交**

```bash
git add scripts/batch_pipeline.py tests/test_generate_enhance.py
git commit -m "feat: add generate_md with card layout, TOC anchors, evidence appendix"
```

---

### Task 4: 重写 cmd_generate + CLI 参数

**Files:**
- Modify: `scripts/batch_pipeline.py:2324-2411`（`cmd_generate` 函数 + CLI parser）

- [ ] **Step 1: 替换 cmd_generate 函数（第 2324-2350 行）**

将整个 `cmd_generate` 函数替换为：

```python
def cmd_generate(args):
    """generate 子命令入口"""
    import warnings

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"RAG 结果文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)

    data = load_rag_results(input_path)
    patient_count = data.get("patient_count", len(data.get("results", [])))
    print(f"加载 RAG 结果: {patient_count} 位患者\n")

    if data.get("results"):
        data["results"] = _sort_results_by_name(data["results"])

    output_dir = Path(args.output_dir).resolve()
    fmt = args.format

    if fmt in ("xlsx", "docx", "pptx"):
        warnings.warn(
            f"--format {fmt} 已废弃，已降级为 Markdown 输出。将在未来版本移除。",
            FutureWarning,
            stacklevel=2,
        )

    generated_at = data.get("generated_at", str(date.today()))
    filename = f"批量指南推荐报告_{generated_at.replace('-', '')}.md"
    generate_md(data, output_dir / filename)

    print(f"\n生成完成 → {output_dir}/")
```

- [ ] **Step 2: 替换 CLI parser 中 generate 子命令的 --format 参数（第 2407-2411 行）**

将：
```python
    p_gen = sub.add_parser("generate", help="从 RAG 结果生成产出物")
    p_gen.add_argument("--input", required=True, help="RAG 结果 JSON 路径")
    p_gen.add_argument("--output-dir", default="Output", help="输出目录")
    p_gen.add_argument("--format", choices=["all", "xlsx", "docx", "pptx"], default="all",
                       help="输出格式 (默认 all)")
```

替换为：
```python
    p_gen = sub.add_parser("generate", help="从 RAG 结果生成 Markdown 报告")
    p_gen.add_argument("--input", required=True, help="RAG 结果 JSON 路径")
    p_gen.add_argument("--output-dir", default="Output", help="输出目录")
    p_gen.add_argument("--format", choices=["all", "md", "xlsx", "docx", "pptx"], default="md",
                       help="输出格式 (默认 md；xlsx/docx/pptx 已废弃)")
```

- [ ] **Step 3: 同时更新文件顶部的 docstring（第 11 行）**

将：
```
  generate    - 从 RAG 结果 JSON 生成 xlsx/docx/pptx 产出物
```
替换为：
```
  generate    - 从 RAG 结果 JSON 生成 Markdown 报告
```

- [ ] **Step 4: 运行全量 generate 相关测试**

Run: `python3 -m pytest tests/test_generate_enhance.py -v`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add scripts/batch_pipeline.py
git commit -m "feat: rewrite cmd_generate for MD output with legacy format deprecation"
```

---

### Task 5: 添加 legacy format 降级测试

**Files:**
- Test: `tests/test_generate_enhance.py`

- [ ] **Step 1: 在 tests/test_generate_enhance.py 末尾追加测试**

```python
def test_generate_legacy_format_xlsx_warning(tmp_path):
    import argparse
    from scripts.batch_pipeline import cmd_generate
    rag_path = _make_rag_results(tmp_path)
    custom_dir = tmp_path / "legacy_out"
    args = argparse.Namespace(input=str(rag_path), output_dir=str(custom_dir), format="xlsx")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cmd_generate(args)
        future_warnings = [x for x in w if issubclass(x.category, FutureWarning)]
        assert len(future_warnings) == 1
        assert "xlsx" in str(future_warnings[0].message)
    md_files = list(custom_dir.glob("*.md"))
    assert len(md_files) == 1


def test_generate_legacy_format_docx_warning(tmp_path):
    import argparse
    from scripts.batch_pipeline import cmd_generate
    rag_path = _make_rag_results(tmp_path)
    custom_dir = tmp_path / "legacy_out"
    args = argparse.Namespace(input=str(rag_path), output_dir=str(custom_dir), format="docx")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cmd_generate(args)
        future_warnings = [x for x in w if issubclass(x.category, FutureWarning)]
        assert len(future_warnings) == 1
        assert "docx" in str(future_warnings[0].message)


def test_generate_legacy_format_pptx_warning(tmp_path):
    import argparse
    from scripts.batch_pipeline import cmd_generate
    rag_path = _make_rag_results(tmp_path)
    custom_dir = tmp_path / "legacy_out"
    args = argparse.Namespace(input=str(rag_path), output_dir=str(custom_dir), format="pptx")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cmd_generate(args)
        future_warnings = [x for x in w if issubclass(x.category, FutureWarning)]
        assert len(future_warnings) == 1
        assert "pptx" in str(future_warnings[0].message)


def test_generate_md_format_no_warning(tmp_path):
    import argparse
    from scripts.batch_pipeline import cmd_generate
    rag_path = _make_rag_results(tmp_path)
    custom_dir = tmp_path / "md_out"
    args = argparse.Namespace(input=str(rag_path), output_dir=str(custom_dir), format="md")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cmd_generate(args)
        future_warnings = [x for x in w if issubclass(x.category, FutureWarning)]
        assert len(future_warnings) == 0
```

- [ ] **Step 2: 运行测试确认通过**

Run: `python3 -m pytest tests/test_generate_enhance.py -k "legacy or md_format_no" -v`
Expected: 4 passed

- [ ] **Step 3: 提交**

```bash
git add tests/test_generate_enhance.py
git commit -m "test: add legacy format deprecation warning tests"
```

---

### Task 6: 删除旧 generate 函数 + 清理模板

**Files:**
- Modify: `scripts/batch_pipeline.py` — 删除 `generate_xlsx()`, `generate_docx()`, `generate_pptx()`
- Delete: `templates/report_template.pptx`

- [ ] **Step 1: 删除 generate_xlsx 函数（约第 1829-1924 行，注意 md_escape/_prepare_patient_rows 已在前面插入，行号已变化）**

定位 `def generate_xlsx(data: dict, output_path: Path):` 到 `print(f"  ✓ 汇总表:` 所在函数末尾，整段删除。

- [ ] **Step 2: 删除 generate_docx 函数**

定位 `def generate_docx(data: dict, output_dir: Path):` 到 `print(f"  ✓ 推荐意见书:` 所在函数末尾，整段删除。

- [ ] **Step 3: 删除 generate_pptx 函数**

定位 `def generate_pptx(data: dict, output_path: Path):` 到其函数末尾（约 280 行），整段删除。

- [ ] **Step 4: 删除 PPTX 模板**

```bash
rm templates/report_template.pptx
```

- [ ] **Step 5: 运行全量测试确认无回归**

Run: `python3 -m pytest tests/ -v`
Expected: 全部通过（旧测试已重写，无引用旧函数）

- [ ] **Step 6: 提交**

```bash
git add scripts/batch_pipeline.py templates/
git commit -m "refactor: remove generate_xlsx, generate_docx, generate_pptx and pptx template"
```

---

### Task 7: 更新文档

**Files:**
- Modify: `AGENTS.md` — 更新 generate 命令描述
- Modify: `SKILL.md` — 更新 generate 相关章节（如存在）

- [ ] **Step 1: 更新 AGENTS.md 中 generate 命令描述**

将命令参考中 generate 相关内容更新为 Markdown 输出：
```
# 4. 合并 + 验证 + 生成
python3 scripts/batch_pipeline.py merge --input-dir Output/batches/ --output Output/rag_results.json
python3 scripts/batch_pipeline.py validate --input Output/rag_results.json --patients Output/patients.json
python3 scripts/batch_pipeline.py generate --input Output/rag_results.json --format md
```

- [ ] **Step 2: 检查 SKILL.md 是否有 generate 相关描述需要更新，如有则更新**

- [ ] **Step 3: 提交**

```bash
git add AGENTS.md SKILL.md
git commit -m "docs: update generate command docs for Markdown output"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** 每条 spec 需求都有对应 Task
- [x] **Placeholder scan:** 无 TBD/TODO/待实现
- [x] **Type consistency:** `_prepare_patient_rows` 返回的 dict key 与 `generate_md` 中消费的 key 一致（`patient_id`, `patient_name`, `primary_site`, `disease_type`, `diagnosis_summary`, `questions[].question`, `questions[].guidelines[].name/version/recommendation/evidence_level/source_file/source_lines`, `questions[].consensus`, `questions[].differences`）
