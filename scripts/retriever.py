"""QMD hybrid retrieval engine service wrapper.

Provides QMDService context manager that manages the QMD HTTP MCP Server
lifecycle. Exposes query() and search() methods.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time

import requests


class QMDStartupError(Exception):
    """QMD service failed to start."""


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

    def __enter__(self) -> "QMDService":
        self._check_port_available()
        self.process = subprocess.Popen(
            ["qmd", "mcp", "--http", "--port", str(self.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._wait_for_ready(timeout=30)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
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
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "query",
                    "arguments": {
                        "query": text,
                        "limit": top_k,
                        "min_score": min_score,
                    },
                },
            },
            timeout=60,
        )
        resp.raise_for_status()
        return self._parse_mcp_response(resp.json())

    def search(self, text: str, top_k: int = 10) -> list[dict]:
        """Pure BM25 search (no LLM, faster).

        Returns:
            [{"content": str, "path": str, "score": float, "context": str}]
        """
        resp = requests.post(
            self.base_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "search",
                    "arguments": {"query": text, "limit": top_k},
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
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                stderr = (
                    self.process.stderr.read().decode()
                    if self.process.stderr
                    else ""
                )
                raise QMDStartupError(
                    f"QMD exited during startup: {stderr[:200]}"
                )
            try:
                resp = requests.post(
                    self.base_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 0,
                        "method": "tools/list",
                    },
                    timeout=5,
                )
                if resp.status_code == 200:
                    # Verify process is still alive after successful health check
                    if self.process.poll() is not None:
                        raise QMDStartupError("QMD exited immediately after health check")
                    return
            except requests.ConnectionError:
                pass
            time.sleep(0.5)
        raise QMDStartupError(f"QMD not ready after {timeout}s")

    @staticmethod
    def _parse_mcp_response(data: dict) -> list[dict]:
        """Parse MCP tools/call response into list of result dicts."""
        result = data.get("result", {})
        content_blocks = result.get("content", [])
        for block in content_blocks:
            if block.get("type") == "text":
                return json.loads(block["text"])
        return []
