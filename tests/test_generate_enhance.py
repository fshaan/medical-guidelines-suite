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
