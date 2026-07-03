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
    # 2026-07-02：org/chunk 双层过滤此前对真实数据是 no-op（bug，已修），
    # 现在是真的在生效——不给 coverage.json 会让 allowed_orgs=[]，_QMD_HITS
    # 里的 nccn/csco hits 全部被 Stage 4 过滤掉，触发新的"零证据"保护直接
    # 拒绝调用 LLM。用真实 patient disease_type（_make_mock_patient 默认
    # "结直肠癌"）对应的 org 覆盖，让这些既有测试的 hits 能正常通过过滤。
    # 不写 chunks.json：留空则 chunk 级过滤走 KBM-06"无 meta 保留"兜底，
    # 不需要为每条 mock hit 伪造匹配的 chunk 元数据。
    (meta_dir / "org_disease_coverage.json").write_text(
        json.dumps({"nccn": ["结直肠癌"], "csco": ["结直肠癌"]}), encoding="utf-8"
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


# ---------------------------------------------------------------------------
# Test: test_run_pipeline_org_filter_case_mismatch_regression
#
# 2026-07-02 codex 对抗式审查发现：现有 mock KB（_setup_kb）不写 coverage/chunks，
# 现有 LLM mock 也不检查 prompt 内容，所以 org 大小写不一致的 P0 bug（pipeline.py:330）
# 完全测不出来——filter_orgs_by_disease 的结果被吞掉，测试照样全绿。
# 这个测试用真实形状的侧车（build_sidecar() 风格小写 key）+ 真实形状的 QMD hit
# path（大写 org），并检查真正传给 LLM 的 prompt 内容，而不是只测 exit code。
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.pipeline.AsyncQMDService")
@patch("scripts.pipeline.httpx.AsyncClient")
@patch("scripts.pipeline.AsyncLLMClient")
async def test_run_pipeline_org_filter_case_mismatch_regression(
    mock_llm_cls, mock_http_cls, mock_qmd_cls
):
    tmp = Path("/tmp/test_pipeline_org_case")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    # patients.json 无 disease_type 字段（真实 parse 输出的形状），
    # 靠 primary_site 触发 _synthesize_from_patient fallback。
    patient = {
        "patient_id": "p001",
        "patient_name": "患者1",
        "primary_site": "直肠(R)",
        "stage": "IV期",
    }
    patients_path = _make_patients_json([patient], tmp)
    args = _default_args(tmp, patients_path)
    args.concurrency_patients = 1

    kb_root = tmp / "kb"
    meta_dir = kb_root / ".metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "synonym_map.yaml").write_text(
        "colorectal:\n  - 结直肠癌\n  - 直肠癌\n  - 结肠癌\n"
        "gastric:\n  - 胃癌\n",
        encoding="utf-8",
    )
    # build_sidecar() 真实写入形态：org key 全小写
    (meta_dir / "org_disease_coverage.json").write_text(
        json.dumps({"nccn": ["colorectal"], "caca": ["gastric"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (meta_dir / "chunks.json").write_text("{}", encoding="utf-8")
    _setup_llm_env()

    mock_http = AsyncMock()
    mock_http_cls.return_value = mock_http

    # 真实 QMD 返回的 path 大写 org（与 collection 目录名一致），
    # 每次 query 都返回同一对 hits：一条属于命中病种的 nccn，一条属于不该命中的 caca。
    matching_hit = {"content": "NCCN 直肠癌一线方案", "path": "qmd://NCCN/nccn-rectalcancer-2026.md", "score": 0.9}
    non_matching_hit = {"content": "CACA 胃癌方案", "path": "qmd://CACA/caca-gastric-2025.md", "score": 0.85}
    mock_qmd = _make_qmd_mock(hits_per_query=[[matching_hit, non_matching_hit]] * 10)
    mock_qmd_cls.return_value = mock_qmd

    captured_messages = {}

    async def capture_complete(messages, *a, **kw):
        captured_messages["messages"] = messages
        return (_VALID_LLM_RESULT, 0.71, "ok")

    mock_llm = MagicMock()
    mock_llm.complete_structured_with_feedback = AsyncMock(side_effect=capture_complete)
    mock_llm_cls.return_value = mock_llm

    try:
        exit_code = await run_pipeline(args)
        assert exit_code == 0

        user_content = captured_messages["messages"][1]["content"]
        # 修复验证：命中病种的 NCCN 检索结果必须进了 prompt
        assert "NCCN 直肠癌一线方案" in user_content
        # 病种过滤验证：不匹配病种的 CACA 检索结果必须被排除
        assert "CACA 胃癌方案" not in user_content
    finally:
        _teardown_llm_env()
        if tmp.exists():
            shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Test: test_run_pipeline_qmd_query_error_writes_failed_shard
#
# 2026-07-02 回归：Stage 3 QMD 查询此前没有专属 except，QMDQueryError/
# httpx.RequestError 会穿透 _run_one_patient，被 gather(..., return_exceptions=True)
# 静默吞掉——患者既不进 patients/ 也不进 _failed/，exit code 却仍是 0。
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.pipeline.AsyncQMDService")
@patch("scripts.pipeline.httpx.AsyncClient")
@patch("scripts.pipeline.AsyncLLMClient")
async def test_run_pipeline_qmd_query_error_writes_failed_shard(
    mock_llm_cls, mock_http_cls, mock_qmd_cls
):
    from scripts.retriever import QMDQueryError

    tmp = Path("/tmp/test_pipeline_qmd_error")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    patients = [_make_mock_patient("p001", "患者1")]
    patients_path = _make_patients_json(patients, tmp)
    args = _default_args(tmp, patients_path)
    _setup_kb(tmp)
    _setup_llm_env()

    mock_http = AsyncMock()
    mock_http_cls.return_value = mock_http

    # QMD 重试耗尽后抛出的真实异常类型（见 retriever.py:_post_tools_call）
    mock_qmd = AsyncMock()
    mock_qmd.query = AsyncMock(
        side_effect=QMDQueryError("QMD tools/call failed after 3 attempts: timeout")
    )
    mock_qmd.__aenter__ = AsyncMock(return_value=mock_qmd)
    mock_qmd.__aexit__ = AsyncMock(return_value=False)
    mock_qmd_cls.return_value = mock_qmd

    mock_llm = MagicMock()
    mock_llm.complete_structured_with_feedback = AsyncMock()
    mock_llm_cls.return_value = mock_llm

    try:
        exit_code = await run_pipeline(args)
        output_dir = Path(args.output_dir)

        # 核心断言：不再静默消失——必须出现在 _failed/，stage="retrieval"
        failed_shards = list((output_dir / "_failed").glob("*.json"))
        assert len(failed_shards) == 1
        shard = json.loads(failed_shards[0].read_text(encoding="utf-8"))
        assert shard["patient_id"] == "p001"
        assert shard["stage"] == "retrieval"
        assert "QMD" in shard["error"] or "timeout" in shard["error"]

        assert list((output_dir / "patients").glob("*.json")) == []
        assert exit_code == 1  # 有 failure，退出码必须非 0
        # LLM 从未被调用——QMD 阶段失败应在 Stage 3 就中断，不进 Stage 6
        mock_llm.complete_structured_with_feedback.assert_not_called()
    finally:
        _teardown_llm_env()
        if tmp.exists():
            shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Test: test_run_pipeline_unexpected_exception_safety_net
#
# 2026-07-02 回归：run_pipeline 顶层 gather 此前不接收返回值，任何完全没被
# _run_one_patient 分类到的异常类型（此测试模拟一个未来才会出现的新类型）
# 也必须被兜底写进 _failed/（stage="unexpected"），而不是被吞掉。
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.pipeline.AsyncQMDService")
@patch("scripts.pipeline.httpx.AsyncClient")
@patch("scripts.pipeline.AsyncLLMClient")
async def test_run_pipeline_unexpected_exception_safety_net(
    mock_llm_cls, mock_http_cls, mock_qmd_cls
):
    tmp = Path("/tmp/test_pipeline_unexpected_exc")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    patients = [_make_mock_patient("p001", "患者1")]
    patients_path = _make_patients_json(patients, tmp)
    args = _default_args(tmp, patients_path)
    _setup_kb(tmp)
    _setup_llm_env()

    mock_http = AsyncMock()
    mock_http_cls.return_value = mock_http

    mock_qmd = _make_qmd_mock()
    mock_qmd_cls.return_value = mock_qmd

    # 一个 _run_one_patient 内部任何 except 分支都不会捕获的异常类型
    mock_llm = MagicMock()
    mock_llm.complete_structured_with_feedback = AsyncMock(
        side_effect=RuntimeError("completely unclassified failure")
    )
    mock_llm_cls.return_value = mock_llm

    try:
        exit_code = await run_pipeline(args)
        output_dir = Path(args.output_dir)

        failed_shards = list((output_dir / "_failed").glob("*.json"))
        assert len(failed_shards) == 1
        shard = json.loads(failed_shards[0].read_text(encoding="utf-8"))
        assert shard["patient_id"] == "p001"
        assert shard["stage"] == "unexpected"
        assert "completely unclassified failure" in shard["error"]
        assert exit_code == 1
    finally:
        _teardown_llm_env()
        if tmp.exists():
            shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Test: test_run_pipeline_zero_hits_never_calls_llm
#
# 2026-07-02 回归（codex 对抗式审查 P0，两轮迭代）：QMD 检索/双层过滤后如果
# hits=[]，此前会照常构造 prompt 并调用 LLM——模型在完全没有真实指南内容
# 的情况下仍可能生成看起来言之有据、引用 CSCO/NCCN 的 JSON（凭训练知识编
# 造，不是真的检索到的内容），而 compute_citation_coverage() 对空
# retrieval_sources 返回 1.0（满分），会被当成正常 "ok" shard 写出。对医学
# 指南系统这是不可接受的静默幻觉风险——必须在调用 LLM 前就拦截。
# 第二轮修正：不应把"该病种在 KB 里没有相关指南"（合法结果，比如罕见病）
# 当成故障计入 failed/exit_code=1——那会让 QG-02"10 例 0 FAIL"对完全没有
# bug 的患者产生假阳性。改为独立 status="no_evidence"，写进 patients/ 而
# 不是 _failed/，exit_code 不受影响。
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@patch("scripts.pipeline.AsyncQMDService")
@patch("scripts.pipeline.httpx.AsyncClient")
@patch("scripts.pipeline.AsyncLLMClient")
async def test_run_pipeline_zero_hits_never_calls_llm(
    mock_llm_cls, mock_http_cls, mock_qmd_cls
):
    tmp = Path("/tmp/test_pipeline_zero_hits")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    patients = [_make_mock_patient("p001", "患者1")]
    patients_path = _make_patients_json(patients, tmp)
    args = _default_args(tmp, patients_path)
    _setup_kb(tmp)  # coverage.json 只覆盖 结直肠癌，QMD 这里返回空结果
    _setup_llm_env()

    mock_http = AsyncMock()
    mock_http_cls.return_value = mock_http

    # QMD 正常返回，但检索不到任何相关内容（不是异常，是真的空结果）
    mock_qmd = AsyncMock()
    mock_qmd.query = AsyncMock(return_value=[])
    mock_qmd.__aenter__ = AsyncMock(return_value=mock_qmd)
    mock_qmd.__aexit__ = AsyncMock(return_value=False)
    mock_qmd_cls.return_value = mock_qmd

    mock_llm = MagicMock()
    mock_llm.complete_structured_with_feedback = AsyncMock()
    mock_llm_cls.return_value = mock_llm

    try:
        exit_code = await run_pipeline(args)
        output_dir = Path(args.output_dir)

        # 核心断言：LLM 绝对不能在零证据下被调用
        mock_llm.complete_structured_with_feedback.assert_not_called()

        # 不进 _failed/，不算故障
        assert list((output_dir / "_failed").glob("*.json")) == []

        patient_shards = list((output_dir / "patients").glob("*.json"))
        assert len(patient_shards) == 1
        shard = json.loads(patient_shards[0].read_text(encoding="utf-8"))
        assert shard["patient_id"] == "p001"
        assert shard["status"] == "no_evidence"
        assert shard["result"] is None
        assert "no relevant retrieval hits" in shard["note"]

        rag_results = json.loads((output_dir / "rag_results.json").read_text(encoding="utf-8"))
        assert rag_results["summary"]["no_evidence"] == 1
        assert rag_results["summary"]["failed"] == 0
        assert rag_results["summary"]["total"] == 1

        assert exit_code == 0  # no_evidence 不是故障，不能拖累 exit code
    finally:
        _teardown_llm_env()
        if tmp.exists():
            shutil.rmtree(tmp)
