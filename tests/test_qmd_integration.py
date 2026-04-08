"""Integration tests for QMD -- require real QMD installation.

Run with: QMD_AVAILABLE=1 python -m pytest tests/test_qmd_integration.py -v -m slow
"""

import json
import os

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("QMD_AVAILABLE"),
        reason="QMD not available (set QMD_AVAILABLE=1 to run)",
    ),
]


def test_qmd_service_real_lifecycle():
    """Start real QMD, query, stop."""
    from scripts.retriever import QMDService

    with QMDService(port=18181) as qmd:
        results = qmd.search("test query")
        assert isinstance(results, list)


def test_qmd_service_query_returns_results(tmp_path):
    """Index a test file, query it, verify results."""
    import subprocess

    test_dir = tmp_path / "test_org" / "extracted"
    test_dir.mkdir(parents=True)
    (test_dir / "test.md").write_text(
        "# Test Guide\n\n## Treatment\n\nChemotherapy is the standard.\n",
        encoding="utf-8",
    )

    subprocess.run(
        ["qmd", "collection", "add", str(test_dir),
         "--name", "test_org", "--mask", "**/*.md"],
        check=True,
    )
    subprocess.run(["qmd", "embed"], check=True)

    from scripts.retriever import QMDService

    with QMDService(port=18182) as qmd:
        results = qmd.query("chemotherapy treatment")
        assert len(results) >= 1
        assert any("chemotherapy" in r["content"].lower() for r in results)

    subprocess.run(["qmd", "collection", "remove", "test_org"], check=False)
