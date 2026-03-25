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
