import json
import pytest
from pathlib import Path
from unittest.mock import patch
from scripts.batch_pipeline import cmd_merge


def _write_batch(tmp_path, filename, data):
    f = tmp_path / filename
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return f


class FakeArgs:
    def __init__(self, input_dir, output):
        self.input_dir = str(input_dir)
        self.output = str(output)


def test_merge_patients_key(tmp_path):
    """merge 兼容 'patients' key → patient_count > 0"""
    _write_batch(tmp_path, "rag_batch_001.json", {
        "batch_id": "batch_001",
        "patients": [{
            "patient_id": "P001",
            "patient_name": "测试",
            "guideline_results": [
                {"guideline": "NCCN", "recommendation": "推荐" * 30,
                 "execution_log": [{"cmd_id": "CMD-P001-NCCN-01", "match_count": 5,
                                    "first_match_snippet": "x" * 30}]},
            ],
            "consensus": ["共识"],
            "differences": ["分歧"],
            "execution_summary": {"total_commands_in_prompt": 1,
                                   "total_commands_executed": 1,
                                   "commands_with_zero_matches": []},
        }],
    })
    output = tmp_path / "merged.json"
    cmd_merge(FakeArgs(tmp_path, output))
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["patient_count"] == 1
    assert result["results"][0]["patient_id"] == "P001"


def test_merge_flat_structure(tmp_path):
    """merge 扁平结构 → clinical_questions 正确嵌套"""
    _write_batch(tmp_path, "rag_batch_001.json", {
        "batch_id": "batch_001",
        "results": [{
            "patient_id": "P001",
            "patient_name": "测试",
            "guideline_results": [
                {"guideline": "NCCN", "recommendation": "test"},
            ],
            "consensus": ["共识"],
            "differences": [],
        }],
    })
    output = tmp_path / "merged.json"
    cmd_merge(FakeArgs(tmp_path, output))
    result = json.loads(output.read_text(encoding="utf-8"))
    patient = result["results"][0]
    assert "clinical_questions" in patient
    # root-level guideline_results 已被清理
    assert "guideline_results" not in patient


def test_merge_empty_batch(tmp_path):
    """merge 空 batch → patient_count = 0"""
    _write_batch(tmp_path, "rag_batch_001.json", {
        "batch_id": "batch_001",
        "results": [],
    })
    output = tmp_path / "merged.json"
    cmd_merge(FakeArgs(tmp_path, output))
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["patient_count"] == 0
    assert result["results"] == []
