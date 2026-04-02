import pytest
from scripts.batch_pipeline import extract_patient_features

def test_structured_full_fields(sample_patients):
    p = sample_patients[0]
    features = extract_patient_features(p)
    assert "胃" in " ".join(features["diagnosis_keywords"])
    assert any("T1" in k or "t1" in k.lower() for k in features["staging_keywords"])
    assert "pMMR" in " ".join(features["molecular_keywords"]) or \
           "MSS" in " ".join(features["molecular_keywords"])
    assert features["confidence"] == "high"
    assert len(features["all_keywords"]) > 0

def test_structured_m1_with_metastasis(sample_patients):
    p = sample_patients[7]
    features = extract_patient_features(p)
    assert any("腹膜" in k or "peritoneal" in k.lower()
               for k in features["metastasis_keywords"])

def test_structured_with_comorbidity(sample_patients):
    p = sample_patients[8]
    features = extract_patient_features(p)
    assert any("糖尿病" in k for k in features["comorbidity_keywords"])

def test_structured_tumor_emergency(sample_patients):
    p = sample_patients[9]
    features = extract_patient_features(p)
    assert any("出血" in k or "bleeding" in k.lower()
               for k in features["event_keywords"])

def test_structured_prior_treatment(sample_patients):
    p = sample_patients[5]
    features = extract_patient_features(p)
    assert any("SOX" in k or "PD-1" in k for k in features["treatment_keywords"])

def test_narrative_format(sample_patients):
    p = sample_patients[10]
    features = extract_patient_features(p)
    assert len(features["all_keywords"]) > 0
    assert any("胃" in k for k in features["diagnosis_keywords"])

def test_narrative_sparse_low_confidence(sample_patients):
    p = sample_patients[11]
    features = extract_patient_features(p)
    assert features["confidence"] in ("high", "low")

def test_all_none_patient():
    p = {k: None for k in ["patient_id", "patient_name", "primary_site", "pathology",
                            "biopsy_molecular", "t_stage", "n_stage", "m_stage",
                            "m_sites", "comorbidities", "clinical_narrative"]}
    p["patient_id"] = "EMPTY"
    features = extract_patient_features(p)
    assert features["confidence"] == "low"
    assert features["all_keywords"] == [] or len(features["all_keywords"]) == 0

def test_feature_dimensions():
    p = {"patient_id": "X", "primary_site": "胃", "pathology": "腺癌",
         "biopsy_molecular": "hj_HER2 3+", "t_stage": "T3", "n_stage": "N1",
         "m_stage": "M0", "clinical_narrative": None}
    features = extract_patient_features(p)
    expected_keys = {
        "diagnosis_keywords", "staging_keywords", "metastasis_keywords",
        "molecular_keywords", "treatment_keywords", "marker_keywords",
        "event_keywords", "comorbidity_keywords", "special_keywords",
        "all_keywords", "confidence",
    }
    assert expected_keys.issubset(set(features.keys()))


def test_lung_patient_no_gastric_keywords():
    """肺癌患者不应出现 gastric 关键词"""
    p = {
        "patient_id": "L001", "patient_name": "肺癌患者",
        "primary_site": "右肺上叶",
        "pathology": "腺癌",
        **{k: None for k in [
            "gender", "age", "siewert_type", "patient_type", "prior_treatment",
            "biopsy_molecular", "gross_molecular", "abnormal_markers", "marker_change",
            "staging_prefix", "t_stage", "t4b_invasion", "n_stage", "m_stage",
            "m_sites", "staging_notes", "symptom_change", "response",
            "tumor_emergency", "comorbidities", "clinical_narrative",
        ]},
    }
    from scripts.batch_pipeline import extract_patient_features
    features = extract_patient_features(p)
    all_kw = " ".join(features["diagnosis_keywords"]).lower()
    assert "gastric" not in all_kw
    assert "胃癌" not in all_kw
    # 应包含肺相关关键词
    assert any(k in all_kw for k in ["lung", "pulmonary", "肺"])


def test_gastric_patient_has_correct_keywords():
    """胃癌患者应映射出正确的中英文关键词"""
    p = {
        "patient_id": "G001", "patient_name": "胃癌患者",
        "primary_site": "胃体",
        "pathology": "腺癌",
        **{k: None for k in [
            "gender", "age", "siewert_type", "patient_type", "prior_treatment",
            "biopsy_molecular", "gross_molecular", "abnormal_markers", "marker_change",
            "staging_prefix", "t_stage", "t4b_invasion", "n_stage", "m_stage",
            "m_sites", "staging_notes", "symptom_change", "response",
            "tumor_emergency", "comorbidities", "clinical_narrative",
        ]},
    }
    from scripts.batch_pipeline import extract_patient_features
    features = extract_patient_features(p)
    all_kw = " ".join(features["diagnosis_keywords"]).lower()
    assert "gastric" in all_kw
    assert "stomach" in all_kw


def test_unmapped_cancer_retains_primary_site():
    """未在映射表中的癌种只保留 primary_site 原值"""
    p = {
        "patient_id": "U001", "patient_name": "罕见癌种",
        "primary_site": "腮腺",
        "pathology": "腺样囊性癌",
        **{k: None for k in [
            "gender", "age", "siewert_type", "patient_type", "prior_treatment",
            "biopsy_molecular", "gross_molecular", "abnormal_markers", "marker_change",
            "staging_prefix", "t_stage", "t4b_invasion", "n_stage", "m_stage",
            "m_sites", "staging_notes", "symptom_change", "response",
            "tumor_emergency", "comorbidities", "clinical_narrative",
        ]},
    }
    from scripts.batch_pipeline import extract_patient_features
    features = extract_patient_features(p)
    assert "腮腺" in features["diagnosis_keywords"]
    all_kw = " ".join(features["diagnosis_keywords"]).lower()
    assert "gastric" not in all_kw


def test_special_pathology_retained_as_keyword():
    """特殊病理描述作为完整关键词保留"""
    p = {
        "patient_id": "S001", "patient_name": "特殊病理",
        "primary_site": "Gastric Signet Ring Cell Carcinoma",
        **{k: None for k in [
            "gender", "age", "pathology", "siewert_type", "patient_type",
            "prior_treatment", "biopsy_molecular", "gross_molecular",
            "abnormal_markers", "marker_change", "staging_prefix", "t_stage",
            "t4b_invasion", "n_stage", "m_stage", "m_sites", "staging_notes",
            "symptom_change", "response", "tumor_emergency", "comorbidities",
            "clinical_narrative",
        ]},
    }
    from scripts.batch_pipeline import extract_patient_features
    features = extract_patient_features(p)
    assert "Gastric Signet Ring Cell Carcinoma" in features["diagnosis_keywords"]
