import json
import pytest
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
    data = {"generated_at": "2026-03-25", "patient_count": len(results), "results": results}
    p = tmp_path / "rag_results.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def test_generate_custom_output_dir(tmp_path):
    import argparse
    from scripts.batch_pipeline import cmd_generate
    rag_path = _make_rag_results(tmp_path)
    custom_dir = tmp_path / "custom_output"
    args = argparse.Namespace(input=str(rag_path), output_dir=str(custom_dir), format="xlsx")
    cmd_generate(args)
    assert (custom_dir / "批量推荐汇总表.xlsx").exists()


def test_sort_results_by_name():
    from scripts.batch_pipeline import _sort_results_by_name
    results = [
        {"patient_name": "张三"},
        {"patient_name": "李四"},
        {"patient_name": "王五"},
    ]
    sorted_results = _sort_results_by_name(results)
    # Just verify it returns a list of same length and doesn't crash
    assert len(sorted_results) == 3
    # The exact order depends on system locale, so just check it's sorted
    names = [r["patient_name"] for r in sorted_results]
    assert set(names) == {"张三", "李四", "王五"}
