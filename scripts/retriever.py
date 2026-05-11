"""QMD hybrid retrieval engine service wrapper.

Provides QMDService context manager that manages the QMD HTTP MCP Server
lifecycle. Exposes query() and search() methods.

Protocol: QMD uses MCP Streamable HTTP. Each session requires:
  1. POST /mcp with Accept: application/json, text/event-stream + initialize payload
     → response header Mcp-Session-Id: <uuid>
  2. Subsequent requests include Mcp-Session-Id header.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time

import requests


class QMDStartupError(Exception):
    """QMD service failed to start."""


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

    def __init__(self, port: int | None = None):
        self.port = port or int(os.environ.get("QMD_PORT", "8181"))
        self.process: subprocess.Popen | None = None
        self.base_url = f"http://localhost:{self.port}/mcp"
        self._session_id: str | None = None

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
