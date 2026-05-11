# 医学指南提取管线重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 MinerU(PDF) + Docling(DOCX) + Gemma-4-31B(VLM) 替换现有 Docling-only 提取管线，解决中文侧栏污染、图片丢失、Unicode 连字错误等质量问题。

**Architecture:** 新建独立脚本 `scripts/extract_guidelines.py`，不修改现有 `extract_all.py`。PDF 走 MinerU CLI subprocess，DOCX 走 Docling Python API。VLM 图片描述通过 LM Studio OpenAI-compatible API 调用。三个阶段（提取→后处理→VLM）独立运行，pipeline 命令编排全流程。

**Tech Stack:** Python 3.9+, MinerU CLI (`uv tool`), Docling (已有), PyMuPDF, OpenAI SDK (LM Studio), argparse

**Spec:** `docs/superpowers/specs/2026-04-16-extraction-pipeline-redesign.md`

---

## File Structure

| 文件 | 职责 |
|------|------|
| `scripts/extract_guidelines.py` | CLI 入口 + 子命令路由 |
| `scripts/extraction/__init__.py` | 包初始化 |
| `scripts/extraction/pdf_extractor.py` | MinerU PDF 提取 + 输出路径扁平化 |
| `scripts/extraction/docx_extractor.py` | Docling DOCX 提取 |
| `scripts/extraction/postprocess.py` | LaTeX 后处理 + 内联术语补回 |
| `scripts/extraction/vlm_describer.py` | VLM 图片分类 + 描述 + 注入 |
| `tests/test_postprocess.py` | LaTeX 后处理单元测试 |
| `tests/test_vlm_describer.py` | VLM 分类/描述/注入单元测试 |
| `tests/test_extract_guidelines.py` | PDF/DOCX 提取集成测试 |

---

## Task 1: LaTeX 后处理（纯函数，TDD）

**Files:**
- Create: `scripts/extraction/__init__.py`
- Create: `scripts/extraction/postprocess.py`
- Create: `tests/test_postprocess.py`

- [ ] **Step 1: 创建包结构和测试文件**

```bash
mkdir -p scripts/extraction
touch scripts/extraction/__init__.py
```

```python
# tests/test_postprocess.py
"""Tests for MinerU LaTeX postprocessing."""

import pytest
from scripts.extraction.postprocess import postprocess_latex


class TestPostprocessLatex:
    def test_percent(self):
        assert postprocess_latex("发病率为$75.7\\%$") == "发病率为75.7%"

    def test_percent_with_tilde(self):
        assert postprocess_latex("$5.7\\%{\\sim}8.1\\%$") == "5.7%~8.1%"

    def test_circled_numbers(self):
        text = "$\\textcircled{1}$高发地区 $\\textcircled{2}$感染者"
        assert postprocess_latex(text) == "①高发地区 ②感染者"

    def test_circled_numbers_all_nine(self):
        for i in range(1, 10):
            circled = chr(0x2460 + i - 1)  # ① ② ③ ...
            assert postprocess_latex(f"$\\textcircled{{{i}}}$") == circled

    def test_geqslant(self):
        assert postprocess_latex("年龄${\\geqslant}40$岁") == "年龄>=40岁"

    def test_greater_than(self):
        assert postprocess_latex("年龄${>}40$岁") == "年龄>40岁"
        assert postprocess_latex("$\\mathord{>}50$岁") == ">50岁"

    def test_mathsf(self):
        assert postprocess_latex("$\\mathsf{GC}$") == "GC"
        assert postprocess_latex("${\\mathsf{CY}}+$") == "CY+"

    def test_simple_dollar_wrap(self):
        # $( n = 7 )$ → (n = 7)
        assert postprocess_latex("专家$( n = 7 )$") == "专家(n = 7)"

    def test_copyright(self):
        assert postprocess_latex("$©$") == "©"

    def test_passthrough_no_latex(self):
        text = "这是一段没有LaTeX标记的普通文本。GC发病率为75.7%。"
        assert postprocess_latex(text) == text

    def test_mixed_real_paragraph(self):
        """Real paragraph from MinerU CACA output."""
        inp = "我国早癌检出率为$20\\%$左右，年龄标化5年生存率为$27.4\\%$、$30.5\\%$、$31.8\\%$和$35.1\\%$"
        exp = "我国早癌检出率为20%左右，年龄标化5年生存率为27.4%、30.5%、31.8%和35.1%"
        assert postprocess_latex(inp) == exp

    def test_multiline(self):
        inp = "高危（$\\textcircled{1}$）\n低危（$\\textcircled{2}$）"
        exp = "高危（①）\n低危（②）"
        assert postprocess_latex(inp) == exp
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python3 -m pytest tests/test_postprocess.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.extraction.postprocess'`

- [ ] **Step 3: 实现 postprocess_latex**

```python
# scripts/extraction/postprocess.py
"""MinerU 输出 LaTeX 标记后处理。"""

from __future__ import annotations

import re


# ① ② ③ ④ ⑤ ⑥ ⑦ ⑧ ⑨
_CIRCLED_MAP = {str(i): chr(0x2460 + i - 1) for i in range(1, 10)}


def postprocess_latex(text: str) -> str:
    """清理 MinerU 输出中的 LaTeX 标记，转为纯文本。

    处理的模式:
      $75.7\\%$          → 75.7%
      $5.7\\%{\\sim}8.1\\%$ → 5.7%~8.1%
      $\\textcircled{N}$  → ①②③...
      ${\\geqslant}40$    → >=40
      ${>}40$            → >40
      $\\mathsf{GC}$      → GC
      $( n = 7 )$        → (n = 7)
      $©$                → ©
    """
    if "$" not in text:
        return text

    # \\textcircled{N} → circled number
    text = re.sub(
        r"\$\\textcircled\{(\d)\}\$",
        lambda m: _CIRCLED_MAP.get(m.group(1), m.group(0)),
        text,
    )

    # Percent: $75.7\%$ or complex like $5.7\%{\sim}8.1\%$
    # First handle {\sim} → ~
    text = re.sub(r"\{\\sim\}", "~", text)
    # Then $...\%...$ patterns — unwrap dollars and convert \% to %
    text = re.sub(
        r"\$([^$]*?)\\%([^$]*?)\$",
        lambda m: (m.group(1) + "%" + m.group(2)).replace("\\%", "%"),
        text,
    )

    # \geqslant
    text = re.sub(r"\$\{?\\geqslant\}?\s*(\d+)\$", r">=\1", text)

    # \mathord{>} or plain {>}
    text = re.sub(r"\$\\mathord\{([<>])\}\s*(\d+)\$", r"\1\2", text)
    text = re.sub(r"\$\{([<>])\}\s*(\d+)\$", r"\1\2", text)

    # \mathsf{...} and \mathrm{...} — extract content
    # Handle ${\\mathsf{CY}}+$ patterns
    text = re.sub(
        r"\$\{?\\math\w+\{([^}]+)\}\}?([^$]*?)\$",
        r"\1\2",
        text,
    )

    # Simple dollar-wrapped content: $( n = 7 )$ → (n = 7)
    # Only match short inline expressions, not block LaTeX
    text = re.sub(r"\$([^$]{1,30})\$", r"\1", text)

    return text
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python3 -m pytest tests/test_postprocess.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/__init__.py scripts/extraction/postprocess.py tests/test_postprocess.py
git commit -m "feat: add LaTeX postprocessor for MinerU output"
```

---

## Task 2: PDF 提取（MinerU wrapper）

**Files:**
- Create: `scripts/extraction/pdf_extractor.py`
- Modify: `tests/test_extract_guidelines.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_extract_guidelines.py
"""Tests for extract_guidelines PDF/DOCX extraction."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, call

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
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python3 -m pytest tests/test_extract_guidelines.py::TestResolveMineruOutput -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 pdf_extractor**

```python
# scripts/extraction/pdf_extractor.py
"""MinerU PDF extraction wrapper."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from scripts.extraction.postprocess import postprocess_latex


def resolve_mineru_output(output_dir: Path, stem: str) -> tuple[Path, Path]:
    """Resolve MinerU's nested output structure.

    MinerU outputs to: output_dir/{stem}/hybrid_auto/{stem}.md
    Images go to:      output_dir/{stem}/hybrid_auto/images/

    Returns:
        (md_path, images_dir)

    Raises:
        FileNotFoundError: if the expected output structure doesn't exist
    """
    md_path = output_dir / stem / "hybrid_auto" / f"{stem}.md"
    images_dir = output_dir / stem / "hybrid_auto" / "images"
    if not md_path.exists():
        raise FileNotFoundError(
            f"MinerU output not found at {md_path}. "
            f"Check that mineru processed the file successfully."
        )
    return md_path, images_dir


def extract_pdf(
    source_path: Path,
    output_dir: Path,
    *,
    mineru_cmd: str = "mineru",
) -> dict:
    """Extract a PDF file using MinerU CLI.

    Args:
        source_path: Path to the source PDF
        output_dir: Directory for MinerU output
        mineru_cmd: MinerU CLI command name

    Returns:
        {"md_path": Path, "images_dir": Path, "image_count": int}
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = source_path.stem

    # Run MinerU
    result = subprocess.run(
        [mineru_cmd, "-p", str(source_path), "-o", str(output_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"MinerU failed for {source_path.name}: {result.stderr[:500]}"
        )

    md_path, images_dir = resolve_mineru_output(output_dir, stem)

    # LaTeX postprocessing
    content = md_path.read_text(encoding="utf-8")
    cleaned = postprocess_latex(content)
    md_path.write_text(cleaned, encoding="utf-8")

    image_count = len(list(images_dir.glob("*.jpg"))) if images_dir.exists() else 0

    return {
        "md_path": md_path,
        "images_dir": images_dir,
        "image_count": image_count,
    }
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python3 -m pytest tests/test_extract_guidelines.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/pdf_extractor.py tests/test_extract_guidelines.py
git commit -m "feat: add MinerU PDF extraction wrapper"
```

---

## Task 3: DOCX 提取（Docling wrapper）

**Files:**
- Create: `scripts/extraction/docx_extractor.py`
- Modify: `tests/test_extract_guidelines.py`

- [ ] **Step 1: 写测试**

追加到 `tests/test_extract_guidelines.py`:

```python
from scripts.extraction.docx_extractor import extract_docx


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
        from scripts.extraction.docx_extractor import extract_docx

        src = tmp_path / "file.pptx"
        src.write_bytes(b"dummy")
        with pytest.raises(ValueError, match="Unsupported"):
            extract_docx(src, tmp_path / "out")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python3 -m pytest tests/test_extract_guidelines.py::TestExtractDocx -v
```

Expected: FAIL

- [ ] **Step 3: 实现 docx_extractor**

```python
# scripts/extraction/docx_extractor.py
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
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python3 -m pytest tests/test_extract_guidelines.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/docx_extractor.py tests/test_extract_guidelines.py
git commit -m "feat: add Docling DOCX extraction wrapper"
```

---

## Task 4: 内联术语补回

**Files:**
- Modify: `scripts/extraction/postprocess.py`
- Modify: `tests/test_postprocess.py`

- [ ] **Step 1: 写测试**

追加到 `tests/test_postprocess.py`:

```python
from scripts.extraction.postprocess import recover_inline_terms


class TestRecoverInlineTerms:
    def test_fills_empty_parens(self, tmp_path):
        """Empty Chinese parens filled from PyMuPDF text."""
        md_text = "据全球最新数据（ ），胃癌（ ， ）发病率居恶性肿瘤第5位"
        pymupdf_text = "据全球最新数据（Globocan 2022），胃癌（Gastric Cancer，GC）发病率居恶性肿瘤第5位"

        result = recover_inline_terms(md_text, pymupdf_text)
        assert "Globocan 2022" in result
        assert "Gastric Cancer" in result
        assert "GC" in result

    def test_no_empty_parens_noop(self):
        """No empty parens → text unchanged."""
        text = "正常文本（有内容）不需要补回"
        result = recover_inline_terms(text, text)
        assert result == text

    def test_pymupdf_also_empty_skip(self):
        """If PyMuPDF also has empty parens, skip."""
        md_text = "数据（ ）很重要"
        pymupdf_text = "数据（ ）很重要"
        result = recover_inline_terms(md_text, pymupdf_text)
        assert result == md_text

    def test_partial_match(self):
        """Fill only matching empty parens, leave others."""
        md_text = "第一个（ ）和第二个（ ）"
        pymupdf_text = "第一个（ABC）和第二个（ ）"
        result = recover_inline_terms(md_text, pymupdf_text)
        assert "ABC" in result
        assert "（ ）" in result  # second one stays empty
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python3 -m pytest tests/test_postprocess.py::TestRecoverInlineTerms -v
```

Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现 recover_inline_terms**

追加到 `scripts/extraction/postprocess.py`:

```python
import re


# Pattern: Chinese parens with only whitespace inside
_EMPTY_PARENS_RE = re.compile(r"（\s*(?:，\s*)*）")


def recover_inline_terms(mineru_text: str, pymupdf_text: str) -> str:
    """Fill empty Chinese parentheses in MinerU output using PyMuPDF text.

    Strategy:
    1. Find all "（ ）" or "（ ， ）" patterns in MinerU text
    2. For each, use surrounding Chinese text as anchor (10 chars before)
    3. Find the same anchor in PyMuPDF text and extract paren content
    4. Replace empty parens with filled content

    Args:
        mineru_text: MinerU markdown output (may have empty parens)
        pymupdf_text: Raw text from PyMuPDF (reference with filled parens)

    Returns:
        Text with empty parens filled where possible
    """
    empty_matches = list(_EMPTY_PARENS_RE.finditer(mineru_text))
    if not empty_matches:
        return mineru_text

    result = mineru_text
    # Process in reverse order to preserve offsets
    for match in reversed(empty_matches):
        start = match.start()
        # Extract anchor: up to 10 chars before the empty paren
        anchor_start = max(0, start - 10)
        anchor = mineru_text[anchor_start:start]
        # Clean anchor for regex (escape special chars)
        anchor_escaped = re.escape(anchor.strip())
        if not anchor_escaped:
            continue

        # Find the same anchor in PyMuPDF text
        pymupdf_pattern = re.compile(
            anchor_escaped + r"\s*（([^）]+)）"
        )
        pymupdf_match = pymupdf_pattern.search(pymupdf_text)
        if pymupdf_match:
            filled_content = pymupdf_match.group(1).strip()
            if filled_content and filled_content not in (" ", "，"):
                result = (
                    result[:match.start()]
                    + f"（{filled_content}）"
                    + result[match.end():]
                )

    return result
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python3 -m pytest tests/test_postprocess.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/postprocess.py tests/test_postprocess.py
git commit -m "feat: add inline term recovery for empty Chinese parentheses"
```

---

## Task 5: VLM 图片分类与描述

**Files:**
- Create: `scripts/extraction/vlm_describer.py`
- Create: `tests/test_vlm_describer.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_vlm_describer.py
"""Tests for VLM image classification and description."""

import hashlib
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from scripts.extraction.vlm_describer import (
    classify_image,
    describe_image,
    inject_descriptions,
    compute_image_hash,
    load_cache,
    save_cache,
    check_vlm_health,
    CLASSIFY_PROMPT,
    PROMPTS_BY_TYPE,
)


class TestImageHash:
    def test_deterministic(self, tmp_path):
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff test image data")
        h1 = compute_image_hash(img)
        h2 = compute_image_hash(img)
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex digest


class TestCache:
    def test_save_and_load(self, tmp_path):
        cache_path = tmp_path / "image_descriptions.json"
        data = {"abc123": {"classification": "流程图", "description": "test"}}
        save_cache(data, cache_path)
        loaded = load_cache(cache_path)
        assert loaded == data

    def test_load_missing_returns_empty(self, tmp_path):
        assert load_cache(tmp_path / "nope.json") == {}


class TestClassifyImage:
    @patch("scripts.extraction.vlm_describer._call_vlm")
    def test_classifies_flowchart(self, mock_call):
        mock_call.return_value = "流程图"
        result = classify_image(Path("test.jpg"), "gemma-4-31b")
        assert result == "流程图"

    @patch("scripts.extraction.vlm_describer._call_vlm")
    def test_classifies_decorative(self, mock_call):
        mock_call.return_value = "装饰性图片"
        result = classify_image(Path("logo.jpg"), "gemma-4-31b")
        assert result == "装饰性图片"


class TestDescribeImage:
    @patch("scripts.extraction.vlm_describer._call_vlm")
    def test_returns_description(self, mock_call):
        mock_call.return_value = "NCCN GAST-1 流程图描述..."
        result = describe_image(Path("test.jpg"), "流程图", "gemma-4-31b")
        assert "NCCN" in result

    @patch("scripts.extraction.vlm_describer._call_vlm")
    def test_skips_decorative(self, mock_call):
        result = describe_image(Path("test.jpg"), "装饰性图片", "gemma-4-31b")
        assert result is None
        mock_call.assert_not_called()


class TestInjectDescriptions:
    def test_injects_after_image_ref(self):
        md = "Some text\n\n![](images/abc.jpg)\n\nMore text"
        descriptions = {"images/abc.jpg": "这是一个流程图描述"}
        result = inject_descriptions(md, descriptions)
        assert "> **[图片描述]**" in result
        assert "流程图描述" in result
        assert "More text" in result

    def test_no_description_unchanged(self):
        md = "Some text\n\n![](images/abc.jpg)\n\nMore text"
        result = inject_descriptions(md, {})
        assert result == md

    def test_multiple_images(self):
        md = "![](images/a.jpg)\n\nText\n\n![](images/b.jpg)"
        descriptions = {
            "images/a.jpg": "描述A",
            "images/b.jpg": "描述B",
        }
        result = inject_descriptions(md, descriptions)
        assert "描述A" in result
        assert "描述B" in result


class TestVlmHealth:
    @patch("scripts.extraction.vlm_describer.requests")
    def test_healthy(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"id": "gemma-4-31b"}]
        }
        mock_req.get.return_value = mock_resp
        assert check_vlm_health("gemma-4-31b") is True

    @patch("scripts.extraction.vlm_describer.requests")
    def test_model_not_loaded(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"id": "other-model"}]
        }
        mock_req.get.return_value = mock_resp
        assert check_vlm_health("gemma-4-31b") is False

    @patch("scripts.extraction.vlm_describer.requests")
    def test_connection_refused(self, mock_req):
        mock_req.get.side_effect = ConnectionError("refused")
        assert check_vlm_health("gemma-4-31b") is False
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python3 -m pytest tests/test_vlm_describer.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 vlm_describer**

```python
# scripts/extraction/vlm_describer.py
"""VLM image classification, description, and injection."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests


LM_STUDIO_URL = "http://localhost:1234/v1"

CLASSIFY_PROMPT = """这张图片属于以下哪个类别？只回答类别名称，不要解释。
- 流程图
- 数据图表
- 医学示意图
- 表格图片
- 装饰性图片"""

PROMPTS_BY_TYPE = {
    "流程图": (
        "你是一个医学指南图片描述专家。请用简体中文详细描述这张临床决策流程图中的所有节点、"
        "分支条件和治疗路径。要求：\n"
        "1）列出每个决策节点和对应的处置方案\n"
        "2）保留所有英文医学术语和分期标记\n"
        "3）描述箭头指向的逻辑关系\n"
        "4）末尾附加逻辑关系汇总表\n"
        "输出格式为结构化 Markdown 文本。"
    ),
    "数据图表": (
        "请用简体中文描述这张医学数据图表。要求：\n"
        "1）说明图表类型（生存曲线/柱状图/折线图等）\n"
        "2）列出所有数据组和关键数据点\n"
        "3）描述主要趋势和统计学差异\n"
        "4）保留所有 p 值、HR、CI 等统计指标"
    ),
    "医学示意图": (
        "请用简体中文描述这张医学示意图的结构和内容。"
        "列出所有标注的解剖结构、分期标记或分类标准。"
    ),
    "表格图片": (
        "请将这张表格图片转换为 Markdown 表格格式。"
        "保留所有行列内容，保持原始语言（中文/英文）。"
    ),
}


def compute_image_hash(image_path: Path) -> str:
    """Compute SHA256 hash of image file content."""
    return hashlib.sha256(image_path.read_bytes()).hexdigest()


def load_cache(cache_path: Path) -> dict:
    """Load image description cache from JSON file."""
    if not cache_path.exists():
        return {}
    return json.loads(cache_path.read_text(encoding="utf-8"))


def save_cache(cache: dict, cache_path: Path) -> None:
    """Save image description cache to JSON file."""
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _call_vlm(
    image_path: Path,
    prompt: str,
    model: str,
    *,
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> str:
    """Call LM Studio VLM API with an image and prompt."""
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    resp = requests.post(
        f"{LM_STUDIO_URL}/chat/completions",
        json={
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def check_vlm_health(target_model: str) -> bool:
    """Check if LM Studio is running and target model is loaded."""
    try:
        resp = requests.get(f"{LM_STUDIO_URL}/models", timeout=5)
        models = [m["id"] for m in resp.json().get("data", [])]
        return any(target_model in m for m in models)
    except (ConnectionError, requests.RequestException):
        return False


def classify_image(image_path: Path, model: str) -> str:
    """Classify an image into one of the predefined categories."""
    result = _call_vlm(image_path, CLASSIFY_PROMPT, model, max_tokens=20)
    # Normalize: extract the category name from the response
    for category in PROMPTS_BY_TYPE:
        if category in result:
            return category
    if "装饰" in result:
        return "装饰性图片"
    return "医学示意图"  # default fallback


def describe_image(
    image_path: Path,
    classification: str,
    model: str,
) -> str | None:
    """Generate a text description of an image based on its classification.

    Returns None for decorative images (skipped).
    """
    if classification == "装饰性图片":
        return None

    prompt = PROMPTS_BY_TYPE.get(classification, PROMPTS_BY_TYPE["医学示意图"])
    return _call_vlm(image_path, prompt, model)


def inject_descriptions(md_text: str, descriptions: dict[str, str]) -> str:
    """Inject image descriptions as blockquotes after image references.

    Args:
        md_text: Markdown content with image references
        descriptions: {image_ref: description_text}

    Returns:
        Markdown with descriptions injected
    """
    if not descriptions:
        return md_text

    def _replace(match):
        full_match = match.group(0)
        img_ref = match.group(1)
        if img_ref in descriptions:
            desc = descriptions[img_ref]
            # Format as blockquote, each line prefixed with >
            desc_lines = desc.strip().split("\n")
            blockquote = "\n".join(f"> {line}" for line in desc_lines)
            return f"{full_match}\n\n> **[图片描述]**\n{blockquote}"
        return full_match

    return re.sub(r"!\[[^\]]*\]\(([^)]+)\)", _replace, md_text)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python3 -m pytest tests/test_vlm_describer.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/vlm_describer.py tests/test_vlm_describer.py
git commit -m "feat: add VLM image classification, description, and injection"
```

---

## Task 6: CLI 入口与 Pipeline 编排

**Files:**
- Create: `scripts/extract_guidelines.py`

- [ ] **Step 1: 实现 CLI 脚本**

```python
# scripts/extract_guidelines.py
#!/usr/bin/env python3
"""医学指南提取管线 — MinerU(PDF) + Docling(DOCX) + VLM(图片描述)。

子命令:
  extract         提取单个文件
  extract-all     批量提取整个知识库
  describe-images 对提取的图片生成 VLM 描述
  pipeline        全流程（提取 + 后处理 + 图片描述）
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

from scripts.extraction.pdf_extractor import extract_pdf
from scripts.extraction.docx_extractor import extract_docx
from scripts.extraction.vlm_describer import (
    check_vlm_health,
    classify_image,
    compute_image_hash,
    describe_image,
    inject_descriptions,
    load_cache,
    save_cache,
)


DEFAULT_VLM_MODEL = "gemma-4-31b-it-mystery-fine-tune-heretic-uncensored-thinking-instruct"


def cmd_extract(args):
    """Extract a single file."""
    src = Path(args.input).resolve()
    out_dir = Path(args.output_dir).resolve()

    if src.suffix.lower() == ".pdf":
        result = extract_pdf(src, out_dir)
        print(f"PDF 提取完成: {result['md_path']}")
        print(f"  图片: {result['image_count']} 张")
    elif src.suffix.lower() == ".docx":
        result = extract_docx(src, out_dir)
        print(f"DOCX 提取完成: {result['md_path']}")
    else:
        print(f"不支持的格式: {src.suffix}", file=sys.stderr)
        sys.exit(1)


def cmd_extract_all(args):
    """Extract all files in a knowledge base directory."""
    kb_root = Path(args.kb_root).resolve()
    force = args.force
    stats = {"success": 0, "skipped": 0, "failed": 0}

    extracted_dir = kb_root / "extracted"
    extracted_dir.mkdir(exist_ok=True)
    images_root = kb_root / "images"
    images_root.mkdir(exist_ok=True)

    # Scan source/ for PDF/DOCX files
    source_dir = kb_root / "source"
    if not source_dir.exists():
        # Fallback: scan org-level directories (legacy layout)
        source_dir = kb_root

    sources = sorted(
        itertools.chain(
            source_dir.rglob("*.[pP][dD][fF]"),
            source_dir.rglob("*.[dD][oO][cC][xX]"),
        )
    )

    for src in sources:
        out_md = extracted_dir / f"{src.stem}.md"
        if out_md.exists() and not force:
            print(f"  跳过 {src.name}: 已存在")
            stats["skipped"] += 1
            continue

        try:
            if src.suffix.lower() == ".pdf":
                img_dir = images_root / src.stem
                img_dir.mkdir(exist_ok=True)
                result = extract_pdf(src, images_root)
                # Flatten: copy md to extracted/
                import shutil
                shutil.copy2(result["md_path"], out_md)
                print(f"  完成 {src.name} (图片: {result['image_count']})")
            else:
                result = extract_docx(src, extracted_dir)
                print(f"  完成 {src.name}")
            stats["success"] += 1
        except Exception as e:
            print(f"  失败 {src.name}: {e}", file=sys.stderr)
            stats["failed"] += 1

    print(f"\n提取完成: 成功 {stats['success']}, 跳过 {stats['skipped']}, 失败 {stats['failed']}")
    return stats


def cmd_describe_images(args):
    """Generate VLM descriptions for extracted images."""
    images_dir = Path(args.input_dir).resolve()
    model = args.model or DEFAULT_VLM_MODEL

    if not check_vlm_health(model):
        print(f"LM Studio 未运行或模型 {model} 未加载", file=sys.stderr)
        sys.exit(1)

    cache_path = images_dir / "image_descriptions.json"
    cache = load_cache(cache_path)

    images = sorted(images_dir.glob("*.jpg"))
    total = len(images)
    new_count = 0

    for i, img_path in enumerate(images, 1):
        img_hash = compute_image_hash(img_path)
        if img_hash in cache:
            print(f"  [{i}/{total}] 缓存命中: {img_path.name}")
            continue

        print(f"  [{i}/{total}] 分类: {img_path.name}...")
        classification = classify_image(img_path, model)
        print(f"           → {classification}")

        description = describe_image(img_path, classification, model)
        cache[img_hash] = {
            "file": img_path.name,
            "classification": classification,
            "description": description,
            "model": model,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        new_count += 1

        if description:
            print(f"           描述: {description[:80]}...")
        else:
            print(f"           跳过（装饰性图片）")

        # Save cache after each image (crash safety)
        save_cache(cache, cache_path)

    print(f"\n描述完成: {new_count} 新增, {total - new_count} 缓存命中")


def cmd_pipeline(args):
    """Full pipeline: extract → postprocess → VLM describe."""
    kb_root = Path(args.kb_root).resolve()
    model = args.model or DEFAULT_VLM_MODEL

    # Phase 1: Extract
    print("=" * 60)
    print("[1/2] 提取阶段")
    print("=" * 60)
    args.force = args.force
    stats = cmd_extract_all(args)

    # Phase 2: VLM (optional)
    print("\n" + "=" * 60)
    print("[2/2] VLM 图片描述阶段")
    print("=" * 60)

    if not check_vlm_health(model):
        print(f"⚠ LM Studio 未运行或模型未加载 → 跳过 VLM 描述")
        print(f"  后续可单独运行: python3 scripts/extract_guidelines.py describe-images ...")
        return

    images_root = kb_root / "images"
    if not images_root.exists():
        print("  无图片目录，跳过")
        return

    for img_dir in sorted(images_root.iterdir()):
        if not img_dir.is_dir():
            continue
        print(f"\n处理 {img_dir.name}/")
        args.input_dir = str(img_dir)
        args.model = model
        cmd_describe_images(args)

    # Inject descriptions into markdown files
    extracted_dir = kb_root / "extracted"
    for md_file in sorted(extracted_dir.glob("*.md")):
        stem = md_file.stem
        cache_path = images_root / stem / "image_descriptions.json"
        if not cache_path.exists():
            continue

        cache = load_cache(cache_path)
        # Build descriptions dict: {image_ref: description}
        descriptions = {}
        for entry in cache.values():
            if entry.get("description"):
                descriptions[f"images/{entry['file']}"] = entry["description"]

        if descriptions:
            content = md_file.read_text(encoding="utf-8")
            updated = inject_descriptions(content, descriptions)
            md_file.write_text(updated, encoding="utf-8")
            print(f"  注入 {len(descriptions)} 个描述到 {md_file.name}")


from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser(
        description="医学指南提取管线（MinerU + Docling + VLM）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # extract
    p_extract = sub.add_parser("extract", help="提取单个文件")
    p_extract.add_argument("--input", required=True, help="输入文件路径")
    p_extract.add_argument("--output-dir", required=True, help="输出目录")

    # extract-all
    p_all = sub.add_parser("extract-all", help="批量提取整个知识库")
    p_all.add_argument("--kb-root", required=True, help="知识库根目录")
    p_all.add_argument("--force", action="store_true", help="强制重新提取")

    # describe-images
    p_desc = sub.add_parser("describe-images", help="VLM 图片描述")
    p_desc.add_argument("--input-dir", required=True, help="图片目录")
    p_desc.add_argument("--model", default=None, help="VLM 模型名称")

    # pipeline
    p_pipe = sub.add_parser("pipeline", help="全流程")
    p_pipe.add_argument("--kb-root", required=True, help="知识库根目录")
    p_pipe.add_argument("--force", action="store_true", help="强制重新提取")
    p_pipe.add_argument("--model", default=None, help="VLM 模型名称")

    args = parser.parse_args()

    commands = {
        "extract": cmd_extract,
        "extract-all": cmd_extract_all,
        "describe-images": cmd_describe_images,
        "pipeline": cmd_pipeline,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 验证 CLI help 输出**

```bash
python3 scripts/extract_guidelines.py --help
python3 scripts/extract_guidelines.py extract --help
python3 scripts/extract_guidelines.py pipeline --help
```

Expected: 显示帮助信息，无错误

- [ ] **Step 3: Commit**

```bash
git add scripts/extract_guidelines.py
git commit -m "feat: add extract_guidelines CLI with extract/describe/pipeline commands"
```

---

## Task 7: 集成测试（gated）

**Files:**
- Modify: `tests/test_extract_guidelines.py`

- [ ] **Step 1: 添加 gated 集成测试**

追加到 `tests/test_extract_guidelines.py`:

```python
@pytest.mark.skipif(
    not os.environ.get("MINERU_AVAILABLE"),
    reason="MINERU_AVAILABLE not set"
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
        assert "$" not in content or content.count("$") < 5, "LaTeX should be cleaned"
        assert result["image_count"] > 0, "Should extract images"


@pytest.mark.skipif(
    not os.environ.get("LM_STUDIO_AVAILABLE"),
    reason="LM_STUDIO_AVAILABLE not set"
)
class TestVlmIntegration:
    def test_classify_flowchart(self):
        """Integration test: classify NCCN flowchart image."""
        img = Path(
            "MD_output_test/output_mineru/"
            "NCCN_GastricCancer_2026.V2_EN/hybrid_auto/images/"
        )
        if not img.exists():
            pytest.skip("Test images not found")

        images = sorted(img.glob("*.jpg"))
        if not images:
            pytest.skip("No images")

        result = classify_image(images[0], DEFAULT_VLM_MODEL)
        assert result in ("流程图", "数据图表", "医学示意图", "表格图片", "装饰性图片")
```

- [ ] **Step 2: 运行单元测试确认通过**

```bash
python3 -m pytest tests/test_extract_guidelines.py tests/test_postprocess.py tests/test_vlm_describer.py -v -m "not slow"
```

Expected: ALL unit tests PASS, integration tests SKIPPED

- [ ] **Step 3: Commit**

```bash
git add tests/test_extract_guidelines.py
git commit -m "test: add gated integration tests for MinerU and VLM"
```

---

## Task 8: 文档更新

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 更新 CLAUDE.md 的命令文档**

在 `## Common Commands` 的 `### Text Extraction (Docling)` 部分之后，添加新提取管线的文档:

```markdown
### Text Extraction v2 (MinerU + Docling + VLM)

```bash
# 提取单个 PDF（MinerU）
python3 scripts/extract_guidelines.py extract --input <file.pdf> --output-dir <dir>

# 提取单个 DOCX（Docling）
python3 scripts/extract_guidelines.py extract --input <file.docx> --output-dir <dir>

# 批量提取整个知识库
python3 scripts/extract_guidelines.py extract-all --kb-root $MEDICAL_GUIDELINES_DIR

# VLM 图片描述（需要 LM Studio 运行）
python3 scripts/extract_guidelines.py describe-images --input-dir <images_dir>

# 全流程（提取 + VLM）
python3 scripts/extract_guidelines.py pipeline --kb-root $MEDICAL_GUIDELINES_DIR
```
```

更新依赖表，在 Dependencies 部分添加:

```markdown
| `mineru` | `uv tool install mineru` | PDF to Markdown extraction (v2) |
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add extract_guidelines v2 commands to CLAUDE.md"
```

---

## Execution Order & Dependencies

```
Task 1 (LaTeX 后处理)          ─┐
                                ├── Task 2 (PDF 提取) ──┐
Task 3 (DOCX 提取)        ─────┤                       ├── Task 6 (CLI + Pipeline)
                                │                       │
Task 4 (术语补回)          ─────┘                       ├── Task 7 (集成测试)
                                                        │
Task 5 (VLM 描述)         ─────────────────────────────┘
                                                        │
                                                  Task 8 (文档)
```

**可并行的任务:**
- Lane A: Task 1 → Task 2 → Task 4（PDF 提取链）
- Lane B: Task 3（DOCX 提取，独立）
- Lane C: Task 5（VLM 描述，独立）
- 汇合: Task 6 → Task 7 → Task 8
