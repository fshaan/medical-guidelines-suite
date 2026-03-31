"""Tests for --profile slim mode."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from batch_pipeline import ProfileConfig, PROFILE_FULL, PROFILE_SLIM, get_profile, generate_grep_commands, filter_orgs_by_disease, generate_batch_prompt, _is_flat_format, _aggregate_flat_results, _generate_consensus


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
                "NCCN": [{"file": "NCCN_GastricCancer_2026.txt", "path": "/kb/NCCN/extracted/NCCN_GastricCancer_2026.txt"}],
                "JGCA": [{"file": "JGCA_Gastric_Guidelines.txt", "path": "/kb/JGCA/extracted/JGCA_Gastric_Guidelines.txt"}],
                "ESMO": [{"file": "ESMO_BreastCancer_2025.txt", "path": "/kb/ESMO/extracted/ESMO_BreastCancer_2025.txt"}],
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
                "NCCN": [{"file": "NCCN_Lung.txt", "path": "..."}],
                "JGCA": [{"file": "JGCA_Lung.txt", "path": "..."}],
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
                "NCCN": [{"file": "NCCN_GastricCancer.txt"}],
                "JGCA": [{"file": "JGCA_Gastric.txt"}],
                "ESMO": [{"file": "ESMO_GastricCancer.txt"}],
            },
        }

    def test_slim_produces_grouped_commands(self):
        config = get_profile("slim")
        features = self._make_features()
        kb = self._make_kb_profile()
        cmds = generate_grep_commands(features, kb, Path("/kb"), config=config)
        # 4 groups x 3 orgs = 12
        assert len(cmds) == 12

    def test_slim_group_merges_keywords(self):
        config = get_profile("slim")
        features = self._make_features()
        kb = self._make_kb_profile()
        cmds = generate_grep_commands(features, kb, Path("/kb"), config=config)
        group1_cmds = [c for c in cmds if c["dimension"] == "diagnosis_staging_metastasis"]
        assert len(group1_cmds) == 3  # one per org

    def test_slim_skips_empty_groups(self):
        config = get_profile("slim")
        features = self._make_features()
        features["comorbidity_keywords"] = []
        features["special_keywords"] = []
        kb = self._make_kb_profile()
        cmds = generate_grep_commands(features, kb, Path("/kb"), config=config)
        # Group 4 empty → 3 groups x 3 orgs = 9
        assert len(cmds) == 9

    def test_full_profile_unchanged(self):
        """config=None produces original behavior."""
        features = self._make_features()
        kb = self._make_kb_profile()
        cmds_default = generate_grep_commands(features, kb, Path("/kb"))
        cmds_explicit = generate_grep_commands(features, kb, Path("/kb"), config=None)
        assert len(cmds_default) == len(cmds_explicit)


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
            "grep_commands": [
                {"org": "NCCN", "dimension": "diagnosis_staging_metastasis",
                 "command": 'grep -n -i --include="*.txt" -r "胃癌" "/kb/NCCN/extracted"'},
            ],
        }]

    def _make_kb_profile(self):
        return {
            "orgs": ["NCCN"],
            "org_files": {"NCCN": [{"file": "NCCN_Gastric.txt"}]},
            "root_index_content": "test index",
        }

    def test_slim_prompt_contains_micro_checkpoints(self):
        config = get_profile("slim")
        prompt = generate_batch_prompt(
            self._make_batch(), self._make_kb_profile(), "/kb", 1, 1, config=config,
        )
        assert "自检" in prompt

    def test_slim_prompt_uses_flat_json_template(self):
        config = get_profile("slim")
        prompt = generate_batch_prompt(
            self._make_batch(), self._make_kb_profile(), "/kb", 1, 1, config=config,
        )
        assert '"guideline":' in prompt
        assert "guideline_results" not in prompt

    def test_slim_prompt_no_execution_log(self):
        config = get_profile("slim")
        prompt = generate_batch_prompt(
            self._make_batch(), self._make_kb_profile(), "/kb", 1, 1, config=config,
        )
        assert "execution_log" not in prompt
        assert "execution_summary" not in prompt

    def test_full_prompt_unchanged(self):
        prompt = generate_batch_prompt(
            self._make_batch(), self._make_kb_profile(), "/kb", 1, 1,
        )
        assert "execution_log" in prompt


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
             "guideline": "NCCN", "recommendation": "推荐A", "evidence_level": "1", "source_file": "a.txt"},
            {"patient_id": "P1", "patient_name": "张三", "clinical_question": "Q1",
             "guideline": "JGCA", "recommendation": "推荐B", "evidence_level": "强", "source_file": "b.txt"},
            {"patient_id": "P2", "patient_name": "李四", "clinical_question": "Q2",
             "guideline": "NCCN", "recommendation": "推荐C", "evidence_level": "2A", "source_file": "c.txt"},
        ]
        result = _aggregate_flat_results(flat)
        assert len(result) == 2
        p1 = [r for r in result if r["patient_id"] == "P1"][0]
        assert len(p1["guideline_results"]) == 2
        assert p1["patient_name"] == "张三"

    def test_single_patient_single_guideline(self):
        flat = [
            {"patient_id": "P1", "patient_name": "A", "clinical_question": "Q",
             "guideline": "NCCN", "recommendation": "R", "evidence_level": "1", "source_file": "f.txt"},
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
