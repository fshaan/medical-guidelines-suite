import json
import pytest
from scripts.batch_pipeline import _bigram_jaccard, _check_cross_batch_similarity, _check_batch_depth_decay


def test_bigram_jaccard_identical():
    assert _bigram_jaccard("完全相同的文本", "完全相同的文本") == 1.0


def test_bigram_jaccard_different():
    score = _bigram_jaccard("胃癌一线治疗方案", "肺癌诊断标准指南")
    assert score < 0.5


def test_bigram_jaccard_empty():
    assert _bigram_jaccard("", "") == 0.0
    assert _bigram_jaccard("abc", "") == 0.0


def test_cross_batch_similar_warning():
    results = [
        {"patient_id": "P001", "batch_source": "batch_001",
         "clinical_questions": [{"guideline_results": [
             {"recommendation": "HER2阳性胃癌一线推荐曲妥珠单抗联合化疗，证据等级Category 1"}
         ]}]},
        {"patient_id": "P002", "batch_source": "batch_002",
         "clinical_questions": [{"guideline_results": [
             {"recommendation": "HER2阳性胃癌一线推荐曲妥珠单抗联合化疗，证据等级Category 1"}
         ]}]},
    ]
    warnings = _check_cross_batch_similarity(results, threshold=0.8)
    assert len(warnings) > 0


def test_cross_batch_no_warning_different():
    results = [
        {"patient_id": "P001", "batch_source": "batch_001",
         "clinical_questions": [{"guideline_results": [
             {"recommendation": "HER2阳性胃癌一线推荐曲妥珠单抗联合化疗"}
         ]}]},
        {"patient_id": "P002", "batch_source": "batch_002",
         "clinical_questions": [{"guideline_results": [
             {"recommendation": "MSI-H胃癌推荐免疫检查点抑制剂单药治疗帕博利珠单抗"}
         ]}]},
    ]
    warnings = _check_cross_batch_similarity(results, threshold=0.8)
    assert len(warnings) == 0


from scripts.batch_pipeline import _check_org_coverage


def test_org_coverage_warning():
    results = [
        {"patient_id": "P001",
         "clinical_questions": [{"guideline_results": [
             {"guideline": "NCCN", "recommendation": "..."},
             {"guideline": "ESMO", "recommendation": "..."},
         ]}]},
    ]
    known_orgs = ["NCCN", "ESMO", "CSCO", "Japanese", "Korean", "CACA"]
    warnings = _check_org_coverage(results, known_orgs)
    assert len(warnings) > 0


def test_org_coverage_pass():
    results = [
        {"patient_id": "P001",
         "clinical_questions": [{"guideline_results": [
             {"guideline": org, "recommendation": "..."} for org in ["NCCN", "ESMO", "CSCO"]
         ]}]},
    ]
    warnings = _check_org_coverage(results, ["NCCN", "ESMO", "CSCO"])
    assert len(warnings) == 0


def test_batch_depth_decay_detected():
    """后半段匹配数 < 前半段 40% → WARNING"""
    results = []
    for bi in range(1, 4):
        results.append({
            "patient_id": f"P{bi:03d}", "batch_source": f"batch_{bi:03d}",
            "clinical_questions": [{"guideline_results": [
                {"guideline": "NCCN", "recommendation": "x" * 100,
                 "execution_log": [
                     {"cmd_id": f"CMD-P001-NCCN-0{i}", "match_count": 10, "first_match_snippet": "y" * 30}
                     for i in range(1, 4)
                 ]},
            ]}],
        })
    for bi in range(4, 7):
        results.append({
            "patient_id": f"P{bi:03d}", "batch_source": f"batch_{bi:03d}",
            "clinical_questions": [{"guideline_results": [
                {"guideline": "NCCN", "recommendation": "x" * 100,
                 "execution_log": [
                     {"cmd_id": f"CMD-P001-NCCN-01", "match_count": 1, "first_match_snippet": "z" * 30}
                 ]},
            ]}],
        })
    warnings = _check_batch_depth_decay(results)
    assert len(warnings) > 0
    assert "衰减" in warnings[0]


def test_batch_depth_no_decay():
    """深度均匀 → 无 WARNING"""
    results = []
    for bi in range(1, 5):
        results.append({
            "patient_id": f"P{bi:03d}", "batch_source": f"batch_{bi:03d}",
            "clinical_questions": [{"guideline_results": [
                {"guideline": "NCCN", "recommendation": "x" * 100,
                 "execution_log": [
                     {"cmd_id": f"CMD-P001-NCCN-0{i}", "match_count": 10, "first_match_snippet": "y" * 30}
                     for i in range(1, 4)
                 ]},
            ]}],
        })
    warnings = _check_batch_depth_decay(results)
    assert len(warnings) == 0


def test_batch_depth_single_batch():
    """单批次 → 跳过衰减检测"""
    results = [{
        "patient_id": "P001", "batch_source": "batch_001",
        "clinical_questions": [{"guideline_results": [
            {"guideline": "NCCN", "recommendation": "x" * 100,
             "execution_log": [{"cmd_id": "CMD-P001-NCCN-01", "match_count": 5, "first_match_snippet": "y" * 30}]},
        ]}],
    }]
    warnings = _check_batch_depth_decay(results)
    assert len(warnings) == 0
