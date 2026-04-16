"""Tests for extract_guidelines PDF/DOCX extraction."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.extraction.pdf_extractor import extract_pdf, resolve_mineru_output


class TestResolveMineruOutput:
    def test_finds_nested_md(self, tmp_path):
        """MinerU outputs to {output}/{stem}/hybrid_auto/{stem}.md"""
        stem = "ESMO_Gastric"
        nested = tmp_path / stem / "hybrid_auto"
        nested.mkdir(parents=True)
        md_file = nested / f"{stem}.md"
        md_file.write_text("# Test", encoding="utf-8")
        (nested / "images").mkdir()

        md_path, img_dir = resolve_mineru_output(tmp_path, stem)
        assert md_path == md_file
        assert img_dir == nested / "images"

    def test_raises_if_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="MinerU output not found"):
            resolve_mineru_output(tmp_path, "nonexistent")


class TestExtractPdf:
    @patch("scripts.extraction.pdf_extractor.subprocess")
    def test_calls_mineru_cli(self, mock_sub, tmp_path):
        """Verify mineru CLI is called with correct args."""
        src = tmp_path / "test.pdf"
        src.write_bytes(b"%PDF-1.4 dummy")
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        # Create fake MinerU output
        nested = out_dir / "test" / "hybrid_auto"
        nested.mkdir(parents=True)
        (nested / "test.md").write_text("# Result\n\nContent $75.7\\%$", encoding="utf-8")
        (nested / "images").mkdir()
        (nested / "images" / "fig1.jpg").write_bytes(b"\xff\xd8\xff dummy jpg")

        mock_sub.run.return_value = MagicMock(returncode=0)

        result = extract_pdf(src, out_dir)

        mock_sub.run.assert_called_once()
        cmd = mock_sub.run.call_args[0][0]
        assert "mineru" in cmd[0]
        assert str(src) in cmd
        # Postprocessing should have cleaned LaTeX
        content = result["md_path"].read_text(encoding="utf-8")
        assert "75.7%" in content
        assert "$" not in content
        assert result["image_count"] == 1

    @patch("scripts.extraction.pdf_extractor.subprocess")
    def test_raises_on_mineru_failure(self, mock_sub, tmp_path):
        src = tmp_path / "bad.pdf"
        src.write_bytes(b"not a pdf")
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        mock_sub.run.return_value = MagicMock(returncode=1, stderr="Error parsing PDF")

        with pytest.raises(RuntimeError, match="MinerU failed"):
            extract_pdf(src, out_dir)
