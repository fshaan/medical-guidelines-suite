"""Tests for QMDService context manager and query API."""

import json
from unittest.mock import MagicMock, patch

import pytest


@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.requests.post")
def test_qmd_service_lifecycle(mock_post, mock_popen):
    """QMDService starts qmd process on enter, kills on exit."""
    proc = MagicMock()
    proc.poll.return_value = None  # process is running
    proc.pid = 12345
    mock_popen.return_value = proc

    mock_post.return_value = MagicMock(status_code=200, json=lambda: {"result": "ok"})

    from scripts.retriever import QMDService

    with QMDService(port=9999) as svc:
        assert svc.process is not None
        assert svc.port == 9999

    proc.terminate.assert_called_once()


@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.requests.post")
def test_qmd_service_query_returns_structured_results(mock_post, mock_popen):
    """query() returns list of dicts with content, path, score, context."""
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 12345
    mock_popen.return_value = proc

    health_resp = MagicMock(status_code=200, json=lambda: {"result": "ok"})
    query_resp = MagicMock(status_code=200, json=lambda: {
        "result": {
            "structuredContent": {
                "results": [
                    {
                        "snippet": "Chemotherapy is recommended for stage IV.",
                        "file": "NCCN/extracted/NCCN_Gastric_2026.md",
                        "score": 0.85,
                        "context": "NCCN gastric guidelines 2026 V2",
                    }
                ]
            }
        }
    })
    mock_post.side_effect = [health_resp, query_resp]

    from scripts.retriever import QMDService

    with QMDService(port=9999) as svc:
        results = svc.query("gastric cancer stage IV treatment")

    assert len(results) == 1
    assert results[0]["content"] == "Chemotherapy is recommended for stage IV."
    assert results[0]["score"] == 0.85


@patch("scripts.retriever.socket.socket")
def test_qmd_service_detects_port_conflict(mock_socket_cls):
    """Raises QMDStartupError if port is already in use."""
    mock_sock = MagicMock()
    mock_sock.connect_ex.return_value = 0
    mock_sock.__enter__ = lambda s: mock_sock
    mock_sock.__exit__ = MagicMock(return_value=False)
    mock_socket_cls.return_value = mock_sock

    from scripts.retriever import QMDService, QMDStartupError

    with pytest.raises(QMDStartupError, match="already in use"):
        with QMDService(port=9999):
            pass


@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.requests.post")
def test_qmd_service_exit_handles_crashed_process(mock_post, mock_popen):
    """__exit__ handles gracefully if QMD already crashed."""
    proc = MagicMock()
    proc.poll.side_effect = [None, None, 1]  # running, then crashed
    proc.pid = 12345
    mock_popen.return_value = proc

    mock_post.return_value = MagicMock(
        status_code=200, json=lambda: {"result": "ok"}
    )

    from scripts.retriever import QMDService

    with QMDService(port=9999):
        pass

    proc.terminate.assert_not_called()
