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


def test_merge_backfills_metadata_from_patients(tmp_path):
    """merge --patients 回注缺失的患者元数据"""
    patients_data = {
        "patient_count": 1,
        "patients": [{
            "patient_id": "P001",
            "patient_name": "张三",
            "primary_site": "胃体",
            "disease_type": "胃腺癌",
            "diagnosis_summary": "cT4aN1M0 HER2+ 局部晚期",
        }],
    }
    patients_file = tmp_path / "patients.json"
    patients_file.write_text(json.dumps(patients_data, ensure_ascii=False), encoding="utf-8")

    _write_batch(tmp_path, "rag_batch_001.json", {
        "batch_id": "batch_001",
        "results": [{
            "patient_id": "P001",
            "clinical_questions": [{
                "guideline_results": [
                    {"guideline": "NCCN", "recommendation": "推荐"},
                ],
                "consensus": [],
                "differences": [],
            }],
        }],
    })

    output = tmp_path / "merged.json"

    class Args(FakeArgs):
        def __init__(self, input_dir, output, patients=None):
            super().__init__(input_dir, output)
            self.patients = patients

    cmd_merge(Args(tmp_path, output, str(patients_file)))
    result = json.loads(output.read_text(encoding="utf-8"))
    p = result["results"][0]
    assert p["patient_name"] == "张三"
    assert p["primary_site"] == "胃体"
    assert p["disease_type"] == "胃腺癌"
    assert p["diagnosis_summary"] == "cT4aN1M0 HER2+ 局部晚期"


def test_merge_no_patients_flag_unchanged(tmp_path):
    """merge 不带 --patients 时行为不变"""
    _write_batch(tmp_path, "rag_batch_001.json", {
        "batch_id": "batch_001",
        "results": [{
            "patient_id": "P001",
            "clinical_questions": [{
                "guideline_results": [{"guideline": "NCCN", "recommendation": "推荐"}],
                "consensus": [],
                "differences": [],
            }],
        }],
    })
    output = tmp_path / "merged.json"

    class Args(FakeArgs):
        def __init__(self, input_dir, output):
            super().__init__(input_dir, output)
            self.patients = None

    cmd_merge(Args(tmp_path, output))
    result = json.loads(output.read_text(encoding="utf-8"))
    p = result["results"][0]
    assert "patient_name" not in p
    assert "primary_site" not in p


def test_merge_llm_fields_not_overwritten(tmp_path):
    """LLM 已输出的字段不被 patients.json 覆盖"""
    patients_data = {
        "patient_count": 1,
        "patients": [{
            "patient_id": "P001",
            "patient_name": "张三",
            "primary_site": "胃体",
            "disease_type": "胃腺癌",
            "diagnosis_summary": "原始摘要",
        }],
    }
    patients_file = tmp_path / "patients.json"
    patients_file.write_text(json.dumps(patients_data, ensure_ascii=False), encoding="utf-8")

    _write_batch(tmp_path, "rag_batch_001.json", {
        "batch_id": "batch_001",
        "results": [{
            "patient_id": "P001",
            "patient_name": "LLM输出的名字",
            "diagnosis_summary": "LLM生成的摘要",
            "clinical_questions": [{
                "guideline_results": [{"guideline": "NCCN", "recommendation": "推荐"}],
                "consensus": [],
                "differences": [],
            }],
        }],
    })

    output = tmp_path / "merged.json"

    class Args(FakeArgs):
        def __init__(self, input_dir, output, patients=None):
            super().__init__(input_dir, output)
            self.patients = patients

    cmd_merge(Args(tmp_path, output, str(patients_file)))
    result = json.loads(output.read_text(encoding="utf-8"))
    p = result["results"][0]
    assert p["patient_name"] == "LLM输出的名字"  # NOT overwritten
    assert p["diagnosis_summary"] == "LLM生成的摘要"  # NOT overwritten
    assert p["primary_site"] == "胃体"  # backfilled (was missing)
    assert p["disease_type"] == "胃腺癌"  # backfilled (was missing)


def test_merge_warns_on_missing_patient_id(tmp_path, capsys):
    """batch 结果中 patient_id 不在 patients.json 中 → 警告但不中断"""
    patients_data = {
        "patient_count": 1,
        "patients": [{"patient_id": "P001", "patient_name": "张三"}],
    }
    patients_file = tmp_path / "patients.json"
    patients_file.write_text(json.dumps(patients_data, ensure_ascii=False), encoding="utf-8")

    _write_batch(tmp_path, "rag_batch_001.json", {
        "batch_id": "batch_001",
        "results": [{
            "patient_id": "P999",
            "clinical_questions": [{
                "guideline_results": [{"guideline": "NCCN", "recommendation": "推荐"}],
                "consensus": [],
                "differences": [],
            }],
        }],
    })
    output = tmp_path / "merged.json"

    class Args(FakeArgs):
        def __init__(self, input_dir, output, patients=None):
            super().__init__(input_dir, output)
            self.patients = patients

    cmd_merge(Args(tmp_path, output, str(patients_file)))
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["patient_count"] == 1
    captured = capsys.readouterr()
    assert "P999" in captured.err
