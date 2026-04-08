import pytest
from scripts.batch_pipeline import generate_batch_prompt, estimate_tokens


def _make_batch_with_retrieval():
    """Helper: create a batch with retrieval_results."""
    return [{
        "patient_id": "P001",
        "patient_name": "Test Patient",
        "disease_type": "gastric cancer",
        "features": {
            "staging_keywords": ["T3"],
            "all_keywords": ["T3", "gastric"],
            "confidence": "high",
        },
        "retrieval_results": [
            {
                "content": "Stage III: perioperative chemo recommended.",
                "path": "NCCN/extracted/NCCN_Gastric_2026.md",
                "score": 0.85,
                "context": "NCCN gastric guidelines 2026 V2",
            },
            {
                "content": "T3N2M0: D2 gastrectomy.",
                "path": "CSCO/extracted/CSCO_Gastric_2026.md",
                "score": 0.72,
                "context": "CSCO gastric guidelines 2026",
            },
        ],
    }]


def _make_kb_profile():
    return {
        "orgs": ["NCCN", "CSCO"],
        "root_index_content": "# KB Root",
    }


def test_prompt_contains_retrieval_results_not_grep():
    """New prompt embeds retrieval results, not grep commands."""
    batch = _make_batch_with_retrieval()
    kb_profile = _make_kb_profile()

    prompt = generate_batch_prompt(batch, kb_profile, "/kb", 1, 1)

    assert "perioperative chemo" in prompt
    assert "D2 gastrectomy" in prompt
    assert "0.85" in prompt
    assert "grep" not in prompt.lower()
    assert "CMD-P" not in prompt
    assert "retrieval_sources" in prompt


def test_prompt_contains_context_reset():
    prompt = generate_batch_prompt(
        batch=_make_batch_with_retrieval(),
        kb_profile=_make_kb_profile(),
        kb_root="/path/to/kb",
        batch_idx=1, total_batches=3,
    )
    assert "<CONTEXT_RESET>" in prompt
    assert "MANDATORY_RULES" in prompt


def test_prompt_contains_all_orgs():
    batch = [{
        "patient_id": "P001", "patient_name": "Test",
        "features": {"all_keywords": ["HER2"], "confidence": "high", "molecular_keywords": ["HER2"]},
        "retrieval_results": [
            {"content": "HER2 positive treatment", "path": "NCCN/extracted/NCCN_Gastric.md", "score": 0.9, "context": "NCCN"},
            {"content": "HER2 targeted therapy", "path": "ESMO/extracted/ESMO_Gastric.md", "score": 0.8, "context": "ESMO"},
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
        "patient_id": "P001", "patient_name": "Zhang San",
        "primary_site": "gastric body",
        "features": {"all_keywords": ["gastric"], "confidence": "high", "diagnosis_keywords": ["gastric"]},
        "retrieval_results": [
            {"content": "result", "path": "NCCN/extracted/x.md", "score": 0.8, "context": "ctx"},
        ],
    }]
    prompt = generate_batch_prompt(
        batch=batch,
        kb_profile={"root_index_content": "# KB", "orgs": ["NCCN"]},
        kb_root="/kb", batch_idx=1, total_batches=1,
    )
    assert "Zhang San" in prompt
    assert "P001" in prompt


def test_estimate_tokens():
    text = "a" * 400
    assert 90 <= estimate_tokens(text) <= 110


def test_prompt_chunk_ids_format():
    """Prompt uses R{patient}-{seq} chunk ID format."""
    batch = _make_batch_with_retrieval()
    prompt = generate_batch_prompt(
        batch=batch,
        kb_profile=_make_kb_profile(),
        kb_root="/kb", batch_idx=1, total_batches=1,
    )
    assert "R001-01" in prompt
    assert "R001-02" in prompt


def test_prompt_citation_coverage_requirement():
    """Prompt includes citation coverage requirement."""
    batch = _make_batch_with_retrieval()
    prompt = generate_batch_prompt(
        batch=batch,
        kb_profile=_make_kb_profile(),
        kb_root="/kb", batch_idx=1, total_batches=1,
    )
    assert "Citation Requirement" in prompt
    assert "50%" in prompt


def test_prompt_contains_full_json_template():
    """Output section contains full JSON template with retrieval_sources."""
    batch = _make_batch_with_retrieval()
    prompt = generate_batch_prompt(
        batch=batch,
        kb_profile=_make_kb_profile(),
        kb_root="/kb", batch_idx=1, total_batches=1,
    )
    assert '"results"' in prompt
    assert '"batch_id"' in prompt
    assert '"consensus"' in prompt
    assert '"differences"' in prompt
    assert '"retrieval_sources"' in prompt
    assert '"citation_coverage"' in prompt
    assert '"patients"' in prompt  # appears in "not patients" instruction


def test_prompt_no_execution_log():
    """New prompt does not contain execution_log or execution_summary."""
    batch = _make_batch_with_retrieval()
    prompt = generate_batch_prompt(
        batch=batch,
        kb_profile=_make_kb_profile(),
        kb_root="/kb", batch_idx=1, total_batches=1,
    )
    assert "execution_log" not in prompt
    assert "execution_summary" not in prompt


def test_prompt_no_retrieval_results_message():
    """Patient with no retrieval results gets appropriate message."""
    batch = [{
        "patient_id": "P001", "patient_name": "Empty",
        "features": {"all_keywords": ["x"], "confidence": "low"},
        "retrieval_results": [],
    }]
    prompt = generate_batch_prompt(
        batch=batch,
        kb_profile=_make_kb_profile(),
        kb_root="/kb", batch_idx=1, total_batches=1,
    )
    assert "No pre-retrieved results" in prompt


def test_prompt_multi_patient_chunk_ids():
    """Multiple patients get distinct chunk ID prefixes."""
    import re
    batch = [
        {
            "patient_id": "P001", "patient_name": "A",
            "features": {"all_keywords": ["x"], "confidence": "high"},
            "retrieval_results": [
                {"content": "result A", "path": "NCCN/extracted/a.md", "score": 0.8, "context": "ctx"},
            ],
        },
        {
            "patient_id": "P002", "patient_name": "B",
            "features": {"all_keywords": ["y"], "confidence": "high"},
            "retrieval_results": [
                {"content": "result B", "path": "NCCN/extracted/b.md", "score": 0.7, "context": "ctx"},
            ],
        },
    ]
    prompt = generate_batch_prompt(
        batch=batch,
        kb_profile=_make_kb_profile(),
        kb_root="/kb", batch_idx=1, total_batches=1,
    )
    assert "R001-01" in prompt
    assert "R002-01" in prompt
    chunk_ids = re.findall(r'\[R\d+-\d+\]', prompt)
    assert len(chunk_ids) == len(set(chunk_ids)), f"Duplicate chunk IDs: {chunk_ids}"
