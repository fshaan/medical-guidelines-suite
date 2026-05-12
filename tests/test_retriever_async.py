"""Tests for AsyncQMDService context manager and async query API.

Coverage: lifecycle / port conflict / startup failure rollback /
Semaphore gating / session-invalid retry / concurrent gather ordering.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mk_http_response(*, status=200, session_id="s1", body=None):
    """Helper: construct httpx.Response mock."""
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"mcp-session-id": session_id} if session_id else {}
    resp.json = MagicMock(return_value=body if body is not None else {"result": {}})
    resp.raise_for_status = MagicMock(return_value=None)
    return resp


def _empty_qmd_result():
    """Empty QMD MCP response body (_parse_mcp_response returns [])."""
    return {"result": {"structuredContent": {"results": []}}}


@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.httpx.AsyncClient")
async def test_async_qmd_service_lifecycle(mock_client_cls, mock_popen):
    """B-1: __aenter__ starts Popen + captures session_id; __aexit__ terminate."""
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 12345
    mock_popen.return_value = proc

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mk_http_response())
    mock_client.aclose = AsyncMock()
    mock_client_cls.return_value = mock_client

    from scripts.retriever import AsyncQMDService

    async with AsyncQMDService(port=9999) as svc:
        assert svc.process is not None
        assert svc.port == 9999
        assert svc._session_id == "s1"

    proc.terminate.assert_called_once()
    mock_client.aclose.assert_awaited()


@pytest.mark.asyncio
@patch("scripts.retriever.socket.socket")
async def test_async_qmd_detects_port_conflict(mock_socket_cls):
    """B-2: port occupied -> QMDStartupError, Popen not called."""
    mock_sock = MagicMock()
    mock_sock.connect_ex.return_value = 0
    mock_sock.__enter__ = lambda s: mock_sock
    mock_sock.__exit__ = MagicMock(return_value=False)
    mock_socket_cls.return_value = mock_sock

    from scripts.retriever import AsyncQMDService, QMDStartupError
    with pytest.raises(QMDStartupError, match="already in use"):
        async with AsyncQMDService(port=9999):
            pass


@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.httpx.AsyncClient")
async def test_async_qmd_startup_failure_kills_process(mock_client_cls, mock_popen):
    """B-3: _wait_for_ready_async raises -> process killed, http_client aclosed."""
    proc = MagicMock()
    proc.poll.return_value = None
    mock_popen.return_value = proc

    mock_client = AsyncMock()
    mock_client.aclose = AsyncMock()
    mock_client_cls.return_value = mock_client

    from scripts.retriever import AsyncQMDService, QMDStartupError
    svc = AsyncQMDService(port=9999, timeout_s=0.01)
    with patch.object(AsyncQMDService, "_wait_for_ready_async",
                      new=AsyncMock(side_effect=QMDStartupError("timeout"))):
        with pytest.raises(QMDStartupError, match="timeout"):
            await svc.__aenter__()

    proc.kill.assert_called_once()
    mock_client.aclose.assert_awaited()


@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.httpx.AsyncClient")
async def test_async_qmd_semaphore_limits_in_flight(mock_client_cls, mock_popen):
    """B-4: inject Semaphore(2), 5 concurrent queries -> in-flight <= 2."""
    proc = MagicMock()
    proc.poll.return_value = None
    mock_popen.return_value = proc

    in_flight = {"current": 0, "peak": 0}

    async def slow_post(*args, **kwargs):
        body = kwargs.get("json", {})
        if body.get("method") == "initialize":
            return _mk_http_response()
        in_flight["current"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["current"])
        await asyncio.sleep(0.05)
        in_flight["current"] -= 1
        return _mk_http_response(body=_empty_qmd_result())

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=slow_post)
    mock_client.aclose = AsyncMock()
    mock_client_cls.return_value = mock_client

    from scripts.retriever import AsyncQMDService

    sem = asyncio.Semaphore(2)
    async with AsyncQMDService(port=9999, semaphore=sem) as svc:
        results = await asyncio.gather(*[svc.query(f"q{i}") for i in range(5)])

    assert len(results) == 5
    assert in_flight["peak"] <= 2, f"in-flight peak {in_flight['peak']} > 2"


@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.httpx.AsyncClient")
async def test_async_qmd_session_invalid_retries_once(mock_client_cls, mock_popen):
    """B-5: first tools/call HTTP 400 -> re-initialize -> second succeeds."""
    proc = MagicMock()
    proc.poll.return_value = None
    mock_popen.return_value = proc

    posts = [
        _mk_http_response(session_id="s1"),
        _mk_http_response(status=400, session_id=None,
                          body={"error": {"code": -32000, "message": "session expired"}}),
        _mk_http_response(session_id="s2"),
        _mk_http_response(session_id="s2", body=_empty_qmd_result()),
    ]

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=posts)
    mock_client.aclose = AsyncMock()
    mock_client_cls.return_value = mock_client

    from scripts.retriever import AsyncQMDService

    async with AsyncQMDService(port=9999) as svc:
        results = await svc.query("test")

    assert results == []
    assert mock_client.post.await_count == 4
    reinit_call = mock_client.post.await_args_list[2]
    assert reinit_call.kwargs.get("json", {}).get("method") == "initialize"


@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.httpx.AsyncClient")
async def test_async_qmd_concurrent_query_ordering(mock_client_cls, mock_popen):
    """B-6: asyncio.gather(q1, q2, q3) preserves order."""
    proc = MagicMock()
    proc.poll.return_value = None
    mock_popen.return_value = proc

    async def echo_post(*args, **kwargs):
        body = kwargs.get("json", {})
        if body.get("method") == "initialize":
            return _mk_http_response()
        q = body["params"]["arguments"]["intent"]
        return _mk_http_response(body={
            "result": {"structuredContent": {"results": [
                {"snippet": q, "file": "f.md", "score": 1.0, "context": ""}
            ]}}
        })

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=echo_post)
    mock_client.aclose = AsyncMock()
    mock_client_cls.return_value = mock_client

    from scripts.retriever import AsyncQMDService
    async with AsyncQMDService(port=9999) as svc:
        r1, r2, r3 = await asyncio.gather(svc.query("q1"), svc.query("q2"), svc.query("q3"))

    assert r1[0]["content"] == "q1"
    assert r2[0]["content"] == "q2"
    assert r3[0]["content"] == "q3"


@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.httpx.AsyncClient")
async def test_async_qmd_exit_handles_crashed_process(mock_client_cls, mock_popen):
    """B-7: __aexit__ with crashed process does not call terminate."""
    proc = MagicMock()
    proc.poll.side_effect = [None, None, 1]
    mock_popen.return_value = proc

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mk_http_response())
    mock_client.aclose = AsyncMock()
    mock_client_cls.return_value = mock_client

    from scripts.retriever import AsyncQMDService
    async with AsyncQMDService(port=9999):
        pass

    proc.terminate.assert_not_called()


@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.httpx.AsyncClient")
async def test_async_qmd_200_no_session_header_does_not_retry(
    mock_client_cls, mock_popen
):
    """BL-01 回归：tools/call 返回 200 但响应缺 mcp-session-id header 时，
    必须不触发 reinit（MCP Streamable HTTP 协议仅 initialize 响应回带该 header）。
    """
    proc = MagicMock()
    proc.poll.return_value = None
    mock_popen.return_value = proc

    posts = [
        _mk_http_response(session_id="s1"),  # initialize
        _mk_http_response(  # tools/call 200 OK，但响应不带 session header
            status=200, session_id=None, body=_empty_qmd_result()
        ),
    ]

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=posts)
    mock_client.aclose = AsyncMock()
    mock_client_cls.return_value = mock_client

    from scripts.retriever import AsyncQMDService

    async with AsyncQMDService(port=9999) as svc:
        results = await svc.query("test")

    assert results == []
    # 期望仅 2 次 HTTP：1 initialize + 1 tools/call，没有 reinit + retry
    assert mock_client.post.await_count == 2, (
        f"Expected 2 HTTP calls (no reinit), got {mock_client.post.await_count}"
    )


@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.httpx.AsyncClient")
async def test_async_qmd_aclose_error_still_kills_process(
    mock_client_cls, mock_popen
):
    """BL-02 回归：__aexit__ 内 aclose() 抛异常时，process.terminate 仍必须执行；
    aclose 异常在 caller 无原始异常时透传出去。
    """
    proc = MagicMock()
    proc.poll.return_value = None
    mock_popen.return_value = proc

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mk_http_response())
    mock_client.aclose = AsyncMock(side_effect=RuntimeError("loop closed"))
    mock_client_cls.return_value = mock_client

    from scripts.retriever import AsyncQMDService

    svc = AsyncQMDService(port=9999)
    await svc.__aenter__()
    with pytest.raises(RuntimeError, match="loop closed"):
        await svc.__aexit__(None, None, None)

    # aclose 抛了，process.terminate 仍执行，subprocess 没泄漏
    proc.terminate.assert_called_once()


@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.httpx.AsyncClient")
async def test_async_qmd_aclose_error_does_not_overwrite_caller_exception(
    mock_client_cls, mock_popen
):
    """BL-02 回归：caller 有原始异常时，aclose 错误必须被吞掉避免覆盖。"""
    proc = MagicMock()
    proc.poll.return_value = None
    mock_popen.return_value = proc

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mk_http_response())
    mock_client.aclose = AsyncMock(side_effect=RuntimeError("aclose failed"))
    mock_client_cls.return_value = mock_client

    from scripts.retriever import AsyncQMDService

    with pytest.raises(ValueError, match="business error"):
        async with AsyncQMDService(port=9999):
            raise ValueError("business error")

    proc.terminate.assert_called_once()
