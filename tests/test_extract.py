"""Tests for Docling-based document extraction."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def tmp_kb(tmp_path):
    """Create a minimal KB directory structure."""
    org_dir = tmp_path / "NCCN"
    org_dir.mkdir()
    (org_dir / "NCCN_Gastric_2026.pdf").write_bytes(b"%PDF-1.4 dummy")
    return tmp_path


def _mock_converter():
    """Build a mock DocumentConverter that returns fixed markdown."""
    mock_doc = MagicMock()
    mock_doc.document.export_to_markdown.return_value = (
        "# Gastric Cancer\n\n## Treatment\n\nChemotherapy recommended.\n"
    )
    converter = MagicMock()
    converter.convert.return_value = mock_doc
    return converter


@patch("scripts.extract_all.DocumentConverter")
def test_extract_file_creates_md(mock_cls, tmp_kb):
    mock_cls.return_value = _mock_converter()

    from scripts.extract_all import extract_file

    org_dir = tmp_kb / "NCCN"
    extracted_dir = org_dir / "extracted"
    extracted_dir.mkdir()

    result = extract_file(org_dir / "NCCN_Gastric_2026.pdf", extracted_dir)

    assert result.name == "NCCN_Gastric_2026.md"
    assert result.exists()
    content = result.read_text(encoding="utf-8")
    assert "# Gastric Cancer" in content
    assert "Chemotherapy" in content


@patch("scripts.extract_all.DocumentConverter")
def test_extract_all_skips_existing(mock_cls, tmp_kb):
    mock_cls.return_value = _mock_converter()

    from scripts.extract_all import extract_all

    stats1 = extract_all(tmp_kb, force=False)
    assert stats1["success"] == 1

    stats2 = extract_all(tmp_kb, force=False)
    assert stats2["skipped"] == 1
    assert stats2["success"] == 0


@patch("scripts.extract_all.DocumentConverter")
def test_extract_all_force_reextracts(mock_cls, tmp_kb):
    mock_cls.return_value = _mock_converter()

    from scripts.extract_all import extract_all

    extract_all(tmp_kb, force=False)
    stats = extract_all(tmp_kb, force=True)
    assert stats["success"] == 1
    assert stats["skipped"] == 0
