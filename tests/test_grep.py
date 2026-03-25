import pytest
from scripts.batch_pipeline import escape_grep_keyword, generate_grep_commands

class TestEscapeGrepKeyword:
    def test_plain_keyword(self):
        assert escape_grep_keyword("HER2") == ["HER2"]

    def test_brackets(self):
        result = escape_grep_keyword("CPS(≥10)")
        assert len(result) == 2
        assert "CPS\\(≥10\\)" in result
        assert any("CPS.*10" in v for v in result)

    def test_square_brackets(self):
        result = escape_grep_keyword("T4b[胰腺]")
        assert any("\\[" in v for v in result)

    def test_dot_and_star(self):
        result = escape_grep_keyword("V2.0")
        assert "V2\\.0" in result

    def test_no_parens_no_variant(self):
        result = escape_grep_keyword("gastric")
        assert len(result) == 1

class TestGenerateGrepCommands:
    def test_basic_generation(self, mock_kb):
        features = {
            "diagnosis_keywords": ["胃癌", "gastric"],
            "staging_keywords": ["T4a", "N1"],
            "all_keywords": ["胃癌", "gastric", "T4a", "N1"],
        }
        profile = {
            "orgs": ["NCCN", "CSCO"],
            "org_files": {
                "NCCN": [{"file": "NCCN_GC.txt", "path": "/kb/NCCN/extracted/NCCN_GC.txt"}],
                "CSCO": [{"file": "CSCO_GC.txt", "path": "/kb/CSCO/extracted/CSCO_GC.txt"}],
            },
        }
        cmds = generate_grep_commands(features, profile, mock_kb)
        org_names = [c["org"] for c in cmds]
        assert "NCCN" in org_names
        assert "CSCO" in org_names
        for c in cmds:
            assert c["command"].startswith("grep")

    def test_special_chars_escaped(self, mock_kb):
        features = {
            "molecular_keywords": ["CPS(≥10)", "HER2"],
            "all_keywords": ["CPS(≥10)", "HER2"],
        }
        profile = {
            "orgs": ["NCCN"],
            "org_files": {"NCCN": [{"file": "x.txt", "path": "/kb/NCCN/extracted/x.txt"}]},
        }
        cmds = generate_grep_commands(features, profile, mock_kb)
        for c in cmds:
            assert "CPS(" not in c["command"]

    def test_empty_keywords(self, mock_kb):
        features = {"all_keywords": []}
        profile = {"orgs": ["NCCN"], "org_files": {"NCCN": [{"file": "x.txt", "path": "/p/x.txt"}]}}
        cmds = generate_grep_commands(features, profile, mock_kb)
        assert cmds == []
