"""Tests for run_pipeline and _run_one_patient (Task 2: 7 cases).

All tests use mocked httpx (no real vLLM/QMD), pytest.mark.asyncio.

Mock strategy:
- Patch httpx.AsyncClient at pipeline module level for context manager
- Patch AsyncQMDService.__aenter__/__aexit__ to skip QMD process startup
- Mock qmd.query() and llm.complete_structured_with_feedback() return values
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scripts.pipeline import (
    _atomic_write_json,
    run_pipeline,
)

# ---------------------------------------------------------------------------
# Shared valid LLM response (passes PATIENT_RECOMMENDATION_SCHEMA validation)
# ---------------------------------------------------------------------------
_VALID_LLM_RESULT = {
    "guideline_results": [
        {
            "guideline": "NCCN",
            "guideline_version": "2026.v1",
            "recommendation": "对于 mCRC，推荐 FOLFOX 或 CAPOX 一线化疗 [1] [2]",
            "evidence_level": "Category 1",
            "source_file": "nccn-colorectal-2026.md",
            "retrieval_sources": [
                {"source_file": "nccn-colorectal-2026.md", "score": 0.92},
                {"source_file": "nccn-colorectal-2026-2.md", "score": 0.85},
            ],
        },
        {
            "guideline": "CSCO",
            "guideline_version": "2024",
            "recommendation": "推荐 FOLFOX 4 方案 [3]",
            "evidence_level": "1A类",
            "source_file": "csco-colorectal-2024.md",
            "retrieval_sources": [
                {"source_file": "csco-colorectal-2024.md", "score": 0.88},
            ],
        },
    ],
    "consensus": ["FOLFOX 为一线推荐"],
    "differences": ["NCCN 推荐 CAPOX 而 CSCO 未明确"],
}

_VALID_LLM_RESULT_LOW_COVERAGE = {
    "guideline_results": [
        {
            "guideline": "NCCN",
            "guideline_version": "2026.v1",
            "recommendation": "推荐方案未引用编号",
            "evidence_level": "Category 1",
            "source_file": "nccn-colorectal-2026.md",
            "retrieval_sources": [
                {"source_file": "nccn-colorectal-2026.md", "score": 0.92},
                {"source_file": "nccn-colorectal-2026-2.md", "score": 0.85},
                {"source_file": "csco-colorectal-2024.md", "score": 0.88},
                {"source_file": "esmo-colorectal-2025.md", "score": 0.80},
            ],
        },
    ],
    "consensus": ["共识条目"],
    "differences": ["差异条目"],
}

_QMD_HITS = [
    {"content": "FOLFOX方案推荐", "path": "qmd://nccn/nccn-colorectal-2026.md", "score": 0.9},
    {"content": "CAPOX方案推荐", "path": "qmd://csco/csco-colorectal-2024.md", "score": 0.85},
]


def _make_mock_patient(pid, name="测试患者", disease_type="结直肠癌"):
    return {
        "patient_id": pid,
        "patient_name": name,
        "disease_type": disease_type,
        "primary_site": "结肠",
        "stage": "IV期",
        "metastasis": "肝转移",
        "molecular": "KRAS 野生型",
        "treatment_history": "FOLFOX 6 周期",
        "diagnosis_summary": "晚期结直肠癌伴肝转移",
    }


def _make_patients_json(patients, tmp_path):
    p = tmp_path / "patients.json"
    p.write_text(json.dumps({"patients": patients}, ensure_ascii=False), encoding="utf-8")
    return p


def _default_args(tmp_path, patients_path):
    return argparse.Namespace(
        patients=str(patients_path),
        output_dir=str(tmp_path / "Output"),
        llm_profile="test-profile",
        concurrency_patients=2,
        concurrency_qmd=4,
        resume=False,
        kb_root=str(tmp_path / "kb"),
    )


def _setup_kb(tmp_path):
    kb_root = tmp_path / "kb"
    meta_dir = kb_root / ".metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "synonym_map.yaml").write_text(
        "结直肠癌:\n  - colorectal\n  - 结肠癌\n", encoding="utf-8"
    )
    return kb_root


def _setup_llm_env():
    os.environ["LLM_BASE_URL"] = "http://test.local/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["LLM_API_KEY"] = "sk-test"


def _teardown_llm_env():
    os.environ.pop("LLM_BASE_URL", None)
    os.environ.pop("LLM_MODEL", None)
    os.environ.pop("LLM_API_KEY", None)


def _make_qmd_mock(num_patients=3, hits_per_query=None):
    """Create a mock AsyncQMDService that returns predetermined hits.

    Each patient generates ~4 QMD queries via build_queries.
    Default: enough responses for num_patients × 4 queries.
    """
    mock_qmd = AsyncMock()
    if hits_per_query is None:
        hits_per_query = [_QMD_HITS] * (num_patients * 5)  # generous buffer
    mock_qmd.query = AsyncMock(side_effect=hits_per_query)
    mock_qmd.__aenter__ = AsyncMock(return_value=mock_qmd)
    mock_qmd.__aexit__ = AsyncMock(return_value=False)
    return mock_qmd


def _make_llm_mock(results):
    """Create a mock AsyncLLMClient.complete_structured_with_feedback.

    results: list of (result_dict, score, status) tuples
    """
    return AsyncMock(side_effect=results)


# ---------------------------------------------------------------------------
# Test 1: test_run_pipeline_happy_path — 3 patients all succeed, exit 0
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.pipeline.AsyncQMDService")
@patch("scripts.pipeline.httpx.AsyncClient")
@patch("scripts.pipeline.AsyncLLMClient")
async def test_run_pipeline_happy_path(
    mock_llm_cls, mock_http_cls, mock_qmd_cls
):
    tmp = Path("/tmp/test_pipeline_happy")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    patients = [_make_mock_patient(f"p{i:03d}", f"患者{i}") for i in range(1, 4)]
    patients_path = _make_patients_json(patients, tmp)
    args = _default_args(tmp, patients_path)
    _setup_kb(tmp)
    _setup_llm_env()

    # Mock httpx.AsyncClient as async context manager
    mock_http = AsyncMock()
    mock_http_cls.return_value = mock_http

    # Mock QMD: returns hits for each query
    mock_qmd = _make_qmd_mock()
    mock_qmd_cls.return_value = mock_qmd

    # Mock LLM: returns valid result with good coverage
    mock_llm = MagicMock()
    mock_llm.complete_structured_with_feedback = _make_llm_mock([
        (_VALID_LLM_RESULT, 0.71, "ok"),
        (_VALID_LLM_RESULT, 0.71, "ok"),
        (_VALID_LLM_RESULT, 0.71, "ok"),
    ])
    mock_llm_cls.return_value = mock_llm

    try:
        exit_code = await run_pipeline(args)
        output_dir = Path(args.output_dir)

        patient_shards = sorted((output_dir / "patients").glob("*.json"))
        assert len(patient_shards) == 3

        failed_shards = list((output_dir / "_failed").glob("*.json"))
        assert len(failed_shards) == 0

        assert exit_code == 0

        assert (output_dir / "rag_results.json").exists()
        merged = json.loads((output_dir / "rag_results.json").read_text(encoding="utf-8"))
        assert merged["summary"]["total"] == 3
        assert merged["summary"]["ok"] == 3
    finally:
        _teardown_llm_env()
        if tmp.exists():
            shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Test 2: test_run_pipeline_partial_accepted
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.pipeline.AsyncQMDService")
@patch("scripts.pipeline.httpx.AsyncClient")
@patch("scripts.pipeline.AsyncLLMClient")
async def test_run_pipeline_partial_accepted(
    mock_llm_cls, mock_http_cls, mock_qmd_cls
):
    tmp = Path("/tmp/test_pipeline_partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    patients = [
        _make_mock_patient("p001", "患者1"),
        _make_mock_patient("p002", "患者2"),
        _make_mock_patient("p003", "患者3"),
    ]
    patients_path = _make_patients_json(patients, tmp)
    args = _default_args(tmp, patients_path)
    _setup_kb(tmp)
    _setup_llm_env()

    mock_http = AsyncMock()
    mock_http_cls.return_value = mock_http

    mock_qmd = _make_qmd_mock()
    mock_qmd_cls.return_value = mock_qmd

    # p001: low coverage both times → partial
    # p002, p003: ok
    mock_llm = MagicMock()
    mock_llm.complete_structured_with_feedback = _make_llm_mock([
        (_VALID_LLM_RESULT_LOW_COVERAGE, 0.0, "partial"),  # p001
        (_VALID_LLM_RESULT, 0.71, "ok"),  # p002
        (_VALID_LLM_RESULT, 0.71, "ok"),  # p003
    ])
    mock_llm_cls.return_value = mock_llm

    try:
        exit_code = await run_pipeline(args)
        output_dir = Path(args.output_dir)

        patient_shards = sorted((output_dir / "patients").glob("*.json"))
        assert len(patient_shards) == 3

        statuses = {}
        for sp in patient_shards:
            shard = json.loads(sp.read_text(encoding="utf-8"))
            statuses[shard["patient_id"]] = shard["status"]

        partial_count = sum(1 for s in statuses.values() if s == "partial")
        ok_count = sum(1 for s in statuses.values() if s == "ok")
        assert partial_count >= 1
        assert ok_count >= 1
        assert exit_code == 0
    finally:
        _teardown_llm_env()
        if tmp.exists():
            shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Test 3: test_run_pipeline_one_failure_isolated
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.pipeline.AsyncQMDService")
@patch("scripts.pipeline.httpx.AsyncClient")
@patch("scripts.pipeline.AsyncLLMClient")
@patch("scripts.pipeline.LLMProfile")
async def test_run_pipeline_one_failure_isolated(
    mock_profile_cls, mock_llm_cls, mock_http_cls, mock_qmd_cls
):
    from scripts.llm_client import LLMFailure

    tmp = Path("/tmp/test_pipeline_failure")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    patients = [
        _make_mock_patient("p001", "患者1"),
        _make_mock_patient("p002", "患者2"),
        _make_mock_patient("p003", "患者3"),
    ]
    patients_path = _make_patients_json(patients, tmp)
    args = _default_args(tmp, patients_path)
    _setup_kb(tmp)

    # Mock profile
    mock_profile = MagicMock()
    mock_profile.timeout_s = 10
    mock_profile.concurrency = 5
    mock_profile_cls.from_env.return_value = mock_profile

    mock_http = AsyncMock()
    mock_http_cls.return_value = mock_http

    mock_qmd = _make_qmd_mock()
    mock_qmd_cls.return_value = mock_qmd

    # p001: LLMFailure; p002, p003: ok
    mock_llm = MagicMock()
    mock_llm.complete_structured_with_feedback = AsyncMock(side_effect=[
        LLMFailure("p001", httpx.TimeoutException("timeout"), stage="transport"),
        (_VALID_LLM_RESULT, 0.71, "ok"),
        (_VALID_LLM_RESULT, 0.71, "ok"),
    ])
    mock_llm_cls.return_value = mock_llm

    try:
        exit_code = await run_pipeline(args)
        output_dir = Path(args.output_dir)

        failed_shards = list((output_dir / "_failed").glob("*.json"))
        assert len(failed_shards) == 1
        failed = json.loads(failed_shards[0].read_text(encoding="utf-8"))
        assert failed["patient_id"] == "p001"
        assert failed["stage"] == "transport"

        patient_shards = sorted((output_dir / "patients").glob("*.json"))
        assert len(patient_shards) == 2
        assert exit_code == 1
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Test 4: test_run_pipeline_resume_skips_existing_shards
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.pipeline.AsyncQMDService")
@patch("scripts.pipeline.httpx.AsyncClient")
@patch("scripts.pipeline.AsyncLLMClient")
async def test_run_pipeline_resume_skips_existing_shards(
    mock_llm_cls, mock_http_cls, mock_qmd_cls
):
    tmp = Path("/tmp/test_pipeline_resume_skip")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    patients = [
        _make_mock_patient("p001", "患者1"),
        _make_mock_patient("p002", "患者2"),
        _make_mock_patient("p003", "患者3"),
    ]
    patients_path = _make_patients_json(patients, tmp)
    args = _default_args(tmp, patients_path)
    args.resume = True

    output_dir = Path(args.output_dir)
    (output_dir / "patients").mkdir(parents=True, exist_ok=True)
    (output_dir / "_failed").mkdir(parents=True, exist_ok=True)
    _atomic_write_json(output_dir / "patients" / "p001.json", {
        "patient_id": "p001", "status": "ok", "citation_coverage": 0.8, "result": {},
    })

    _setup_kb(tmp)
    _setup_llm_env()

    mock_http = AsyncMock()
    mock_http_cls.return_value = mock_http

    mock_qmd = _make_qmd_mock()
    mock_qmd_cls.return_value = mock_qmd

    # Only p002 and p003 need processing
    mock_llm = MagicMock()
    mock_llm.complete_structured_with_feedback = _make_llm_mock([
        (_VALID_LLM_RESULT, 0.71, "ok"),
        (_VALID_LLM_RESULT, 0.71, "ok"),
    ])
    mock_llm_cls.return_value = mock_llm

    try:
        exit_code = await run_pipeline(args)

        patient_shards = sorted((output_dir / "patients").glob("*.json"))
        assert len(patient_shards) == 3
        assert exit_code == 0
    finally:
        _teardown_llm_env()
        if tmp.exists():
            shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Test 5: test_run_pipeline_resume_retries_failed_shards
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.pipeline.AsyncQMDService")
@patch("scripts.pipeline.httpx.AsyncClient")
@patch("scripts.pipeline.AsyncLLMClient")
async def test_run_pipeline_resume_retries_failed_shards(
    mock_llm_cls, mock_http_cls, mock_qmd_cls
):
    tmp = Path("/tmp/test_pipeline_resume_retry")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    patients = [_make_mock_patient("p001", "患者1")]
    patients_path = _make_patients_json(patients, tmp)
    args = _default_args(tmp, patients_path)
    args.resume = True

    output_dir = Path(args.output_dir)
    (output_dir / "patients").mkdir(parents=True, exist_ok=True)
    (output_dir / "_failed").mkdir(parents=True, exist_ok=True)
    _atomic_write_json(output_dir / "_failed" / "p001.json", {
        "patient_id": "p001", "error": "old error", "stage": "transport",
    })

    _setup_kb(tmp)
    _setup_llm_env()

    mock_http = AsyncMock()
    mock_http_cls.return_value = mock_http

    mock_qmd = _make_qmd_mock()
    mock_qmd_cls.return_value = mock_qmd

    # p001 succeeds this time
    mock_llm = MagicMock()
    mock_llm.complete_structured_with_feedback = _make_llm_mock([
        (_VALID_LLM_RESULT, 0.71, "ok"),
    ])
    mock_llm_cls.return_value = mock_llm

    try:
        exit_code = await run_pipeline(args)

        assert not (output_dir / "_failed" / "p001.json").exists()
        assert (output_dir / "patients" / "p001.json").exists()
        shard = json.loads((output_dir / "patients" / "p001.json").read_text(encoding="utf-8"))
        assert shard["status"] == "ok"
        assert exit_code == 0
    finally:
        _teardown_llm_env()
        if tmp.exists():
            shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Test 6: test_atomic_write_no_partial_on_crash
# ---------------------------------------------------------------------------
def test_atomic_write_no_partial_on_crash(tmp_path):
    """If write_text throws, target file should not exist."""
    target = tmp_path / "test.json"
    data = {"key": "value"}

    import unittest.mock as mock
    with mock.patch.object(Path, "write_text", side_effect=OSError("disk full")):
        from scripts.pipeline import _atomic_write_json
        with pytest.raises(OSError, match="disk full"):
            _atomic_write_json(target, data)

    assert not target.exists()


# ---------------------------------------------------------------------------
# Test 7: test_concurrency_caps_in_flight
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.pipeline.AsyncQMDService")
@patch("scripts.pipeline.httpx.AsyncClient")
@patch("scripts.pipeline.AsyncLLMClient")
async def test_concurrency_caps_in_flight(
    mock_llm_cls, mock_http_cls, mock_qmd_cls
):
    tmp = Path("/tmp/test_pipeline_concurrency")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    patients = [_make_mock_patient(f"p{i:03d}", f"患者{i}") for i in range(5)]
    patients_path = _make_patients_json(patients, tmp)
    args = _default_args(tmp, patients_path)
    args.concurrency_patients = 2

    _setup_kb(tmp)
    _setup_llm_env()

    mock_http = AsyncMock()
    mock_http_cls.return_value = mock_http

    mock_qmd = _make_qmd_mock(num_patients=5)
    mock_qmd_cls.return_value = mock_qmd

    in_flight = {"current": 0, "peak": 0}

    async def slow_complete(*args, **kwargs):
        in_flight["current"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["current"])
        await asyncio.sleep(0.05)
        in_flight["current"] -= 1
        return (_VALID_LLM_RESULT, 0.71, "ok")

    mock_llm = MagicMock()
    mock_llm.complete_structured_with_feedback = AsyncMock(
        side_effect=[slow_complete for _ in patients]
    )
    mock_llm_cls.return_value = mock_llm

    try:
        await run_pipeline(args)
        assert in_flight["peak"] <= 2
    finally:
        _teardown_llm_env()
        if tmp.exists():
            shutil.rmtree(tmp)
