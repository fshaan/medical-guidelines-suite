"""Docling DOCX extraction wrapper."""

from __future__ import annotations

from pathlib import Path

from docling.document_converter import DocumentConverter


def extract_docx(source_path: Path, output_dir: Path) -> dict:
    """Extract a DOCX file using Docling.

    Args:
        source_path: Path to the source DOCX
        output_dir: Directory for output

    Returns:
        {"md_path": Path, "images_dir": Path | None, "image_count": int}
    """
    if source_path.suffix.lower() != ".docx":
        raise ValueError(f"Unsupported format: {source_path.suffix}")

    output_dir.mkdir(parents=True, exist_ok=True)

    converter = DocumentConverter()
    result = converter.convert(str(source_path))
    md_content = result.document.export_to_markdown(image_mode="referenced")

    md_path = output_dir / f"{source_path.stem}.md"
    md_path.write_text(md_content, encoding="utf-8")

    return {
        "md_path": md_path,
        "images_dir": None,
        "image_count": 0,
    }
