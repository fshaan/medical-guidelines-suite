import pytest
from scripts.batch_pipeline import generate_batch_prompt, estimate_tokens

def test_prompt_contains_context_reset():
    prompt = generate_batch_prompt(
        batch=[{"patient_id": "P001", "patient_name": "测试", "features": {
            "all_keywords": ["胃癌"], "confidence": "high",
            "diagnosis_keywords": ["胃癌"],
        }, "grep_commands": [{"org": "NCCN", "dimension": "diagnosis", "command": "grep -n -i 胃癌 /path/*.txt"}]}],
        kb_profile={"root_index_content": "# 测试知识库", "orgs": ["NCCN"]},
        kb_root="/path/to/kb",
        batch_idx=1, total_batches=3,
    )
    assert "<CONTEXT_RESET>" in prompt
    assert "MANDATORY_RULES" in prompt

def test_prompt_contains_all_orgs():
    batch = [{
        "patient_id": "P001", "patient_name": "测试",
        "features": {"all_keywords": ["HER2"], "confidence": "high", "molecular_keywords": ["HER2"]},
        "grep_commands": [
            {"org": "NCCN", "dimension": "molecular", "command": "grep -n -i HER2 /nccn/*.txt"},
            {"org": "ESMO", "dimension": "molecular", "command": "grep -n -i HER2 /esmo/*.txt"},
        ],
    }]
    prompt = generate_batch_prompt(
        batch=batch,
        kb_profile={"root_index_content": "# KB", "orgs": ["NCCN", "ESMO"]},
        kb_root="/kb", batch_idx=1, total_batches=1,
    )
    assert "NCCN" in prompt
    assert "ESMO" in prompt

def test_prompt_contains_patient_info():
    batch = [{
        "patient_id": "P001", "patient_name": "张三",
        "primary_site": "胃体",
        "features": {"all_keywords": ["胃癌"], "confidence": "high", "diagnosis_keywords": ["胃癌"]},
        "grep_commands": [{"org": "NCCN", "dimension": "d", "command": "grep x"}],
    }]
    prompt = generate_batch_prompt(
        batch=batch,
        kb_profile={"root_index_content": "# KB", "orgs": ["NCCN"]},
        kb_root="/kb", batch_idx=1, total_batches=1,
    )
    assert "张三" in prompt
    assert "P001" in prompt

def test_estimate_tokens():
    text = "a" * 400
    assert 90 <= estimate_tokens(text) <= 110

def test_prompt_low_confidence_hint():
    batch = [{
        "patient_id": "P001", "patient_name": "稀疏",
        "features": {"all_keywords": ["胃"], "confidence": "low", "diagnosis_keywords": ["胃"]},
        "grep_commands": [{"org": "NCCN", "dimension": "d", "command": "grep x"}],
    }]
    prompt = generate_batch_prompt(
        batch=batch,
        kb_profile={"root_index_content": "# KB", "orgs": ["NCCN"]},
        kb_root="/kb", batch_idx=1, total_batches=1,
    )
    assert "稀疏" in prompt or "补充" in prompt or "low" in prompt.lower()


def test_prompt_contains_cmd_ids():
    """prompt 中 grep 命令使用 CMD-P{n}-{org}-{seq} 格式"""
    batch = [{
        "patient_id": "P001", "patient_name": "测试",
        "features": {"all_keywords": ["胃癌", "HER2"], "confidence": "high",
                     "diagnosis_keywords": ["胃癌"], "molecular_keywords": ["HER2"]},
        "grep_commands": [
            {"org": "NCCN", "dimension": "diagnosis", "command": "grep -n -i '胃癌' /kb/NCCN/extracted/*.txt"},
            {"org": "NCCN", "dimension": "molecular", "command": "grep -n -i 'HER2' /kb/NCCN/extracted/*.txt"},
            {"org": "ESMO", "dimension": "diagnosis", "command": "grep -n -i '胃癌' /kb/ESMO/extracted/*.txt"},
        ],
    }]
    prompt = generate_batch_prompt(
        batch=batch,
        kb_profile={"root_index_content": "# KB", "orgs": ["NCCN", "ESMO"]},
        kb_root="/kb", batch_idx=1, total_batches=1,
    )
    assert "CMD-P001-NCCN-01:" in prompt
    assert "CMD-P001-NCCN-02:" in prompt
    assert "CMD-P001-ESMO-01:" in prompt


def test_cmd_id_uniqueness():
    """同一 prompt 内 CMD-ID 无重复"""
    import re
    batch = [
        {"patient_id": "P001", "patient_name": "甲",
         "features": {"all_keywords": ["x"], "confidence": "high", "diagnosis_keywords": ["x"]},
         "grep_commands": [
             {"org": "NCCN", "dimension": "d", "command": "grep x"},
             {"org": "ESMO", "dimension": "d", "command": "grep y"},
         ]},
        {"patient_id": "P002", "patient_name": "乙",
         "features": {"all_keywords": ["y"], "confidence": "high", "diagnosis_keywords": ["y"]},
         "grep_commands": [
             {"org": "NCCN", "dimension": "d", "command": "grep z"},
         ]},
    ]
    prompt = generate_batch_prompt(
        batch=batch,
        kb_profile={"root_index_content": "# KB", "orgs": ["NCCN", "ESMO"]},
        kb_root="/kb", batch_idx=1, total_batches=1,
    )
    cmd_ids = re.findall(r'(CMD-P\d+-[\w-]+-\d+):', prompt)
    assert len(cmd_ids) == len(set(cmd_ids)), f"重复 CMD-ID: {cmd_ids}"


def test_prompt_contains_checkpoint():
    """每位患者的 grep 命令列表后有检查点"""
    batch = [{
        "patient_id": "P001", "patient_name": "张三",
        "features": {"all_keywords": ["x"], "confidence": "high", "diagnosis_keywords": ["x"]},
        "grep_commands": [
            {"org": "NCCN", "dimension": "d", "command": "grep x /path/*.txt"},
        ],
    }]
    prompt = generate_batch_prompt(
        batch=batch,
        kb_profile={"root_index_content": "# KB", "orgs": ["NCCN"]},
        kb_root="/kb", batch_idx=1, total_batches=1,
    )
    assert "检查点" in prompt
    assert "execution_summary" in prompt
    assert "total_commands_in_prompt" in prompt


def test_prompt_supplemental_after_mandatory():
    """补充检索要求在必须命令之后"""
    batch = [{
        "patient_id": "P001", "patient_name": "测试",
        "features": {"all_keywords": ["x"], "confidence": "high", "diagnosis_keywords": ["x"]},
        "grep_commands": [{"org": "NCCN", "dimension": "d", "command": "grep x"}],
    }]
    prompt = generate_batch_prompt(
        batch=batch,
        kb_profile={"root_index_content": "# KB", "orgs": ["NCCN"]},
        kb_root="/kb", batch_idx=1, total_batches=1,
    )
    assert "CMD-" in prompt
    assert "execution_log" in prompt
    checkpoint_pos = prompt.index("检查点")
    supplement_pos = prompt.index("补充检索")
    assert supplement_pos > checkpoint_pos


def test_prompt_contains_execution_log_schema():
    """输出要求部分包含 execution_log JSON 示例"""
    batch = [{
        "patient_id": "P001", "patient_name": "测试",
        "features": {"all_keywords": ["x"], "confidence": "high", "diagnosis_keywords": ["x"]},
        "grep_commands": [{"org": "NCCN", "dimension": "d", "command": "grep x"}],
    }]
    prompt = generate_batch_prompt(
        batch=batch,
        kb_profile={"root_index_content": "# KB", "orgs": ["NCCN"]},
        kb_root="/kb", batch_idx=1, total_batches=1,
    )
    assert "execution_log" in prompt
    assert "cmd_id" in prompt
    assert "match_count" in prompt
    assert "first_match_snippet" in prompt
    assert "execution_summary" in prompt
