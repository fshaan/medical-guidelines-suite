import json
import pytest
from pathlib import Path

@pytest.fixture
def sample_patients():
    """12 位模拟患者（覆盖 structured + narrative 格式）"""
    patients = []
    for i in range(10):
        patients.append({
            "patient_id": f"P{i+1:03d}",
            "patient_name": f"患者{i+1}",
            "gender": "男" if i % 2 == 0 else "女",
            "age": 50 + i,
            "primary_site": "胃体" if i % 3 != 0 else "食管胃结合部",
            "siewert_type": "II" if i % 3 == 0 else None,
            "pathology": "腺癌" if i % 2 == 0 else "印戒细胞癌",
            "patient_type": "zq_初治" if i < 5 else "sq_术前治疗后（包括外院治疗）",
            "prior_treatment": None if i < 5 else f"{i}C SOX+PD-1",
            "biopsy_molecular": "hj_pMMR/MSS,hj_Her2 0,hj_CPS<1" if i % 2 == 0 else "hj_dMMR/MSI-H,hj_Her2 3+,hj_CPS≥10",
            "gross_molecular": None,
            "abnormal_markers": "CEA,CA199" if i % 3 == 0 else None,
            "marker_change": "升高" if i % 3 == 0 else None,
            "staging_prefix": "c" if i < 5 else "yc",
            "t_stage": f"T{min(i % 4 + 1, 4)}{'b' if i == 3 else 'a' if i % 4 == 3 else ''}",
            "t4b_invasion": "胰腺" if i == 3 else None,
            "n_stage": f"N{min(i % 4, 3)}",
            "m_stage": "M0" if i < 7 else "M1",
            "m_sites": None if i < 7 else "腹膜" if i == 7 else "肝",
            "staging_notes": None,
            "symptom_change": None,
            "response": "不适用" if i < 5 else ["PD", "SD", "PR"][i % 3],
            "tumor_emergency": "出血" if i == 9 else "无",
            "comorbidities": "糖尿病" if i == 8 else None,
            "clinical_narrative": None,
        })
    patients.append({
        "patient_id": "N001",
        "patient_name": "叙述患者1",
        "clinical_narrative": "男，69岁，胃中部（M）印戒细胞癌，ycT4aN1M1腹膜转移，活检hj_pMMR/MSS，4C SOX+PD-1，PD。",
        **{k: None for k in [
            "gender", "age", "primary_site", "siewert_type", "pathology",
            "patient_type", "prior_treatment", "lesion_count", "biopsy_molecular",
            "gross_molecular", "abnormal_markers", "marker_change", "staging_prefix",
            "t_stage", "t4b_invasion", "n_stage", "m_stage", "m_sites",
            "staging_notes", "symptom_change", "response", "tumor_emergency", "comorbidities",
        ]},
    })
    patients.append({
        "patient_id": "N002",
        "patient_name": "叙述患者2",
        "clinical_narrative": "女，45岁，胃窦低分化腺癌，cT2N0M0。",
        **{k: None for k in [
            "gender", "age", "primary_site", "siewert_type", "pathology",
            "patient_type", "prior_treatment", "lesion_count", "biopsy_molecular",
            "gross_molecular", "abnormal_markers", "marker_change", "staging_prefix",
            "t_stage", "t4b_invasion", "n_stage", "m_stage", "m_sites",
            "staging_notes", "symptom_change", "response", "tumor_emergency", "comorbidities",
        ]},
    })
    return patients

@pytest.fixture
def patients_json(tmp_path, sample_patients):
    """写入 patients.json 并返回路径"""
    data = {
        "input_format": "structured",
        "input_file": "/mock/input.xlsx",
        "parsed_at": "2026-03-25",
        "patient_count": len(sample_patients),
        "patients": sample_patients,
    }
    p = tmp_path / "patients.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p

@pytest.fixture
def mock_kb(tmp_path):
    """模拟知识库目录结构"""
    kb = tmp_path / "guidelines"
    for org in ["NCCN", "ESMO", "CSCO"]:
        ext_dir = kb / org / "extracted"
        ext_dir.mkdir(parents=True)
        (ext_dir / f"{org}_GastricCancer.txt").write_text(
            f"Line 1: {org} Gastric Cancer Guideline\n" * 100,
            encoding="utf-8",
        )
        (kb / org / "data_structure.md").write_text(
            f"# {org} 指南\n\n## 文件清单\n\n### 提取文件（推荐使用）\n\n"
            f"| 文件 | 版本 | 状态 | 行数 | 说明 |\n"
            f"|------|------|------|------|------|\n"
            f"| extracted/{org}_GastricCancer.txt | 2026 | **默认** | 100 | 胃癌 |\n\n"
            f"## 常用检索关键词\n\n### 诊断相关\n- diagnosis, 诊断\n- HER2\n\n"
            f"### 治疗相关\n- surgery, 手术\n- chemotherapy, 化疗\n",
            encoding="utf-8",
        )
    (kb / "data_structure.md").write_text(
        "# 临床指南知识库总览\n\n## 指南目录\n\n"
        "### NCCN/\n- **机构**: National Comprehensive Cancer Network\n- **版本**: 2026\n\n"
        "### ESMO/\n- **机构**: European Society for Medical Oncology\n- **版本**: 2024\n\n"
        "### CSCO/\n- **机构**: Chinese Society of Clinical Oncology\n- **版本**: 2025\n\n"
        "## 常见临床问题 → 指南映射\n\n"
        "| 临床问题类别 | 首选指南 | 补充参考 |\n"
        "|-------------|---------|----------|\n"
        "| 手术术式选择 | NCCN | CSCO |\n"
        "| 围手术期化疗 | NCCN, ESMO | CSCO |\n"
        "| 晚期一线治疗 | NCCN, ESMO | CSCO |\n",
        encoding="utf-8",
    )
    return kb
