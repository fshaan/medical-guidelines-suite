"""Tests for AsyncLLMClient: happy path / 3 retry paths / sem / LLMFailure."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scripts.llm_client import (
    AsyncLLMClient,
    LLMFailure,
    LLMProfile,
    PATIENT_RECOMMENDATION_SCHEMA,
    SchemaError,
    _DEFAULT_FEEDBACK_TEMPLATE,
    _EVIDENCE_LEVEL_ENUM,
)

_MOCK_RESPONSE_PATH = Path(__file__).parent / "fixtures" / "mock_llm_response.json"
VALID_RESPONSE = json.loads(_MOCK_RESPONSE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def profile():
    return LLMProfile(
        name="test", base_url="http://test.local/v1", model="test-model",
        api_key_env="TEST_API_KEY", timeout_s=10, structured_mode="json_schema",
        concurrency=5,
    )


@pytest.fixture
def mock_http():
    return AsyncMock(spec=httpx.AsyncClient)


def _mk_resp(*, status=200, content=None, headers=None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.request = MagicMock()
    body_content = content if content is not None else json.dumps(VALID_RESPONSE)
    resp.json = MagicMock(return_value={
        "choices": [{"message": {"content": body_content}}]
    })
    resp.raise_for_status = MagicMock(return_value=None)
    return resp


@pytest.mark.asyncio
async def test_complete_structured_happy_path(profile, mock_http, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-test")
    mock_http.post.return_value = _mk_resp()
    client = AsyncLLMClient(profile, http=mock_http)
    result = await client.complete_structured(
        [{"role": "user", "content": "test"}],
        PATIENT_RECOMMENDATION_SCHEMA,
        patient_id="p001",
    )
    assert result == VALID_RESPONSE
    assert mock_http.post.await_count == 1


@pytest.mark.asyncio
async def test_response_format_strict_schema_payload(profile, mock_http):
    mock_http.post.return_value = _mk_resp()
    client = AsyncLLMClient(profile, http=mock_http)
    await client.complete_structured(
        [{"role": "user", "content": "q"}], PATIENT_RECOMMENDATION_SCHEMA,
    )
    call_kwargs = mock_http.post.await_args.kwargs
    payload = call_kwargs["json"]
    rf = payload["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "patient_recommendation"
    assert rf["json_schema"]["schema"] is PATIENT_RECOMMENDATION_SCHEMA


@pytest.mark.asyncio
async def test_response_format_json_object_mode(mock_http):
    profile = LLMProfile(name="ds", base_url="http://ds/v1", model="ds-chat",
                         structured_mode="json_object")
    mock_http.post.return_value = _mk_resp()
    client = AsyncLLMClient(profile, http=mock_http)
    await client.complete_structured(
        [{"role": "user", "content": "q"}], PATIENT_RECOMMENDATION_SCHEMA,
    )
    rf = mock_http.post.await_args.kwargs["json"]["response_format"]
    assert rf == {"type": "json_object"}


def test_evidence_level_enum_completeness():
    assert len(_EVIDENCE_LEVEL_ENUM) == 27
    assert {"I,A", "II,B", "III,C", "IV,D"}.issubset(set(_EVIDENCE_LEVEL_ENUM))
    must_have = {"1A类", "I级推荐", "Category 1", "Category 3",
                 "强推荐", "Strong", "弱推荐", "Weak", "N/A", "不适用"}
    assert must_have.issubset(set(_EVIDENCE_LEVEL_ENUM))
    enum_in_schema = PATIENT_RECOMMENDATION_SCHEMA["properties"]["guideline_results"][
        "items"]["properties"]["evidence_level"]["enum"]
    assert enum_in_schema == _EVIDENCE_LEVEL_ENUM


def test_schema_rejects_additional_properties():
    import jsonschema
    extra_at_root = dict(VALID_RESPONSE)
    extra_at_root["hallucinated_field"] = "ghost"
    with pytest.raises(jsonschema.ValidationError, match="hallucinated_field"):
        jsonschema.validate(extra_at_root, PATIENT_RECOMMENDATION_SCHEMA)

    extra_in_item = json.loads(json.dumps(VALID_RESPONSE))
    extra_in_item["guideline_results"][0]["bogus"] = "x"
    with pytest.raises(jsonschema.ValidationError, match="bogus"):
        jsonschema.validate(extra_in_item, PATIENT_RECOMMENDATION_SCHEMA)

    extra_in_source = json.loads(json.dumps(VALID_RESPONSE))
    extra_in_source["guideline_results"][0]["retrieval_sources"][0]["fake"] = 1
    with pytest.raises(jsonschema.ValidationError, match="fake"):
        jsonschema.validate(extra_in_source, PATIENT_RECOMMENDATION_SCHEMA)


def test_schema_required_fields():
    import jsonschema
    bad = {
        "guideline_results": [{
            "guideline": "CSCO",
            "guideline_version": "2024",
            "evidence_level": "1A类",
            "source_file": "x.md",
            "retrieval_sources": [{"source_file": "x.md", "score": 0.5}],
        }],
        "consensus": [],
        "differences": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, PATIENT_RECOMMENDATION_SCHEMA)


@pytest.mark.asyncio
@patch("scripts.llm_client.asyncio.sleep", new_callable=AsyncMock)
async def test_retry_on_429(mock_sleep, profile, mock_http):
    mock_http.post.side_effect = [_mk_resp(status=429), _mk_resp(status=429), _mk_resp()]
    client = AsyncLLMClient(profile, http=mock_http)
    result = await client.complete_structured(
        [{"role": "user", "content": "q"}], PATIENT_RECOMMENDATION_SCHEMA,
    )
    assert result == VALID_RESPONSE
    assert mock_http.post.await_count == 3
    assert [c.args[0] for c in mock_sleep.await_args_list] == [1.0, 2.0]


@pytest.mark.asyncio
@patch("scripts.llm_client.asyncio.sleep", new_callable=AsyncMock)
async def test_retry_on_5xx(mock_sleep, profile, mock_http):
    mock_http.post.side_effect = [_mk_resp(status=503), _mk_resp()]
    client = AsyncLLMClient(profile, http=mock_http)
    await client.complete_structured(
        [{"role": "user", "content": "q"}], PATIENT_RECOMMENDATION_SCHEMA,
    )
    assert mock_http.post.await_count == 2
    assert [c.args[0] for c in mock_sleep.await_args_list] == [1.0]


@pytest.mark.asyncio
@patch("scripts.llm_client.asyncio.sleep", new_callable=AsyncMock)
async def test_retry_on_timeout_exhausts(mock_sleep, profile, mock_http):
    mock_http.post.side_effect = [httpx.TimeoutException("t")] * 4
    client = AsyncLLMClient(profile, http=mock_http)
    with pytest.raises(LLMFailure) as exc_info:
        await client.complete_structured(
            [{"role": "user", "content": "q"}], PATIENT_RECOMMENDATION_SCHEMA,
            patient_id="p001",
        )
    assert exc_info.value.stage == "transport"
    assert exc_info.value.patient_id == "p001"
    assert mock_http.post.await_count == 4
    assert [c.args[0] for c in mock_sleep.await_args_list] == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
@patch("scripts.llm_client.asyncio.sleep", new_callable=AsyncMock)
async def test_schema_retry_once_succeeds(mock_sleep, profile, mock_http):
    mock_http.post.side_effect = [
        _mk_resp(content="not json{"),
        _mk_resp(),
    ]
    client = AsyncLLMClient(profile, http=mock_http)
    result = await client.complete_structured(
        [{"role": "user", "content": "q"}], PATIENT_RECOMMENDATION_SCHEMA,
    )
    assert result == VALID_RESPONSE
    assert mock_http.post.await_count == 2
    assert mock_sleep.await_count == 0


@pytest.mark.asyncio
@patch("scripts.llm_client.asyncio.sleep", new_callable=AsyncMock)
async def test_schema_retry_exhausts(mock_sleep, profile, mock_http):
    mock_http.post.side_effect = [
        _mk_resp(content="not json{"),
        _mk_resp(content="still not json{"),
    ]
    client = AsyncLLMClient(profile, http=mock_http)
    with pytest.raises(LLMFailure) as exc_info:
        await client.complete_structured(
            [{"role": "user", "content": "q"}], PATIENT_RECOMMENDATION_SCHEMA,
            patient_id="p002",
        )
    assert exc_info.value.stage == "schema"
    assert exc_info.value.patient_id == "p002"
    assert mock_http.post.await_count == 2


@pytest.mark.asyncio
async def test_feedback_skip_when_first_call_meets_threshold(profile, mock_http):
    mock_http.post.return_value = _mk_resp()
    client = AsyncLLMClient(profile, http=mock_http)
    result, score, status = await client.complete_structured_with_feedback(
        [{"role": "user", "content": "q"}], PATIENT_RECOMMENDATION_SCHEMA,
        feedback_check=lambda r: 0.8, threshold=0.5,
    )
    assert score == 0.8
    assert status == "ok"
    assert mock_http.post.await_count == 1


@pytest.mark.asyncio
async def test_feedback_retry_recovers_above_threshold(profile, mock_http):
    mock_http.post.return_value = _mk_resp()
    client = AsyncLLMClient(profile, http=mock_http)
    scores = iter([0.3, 0.7])

    def check(r):
        return next(scores)

    result, score, status = await client.complete_structured_with_feedback(
        [{"role": "user", "content": "q"}], PATIENT_RECOMMENDATION_SCHEMA,
        feedback_check=check, threshold=0.5,
    )
    assert score == 0.7
    assert status == "ok"
    assert mock_http.post.await_count == 2
    second_call_msgs = mock_http.post.await_args_list[1].kwargs["json"]["messages"]
    feedback_msg = second_call_msgs[-1]["content"]
    assert "0.30" in feedback_msg
    assert "0.50" in feedback_msg
    assert second_call_msgs[-1]["role"] == "user"


@pytest.mark.asyncio
async def test_feedback_retry_accepts_partial(profile, mock_http):
    mock_http.post.return_value = _mk_resp()
    client = AsyncLLMClient(profile, http=mock_http)
    scores = iter([0.3, 0.4])
    result, score, status = await client.complete_structured_with_feedback(
        [{"role": "user", "content": "q"}], PATIENT_RECOMMENDATION_SCHEMA,
        feedback_check=lambda r: next(scores), threshold=0.5,
    )
    assert score == 0.4
    assert status == "partial"
    assert mock_http.post.await_count == 2


def test_llm_failure_carries_patient_id_and_stage():
    err = LLMFailure("p001", ValueError("boom"), "transport")
    assert err.patient_id == "p001"
    assert err.stage == "transport"
    assert isinstance(err.last_error, ValueError)
    s = str(err)
    assert "patient=p001" in s
    assert "stage=transport" in s


@pytest.mark.asyncio
async def test_semaphore_limits_in_flight(profile, mock_http):
    in_flight = {"current": 0, "peak": 0}

    async def slow_post(*args, **kwargs):
        in_flight["current"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["current"])
        await asyncio.sleep(0.02)
        in_flight["current"] -= 1
        return _mk_resp()

    mock_http.post = AsyncMock(side_effect=slow_post)
    sem = asyncio.Semaphore(2)
    client = AsyncLLMClient(profile, http=mock_http, semaphore=sem)
    await asyncio.gather(*[
        client.complete_structured(
            [{"role": "user", "content": f"q{i}"}],
            PATIENT_RECOMMENDATION_SCHEMA,
        )
        for i in range(5)
    ])
    assert in_flight["peak"] <= 2


@pytest.mark.asyncio
@patch("scripts.llm_client.asyncio.sleep", new_callable=AsyncMock)
async def test_4xx_non_429_fails_immediately(mock_sleep, profile, mock_http):
    mock_http.post.return_value = _mk_resp(status=400)
    client = AsyncLLMClient(profile, http=mock_http)
    with pytest.raises(LLMFailure) as exc_info:
        await client.complete_structured(
            [{"role": "user", "content": "q"}], PATIENT_RECOMMENDATION_SCHEMA,
            patient_id="p003",
        )
    assert exc_info.value.stage == "transport"
    assert mock_http.post.await_count == 1
    assert mock_sleep.await_count == 0


@pytest.mark.asyncio
async def test_api_key_resolved_lazily(profile, mock_http, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-secret-xyz")
    mock_http.post.return_value = _mk_resp()
    client = AsyncLLMClient(profile, http=mock_http)
    assert "sk-secret-xyz" not in repr(profile)
    await client.complete_structured(
        [{"role": "user", "content": "q"}], PATIENT_RECOMMENDATION_SCHEMA,
    )
    headers = mock_http.post.await_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer sk-secret-xyz"


@pytest.mark.asyncio
@patch("scripts.llm_client.asyncio.sleep", new_callable=AsyncMock)
async def test_connect_error_retried_and_wrapped(mock_sleep, profile, mock_http):
    mock_http.post.side_effect = [httpx.ConnectError("conn reset")] * 4
    client = AsyncLLMClient(profile, http=mock_http)
    with pytest.raises(LLMFailure) as exc_info:
        await client.complete_structured(
            [{"role": "user", "content": "q"}], PATIENT_RECOMMENDATION_SCHEMA,
            patient_id="p004",
        )
    assert exc_info.value.stage == "transport"
    assert exc_info.value.patient_id == "p004"
    assert isinstance(exc_info.value.last_error, httpx.ConnectError)
    assert mock_http.post.await_count == 4


@pytest.mark.asyncio
async def test_timeout_applied_from_profile(profile, mock_http):
    mock_http.post.return_value = _mk_resp()
    client = AsyncLLMClient(profile, http=mock_http)
    await client.complete_structured(
        [{"role": "user", "content": "q"}], PATIENT_RECOMMENDATION_SCHEMA,
    )
    assert mock_http.post.await_args.kwargs["timeout"] == profile.timeout_s


@pytest.mark.asyncio
async def test_api_key_missing_no_auth_header(profile, mock_http, monkeypatch):
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    mock_http.post.return_value = _mk_resp()
    client = AsyncLLMClient(profile, http=mock_http)
    await client.complete_structured(
        [{"role": "user", "content": "q"}], PATIENT_RECOMMENDATION_SCHEMA,
    )
    headers = mock_http.post.await_args.kwargs["headers"]
    assert "Authorization" not in headers
