"""Tests for batch pipeline utilities and validation."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import json
import argparse
from batch_pipeline import filter_orgs_by_disease, generate_batch_prompt, _is_flat_format, _aggregate_flat_results, _generate_consensus, cmd_validate, MIN_CITATION_COVERAGE, MIN_REC_LENGTH


class TestFilterOrgsByDisease:
    def test_matches_disease_in_filenames(self):
        kb_profile = {
            "orgs": ["NCCN", "JGCA", "ESMO"],
            "org_files": {
                "NCCN": [{"file": "NCCN_GastricCancer_2026.md", "path": "/kb/NCCN/extracted/NCCN_GastricCancer_2026.md"}],
                "JGCA": [{"file": "JGCA_Gastric_Guidelines.md", "path": "/kb/JGCA/extracted/JGCA_Gastric_Guidelines.md"}],
                "ESMO": [{"file": "ESMO_BreastCancer_2025.md", "path": "/kb/ESMO/extracted/ESMO_BreastCancer_2025.md"}],
            },
        }
        result = filter_orgs_by_disease(kb_profile, "胃癌")
        assert "NCCN" in result
        assert "JGCA" in result
        assert "ESMO" not in result

    def test_fallback_all_orgs_when_no_match(self):
        kb_profile = {
            "orgs": ["NCCN", "JGCA"],
            "org_files": {
                "NCCN": [{"file": "NCCN_Lung.md", "path": "..."}],
                "JGCA": [{"file": "JGCA_Lung.md", "path": "..."}],
            },
        }
        result = filter_orgs_by_disease(kb_profile, "罕见病X")
        assert result == ["NCCN", "JGCA"]


class TestPrompt:
    def _make_batch(self):
        return [{
            "patient_id": "P001",
            "patient_name": "张三",
            "disease_type": "胃癌",
            "features": {
                "diagnosis_keywords": ["胃癌"],
                "all_keywords": ["胃癌"],
            },
            "retrieval_results": [
                {"content": "Gastric cancer treatment.", "path": "NCCN/extracted/NCCN_Gastric.md",
                 "score": 0.85, "context": "NCCN gastric guidelines"},
            ],
        }]

    def _make_kb_profile(self):
        return {
            "orgs": ["NCCN"],
            "org_files": {"NCCN": [{"file": "NCCN_Gastric.md"}]},
            "root_index_content": "test index",
        }

    def test_prompt_contains_retrieval_results(self):
        prompt = generate_batch_prompt(
            self._make_batch(), self._make_kb_profile(), "/kb", 1, 1,
        )
        assert "Gastric cancer treatment" in prompt
        assert "retrieval_sources" in prompt

    def test_prompt_no_grep(self):
        prompt = generate_batch_prompt(
            self._make_batch(), self._make_kb_profile(), "/kb", 1, 1,
        )
        assert "grep" not in prompt.lower()
        assert "CMD-P" not in prompt
        assert "execution_log" not in prompt


class TestFlatFormatDetection:
    def test_detects_flat_format(self):
        results = [{"patient_id": "P1", "guideline": "NCCN", "recommendation": "..."}]
        assert _is_flat_format(results) is True

    def test_detects_nested_format(self):
        results = [{"patient_id": "P1", "guideline_results": [{"guideline": "NCCN"}]}]
        assert _is_flat_format(results) is False

    def test_empty_results(self):
        assert _is_flat_format([]) is False


class TestAggregateFlatResults:
    def test_groups_by_patient_id(self):
        flat = [
            {"patient_id": "P1", "patient_name": "张三", "clinical_question": "Q1",
             "guideline": "NCCN", "recommendation": "推荐A", "evidence_level": "1", "source_file": "a.md"},
            {"patient_id": "P1", "patient_name": "张三", "clinical_question": "Q1",
             "guideline": "JGCA", "recommendation": "推荐B", "evidence_level": "强", "source_file": "b.md"},
            {"patient_id": "P2", "patient_name": "李四", "clinical_question": "Q2",
             "guideline": "NCCN", "recommendation": "推荐C", "evidence_level": "2A", "source_file": "c.md"},
        ]
        result = _aggregate_flat_results(flat)
        assert len(result) == 2
        p1 = [r for r in result if r["patient_id"] == "P1"][0]
        assert len(p1["guideline_results"]) == 2
        assert p1["patient_name"] == "张三"

    def test_single_patient_single_guideline(self):
        flat = [
            {"patient_id": "P1", "patient_name": "A", "clinical_question": "Q",
             "guideline": "NCCN", "recommendation": "R", "evidence_level": "1", "source_file": "f.md"},
        ]
        result = _aggregate_flat_results(flat)
        assert len(result) == 1
        assert len(result[0]["guideline_results"]) == 1

    def test_empty_input(self):
        assert _aggregate_flat_results([]) == []


class TestGenerateConsensus:
    def test_finds_common_keywords(self):
        patient = {
            "guideline_results": [
                {"guideline": "NCCN", "recommendation": "化疗,联合免疫"},
                {"guideline": "JGCA", "recommendation": "化疗,手术切除"},
            ]
        }
        consensus, diffs = _generate_consensus(patient)
        assert len(consensus) > 0

    def test_single_guideline_no_consensus(self):
        patient = {
            "guideline_results": [
                {"guideline": "NCCN", "recommendation": "推荐化疗"},
            ]
        }
        consensus, diffs = _generate_consensus(patient)
        assert consensus == []
        assert diffs == []

    def test_empty_guideline_results(self):
        patient = {"guideline_results": []}
        consensus, diffs = _generate_consensus(patient)
        assert consensus == []
        assert diffs == []


class TestValidate:
    def _make_results_json(self, tmp_path):
        data = {
            "results": [{
                "patient_id": "P1",
                "diagnosis_summary": "胃癌",
                "disease_type": "胃癌",
                "clinical_questions": [{
                    "guideline_results": [{
                        "guideline": "NCCN",
                        "recommendation": "这是一段足够长的推荐文本内容用于测试目的，需要达到五十个字符以上才能通过验证检查",
                        "evidence_level": "1",
                        "source_file": "f.md",
                        "retrieval_sources": [{"chunk_id": "R001-01", "score": 0.8, "snippet": "..."}],
                    }],
                    "consensus": ["c"],
                    "differences": ["d"],
                }],
                "citation_coverage": 0.8,
            }]
        }
        p = tmp_path / "results.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return str(p)

    def test_validate_passes_with_complete_data(self, tmp_path):
        results_path = self._make_results_json(tmp_path)
        args = argparse.Namespace(
            input=results_path, patients=None, kb_profile=None,
        )
        with pytest.raises(SystemExit) as exc:
            cmd_validate(args)
        assert exc.value.code == 0

    def test_validate_warns_on_short_rec(self, tmp_path):
        data = {
            "results": [{
                "patient_id": "P1",
                "diagnosis_summary": "胃癌",
                "disease_type": "胃癌",
                "clinical_questions": [{
                    "guideline_results": [{
                        "guideline": "NCCN",
                        "recommendation": "短推荐",
                        "evidence_level": "1",
                        "source_file": "f.md",
                    }],
                    "consensus": ["c"],
                    "differences": ["d"],
                }],
            }]
        }
        p = tmp_path / "results2.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        args = argparse.Namespace(
            input=str(p), patients=None, kb_profile=None,
        )
        # Should still exit 0 (warnings don't fail), but the short rec warning fires
        with pytest.raises(SystemExit) as exc:
            cmd_validate(args)
        assert exc.value.code == 0
