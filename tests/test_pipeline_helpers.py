"""Tests for pipeline.py helper functions (Task 1: 16 cases).

All tests are synchronous — helper functions are pure Python, no asyncio needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

import pytest


# ---------------------------------------------------------------------------
# T1: compute_citation_coverage — 2 guideline_results × 2 sources, [1] [2] → 0.5
# ---------------------------------------------------------------------------
def test_t1_citation_coverage_partial():
    from scripts.pipeline import compute_citation_coverage

    result = {
        "guideline_results": [
            {
                "recommendation": "推荐 FOLFOX 方案 [1]，也可考虑 CAPOX [2]",
                "retrieval_sources": [
                    {"source_file": "a.md", "score": 0.9},
                    {"source_file": "b.md", "score": 0.8},
                ],
            },
            {
                "recommendation": "二线推荐贝伐珠单抗",
                "retrieval_sources": [
                    {"source_file": "c.md", "score": 0.7},
                    {"source_file": "d.md", "score": 0.6},
                ],
            },
        ]
    }
    assert compute_citation_coverage(result) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# T2: total_sources=0 → 1.0
# ---------------------------------------------------------------------------
def test_t2_citation_coverage_no_sources():
    from scripts.pipeline import compute_citation_coverage

    result = {
        "guideline_results": [
            {"recommendation": "some text", "retrieval_sources": []},
            {"recommendation": "more text", "retrieval_sources": []},
        ]
    }
    assert compute_citation_coverage(result) == 1.0


# ---------------------------------------------------------------------------
# T3: recommendation has no [n] but total_sources=4 → 0.0
# ---------------------------------------------------------------------------
def test_t3_citation_coverage_no_citations():
    from scripts.pipeline import compute_citation_coverage

    result = {
        "guideline_results": [
            {
                "recommendation": "推荐方案未引用任何编号",
                "retrieval_sources": [
                    {"source_file": "a.md", "score": 0.9},
                    {"source_file": "b.md", "score": 0.8},
                ],
            },
            {
                "recommendation": "another recommendation",
                "retrieval_sources": [
                    {"source_file": "c.md", "score": 0.7},
                    {"source_file": "d.md", "score": 0.6},
                ],
            },
        ]
    }
    assert compute_citation_coverage(result) == 0.0


# ---------------------------------------------------------------------------
# T4: result missing guideline_results key → 0.0
# ---------------------------------------------------------------------------
def test_t4_citation_coverage_missing_key():
    from scripts.pipeline import compute_citation_coverage

    assert compute_citation_coverage({}) == 0.0


# ---------------------------------------------------------------------------
# T5: build_patient_prompt — patient + 2 hits → length 2, system role, user content
# ---------------------------------------------------------------------------
def test_t5_build_patient_prompt_with_hits():
    from scripts.pipeline import build_patient_prompt

    patient = {
        "patient_id": "p001",
        "patient_name": "贾常山",
        "disease_type": "结直肠癌",
    }
    hits = [
        {"path": "qmd://nccn/x.md", "score": 0.9, "content": "FOLFOX 方案推荐"},
        {"path": "qmd://esmo/y.md", "score": 0.8, "content": "CAPOX 方案推荐"},
    ]
    messages = build_patient_prompt(patient, hits)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    user_content = messages[1]["content"]
    assert "[1]" in user_content
    assert "[2]" in user_content
    assert "贾常山" in user_content


# ---------------------------------------------------------------------------
# T6: build_patient_prompt — empty retrieval_hits → no [n] numbering lines
# ---------------------------------------------------------------------------
def test_t6_build_patient_prompt_empty_hits():
    from scripts.pipeline import build_patient_prompt

    patient = {"patient_id": "p002", "patient_name": "李学"}
    messages = build_patient_prompt(patient, [])
    assert len(messages) == 2
    user_content = messages[1]["content"]
    assert "## 检索结果" in user_content
    # No numbering lines with [n] pattern for hit entries
    lines_with_bracket_num = [
        line for line in user_content.split("\n")
        if line.strip().startswith("[") and "]" in line[:6]
    ]
    assert len(lines_with_bracket_num) == 0


# ---------------------------------------------------------------------------
# T7: _atomic_write_json — write to tmp_path, verify content, no .tmp residue
# ---------------------------------------------------------------------------
def test_t7_atomic_write_json(tmp_path):
    from scripts.pipeline import _atomic_write_json

    target = tmp_path / "test.json"
    data = {"key": "value", "num": 42}
    _atomic_write_json(target, data)
    assert target.exists()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == data
    # No .tmp residue
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0


# ---------------------------------------------------------------------------
# T8: _atomic_write_json — parent dir missing → FileNotFoundError
# ---------------------------------------------------------------------------
def test_t8_atomic_write_json_missing_dir(tmp_path):
    from scripts.pipeline import _atomic_write_json

    target = tmp_path / "nonexistent" / "dir" / "test.json"
    with pytest.raises(FileNotFoundError):
        _atomic_write_json(target, {"k": "v"})


# ---------------------------------------------------------------------------
# T9: _write_failed — writes _failed/<pid>.json with 5 fields
# ---------------------------------------------------------------------------
def test_t9_write_failed(tmp_path):
    from scripts.pipeline import _write_failed

    _write_failed(tmp_path, "p005", "Connection reset", "transport", None)
    failed_file = tmp_path / "_failed" / "p005.json"
    assert failed_file.exists()
    shard = json.loads(failed_file.read_text(encoding="utf-8"))
    assert shard["patient_id"] == "p005"
    assert shard["error"] == "Connection reset"
    assert shard["stage"] == "transport"
    assert shard["last_llm_output"] is None
    assert "attempted_at" in shard
    # Verify ISO format with Z suffix
    assert shard["attempted_at"].endswith("Z")


# ---------------------------------------------------------------------------
# T10: _merge_rag_results — 2 ok + 1 partial + 1 failed → summary counts
# ---------------------------------------------------------------------------
def test_t10_merge_rag_results(tmp_path):
    from scripts.pipeline import _merge_rag_results, _atomic_write_json

    patients_dir = tmp_path / "patients"
    failed_dir = tmp_path / "_failed"
    patients_dir.mkdir()
    failed_dir.mkdir()

    # 2 ok
    for pid in ("p001", "p002"):
        _atomic_write_json(patients_dir / f"{pid}.json", {
            "patient_id": pid, "status": "ok",
            "citation_coverage": 0.7, "result": {},
        })
    # 1 partial
    _atomic_write_json(patients_dir / "p003.json", {
        "patient_id": "p003", "status": "partial",
        "citation_coverage": 0.3, "result": {},
    })
    # 1 failed
    _atomic_write_json(failed_dir / "p005.json", {
        "patient_id": "p005", "error": "timeout", "stage": "transport",
    })

    merged = _merge_rag_results(tmp_path, 120.0)
    assert merged["summary"]["total"] == 4
    assert merged["summary"]["ok"] == 2
    assert merged["summary"]["partial"] == 1
    assert merged["summary"]["failed"] == 1
    assert merged["summary"]["wall_time_s"] == 120.0
    assert len(merged["patients"]) == 3
    assert len(merged["failures"]) == 1


# ---------------------------------------------------------------------------
# T11: _scan_resume — p1 done, p2 failed, p3 new → [p2, p3], _failed/p2 deleted
# ---------------------------------------------------------------------------
def test_t11_scan_resume_retry_failed(tmp_path):
    from scripts.pipeline import _scan_resume, _atomic_write_json

    patients_dir = tmp_path / "patients"
    failed_dir = tmp_path / "_failed"
    patients_dir.mkdir()
    failed_dir.mkdir()

    # p1 already done
    _atomic_write_json(patients_dir / "p1.json", {"patient_id": "p1"})
    # p2 previously failed
    _atomic_write_json(failed_dir / "p2.json", {"patient_id": "p2", "error": "x"})

    patients = [
        {"patient_id": "p1"},
        {"patient_id": "p2"},
        {"patient_id": "p3"},
    ]
    to_run = _scan_resume(patients, tmp_path, resume=True)
    ids = [p["patient_id"] for p in to_run]
    assert ids == ["p2", "p3"]
    # _failed/p2.json should be deleted
    assert not (failed_dir / "p2.json").exists()


# ---------------------------------------------------------------------------
# T12: _scan_resume — resume=False → return original list unchanged
# ---------------------------------------------------------------------------
def test_t12_scan_resume_disabled():
    from scripts.pipeline import _scan_resume

    patients = [
        {"patient_id": "p1"},
        {"patient_id": "p2"},
        {"patient_id": "p3"},
    ]
    to_run = _scan_resume(patients, Path("/tmp/nonexistent"), resume=False)
    assert to_run == patients


# ---------------------------------------------------------------------------
# T13: _dedupe_hits — 4 hits with 2 duplicates → 3 unique
# ---------------------------------------------------------------------------
def test_t13_dedupe_hits():
    from scripts.pipeline import _dedupe_hits

    hits = [
        {"path": "a.md", "content": "alpha beta gamma delta epsilon zeta"},
        {"path": "b.md", "content": "first second third fourth fifth"},
        {"path": "a.md", "content": "alpha beta gamma delta epsilon zeta"},  # dup
        {"path": "c.md", "content": "uno dos tres cuatro"},
    ]
    deduped = _dedupe_hits(hits)
    assert len(deduped) == 3
    # First occurrence preserved
    assert deduped[0]["path"] == "a.md"
    assert deduped[1]["path"] == "b.md"
    assert deduped[2]["path"] == "c.md"


# ---------------------------------------------------------------------------
# T14: _load_patients — {"patients": [...]} → [...]
# ---------------------------------------------------------------------------
def test_t14_load_patients_wrapped(tmp_path):
    from scripts.pipeline import _load_patients

    f = tmp_path / "patients.json"
    f.write_text(json.dumps({"patients": [{"patient_id": "p1"}]}), encoding="utf-8")
    result = _load_patients(f)
    assert result == [{"patient_id": "p1"}]


# ---------------------------------------------------------------------------
# T15: _load_kb_metadata — no .metadata/ → ({}, {}) no error
# ---------------------------------------------------------------------------
def test_t15_load_kb_metadata_missing(tmp_path):
    from scripts.pipeline import _load_kb_metadata

    chunks, coverage = _load_kb_metadata(tmp_path)
    assert chunks == {}
    assert coverage == {}


# ---------------------------------------------------------------------------
# T16: _hit_org — extract org from various path formats
# ---------------------------------------------------------------------------
def test_t16_hit_org_various_paths():
    from scripts.pipeline import _hit_org

    assert _hit_org({"path": "qmd://nccn/foo.md"}) == "NCCN"
    assert _hit_org({"path": "qmd://esmo/x.md"}) == "ESMO"
    assert _hit_org({"path": "/x/NCCN/extracted/y.md"}) == "NCCN"
    assert _hit_org({"path": "/data/CSCO/extracted/guideline.md"}) == "CSCO"
    assert _hit_org({"path": ""}) == ""
