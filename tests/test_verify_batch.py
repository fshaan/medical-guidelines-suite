import pytest
from scripts.batch_pipeline import _parse_prompt_commands, _verify_snippet, _verify_batch_results


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


def _make_prompt_with_cmds(cmd_ids_and_commands):
    """辅助：生成包含指定 CMD-ID 的 prompt 文本"""
    lines = []
    for cmd_id, command in cmd_ids_and_commands:
        lines.append(f"{cmd_id}: {command}")
        lines.append(f"  → 记录到 execution_log")
    return "\n".join(lines)


def _make_batch_json(patient_results):
    """辅助：生成 rag_batch JSON 结构"""
    return {"batch_id": "batch_001", "results": patient_results}


def test_verify_batch_all_pass():
    """全部命令覆盖 → PASS"""
    prompt = _make_prompt_with_cmds([
        ("CMD-P001-NCCN-01", "grep -n -i 'x' /kb/*.txt"),
        ("CMD-P001-ESMO-01", "grep -n -i 'y' /kb/*.txt"),
    ])
    batch_json = _make_batch_json([{
        "patient_id": "P001",
        "execution_summary": {
            "total_commands_in_prompt": 2,
            "total_commands_executed": 2,
            "commands_with_zero_matches": [],
        },
        "clinical_questions": [{
            "guideline_results": [
                {"guideline": "NCCN", "recommendation": "推荐内容足够长" * 5,
                 "execution_log": [
                     {"cmd_id": "CMD-P001-NCCN-01", "match_count": 5, "first_match_snippet": "x" * 30}
                 ]},
                {"guideline": "ESMO", "recommendation": "推荐内容足够长" * 5,
                 "execution_log": [
                     {"cmd_id": "CMD-P001-ESMO-01", "match_count": 3, "first_match_snippet": "y" * 30}
                 ]},
            ]
        }],
    }])
    errors, warnings = _verify_batch_results(prompt, batch_json)
    assert len(errors) == 0


def test_verify_batch_missing_cmd():
    """缺失 CMD-ID → ERROR"""
    prompt = _make_prompt_with_cmds([
        ("CMD-P001-NCCN-01", "grep x"),
        ("CMD-P001-ESMO-01", "grep y"),
    ])
    batch_json = _make_batch_json([{
        "patient_id": "P001",
        "execution_summary": {
            "total_commands_in_prompt": 2,
            "total_commands_executed": 1,
            "commands_with_zero_matches": [],
        },
        "clinical_questions": [{
            "guideline_results": [
                {"guideline": "NCCN", "recommendation": "推荐" * 30,
                 "execution_log": [
                     {"cmd_id": "CMD-P001-NCCN-01", "match_count": 5, "first_match_snippet": "x" * 30}
                 ]},
            ]
        }],
    }])
    errors, warnings = _verify_batch_results(prompt, batch_json)
    assert any("CMD-P001-ESMO-01" in e for e in errors)


def test_verify_batch_count_mismatch():
    """计数不一致 → ERROR"""
    prompt = _make_prompt_with_cmds([
        ("CMD-P001-NCCN-01", "grep x"),
        ("CMD-P001-NCCN-02", "grep y"),
        ("CMD-P001-ESMO-01", "grep z"),
    ])
    batch_json = _make_batch_json([{
        "patient_id": "P001",
        "execution_summary": {
            "total_commands_in_prompt": 2,  # Wrong! Should be 3
            "total_commands_executed": 3,
            "commands_with_zero_matches": [],
        },
        "clinical_questions": [{
            "guideline_results": [
                {"guideline": "NCCN", "recommendation": "推荐" * 30,
                 "execution_log": [
                     {"cmd_id": "CMD-P001-NCCN-01", "match_count": 5, "first_match_snippet": "x" * 30},
                     {"cmd_id": "CMD-P001-NCCN-02", "match_count": 3, "first_match_snippet": "y" * 30},
                 ]},
                {"guideline": "ESMO", "recommendation": "推荐" * 30,
                 "execution_log": [
                     {"cmd_id": "CMD-P001-ESMO-01", "match_count": 2, "first_match_snippet": "z" * 30},
                 ]},
            ]
        }],
    }])
    errors, warnings = _verify_batch_results(prompt, batch_json)
    assert any("total_commands_in_prompt" in e for e in errors)


def test_verify_batch_zero_match_warn():
    """match_count=0 但有推荐内容 → WARNING"""
    prompt = _make_prompt_with_cmds([
        ("CMD-P001-NCCN-01", "grep x"),
    ])
    batch_json = _make_batch_json([{
        "patient_id": "P001",
        "execution_summary": {
            "total_commands_in_prompt": 1,
            "total_commands_executed": 1,
            "commands_with_zero_matches": ["CMD-P001-NCCN-01"],
        },
        "clinical_questions": [{
            "guideline_results": [
                {"guideline": "NCCN", "recommendation": "这是一段足够长的推荐内容用来测试空匹配矛盾检测功能是否正常工作，此处需要超过五十个字符才能触发警告逻辑",
                 "execution_log": [
                     {"cmd_id": "CMD-P001-NCCN-01", "match_count": 0, "first_match_snippet": ""}
                 ]},
            ]
        }],
    }])
    errors, warnings = _verify_batch_results(prompt, batch_json)
    assert any("match_count=0" in w for w in warnings)
