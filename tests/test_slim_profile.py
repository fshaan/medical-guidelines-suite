"""Tests for --profile slim mode."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import json
import argparse
from batch_pipeline import ProfileConfig, PROFILE_FULL, PROFILE_SLIM, get_profile, filter_orgs_by_disease, generate_batch_prompt, _is_flat_format, _aggregate_flat_results, _generate_consensus, cmd_validate


class TestProfileConfig:
    def test_full_profile_defaults(self):
        config = get_profile("full")
        assert config.name == "full"
        assert config.dimension_groups is None
        assert config.min_rec_length == 50
        assert config.skip_anti_laziness is False
        assert config.flat_json is False

    def test_slim_profile_values(self):
        config = get_profile("slim")
        assert config.name == "slim"
        assert len(config.dimension_groups) == 4
        assert config.min_rec_length == 20
        assert config.skip_anti_laziness is True
        assert config.flat_json is True
        assert config.org_filter_by_disease is True
        assert config.micro_checkpoints is True

    def test_unknown_profile_raises(self):
        with pytest.raises(KeyError):
            get_profile("unknown")


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


class TestGrepGenerationSlim:
    def _make_features(self):
        return {
            "diagnosis_keywords": ["胃癌", "gastric"],
            "staging_keywords": ["T3"],
            "metastasis_keywords": ["peritoneal"],
            "molecular_keywords": ["HER2"],
            "marker_keywords": ["CEA"],
            "treatment_keywords": ["SOX"],
            "event_keywords": ["术后"],
            "comorbidity_keywords": ["diabetes"],
            "special_keywords": ["elderly"],
            "all_keywords": ["胃癌", "gastric", "T3", "peritoneal", "HER2", "CEA", "SOX", "术后", "diabetes", "elderly"],
        }

    def _make_kb_profile(self):
        return {
            "orgs": ["NCCN", "JGCA", "ESMO"],
            "org_files": {
                "NCCN": [{"file": "NCCN_GastricCancer.md"}],
                "JGCA": [{"file": "JGCA_Gastric.md"}],
                "ESMO": [{"file": "ESMO_GastricCancer.md"}],
            },
        }

    def test_slim_profile_has_dimension_groups(self):
        config = get_profile("slim")
        assert config.dimension_groups is not None
        assert len(config.dimension_groups) == 4


class TestSlimPrompt:
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

    def test_slim_prompt_contains_retrieval_results(self):
        config = get_profile("slim")
        prompt = generate_batch_prompt(
            self._make_batch(), self._make_kb_profile(), "/kb", 1, 1, config=config,
        )
        assert "Gastric cancer treatment" in prompt
        assert "retrieval_sources" in prompt

    def test_slim_prompt_no_grep(self):
        config = get_profile("slim")
        prompt = generate_batch_prompt(
            self._make_batch(), self._make_kb_profile(), "/kb", 1, 1, config=config,
        )
        assert "grep" not in prompt.lower()
        assert "CMD-P" not in prompt
        assert "execution_log" not in prompt

    def test_full_prompt_same_structure(self):
        prompt = generate_batch_prompt(
            self._make_batch(), self._make_kb_profile(), "/kb", 1, 1,
        )
        assert "retrieval_sources" in prompt
        assert "grep" not in prompt.lower()


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


class TestValidateSlim:
    def _make_results_json(self, tmp_path):
        data = {
            "results": [{
                "patient_id": "P1",
                "diagnosis_summary": "胃癌",
                "disease_type": "胃癌",
                "clinical_questions": [{
                    "guideline_results": [{
                        "guideline": "NCCN",
                        "recommendation": "这是一段至少二十个字的推荐文本内容用于测试",
                    }],
                    "consensus": [],
                    "differences": [],
                }],
            }]
        }
        p = tmp_path / "results.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return str(p)

    def test_slim_validate_missing_evidence_is_not_error(self, tmp_path):
        """In slim mode, missing evidence_level should not cause exit(1)."""
        results_path = self._make_results_json(tmp_path)
        args = argparse.Namespace(
            input=results_path, patients=None, kb_profile=None, profile="slim",
        )
        with pytest.raises(SystemExit) as exc:
            cmd_validate(args)
        assert exc.value.code == 0

    def test_slim_validate_short_rec_passes_at_20(self, tmp_path):
        """Slim mode allows rec >= 20 chars."""
        data = {
            "results": [{
                "patient_id": "P1",
                "diagnosis_summary": "胃癌",
                "disease_type": "胃癌",
                "clinical_questions": [{
                    "guideline_results": [{
                        "guideline": "NCCN",
                        "recommendation": "这是二十字的推荐文本至少够了吧应该",
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
            input=str(p), patients=None, kb_profile=None, profile="slim",
        )
        with pytest.raises(SystemExit) as exc:
            cmd_validate(args)
        assert exc.value.code == 0


import subprocess


class TestCLIIntegration:
    def test_orchestrate_accepts_profile_flag(self):
        result = subprocess.run(
            ["python3", "scripts/batch_pipeline.py", "orchestrate", "--help"],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert "--profile" in result.stdout
        assert "slim" in result.stdout

    def test_validate_accepts_profile_flag(self):
        result = subprocess.run(
            ["python3", "scripts/batch_pipeline.py", "validate", "--help"],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert "--profile" in result.stdout

    def test_verify_batch_accepts_profile_flag(self):
        result = subprocess.run(
            ["python3", "scripts/batch_pipeline.py", "verify-batch", "--help"],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert "--profile" in result.stdout
