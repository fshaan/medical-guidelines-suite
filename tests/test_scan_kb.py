import pytest
from scripts.batch_pipeline import scan_knowledge_base

def test_scan_normal(mock_kb):
    """正常解析包含 data_structure.md 的知识库"""
    profile = scan_knowledge_base(mock_kb)
    assert set(profile["orgs"]) == {"NCCN", "ESMO", "CSCO"}
    for org in profile["orgs"]:
        assert org in profile["org_files"]
        assert len(profile["org_files"][org]) > 0
    assert "知识库总览" in profile["root_index_content"]

def test_scan_fallback_no_root_ds(mock_kb):
    """根 data_structure.md 格式异常时 fallback 到目录枚举"""
    (mock_kb / "data_structure.md").write_text("这不是有效的markdown表格")
    profile = scan_knowledge_base(mock_kb)
    assert len(profile["orgs"]) == 3

def test_scan_org_no_extracted(mock_kb):
    """某 org 无 extracted/ 子目录时跳过该 org"""
    import shutil
    shutil.rmtree(mock_kb / "ESMO" / "extracted")
    profile = scan_knowledge_base(mock_kb)
    assert "ESMO" not in profile["orgs"]

def test_scan_empty_kb(tmp_path):
    """空知识库"""
    (tmp_path / "data_structure.md").write_text("# Empty")
    profile = scan_knowledge_base(tmp_path)
    assert profile["orgs"] == []

def test_scan_org_no_ds(mock_kb):
    """某 org 无 data_structure.md 时仍可通过 extracted/ 发现"""
    import os
    os.remove(mock_kb / "NCCN" / "data_structure.md")
    profile = scan_knowledge_base(mock_kb)
    assert "NCCN" in profile["orgs"]

def test_scan_clinical_question_map(mock_kb):
    """解析临床问题→指南映射表"""
    profile = scan_knowledge_base(mock_kb)
    if profile.get("clinical_question_map"):
        assert isinstance(profile["clinical_question_map"], dict)
