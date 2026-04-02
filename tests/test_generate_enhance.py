import json
import pytest
import warnings
from pathlib import Path


def _make_rag_results(tmp_path, names=None):
    names = names or [("P001", "张三"), ("P002", "李四"), ("P003", "王五")]
    results = []
    for pid, name in names:
        results.append(
            {
                "patient_id": pid,
                "patient_name": name,
                "primary_site": "胃体",
                "disease_type": "胃癌",
                "diagnosis_summary": "测试诊断",
                "clinical_questions": [
                    {
                        "question": "测试问题",
                        "guideline_results": [
                            {
                                "guideline": "NCCN",
                                "version": "2026",
                                "recommendation": "测试推荐",
                                "evidence_level": "Category 1",
                                "source_file": "test.txt",
                                "source_lines": "1-10",
                            }
                        ],
                        "consensus": ["共识1"],
                        "differences": ["差异1"],
                    }
                ],
            }
        )
    data = {
        "generated_at": "2026-04-02",
        "patient_count": len(results),
        "results": results,
    }
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


def test_prepare_patient_rows_basic():
    from scripts.batch_pipeline import _prepare_patient_rows

    data = {
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "张三",
                "primary_site": "胃体",
                "disease_type": "胃癌",
                "diagnosis_summary": "摘要",
                "clinical_questions": [
                    {
                        "question": "治疗方案",
                        "guideline_results": [
                            {
                                "guideline": "NCCN",
                                "version": "2026",
                                "recommendation": "手术",
                                "evidence_level": "Category 1",
                                "source_file": "test.txt",
                                "source_lines": "1-10",
                            },
                            {
                                "guideline": "ESMO",
                                "version": "2026",
                                "recommendation": "化疗",
                                "evidence_level": "Level I",
                                "source_file": "test2.txt",
                                "source_lines": "20-30",
                            },
                        ],
                        "consensus": ["手术为主"],
                        "differences": ["NCCN 独有: 术后辅助"],
                    }
                ],
            }
        ],
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

    data = {
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "张三",
                "primary_site": "胃体",
                "disease_type": "胃癌",
                "diagnosis_summary": "摘要",
                "clinical_questions": [],
            }
        ]
    }
    rows = _prepare_patient_rows(data)
    assert len(rows) == 1
    assert rows[0]["questions"] == []


def test_prepare_patient_rows_no_guidelines():
    from scripts.batch_pipeline import _prepare_patient_rows

    data = {
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "张三",
                "primary_site": "胃体",
                "disease_type": "胃癌",
                "diagnosis_summary": "摘要",
                "clinical_questions": [
                    {
                        "question": "Q1",
                        "guideline_results": [],
                        "consensus": [],
                        "differences": [],
                    }
                ],
            }
        ]
    }
    rows = _prepare_patient_rows(data)
    assert len(rows) == 1
    assert rows[0]["questions"][0]["guidelines"] == []


def test_generate_md_happy_path(tmp_path):
    from scripts.batch_pipeline import generate_md

    data = {
        "generated_at": "2026-04-02",
        "patient_count": 1,
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "张三",
                "primary_site": "胃体",
                "disease_type": "胃癌",
                "diagnosis_summary": "测试诊断",
                "clinical_questions": [
                    {
                        "question": "测试问题",
                        "guideline_results": [
                            {
                                "guideline": "NCCN",
                                "version": "2026",
                                "recommendation": "测试推荐",
                                "evidence_level": "Category 1",
                                "source_file": "test.txt",
                                "source_lines": "1-10",
                            }
                        ],
                        "consensus": ["共识1"],
                        "differences": ["差异1"],
                    }
                ],
            }
        ],
    }
    output_path = tmp_path / "report.md"
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
        "generated_at": "2026-04-02",
        "patient_count": 1,
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "张三",
                "primary_site": "胃体",
                "disease_type": "胃癌",
                "diagnosis_summary": "摘要",
                "clinical_questions": [
                    {
                        "question": "治疗方案",
                        "guideline_results": [
                            {
                                "guideline": "NCCN",
                                "version": "2026",
                                "recommendation": "推荐手术切除",
                                "evidence_level": "Category 1",
                                "source_file": "a.txt",
                                "source_lines": "1-10",
                            },
                            {
                                "guideline": "ESMO",
                                "version": "2026",
                                "recommendation": "推荐围手术期化疗",
                                "evidence_level": "Level I",
                                "source_file": "b.txt",
                                "source_lines": "20-30",
                            },
                        ],
                        "consensus": ["手术为主"],
                        "differences": ["NCCN 独有: D2清扫"],
                    }
                ],
            }
        ],
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
        "generated_at": "2026-04-02",
        "patient_count": 2,
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "张三",
                "primary_site": "胃体",
                "disease_type": "胃癌",
                "diagnosis_summary": "摘要",
                "clinical_questions": [],
            },
            {
                "patient_id": "P002",
                "patient_name": "李四",
                "primary_site": "结肠",
                "disease_type": "结肠癌",
                "diagnosis_summary": "摘要",
                "clinical_questions": [],
            },
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
        "generated_at": "2026-04-02",
        "patient_count": 1,
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "张三",
                "primary_site": "胃体",
                "disease_type": "胃癌",
                "diagnosis_summary": "摘要",
                "clinical_questions": [
                    {
                        "question": "Q1",
                        "guideline_results": [],
                        "consensus": [],
                        "differences": [],
                    }
                ],
            }
        ],
    }
    output_path = tmp_path / "report.md"
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    assert "Q1" in content


def test_generate_md_consensus_differences(tmp_path):
    from scripts.batch_pipeline import generate_md

    data = {
        "generated_at": "2026-04-02",
        "patient_count": 1,
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "张三",
                "primary_site": "胃体",
                "disease_type": "胃癌",
                "diagnosis_summary": "摘要",
                "clinical_questions": [
                    {
                        "question": "Q1",
                        "guideline_results": [
                            {
                                "guideline": "NCCN",
                                "version": "2026",
                                "recommendation": "R",
                                "evidence_level": "C1",
                                "source_file": "a.txt",
                                "source_lines": "1",
                            }
                        ],
                        "consensus": ["共识A", "共识B"],
                        "differences": ["差异X"],
                    }
                ],
            }
        ],
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
        "generated_at": "2026-04-02",
        "patient_count": 1,
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "张三",
                "primary_site": "胃体",
                "disease_type": "胃癌",
                "diagnosis_summary": "摘要",
                "clinical_questions": [
                    {
                        "question": "Q1",
                        "guideline_results": [
                            {
                                "guideline": "NCCN",
                                "version": "2026",
                                "recommendation": "R",
                                "evidence_level": "Category 1",
                                "source_file": "a.txt",
                                "source_lines": "1",
                            },
                        ],
                        "consensus": [],
                        "differences": [],
                    }
                ],
            }
        ],
    }
    output_path = tmp_path / "report.md"
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    assert "#### 证据等级对照" in content


def test_generate_md_evidence_appendix(tmp_path):
    from scripts.batch_pipeline import generate_md

    data = {
        "generated_at": "2026-04-02",
        "patient_count": 1,
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "张三",
                "primary_site": "胃体",
                "disease_type": "胃癌",
                "diagnosis_summary": "摘要",
                "clinical_questions": [
                    {
                        "question": "Q1",
                        "guideline_results": [
                            {
                                "guideline": "NCCN",
                                "version": "2026",
                                "recommendation": "R",
                                "evidence_level": "Category 1",
                                "source_file": "a.txt",
                                "source_lines": "1",
                            },
                            {
                                "guideline": "ESMO",
                                "version": "2026",
                                "recommendation": "R2",
                                "evidence_level": "Level I",
                                "source_file": "b.txt",
                                "source_lines": "2",
                            },
                        ],
                        "consensus": [],
                        "differences": [],
                    }
                ],
            }
        ],
    }
    output_path = tmp_path / "report.md"
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    assert "## 附录：证据等级参考" in content
    appendix_section = content.split("## 附录：证据等级参考")[1]
    assert "NCCN" in appendix_section
    assert "ESMO" in appendix_section


def test_generate_md_sort_by_name(tmp_path):
    from scripts.batch_pipeline import generate_md

    data = {
        "generated_at": "2026-04-02",
        "patient_count": 3,
        "results": [
            {
                "patient_id": "P002",
                "patient_name": "李四",
                "primary_site": "胃体",
                "disease_type": "胃癌",
                "diagnosis_summary": "摘要",
                "clinical_questions": [],
            },
            {
                "patient_id": "P003",
                "patient_name": "王五",
                "primary_site": "胃体",
                "disease_type": "胃癌",
                "diagnosis_summary": "摘要",
                "clinical_questions": [],
            },
            {
                "patient_id": "P001",
                "patient_name": "张三",
                "primary_site": "胃体",
                "disease_type": "胃癌",
                "diagnosis_summary": "摘要",
                "clinical_questions": [],
            },
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
        "generated_at": "2026-04-02",
        "patient_count": 1,
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "张三",
                "primary_site": "胃*体",
                "disease_type": "胃|癌",
                "diagnosis_summary": "摘要[1]",
                "clinical_questions": [
                    {
                        "question": "Q1",
                        "guideline_results": [
                            {
                                "guideline": "NCCN",
                                "version": "2026",
                                "recommendation": "用*药|方案[2]",
                                "evidence_level": "C1",
                                "source_file": "a.txt",
                                "source_lines": "1",
                            }
                        ],
                        "consensus": [],
                        "differences": [],
                    }
                ],
            }
        ],
    }
    output_path = tmp_path / "report.md"
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    assert "胃\\|癌" in content, "pipe in disease_type should be escaped"
    assert "胃|癌" not in content.replace("\\|", ""), "unescaped pipe in disease_type"
    assert "用\\*药\\|方案\\[2\\]" in content, (
        "special chars in recommendation should be escaped"
    )


def test_generate_md_custom_output_dir(tmp_path):
    import argparse
    from scripts.batch_pipeline import cmd_generate

    rag_path = _make_rag_results(tmp_path)
    custom_dir = tmp_path / "custom_output"
    args = argparse.Namespace(
        input=str(rag_path), output_dir=str(custom_dir), format="md"
    )
    cmd_generate(args)
    md_files = list(custom_dir.glob("*.md"))
    assert len(md_files) == 1


def test_generate_md_single_patient(tmp_path):
    from scripts.batch_pipeline import generate_md

    data = {
        "generated_at": "2026-04-02",
        "patient_count": 1,
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "独一",
                "primary_site": "胃体",
                "disease_type": "胃癌",
                "diagnosis_summary": "摘要",
                "clinical_questions": [
                    {
                        "question": "Q1",
                        "guideline_results": [
                            {
                                "guideline": "NCCN",
                                "version": "2026",
                                "recommendation": "R",
                                "evidence_level": "C1",
                                "source_file": "a.txt",
                                "source_lines": "1",
                            }
                        ],
                        "consensus": [],
                        "differences": [],
                    }
                ],
            }
        ],
    }
    output_path = tmp_path / "report.md"
    generate_md(data, output_path)
    content = output_path.read_text(encoding="utf-8")
    assert "独一" in content
    assert "患者数: 1" in content


def test_generate_legacy_format_xlsx_warning(tmp_path):
    import argparse
    from scripts.batch_pipeline import cmd_generate

    rag_path = _make_rag_results(tmp_path)
    custom_dir = tmp_path / "legacy_out"
    args = argparse.Namespace(
        input=str(rag_path), output_dir=str(custom_dir), format="xlsx"
    )
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
    args = argparse.Namespace(
        input=str(rag_path), output_dir=str(custom_dir), format="docx"
    )
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
    args = argparse.Namespace(
        input=str(rag_path), output_dir=str(custom_dir), format="pptx"
    )
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
    args = argparse.Namespace(
        input=str(rag_path), output_dir=str(custom_dir), format="md"
    )
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cmd_generate(args)
        future_warnings = [x for x in w if issubclass(x.category, FutureWarning)]
        assert len(future_warnings) == 0
