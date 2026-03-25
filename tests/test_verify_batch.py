import pytest
from scripts.batch_pipeline import _parse_prompt_commands, _verify_snippet


def test_parse_prompt_commands_basic():
    """从标准 prompt 中提取 CMD-ID 和命令"""
    prompt = """
**NCCN（必须）:**
CMD-P001-NCCN-01: grep -n -i 'HER2' /kb/NCCN/extracted/*.txt
  → 记录到 execution_log: {cmd_id, match_count, first_match_snippet (≥30字)}
CMD-P001-NCCN-02: grep -n -i '胃癌' /kb/NCCN/extracted/*.txt
  → 记录到 execution_log: {cmd_id, match_count, first_match_snippet (≥30字)}
**ESMO（必须）:**
CMD-P001-ESMO-01: grep -n -i 'HER2' /kb/ESMO/extracted/*.txt
  → 记录到 execution_log: {cmd_id, match_count, first_match_snippet (≥30字)}
"""
    cmds = _parse_prompt_commands(prompt)
    assert len(cmds) == 3
    assert cmds[0]["cmd_id"] == "CMD-P001-NCCN-01"
    assert "grep" in cmds[0]["command"]
    assert cmds[0]["org"] == "NCCN"
    assert cmds[2]["cmd_id"] == "CMD-P001-ESMO-01"
    assert cmds[2]["org"] == "ESMO"


def test_parse_prompt_commands_multi_patient():
    """多患者 prompt 正确解析"""
    prompt = """
CMD-P001-NCCN-01: grep -n -i 'x' /kb/*.txt
  → 记录
CMD-P002-NCCN-01: grep -n -i 'y' /kb/*.txt
  → 记录
CMD-P002-ESMO-01: grep -n -i 'z' /kb/*.txt
  → 记录
"""
    cmds = _parse_prompt_commands(prompt)
    assert len(cmds) == 3
    patient_indices = [c["patient_index"] for c in cmds]
    assert patient_indices == [1, 2, 2]


def test_parse_prompt_commands_hyphenated_org():
    """org 名含连字符（如 NCCN-Asia）能正确解析"""
    prompt = "CMD-P001-NCCN-Asia-01: grep -n -i 'test' /kb/*.txt\n"
    cmds = _parse_prompt_commands(prompt)
    assert len(cmds) == 1
    assert cmds[0]["org"] == "NCCN-Asia"


def test_verify_snippet_found(mock_kb):
    """snippet 存在于知识库文件中 → True"""
    result = _verify_snippet(
        snippet="NCCN Gastric Cancer Guideline",
        source_file="NCCN_GastricCancer.txt",
        kb_root=str(mock_kb),
    )
    assert result is True


def test_verify_snippet_not_found(mock_kb):
    """snippet 不存在 → False"""
    result = _verify_snippet(
        snippet="This text does not exist anywhere in the knowledge base",
        source_file="NCCN_GastricCancer.txt",
        kb_root=str(mock_kb),
    )
    assert result is False


def test_verify_snippet_whitespace_normalized(mock_kb):
    """空白差异不影响匹配"""
    result = _verify_snippet(
        snippet="NCCN  Gastric   Cancer  Guideline",
        source_file="NCCN_GastricCancer.txt",
        kb_root=str(mock_kb),
    )
    assert result is True


def test_verify_snippet_file_not_found(mock_kb):
    """源文件不存在 → False"""
    result = _verify_snippet(
        snippet="anything",
        source_file="nonexistent.txt",
        kb_root=str(mock_kb),
    )
    assert result is False
