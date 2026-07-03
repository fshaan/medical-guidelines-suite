"""退化感知逻辑的回归测试（2026-07-03 codex 审查后新增）。

覆盖：
- _repetition_score：重复度计算
- _select_diverse_hits：按 org 保底精简
- _parse_and_validate 退化检测：finish=length+高重复 → DegenerationError
- _llm_with_degeneration_fallback：三档降级链
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scripts.llm_client import (
    AsyncLLMClient,
    DegenerationError,
    LLMProfile,
    PATIENT_RECOMMENDATION_SCHEMA,
    _repetition_score,
)
from scripts.pipeline import _llm_with_degeneration_fallback, _select_diverse_hits


# ── _repetition_score ───────────────────────────────────────────────────────

def test_repetition_score_short_text_zero():
    assert _repetition_score("短文本") == 0.0


def test_repetition_score_highly_repetitive():
    # 真实退化是大段重复（实测 rep 0.89-0.97），用对齐 200 字符块的大段重复
    text = "推" * 2000  # 200-字符块全相同
    assert _repetition_score(text) > 0.5


def test_repetition_score_diverse_text_low():
    # 每句不同，低重复
    text = "".join(f"这是第{i}句完全不同的内容用于测试多样性。" for i in range(40))
    assert _repetition_score(text) < 0.3


# ── _select_diverse_hits ─────────────────────────────────────────────────────

def _mk_hit(path: str, score: float) -> dict:
    return {"path": path, "score": score, "content": "x"}


def test_select_diverse_hits_small_set_unchanged():
    hits = [_mk_hit("CSCO/a.md", 0.9), _mk_hit("NCCN/b.md", 0.8)]
    assert _select_diverse_hits(hits) == hits


def test_select_diverse_hits_per_org_floor():
    # 5 org 各 4 条 = 20 条，per_org=3 max_total=15 → 每 org 保底 3，但 max_total 截断
    hits = []
    for org in ["CSCO", "NCCN", "ESMO", "JGCA", "CACA"]:
        for i in range(4):
            hits.append(_mk_hit(f"{org}/{org.lower()}_{i}.md", 0.9 - i * 0.01))
    out = _select_diverse_hits(hits, per_org=3, max_total=15)
    assert len(out) == 15
    orgs = {h["path"].split("/")[0] for h in out}
    assert orgs == {"CSCO", "NCCN", "ESMO", "JGCA", "CACA"}  # 5 org 都有代表


def test_select_diverse_hits_does_not_starve_low_score_org():
    # CSCO 高分多条，CACA 仅 1 条低分 → CACA 仍保留（保底）
    hits = [_mk_hit("CSCO/a.md", 0.95) for _ in range(10)]
    hits.append(_mk_hit("CACA/rare.md", 0.3))
    out = _select_diverse_hits(hits, per_org=3, max_total=8)
    assert any(h["path"] == "CACA/rare.md" for h in out)


# ── _parse_and_validate 退化检测 ─────────────────────────────────────────────

def _mk_client() -> AsyncLLMClient:
    profile = LLMProfile(name="t", base_url="http://x/v1", model="m")
    return AsyncLLMClient(profile, http=MagicMock(spec=httpx.AsyncClient))


def _mk_response(content: str, finish_reason: str, comp_tok: int = 9999) -> dict:
    return {
        "choices": [{
            "message": {"content": content},
            "finish_reason": finish_reason,
        }],
        "usage": {"completion_tokens": comp_tok, "prompt_tokens": 100},
    }


def _valid_result_json() -> str:
    repeat = "推" * 2000  # 大段重复，触发 _repetition_score > 0.5
    return json.dumps({
        "guideline_results": [{
            "guideline": "CSCO",
            "guideline_version": "2026 版",
            "recommendation": repeat,
            "evidence_level": "1A类",
            "source_file": "csco.md",
            "retrieval_sources": [{"source_file": "csco.md", "score": 0.9}],
        }],
        "consensus": ["共识一"],
        "differences": ["差异一"],
    }, ensure_ascii=False)


def test_parse_flags_degeneration_on_length_and_repetition():
    client = _mk_client()
    resp = _mk_response(_valid_result_json(), finish_reason="length", comp_tok=8192)
    with pytest.raises(DegenerationError) as exc:
        client._parse_and_validate(resp, PATIENT_RECOMMENDATION_SCHEMA, "pid1")
    assert exc.value.finish_reason == "length"
    assert exc.value.completion_tokens == 8192
    assert exc.value.repetition > 0.5


def test_parse_passes_when_stop_even_if_long():
    # finish=stop + 长内容 → 不抛退化（正常完成）
    client = _mk_client()
    resp = _mk_response(_valid_result_json(), finish_reason="stop", comp_tok=1200)
    parsed = client._parse_and_validate(resp, PATIENT_RECOMMENDATION_SCHEMA, "pid1")
    assert parsed["guideline_results"][0]["guideline"] == "CSCO"


def test_parse_length_low_repetition_not_flagged():
    # finish=length 但低重复 → 不判退化（可能真截断，留给上层升档判断）
    client = _mk_client()
    diverse = "".join(f"第{i}句完全不同的多样化内容。" for i in range(40))
    content = json.dumps({
        "guideline_results": [{
            "guideline": "NCCN", "guideline_version": "2026 版",
            "recommendation": diverse, "evidence_level": "Category 1",
            "source_file": "nccn.md",
            "retrieval_sources": [{"source_file": "nccn.md", "score": 0.9}],
        }],
        "consensus": ["c"], "differences": ["d"],
    }, ensure_ascii=False)
    resp = _mk_response(content, finish_reason="length", comp_tok=4096)
    parsed = client._parse_and_validate(resp, PATIENT_RECOMMENDATION_SCHEMA, "pid1")
    assert parsed is not None  # 未抛退化


# ── _llm_with_degeneration_fallback 三档降级链 ───────────────────────────────

def _ok_result() -> tuple:
    return ({
        "guideline_results": [{
            "guideline": "CSCO", "guideline_version": "2026 版",
            "recommendation": "推荐内容 [1] [2]", "evidence_level": "1A类",
            "source_file": "csco.md",
            "retrieval_sources": [{"source_file": "csco.md", "score": 0.9}],
        }],
        "consensus": ["c"], "differences": ["d"],
    }, 0.8, "ok")


@pytest.mark.asyncio
async def test_fallback_attempt1_success():
    llm = MagicMock()
    llm.complete_structured_with_feedback = AsyncMock(return_value=_ok_result())
    hits = [_mk_hit("CSCO/a.md", 0.9)]
    r, s, st, meta = await _llm_with_degeneration_fallback(llm, {"patient_id": "p"}, hits, "p")
    assert meta["attempt"] == 1
    assert meta["mode"] == "strict"
    assert st == "ok"
    assert llm.complete_structured_with_feedback.await_count == 1


@pytest.mark.asyncio
async def test_fallback_attempt1_degenerate_to_attempt2():
    llm = MagicMock()
    deg = DegenerationError("p", "length", 8192, 0.9, "preview")
    llm.complete_structured_with_feedback = AsyncMock(
        side_effect=[deg, _ok_result()]  # 档1退化，档2成功
    )
    hits = [_mk_hit(f"CSCO/a{i}.md", 0.9 - i * 0.01) for i in range(6)]
    r, s, st, meta = await _llm_with_degeneration_fallback(llm, {"patient_id": "p"}, hits, "p")
    assert meta["attempt"] == 2
    assert meta["penalty"] == 0.3
    assert llm.complete_structured_with_feedback.await_count == 2


@pytest.mark.asyncio
async def test_fallback_all_three_degenerate_raises():
    llm = MagicMock()
    deg = DegenerationError("p", "length", 8192, 0.9, "preview")
    llm.complete_structured_with_feedback = AsyncMock(side_effect=[deg, deg, deg])
    hits = [_mk_hit(f"CSCO/a{i}.md", 0.9 - i * 0.01) for i in range(6)]
    with pytest.raises(DegenerationError):
        await _llm_with_degeneration_fallback(llm, {"patient_id": "p"}, hits, "p")
    assert llm.complete_structured_with_feedback.await_count == 3


@pytest.mark.asyncio
async def test_fallback_attempt3_uses_json_object_mode():
    # 档1档2退化，档3 json_object 成功 → attempt=3 mode=json_object
    llm = MagicMock()
    deg = DegenerationError("p", "length", 8192, 0.9, "preview")
    llm.complete_structured_with_feedback = AsyncMock(
        side_effect=[deg, deg, _ok_result()]
    )
    hits = [_mk_hit(f"CSCO/a{i}.md", 0.9 - i * 0.01) for i in range(6)]
    r, s, st, meta = await _llm_with_degeneration_fallback(llm, {"patient_id": "p"}, hits, "p")
    assert meta["attempt"] == 3
    assert meta["mode"] == "json_object"
