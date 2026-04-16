"""Tests for extract_guidelines PDF/DOCX extraction."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.extraction.pdf_extractor import extract_pdf, resolve_mineru_output
from scripts.extraction.docx_extractor import extract_docx


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


class TestExtractDocx:
    @patch("scripts.extraction.docx_extractor.DocumentConverter")
    def test_creates_md_output(self, mock_cls, tmp_path):
        mock_doc = MagicMock()
        mock_doc.document.export_to_markdown.return_value = (
            "# 胃癌指南\n\n## 第一部分\n\n内容\n"
        )
        mock_converter = MagicMock()
        mock_converter.convert.return_value = mock_doc
        mock_cls.return_value = mock_converter

        src = tmp_path / "guide.docx"
        src.write_bytes(b"PK dummy docx")
        out_dir = tmp_path / "output"

        result = extract_docx(src, out_dir)

        assert result["md_path"].exists()
        content = result["md_path"].read_text(encoding="utf-8")
        assert "# 胃癌指南" in content
        mock_doc.document.export_to_markdown.assert_called_once_with(
            image_mode="referenced"
        )

    def test_unsupported_format_raises(self, tmp_path):
        src = tmp_path / "file.pptx"
        src.write_bytes(b"dummy")
        with pytest.raises(ValueError, match="Unsupported"):
            extract_docx(src, tmp_path / "out")


# --- Gated integration tests ---

@pytest.mark.skipif(
    not os.environ.get("MINERU_AVAILABLE"),
    reason="MINERU_AVAILABLE not set",
)
class TestMineruIntegration:
    def test_extract_small_pdf(self, tmp_path):
        """Integration test: extract ESMO PDF with real MinerU."""
        src = Path("MD_output_test/ESMO PAN-Asia gastric cancer 2024.pdf")
        if not src.exists():
            pytest.skip("Test PDF not found")

        result = extract_pdf(src, tmp_path)
        content = result["md_path"].read_text(encoding="utf-8")

        # Verify key quality improvements
        assert "/uniFB01" not in content, "Unicode ligature should be fixed"
        assert result["image_count"] > 0, "Should extract images"


@pytest.mark.skipif(
    not os.environ.get("LM_STUDIO_AVAILABLE"),
    reason="LM_STUDIO_AVAILABLE not set",
)
class TestVlmIntegration:
    def test_classify_flowchart(self):
        """Integration test: classify an extracted image."""
        from scripts.extraction.vlm_describer import classify_image

        img_dir = Path(
            "MD_output_test/output_mineru/"
            "NCCN_GastricCancer_2026.V2_EN/hybrid_auto/images/"
        )
        if not img_dir.exists():
            pytest.skip("Test images not found")

        images = sorted(img_dir.glob("*.jpg"))
        if not images:
            pytest.skip("No images")

        DEFAULT_MODEL = (
            "gemma-4-31b-it-mystery-fine-tune-heretic-"
            "uncensored-thinking-instruct"
        )
        result = classify_image(images[0], DEFAULT_MODEL)
        valid_types = ("流程图", "数据图表", "医学示意图", "表格图片", "装饰性图片")
        assert result in valid_types, f"Unexpected classification: {result}"
