#!/usr/bin/env python3
"""
批量提取所有指南文档（PDF/DOCX -> Markdown）
使用 Docling 统一提取，输出到 extracted/*.md
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

from docling.document_converter import DocumentConverter


def extract_file(source_path: Path, output_dir: Path) -> Path:
    """将单个 PDF/DOCX 转换为 Markdown。

    Args:
        source_path: 源文件路径（PDF 或 DOCX）
        output_dir: 输出目录（通常是 extracted/）

    Returns:
        输出的 .md 文件路径
    """
    converter = DocumentConverter()
    result = converter.convert(source_path)
    md_content = result.document.export_to_markdown()
    out_path = output_dir / f"{source_path.stem}.md"
    out_path.write_text(md_content, encoding="utf-8")
    return out_path


def extract_all(kb_root: Path, force: bool = False) -> dict:
    """遍历 KB 下所有 org 目录，提取 PDF/DOCX -> extracted/*.md。

    Args:
        kb_root: 知识库根目录
        force: 是否强制重新提取

    Returns:
        {"success": int, "skipped": int, "failed": int}
    """
    stats = {"success": 0, "skipped": 0, "failed": 0}

    for org_dir in sorted(kb_root.iterdir()):
        if not org_dir.is_dir() or org_dir.name.startswith("."):
            continue
        if org_dir.name in ("scripts", "extracted"):
            continue

        extracted_dir = org_dir / "extracted"
        extracted_dir.mkdir(exist_ok=True)

        sources = itertools.chain(
            org_dir.glob("*.[pP][dD][fF]"),
            org_dir.glob("*.[dD][oO][cC][xX]"),
        )
        for src in sorted(sources):
            out = extracted_dir / f"{src.stem}.md"
            if out.exists() and not force:
                print(f"  skip {src.name}: already exists")
                stats["skipped"] += 1
                continue
            try:
                extract_file(src, extracted_dir)
                print(f"  done {src.name}")
                stats["success"] += 1
            except Exception as e:
                print(f"  FAIL {src.name}: {e}", file=sys.stderr)
                stats["failed"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="批量提取指南文档（PDF/DOCX -> Markdown）"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新提取（覆盖已有文件）",
    )
    parser.add_argument(
        "--guidelines-dir",
        type=Path,
        default=Path("guidelines"),
        help="指南根目录（默认为 guidelines/）",
    )
    args = parser.parse_args()

    guidelines_dir = args.guidelines_dir.resolve()
    if not guidelines_dir.exists():
        print(f"指南目录不存在: {guidelines_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"知识库: {guidelines_dir}")
    stats = extract_all(guidelines_dir, force=args.force)
    print(
        f"\n统计: 成功 {stats['success']}, "
        f"跳过 {stats['skipped']}, 失败 {stats['failed']}"
    )


if __name__ == "__main__":
    main()
