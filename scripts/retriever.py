"""QMD hybrid retrieval engine service wrapper.

Provides QMDService context manager that manages the QMD HTTP MCP Server
lifecycle. Exposes query() and search() methods.

Protocol: QMD uses MCP Streamable HTTP. Each session requires:
  1. POST /mcp with Accept: application/json, text/event-stream + initialize payload
     → response header Mcp-Session-Id: <uuid>
  2. Subsequent requests include Mcp-Session-Id header.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import time
from typing import Optional
from pathlib import Path

import httpx
import requests


class QMDStartupError(Exception):
    """QMD service failed to start."""


class QMDQueryError(Exception):
    """QMD tools/call request failed after exhausting retries (timeout/transport)."""


_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

_INIT_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "medical-guidelines-suite", "version": "3.0.0"},
    },
}


class QMDService:
    """Manage QMD HTTP MCP Server lifecycle.

    Usage:
        with QMDService(port=8181) as qmd:
            results = qmd.query("gastric cancer treatment")
    """

    def __init__(self, port: Optional[int] = None):
        self.port = port or int(os.environ.get("QMD_PORT", "8181"))
        self.process: Optional[subprocess.Popen] = None
        self.base_url = f"http://localhost:{self.port}/mcp"
        self._session_id: Optional[str] = None

    @property
    def _session_headers(self) -> dict:
        if not self._session_id:
            raise RuntimeError("QMD session not initialized")
        return {**_MCP_HEADERS, "Mcp-Session-Id": self._session_id}

    def __enter__(self) -> "QMDService":
        self._check_port_available()
        self.process = subprocess.Popen(
            ["qmd", "mcp", "--http", "--port", str(self.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            self._wait_for_ready(timeout=30)
        except Exception:
            if self.process and self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=3)
            self.process = None
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._session_id = None
        if self.process is None:
            return
        if self.process.poll() is not None:
            self.process = None
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        self.process = None

    def query(
        self, text: str, top_k: int = 10, min_score: float = 0.3
    ) -> list[dict]:
        """Hybrid query (BM25 + vector + LLM reranking).

        Returns:
            [{"content": str, "path": str, "score": float, "context": str}]
        """
        resp = requests.post(
            self.base_url,
            headers=self._session_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "query",
                    "arguments": {
                        "searches": [
                            {"type": "lex", "query": text},
                            {"type": "vec", "query": text},
                        ],
                        "intent": text,
                        "limit": top_k,
                        "minScore": min_score,
                    },
                },
            },
            timeout=60,
        )
        resp.raise_for_status()
        return self._parse_mcp_response(resp.json())

    def search(self, text: str, top_k: int = 10) -> list[dict]:
        """Pure BM25 keyword search (no LLM, faster).

        Returns:
            [{"content": str, "path": str, "score": float, "context": str}]
        """
        resp = requests.post(
            self.base_url,
            headers=self._session_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "query",
                    "arguments": {
                        "searches": [{"type": "lex", "query": text}],
                        "intent": text,
                        "limit": top_k,
                    },
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        return self._parse_mcp_response(resp.json())

    def _check_port_available(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("localhost", self.port)) == 0:
                raise QMDStartupError(
                    f"Port {self.port} already in use. "
                    f"Kill the existing process or set QMD_PORT env var."
                )

    def _wait_for_ready(self, timeout: int = 30) -> None:
        """Poll until QMD HTTP server accepts initialize, then store session ID."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise QMDStartupError(
                    f"QMD exited during startup (exit code: {self.process.returncode})"
                )
            try:
                resp = requests.post(
                    self.base_url,
                    headers=_MCP_HEADERS,
                    json=_INIT_PAYLOAD,
                    timeout=5,
                )
                if resp.status_code == 200:
                    if self.process.poll() is not None:
                        raise QMDStartupError("QMD exited immediately after health check")
                    self._session_id = resp.headers.get("mcp-session-id")
                    return
            except requests.ConnectionError:
                pass
            time.sleep(0.5)
        raise QMDStartupError(f"QMD not ready after {timeout}s")

    @staticmethod
    def _parse_mcp_response(data: dict) -> list[dict]:
        """Parse MCP tools/call response into list of result dicts."""
        if "error" in data:
            err = data["error"]
            raise RuntimeError(
                f"QMD error {err.get('code', '?')}: {err.get('message', '')}"
            )
        result = data.get("result", {})

        # Prefer structuredContent.results (machine-readable)
        structured = result.get("structuredContent", {})
        raw_results = structured.get("results")
        if raw_results is not None:
            return [
                {
                    "content": r.get("snippet", ""),
                    "path": r.get("file", ""),
                    "score": r.get("score", 0.0),
                    "context": r.get("context", ""),
                }
                for r in raw_results
            ]

        # Fallback: text block (older qmd versions)
        for block in result.get("content", []):
            if block.get("type") == "text":
                return [{"content": block["text"], "path": "", "score": 0.0, "context": ""}]
        return []


class AsyncQMDService:
    """Async QMD MCP client (httpx-based) for concurrent retrieval.

    Phase 1 (RTR-01..04): 第一段异步代码，与同步 QMDService 共存。
    Phase 3 pipeline.py 通过注入式 semaphore/http_client 共享并发预算。

    Lifecycle contracts (IN-03):
    - Default (no http_client injected): __aenter__ creates an httpx.AsyncClient
      and __aexit__ closes it. self._owns_http = True.
    - Injected http_client: the client is borrowed; __aexit__ does NOT aclose
      it. Caller owns the lifecycle and must close/dispose the client externally
      after all AsyncQMDService usage completes.
    - Injected semaphore: caller-owned; AsyncQMDService never touches its state
      outside `async with self._sem`.
    """

    def __init__(
        self,
        port: Optional[int] = None,
        *,
        timeout_s: float = 60.0,
        semaphore: Optional[asyncio.Semaphore] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.port = port or int(os.environ.get("QMD_PORT", "8181"))
        self.base_url = f"http://localhost:{self.port}/mcp"
        self._timeout = timeout_s
        self._sem = semaphore or asyncio.Semaphore(8)   # D-03 默认 8（准入控制，见下）
        # 2026-07-02 实测发现：qmd MCP server 的单个 session 不支持真正并发的
        # tools/call——两个并发请求打到同一 session 时，一个正常返回，另一个
        # 永远收不到响应（不是变慢，是卡死到读超时）。用两个独立 AsyncQMDService
        # 实例（各自独立 session/进程）并发反而完全没问题，证实这是 session 级
        # 限制而不是本类的语义 bug。self._sem 仍按调用方配置的并发数做准入控制
        # （限制同时等待的调用数量），但真正打到 server 的 HTTP 往返用这把锁
        # 强制串行，避免同一 session 上出现第二个在途请求。
        self._session_lock = asyncio.Lock()
        self._http = http_client
        self._owns_http = http_client is None
        self.process: Optional[subprocess.Popen] = None
        self._session_id: Optional[str] = None

    @property
    def _session_headers(self) -> dict:
        if not self._session_id:
            raise RuntimeError("QMD session not initialized")
        return {**_MCP_HEADERS, "Mcp-Session-Id": self._session_id}

    def _check_port_available(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("localhost", self.port)) == 0:
                raise QMDStartupError(
                    f"Port {self.port} already in use. "
                    f"Kill the existing process or set QMD_PORT env var."
                )

    async def __aenter__(self) -> "AsyncQMDService":
        self._check_port_available()
        qmd_node = os.environ.get("QMD_NODE_BIN")
        if qmd_node:
            qmd_js = Path(__file__).resolve().parent.parent / "node_modules" / "@tobilu" / "qmd" / "dist" / "cli" / "qmd.js"
            if not qmd_js.exists():
                qmd_js = Path(os.environ.get("QMD_NODE_MODULE", "~/.local/lib/node_modules/@tobilu/qmd/dist/cli/qmd.js")).expanduser()
            self.process = subprocess.Popen(
                [qmd_node, str(qmd_js), "mcp", "--http", "--port", str(self.port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            self.process = subprocess.Popen(
                ["qmd", "mcp", "--http", "--port", str(self.port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if self._owns_http:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        try:
            await self._wait_for_ready_async(timeout=30)
        except Exception:
            if self.process and self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=3)
            self.process = None
            if self._owns_http and self._http is not None:
                await self._http.aclose()
                self._http = None
            raise
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        # BL-02 修复：aclose() 和 process 清理彼此独立兜底——
        # aclose 抛异常不能吞掉 subprocess 清理，否则测试套件反复运行会
        # 积累僵尸 QMD 进程；caller 已有原始异常时不要再覆盖。
        self._session_id = None
        aclose_err: Optional[Exception] = None
        try:
            if self._owns_http and self._http is not None:
                await self._http.aclose()
        except Exception as e:  # noqa: BLE001 — 故意宽 catch，独立兜底
            aclose_err = e
        finally:
            self._http = None

        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            except OSError:
                pass  # 进程已不在/权限错误：放弃，不让清理失败覆盖业务异常
        self.process = None

        # 仅在 caller 没有原始异常时把 aclose 错误抛出去；否则吞掉避免覆盖
        if aclose_err is not None and exc_type is None:
            raise aclose_err

    async def _wait_for_ready_async(self, timeout: int = 30) -> None:
        """Poll QMD HTTP server async; store mcp-session-id from first 200."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise QMDStartupError(
                    f"QMD exited during startup (exit code: {self.process.returncode})"
                )
            try:
                resp = await self._http.post(
                    self.base_url,
                    headers=_MCP_HEADERS,
                    json=_INIT_PAYLOAD,
                    timeout=5,
                )
                if resp.status_code == 200:
                    if self.process.poll() is not None:
                        raise QMDStartupError("QMD exited immediately after health check")
                    self._session_id = resp.headers.get("mcp-session-id")
                    return
            except httpx.TransportError:
                # 涵盖 ConnectError / ReadTimeout / RemoteProtocolError /
                # ConnectTimeout / WriteTimeout / PoolTimeout 等所有传输层失败。
                # QMD 启动慢时常见「先 accept 再卡在 ready 前」，会抛
                # ReadTimeout/RemoteProtocolError，仅 catch ConnectError 会让
                # 这些 transient 错误绕过 30s deadline 保护。
                pass
            await asyncio.sleep(0.5)
        raise QMDStartupError(f"QMD not ready after {timeout}s")

    async def _reinitialize_session(self) -> None:
        """Re-send initialize and capture new mcp-session-id (D-04 retry path)."""
        resp = await self._http.post(
            self.base_url, headers=_MCP_HEADERS, json=_INIT_PAYLOAD, timeout=5,
        )
        resp.raise_for_status()
        self._session_id = resp.headers.get("mcp-session-id")

    async def _post_tools_call(self, payload: dict) -> list[dict]:
        """POST tools/call with one re-initialize retry on session invalidation,
        plus up to 3 attempts with exponential backoff on transport-level errors
        (timeout / connection failure).

        2026-07-02 fix (round 1): a single slow/hung QMD response used to raise
        a bare httpx.RequestError with no retry — that exception type was not
        caught by any except clause in pipeline.py:_run_one_patient, so it
        propagated out and was silently discarded by run_pipeline's
        gather(..., return_exceptions=True) (whose result was never inspected).
        A patient could burn a full 180s read-timeout and vanish with zero
        trace in patients/ or _failed/.

        2026-07-02 fix (round 2, codex 对抗式审查发现): round 1 kept the 180s
        read timeout AND added 3 retries — worst case per query went from
        "silent 180s disappearance" to "543s before QMDQueryError", which is
        worse for the QG-01 wall-time SLA, not better. Real QMD query() calls
        observed in isolation and under light concurrency complete in 0.4-5s
        (see phase3_e2e_test_report_2026-07-02.md 附录); 180s was tuned for
        MCP session _startup_ health-checks (_wait_for_ready_async), not for
        steady-state per-query calls. Read timeout dropped to 30s here (6-10x
        headroom over observed worst case) so a genuinely stuck call fails
        fast enough for --resume to retry the patient instead of blocking the
        whole batch for multiples of 180s.

        2026-07-02 fix (round 3, real E2E run against spark reproduced this
        reliably): even at 30s the real 10-patient run still had EVERY
        patient fail — root cause isolated to two concurrent tools/call
        requests sharing one MCP session: one gets a normal response, the
        other never gets any response at all (full timeout, not "slow").
        Two separate AsyncQMDService instances (separate sessions/processes)
        running concurrently both succeeded fine, proving this is a session-
        level limitation of the qmd server, not a semantic bug in this class.
        The whole retry loop is now wrapped in self._session_lock so at most
        one tools/call HTTP round trip is ever in flight against this
        session at a time — self._sem still gates how many callers may be
        *waiting*, this lock gates how many are *actually talking to qmd*.
        """
        async with self._session_lock:
            last_error: Optional[Exception] = None
            for attempt in range(3):
                try:
                    resp = await self._http.post(
                        self.base_url,
                        headers=self._session_headers,
                        json=payload,
                        timeout=httpx.Timeout(self._timeout, read=30),
                    )
                    # D-04: 仅 HTTP 400 视为 session invalid 触发 reinit。
                    # 不要把"响应缺 mcp-session-id header"当信号——MCP Streamable HTTP
                    # 协议仅 initialize 响应回带该 header，tools/call 正常响应通常不带，
                    # 误判会让每次 query 多发 2 次 HTTP（reinit + retry）。
                    if resp.status_code == 400:
                        await self._reinitialize_session()
                        resp = await self._http.post(
                            self.base_url,
                            headers=self._session_headers,
                            json=payload,
                            timeout=self._timeout,
                        )
                    resp.raise_for_status()
                    return QMDService._parse_mcp_response(resp.json())
                except httpx.RequestError as e:
                    last_error = e
                    if attempt < 2:
                        await asyncio.sleep(1.0 * (2 ** attempt))
                        continue
                    break
            raise QMDQueryError(
                f"QMD tools/call failed after 3 attempts: {last_error}"
            ) from last_error

    async def query(
        self, text: str, top_k: int = 10, min_score: float = 0.3
    ) -> list[dict]:
        """Async hybrid query (BM25 + vector + LLM reranking).

        Semaphore-gated: in-flight requests bounded by the injected semaphore
        (default capacity 8, see __init__ signature). Do not rely on
        asyncio.Semaphore._value externally — it is a CPython implementation
        detail.
        """
        async with self._sem:
            return await self._post_tools_call({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "query",
                    "arguments": {
                        "searches": [
                            {"type": "lex", "query": text},
                            {"type": "vec", "query": text},
                        ],
                        "intent": text,
                        "limit": top_k,
                        "minScore": min_score,
                    },
                },
            })

    async def search(self, text: str, top_k: int = 10) -> list[dict]:
        """Async pure BM25 keyword search (no LLM, faster)."""
        async with self._sem:
            return await self._post_tools_call({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "query",
                    "arguments": {
                        "searches": [{"type": "lex", "query": text}],
                        "intent": text,
                        "limit": top_k,
                    },
                },
            })
