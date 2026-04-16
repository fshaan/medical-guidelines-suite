"""MinerU 输出 LaTeX 标记后处理 + 内联术语补回。"""

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
      $( n = 7 )$        → ( n = 7 )
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

    # Percent: handle {\sim} → ~ first
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
    # Handle ${\mathsf{CY}}+$ patterns
    text = re.sub(
        r"\$\{?\\math\w+\{([^}]+)\}\}?([^$]*?)\$",
        r"\1\2",
        text,
    )

    # Simple dollar-wrapped content: $( n = 7 )$ → ( n = 7 )
    # Only match short inline expressions, not block LaTeX
    text = re.sub(r"\$([^$]{1,30})\$", r"\1", text)

    return text


# Pattern: Chinese parens with only whitespace/commas inside
_EMPTY_PARENS_RE = re.compile(r"（\s*(?:，\s*)*）")


def recover_inline_terms(mineru_text: str, pymupdf_text: str) -> str:
    """Fill empty Chinese parentheses in MinerU output using PyMuPDF text.

    Strategy:
    1. Find all "（ ）" or "（ ， ）" patterns in MinerU text
    2. Use surrounding Chinese text as anchor (up to 10 chars before)
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
        # Extract anchor: up to 10 chars before the empty paren,
        # but only use text after the last "）" to avoid including
        # other parenthesized content in the anchor
        anchor_start = max(0, start - 10)
        raw_anchor = mineru_text[anchor_start:start]
        # Trim to text after last closing paren (if any)
        last_paren = raw_anchor.rfind("）")
        if last_paren >= 0:
            raw_anchor = raw_anchor[last_paren + 1 :]
        anchor = raw_anchor.strip()
        anchor_escaped = re.escape(anchor)
        if not anchor_escaped:
            continue

        # Find the same anchor in PyMuPDF text
        pymupdf_pattern = re.compile(anchor_escaped + r"\s*（([^）]+)）")
        pymupdf_match = pymupdf_pattern.search(pymupdf_text)
        if pymupdf_match:
            filled_content = pymupdf_match.group(1).strip()
            if filled_content and filled_content not in (" ", "，"):
                result = (
                    result[: match.start()]
                    + f"（{filled_content}）"
                    + result[match.end() :]
                )

    return result
