"""Tests for MinerU LaTeX postprocessing and inline term recovery."""

import pytest
from scripts.extraction.postprocess import postprocess_latex, recover_inline_terms


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
        assert postprocess_latex("专家$( n = 7 )$") == "专家( n = 7 )"

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


class TestRecoverInlineTerms:
    def test_fills_empty_parens(self):
        """Empty Chinese parens filled from PyMuPDF text."""
        md_text = "据全球最新数据（ ），胃癌（ ， ）发病率居恶性肿瘤第5位"
        pymupdf_text = "据全球最新数据（Globocan 2022），胃癌（Gastric Cancer，GC）发病率居恶性肿瘤第5位"

        result = recover_inline_terms(md_text, pymupdf_text)
        assert "Globocan 2022" in result
        assert "Gastric Cancer" in result

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
