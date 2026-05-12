"""Tests for run_pipeline and _run_one_patient (Task 2: 7 cases).

All tests use mocked httpx (no real vLLM/QMD), pytest.mark.asyncio.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
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


def _mk_qmd_resp(*, session_id="s1", hits=None):
    """QMD MCP response mock: initialize or tools/call."""
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"mcp-session-id": session_id}
    if hits is not None:
        body = {
            "result": {
                "structuredContent": {
                    "results": [
                        {
                            "content": hits[0]["content"],
                            "path": hits[0]["path"],
                            "score": hits[0]["score"],
                        }
                    ]
                }
            }
        }
    else:
        body = {"result": {}}
    resp.json = MagicMock(return_value=body)
    resp.raise_for_status = MagicMock(return_value=None)
    return resp


def _mk_llm_resp(*, result=None):
    """LLM chat/completions response mock."""
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.request = MagicMock()
    content = result or _VALID_LLM_RESULT
    resp.json = MagicMock(return_value={
        "choices": [{"message": {"content": json.dumps(content)}}]
    })
    resp.raise_for_status = MagicMock(return_value=None)
    return resp


def _make_mock_patient(pid, name="测试患者", disease_type="结直肠癌"):
    """Create a minimal patient dict that extract_patient_features can handle."""
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
    """Write patients.json and return path."""
    p = tmp_path / "patients.json"
    p.write_text(json.dumps({"patients": patients}, ensure_ascii=False), encoding="utf-8")
    return p


def _default_args(tmp_path, patients_path):
    """Build default argparse.Namespace for run_pipeline."""
    return argparse.Namespace(
        patients=str(patients_path),
        output_dir=str(tmp_path / "Output"),
        llm_profile="test-profile",
        concurrency_patients=2,
        concurrency_qmd=4,
        resume=False,
        kb_root=str(tmp_path / "kb"),
    )


# ---------------------------------------------------------------------------
# Test 1: test_run_pipeline_happy_path — 3 patients all succeed, exit 0
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.AsyncQMDService._wait_for_ready_async", new_callable=AsyncMock)
@patch("scripts.retriever.httpx.AsyncClient")
@patch("scripts.llm_client.httpx.AsyncClient")
async def test_run_pipeline_happy_path(
    mock_llm_http_cls, mock_qmd_http_cls, mock_wait, mock_popen
):
    from scripts.pipeline import _merge_rag_results

    tmp = Path("/tmp/test_pipeline_happy")
    # Cleanup and setup
    import shutil
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    patients = [_make_mock_patient(f"p{i:03d}", f"患者{i}") for i in range(1, 4)]
    patients_path = _make_patients_json(patients, tmp)
    args = _default_args(tmp, patients_path)

    # Create KB root with .metadata for synonym map
    kb_root = tmp / "kb"
    meta_dir = kb_root / ".metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "synonym_map.yaml").write_text("结直肠癌:\n  - colorectal\n  - 结肠癌\n", encoding="utf-8")

    # Mock LLM profile env
    os.environ["LLM_BASE_URL"] = "http://test.local/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["LLM_API_KEY"] = "sk-test"

    # Setup mock LLM httpx
    mock_llm_http = AsyncMock()
    # For each patient: 1 LLM call (coverage >= 0.5, no feedback retry needed)
    mock_llm_http.post = AsyncMock(side_effect=[_mk_llm_resp()] * 6)  # 3 patients × up to 2 calls each (reserve)
    mock_llm_http.aclose = AsyncMock()
    mock_llm_http_cls.return_value = mock_llm_http

    # Setup QMD mocks
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_popen.return_value = mock_proc

    mock_qmd_http = AsyncMock()
    # initialize + tools/call for each query per patient
    qmd_responses = [_mk_qmd_resp()]  # initialize
    for _ in patients:
        qmd_responses.append(_mk_qmd_resp(hits=[
            {"content": "FOLFOX推荐", "path": "qmd://nccn/x.md", "score": 0.9}
        ]))
        qmd_responses.append(_mk_qmd_resp(hits=[
            {"content": "CAPOX推荐", "path": "qmd://csco/y.md", "score": 0.85}
        ]))
    mock_qmd_http.post = AsyncMock(side_effect=qmd_responses)
    mock_qmd_http.aclose = AsyncMock()
    mock_qmd_http_cls.return_value = mock_qmd_http

    try:
        exit_code = await run_pipeline(args)

        output_dir = Path(args.output_dir)
        # All 3 patients should be in patients/
        patient_shards = sorted((output_dir / "patients").glob("*.json"))
        assert len(patient_shards) == 3

        # No failures
        failed_shards = list((output_dir / "_failed").glob("*.json"))
        assert len(failed_shards) == 0

        # Exit code 0
        assert exit_code == 0

        # rag_results.json exists
        assert (output_dir / "rag_results.json").exists()
        merged = json.loads((output_dir / "rag_results.json").read_text(encoding="utf-8"))
        assert merged["summary"]["total"] == 3
        assert merged["summary"]["ok"] == 3
    finally:
        # Cleanup env
        os.environ.pop("LLM_BASE_URL", None)
        os.environ.pop("LLM_MODEL", None)
        os.environ.pop("LLM_API_KEY", None)
        if tmp.exists():
            shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Test 2: test_run_pipeline_partial_accepted — feedback retry still low → partial
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.AsyncQMDService._wait_for_ready_async", new_callable=AsyncMock)
@patch("scripts.retriever.httpx.AsyncClient")
@patch("scripts.llm_client.httpx.AsyncClient")
async def test_run_pipeline_partial_accepted(
    mock_llm_http_cls, mock_qmd_http_cls, mock_wait, mock_popen
):
    import shutil
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

    kb_root = tmp / "kb"
    meta_dir = kb_root / ".metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "synonym_map.yaml").write_text("结直肠癌:\n  - colorectal\n", encoding="utf-8")

    os.environ["LLM_BASE_URL"] = "http://test.local/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["LLM_API_KEY"] = "sk-test"

    # p001 gets low-coverage result twice (feedback retry still < threshold → partial)
    # p002, p003 get valid result (ok)
    llm_responses = [
        _mk_llm_resp(result=_VALID_LLM_RESULT_LOW_COVERAGE),  # p001 1st call
        _mk_llm_resp(result=_VALID_LLM_RESULT_LOW_COVERAGE),  # p001 2nd call (feedback retry)
        _mk_llm_resp(),  # p002
        _mk_llm_resp(),  # p003
    ]
    mock_llm_http = AsyncMock()
    mock_llm_http.post = AsyncMock(side_effect=llm_responses)
    mock_llm_http.aclose = AsyncMock()
    mock_llm_http_cls.return_value = mock_llm_http

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_popen.return_value = mock_proc

    mock_qmd_http = AsyncMock()
    qmd_responses = [_mk_qmd_resp()]  # initialize
    for _ in patients:
        qmd_responses.append(_mk_qmd_resp(hits=[
            {"content": "FOLFOX推荐", "path": "qmd://nccn/x.md", "score": 0.9}
        ]))
    mock_qmd_http.post = AsyncMock(side_effect=qmd_responses)
    mock_qmd_http.aclose = AsyncMock()
    mock_qmd_http_cls.return_value = mock_qmd_http

    try:
        exit_code = await run_pipeline(args)
        output_dir = Path(args.output_dir)

        # All 3 in patients/ (no _failed)
        patient_shards = sorted((output_dir / "patients").glob("*.json"))
        assert len(patient_shards) == 3

        # Check statuses
        statuses = {}
        for shard_path in patient_shards:
            shard = json.loads(shard_path.read_text(encoding="utf-8"))
            statuses[shard["patient_id"]] = shard["status"]

        # p001 should be partial (coverage < 0.5 both times)
        # p002 and p003 should be ok
        ok_count = sum(1 for s in statuses.values() if s == "ok")
        partial_count = sum(1 for s in statuses.values() if s == "partial")
        assert partial_count >= 1
        assert ok_count >= 1

        # exit 0 (partial is still success)
        assert exit_code == 0
    finally:
        os.environ.pop("LLM_BASE_URL", None)
        os.environ.pop("LLM_MODEL", None)
        os.environ.pop("LLM_API_KEY", None)
        if tmp.exists():
            shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Test 3: test_run_pipeline_one_failure_isolated — transport fail → _failed, others ok
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.AsyncQMDService._wait_for_ready_async", new_callable=AsyncMock)
@patch("scripts.retriever.httpx.AsyncClient")
@patch("scripts.llm_client.httpx.AsyncClient")
async def test_run_pipeline_one_failure_isolated(
    mock_llm_http_cls, mock_qmd_http_cls, mock_wait, mock_popen
):
    import shutil
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

    kb_root = tmp / "kb"
    meta_dir = kb_root / ".metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "synonym_map.yaml").write_text("结直肠癌:\n  - colorectal\n", encoding="utf-8")

    os.environ["LLM_BASE_URL"] = "http://test.local/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["LLM_API_KEY"] = "sk-test"

    # p001: timeout on all retries → LLMFailure(stage="transport")
    # p002, p003: success
    timeout_err = httpx.TimeoutException("timeout")
    llm_responses = []
    # p001: 4 retries all timeout (AsyncLLMClient._post_with_retry does 4 attempts)
    for _ in range(4):
        llm_responses.append(httpx.TimeoutException("timeout"))
    # p002 and p003: success
    llm_responses.append(_mk_llm_resp())
    llm_responses.append(_mk_llm_resp())

    mock_llm_http = AsyncMock()
    mock_llm_http.post = AsyncMock(side_effect=llm_responses)
    mock_llm_http.aclose = AsyncMock()
    mock_llm_http_cls.return_value = mock_llm_http

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_popen.return_value = mock_proc

    mock_qmd_http = AsyncMock()
    qmd_responses = [_mk_qmd_resp()]  # initialize
    for _ in patients:
        qmd_responses.append(_mk_qmd_resp(hits=[
            {"content": "推荐", "path": "qmd://nccn/x.md", "score": 0.9}
        ]))
    mock_qmd_http.post = AsyncMock(side_effect=qmd_responses)
    mock_qmd_http.aclose = AsyncMock()
    mock_qmd_http_cls.return_value = mock_qmd_http

    try:
        exit_code = await run_pipeline(args)
        output_dir = Path(args.output_dir)

        # p001 should be in _failed/
        failed_shards = list((output_dir / "_failed").glob("*.json"))
        assert len(failed_shards) == 1
        failed = json.loads(failed_shards[0].read_text(encoding="utf-8"))
        assert failed["patient_id"] == "p001"
        assert failed["stage"] == "transport"

        # p002 and p003 in patients/
        patient_shards = sorted((output_dir / "patients").glob("*.json"))
        assert len(patient_shards) == 2

        # Exit 1 (at least one failure)
        assert exit_code == 1
    finally:
        os.environ.pop("LLM_BASE_URL", None)
        os.environ.pop("LLM_MODEL", None)
        os.environ.pop("LLM_API_KEY", None)
        if tmp.exists():
            shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Test 4: test_run_pipeline_resume_skips_existing_shards
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.AsyncQMDService._wait_for_ready_async", new_callable=AsyncMock)
@patch("scripts.retriever.httpx.AsyncClient")
@patch("scripts.llm_client.httpx.AsyncClient")
async def test_run_pipeline_resume_skips_existing_shards(
    mock_llm_http_cls, mock_qmd_http_cls, mock_wait, mock_popen
):
    import shutil
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

    # Pre-create output dir with p001 already done
    output_dir = Path(args.output_dir)
    (output_dir / "patients").mkdir(parents=True, exist_ok=True)
    (output_dir / "_failed").mkdir(parents=True, exist_ok=True)
    _atomic_write_json(output_dir / "patients" / "p001.json", {
        "patient_id": "p001", "status": "ok", "citation_coverage": 0.8, "result": {},
    })

    kb_root = tmp / "kb"
    meta_dir = kb_root / ".metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "synonym_map.yaml").write_text("结直肠癌:\n  - colorectal\n", encoding="utf-8")

    os.environ["LLM_BASE_URL"] = "http://test.local/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["LLM_API_KEY"] = "sk-test"

    # Only p002 and p003 need LLM calls (p001 skipped)
    llm_responses = [_mk_llm_resp(), _mk_llm_resp()]  # 2 calls
    mock_llm_http = AsyncMock()
    mock_llm_http.post = AsyncMock(side_effect=llm_responses)
    mock_llm_http.aclose = AsyncMock()
    mock_llm_http_cls.return_value = mock_llm_http

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_popen.return_value = mock_proc

    mock_qmd_http = AsyncMock()
    qmd_responses = [_mk_qmd_resp()]  # initialize
    # Only 2 patients' QMD queries
    for _ in range(2):
        qmd_responses.append(_mk_qmd_resp(hits=[
            {"content": "推荐", "path": "qmd://nccn/x.md", "score": 0.9}
        ]))
    mock_qmd_http.post = AsyncMock(side_effect=qmd_responses)
    mock_qmd_http.aclose = AsyncMock()
    mock_qmd_http_cls.return_value = mock_qmd_http

    try:
        exit_code = await run_pipeline(args)

        # All 3 patients in patients/
        patient_shards = sorted((output_dir / "patients").glob("*.json"))
        assert len(patient_shards) == 3
        assert exit_code == 0
    finally:
        os.environ.pop("LLM_BASE_URL", None)
        os.environ.pop("LLM_MODEL", None)
        os.environ.pop("LLM_API_KEY", None)
        if tmp.exists():
            shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Test 5: test_run_pipeline_resume_retries_failed_shards
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.AsyncQMDService._wait_for_ready_async", new_callable=AsyncMock)
@patch("scripts.retriever.httpx.AsyncClient")
@patch("scripts.llm_client.httpx.AsyncClient")
async def test_run_pipeline_resume_retries_failed_shards(
    mock_llm_http_cls, mock_qmd_http_cls, mock_wait, mock_popen
):
    import shutil
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

    # Pre-create _failed/p001.json
    _atomic_write_json(output_dir / "_failed" / "p001.json", {
        "patient_id": "p001", "error": "old error", "stage": "transport",
    })

    kb_root = tmp / "kb"
    meta_dir = kb_root / ".metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "synonym_map.yaml").write_text("结直肠癌:\n  - colorectal\n", encoding="utf-8")

    os.environ["LLM_BASE_URL"] = "http://test.local/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["LLM_API_KEY"] = "sk-test"

    mock_llm_http = AsyncMock()
    mock_llm_http.post = AsyncMock(side_effect=[_mk_llm_resp()])
    mock_llm_http.aclose = AsyncMock()
    mock_llm_http_cls.return_value = mock_llm_http

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_popen.return_value = mock_proc

    mock_qmd_http = AsyncMock()
    mock_qmd_http.post = AsyncMock(side_effect=[
        _mk_qmd_resp(),
        _mk_qmd_resp(hits=[{"content": "推荐", "path": "qmd://nccn/x.md", "score": 0.9}]),
    ])
    mock_qmd_http.aclose = AsyncMock()
    mock_qmd_http_cls.return_value = mock_qmd_http

    try:
        exit_code = await run_pipeline(args)

        # _failed/p001.json should be gone
        assert not (output_dir / "_failed" / "p001.json").exists()

        # patients/p001.json should exist (success this time)
        assert (output_dir / "patients" / "p001.json").exists()
        shard = json.loads((output_dir / "patients" / "p001.json").read_text(encoding="utf-8"))
        assert shard["status"] == "ok"
        assert exit_code == 0
    finally:
        os.environ.pop("LLM_BASE_URL", None)
        os.environ.pop("LLM_MODEL", None)
        os.environ.pop("LLM_API_KEY", None)
        if tmp.exists():
            shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Test 6: test_atomic_write_no_partial_on_crash
# ---------------------------------------------------------------------------
def test_atomic_write_no_partial_on_crash(tmp_path):
    """If write_text throws, target file should not exist."""
    target = tmp_path / "test.json"
    data = {"key": "value"}

    # Patch write_text to raise
    import unittest.mock as mock
    with mock.patch.object(Path, "write_text", side_effect=OSError("disk full")):
        from scripts.pipeline import _atomic_write_json
        with pytest.raises(OSError, match="disk full"):
            _atomic_write_json(target, data)

    # Target should not exist
    assert not target.exists()


# ---------------------------------------------------------------------------
# Test 7: test_concurrency_caps_in_flight
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.AsyncQMDService._wait_for_ready_async", new_callable=AsyncMock)
@patch("scripts.retriever.httpx.AsyncClient")
@patch("scripts.llm_client.httpx.AsyncClient")
async def test_concurrency_caps_in_flight(
    mock_llm_http_cls, mock_qmd_http_cls, mock_wait, mock_popen
):
    import shutil
    tmp = Path("/tmp/test_pipeline_concurrency")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    # 5 patients, sem=2 → in-flight peak should be ≤ 2
    patients = [_make_mock_patient(f"p{i:03d}", f"患者{i}") for i in range(5)]
    patients_path = _make_patients_json(patients, tmp)
    args = _default_args(tmp, patients_path)
    args.concurrency_patients = 2

    kb_root = tmp / "kb"
    meta_dir = kb_root / ".metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "synonym_map.yaml").write_text("结直肠癌:\n  - colorectal\n", encoding="utf-8")

    os.environ["LLM_BASE_URL"] = "http://test.local/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["LLM_API_KEY"] = "sk-test"

    in_flight = {"current": 0, "peak": 0}

    async def slow_llm_post(*args, **kwargs):
        in_flight["current"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["current"])
        await asyncio.sleep(0.05)
        in_flight["current"] -= 1
        return _mk_llm_resp()

    mock_llm_http = AsyncMock()
    mock_llm_http.post = AsyncMock(side_effect=[slow_llm_post()] * 10)
    mock_llm_http.aclose = AsyncMock()
    mock_llm_http_cls.return_value = mock_llm_http

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_popen.return_value = mock_proc

    mock_qmd_http = AsyncMock()
    qmd_responses = [_mk_qmd_resp()]
    for _ in patients:
        qmd_responses.append(_mk_qmd_resp(hits=[
            {"content": "推荐", "path": "qmd://nccn/x.md", "score": 0.9}
        ]))
    mock_qmd_http.post = AsyncMock(side_effect=qmd_responses)
    mock_qmd_http.aclose = AsyncMock()
    mock_qmd_http_cls.return_value = mock_qmd_http

    try:
        await run_pipeline(args)
        assert in_flight["peak"] <= 2
    finally:
        os.environ.pop("LLM_BASE_URL", None)
        os.environ.pop("LLM_MODEL", None)
        os.environ.pop("LLM_API_KEY", None)
        if tmp.exists():
            shutil.rmtree(tmp)
