#!/usr/bin/env python3
"""
PDF 文本提取脚本
使用 pdftotext 提取纯文本，可选使用 pdfplumber 提取表格
"""

import argparse
import subprocess
import sys
from pathlib import Path


def extract_text_pdftotext(pdf_path: Path, output_path: Path) -> bool:
    """使用 pdftotext 提取纯文本"""
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), str(output_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            # 添加页码标记
            content = output_path.read_text(encoding="utf-8", errors="replace")
            lines = content.split("\n")
            new_lines = []
            page_num = 1
            for line in lines:
                if line.strip() and not line.startswith("\x0c"):
                    new_lines.append(line)
                elif line.startswith("\x0c"):
                    new_lines.append(f"\n--- Page {page_num} ---\n")
                    page_num += 1
            output_path.write_text("\n".join(new_lines), encoding="utf-8")
            return True
        else:
            print(f"pdftotext 错误: {result.stderr}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"提取失败: {e}", file=sys.stderr)
        return False


def extract_tables_pdfplumber(pdf_path: Path, output_path: Path) -> bool:
    """使用 pdfplumber 提取表格"""
    try:
        import pdfplumber

        tables_content = []
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                tables = page.extract_tables()
                if tables:
                    tables_content.append(f"\n--- Page {i} Tables ---\n")
                    for j, table in enumerate(tables, 1):
                        tables_content.append(f"\nTable {j}:")
                        for row in table:
                            # 转换为 markdown 表格格式
                            cells = [
                                str(cell).replace("\n", " ") if cell else ""
                                for cell in row
                            ]
                            tables_content.append("| " + " | ".join(cells) + " |")

        if tables_content:
            output_path.write_text("\n".join(tables_content), encoding="utf-8")
            return True
        return False
    except ImportError:
        print("pdfplumber 未安装，跳过表格提取", file=sys.stderr)
        return False
    except Exception as e:
        print(f"表格提取失败: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="提取 PDF 文本")
    parser.add_argument("input", type=Path, help="输入 PDF 文件路径")
    parser.add_argument(
        "--tables", action="store_true", help="同时提取表格（使用 pdfplumber）"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="输出目录（默认为 PDF 同目录的 extracted/ 子目录）",
    )
    args = parser.parse_args()

    pdf_path = args.input.resolve()
    if not pdf_path.exists():
        print(f"文件不存在: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    # 确定输出目录
    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = pdf_path.parent / "extracted"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 提取纯文本
    base_name = pdf_path.stem
    text_output = output_dir / f"{base_name}.txt"
    print(f"提取文本: {pdf_path.name} -> {text_output.name}")
    if extract_text_pdftotext(pdf_path, text_output):
        print(f"✓ 文本提取完成: {text_output}")

    # 可选：提取表格
    if args.tables:
        tables_output = output_dir / f"{base_name}_tables.txt"
        print(f"提取表格: {pdf_path.name} -> {tables_output.name}")
        if extract_tables_pdfplumber(pdf_path, tables_output):
            print(f"✓ 表格提取完成: {tables_output}")
        else:
            print("⚠ 未检测到表格或提取失败")


if __name__ == "__main__":
    main()
