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
