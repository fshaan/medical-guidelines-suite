import pytest
from scripts.batch_pipeline import _extract_patient_list


def test_results_key():
    """'results' key 正常提取"""
    data = {"batch_id": "batch_001", "results": [
        {"patient_id": "P001", "clinical_questions": [{"guideline_results": []}]},
    ]}
    patients = _extract_patient_list(data)
    assert len(patients) == 1
    assert patients[0]["patient_id"] == "P001"


def test_patients_key_fallback():
    """'patients' key 兼容提取"""
    data = {"batch_id": "batch_001", "patients": [
        {"patient_id": "P002", "clinical_questions": [{"guideline_results": []}]},
    ]}
    patients = _extract_patient_list(data)
    assert len(patients) == 1
    assert patients[0]["patient_id"] == "P002"


def test_missing_key_warns(capsys):
    """两个 key 都没有 → 返回空 + stderr 警告"""
    data = {"batch_id": "batch_099", "other_stuff": [1, 2, 3]}
    patients = _extract_patient_list(data)
    assert patients == []
    captured = capsys.readouterr()
    assert "batch_099" in captured.err
    assert "results/patients" in captured.err


def test_flat_structure_auto_wrap():
    """扁平结构（无 clinical_questions）自动包装"""
    data = {"batch_id": "batch_001", "results": [{
        "patient_id": "P001",
        "guideline_results": [
            {"guideline": "NCCN", "recommendation": "test"},
        ],
        "consensus": ["共识1"],
        "differences": ["分歧1"],
    }]}
    patients = _extract_patient_list(data)
    p = patients[0]
    assert "clinical_questions" in p
    assert len(p["clinical_questions"]) == 1
    cq = p["clinical_questions"][0]
    assert cq["guideline_results"][0]["guideline"] == "NCCN"
    assert cq["consensus"] == ["共识1"]
    assert cq["differences"] == ["分歧1"]
    # 原始 root-level 字段已被 pop
    assert "guideline_results" not in p
    assert "consensus" not in p


def test_nested_structure_passthrough():
    """已有 clinical_questions 的嵌套结构直接通过"""
    original_cq = [{"guideline_results": [{"guideline": "ESMO"}]}]
    data = {"batch_id": "batch_001", "results": [{
        "patient_id": "P001",
        "clinical_questions": original_cq,
        "guideline_results": [{"guideline": "NCCN"}],  # root-level 冗余
    }]}
    patients = _extract_patient_list(data)
    p = patients[0]
    # clinical_questions 保持原样
    assert p["clinical_questions"] is original_cq
    # root-level guideline_results 不被 pop（因为 clinical_questions 已存在）
    assert "guideline_results" in p


def test_dedup_removes_repeated_guideline_results():
    """重复的 guideline_results 只保留第一条"""
    rec = {"guideline": "NCCN", "recommendation": "推荐曲妥珠单抗联合化疗"}
    data = {"batch_id": "batch_001", "results": [{
        "patient_id": "P001",
        "guideline_results": [
            {**rec, "source_lines": "10-20"},
            {**rec, "source_lines": "10-20"},
            {**rec, "source_lines": "30-40"},  # same rec, different lines
            {"guideline": "ESMO", "recommendation": "推荐曲妥珠单抗联合化疗"},  # different guideline
            {**rec, "source_lines": "10-20"},
            {**rec, "source_lines": "10-20"},
        ],
        "consensus": ["共识A", "共识A", "共识B", "共识A"],
        "differences": ["差异X", "差异X"],
    }]}
    patients = _extract_patient_list(data)
    p = patients[0]
    cq = p["clinical_questions"][0]
    # NCCN rec kept once + ESMO rec kept once = 2
    assert len(cq["guideline_results"]) == 2
    assert cq["guideline_results"][0]["guideline"] == "NCCN"
    assert cq["guideline_results"][1]["guideline"] == "ESMO"
    # consensus deduped preserving order
    assert cq["consensus"] == ["共识A", "共识B"]
    # differences deduped
    assert cq["differences"] == ["差异X"]


def test_dedup_no_duplicates_unchanged():
    """无重复时 guideline_results 不变"""
    data = {"batch_id": "batch_001", "results": [{
        "patient_id": "P001",
        "guideline_results": [
            {"guideline": "NCCN", "recommendation": "rec1"},
            {"guideline": "ESMO", "recommendation": "rec2"},
        ],
        "consensus": ["共识"],
        "differences": [],
    }]}
    patients = _extract_patient_list(data)
    cq = patients[0]["clinical_questions"][0]
    assert len(cq["guideline_results"]) == 2


def test_dedup_different_guidelines_same_rec_kept():
    """不同指南的相同推荐文本不被误去重"""
    data = {"batch_id": "batch_001", "results": [{
        "patient_id": "P001",
        "guideline_results": [
            {"guideline": "NCCN", "recommendation": "一线推荐曲妥珠单抗"},
            {"guideline": "ESMO", "recommendation": "一线推荐曲妥珠单抗"},
            {"guideline": "CSCO", "recommendation": "一线推荐曲妥珠单抗"},
        ],
    }]}
    patients = _extract_patient_list(data)
    cq = patients[0]["clinical_questions"][0]
    assert len(cq["guideline_results"]) == 3
