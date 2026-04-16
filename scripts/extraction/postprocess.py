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
