"""把 Markdown 临床案例构造为 pipeline 的 patients.json 格式。

10 个真实案例（2026-07-03 用户提供），含分期解析（cT3N2M0 → prefix/t/n/m）。
输出 Output/patients_new.json，供 batch_pipeline.py run 使用。
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

# 10 个真实案例（按用户提供顺序）
CASES = [
    {"gender": "男", "age": 66, "primary_site": "胃食管结合部（EGJ）", "pathology": "腺癌",
     "stage": "cT3N2M0", "m_sites": "无",
     "biopsy_molecular": "hj_pMMR/MSS,hj_CPS≥10,hj_Her2 1+,hj_Claudin 18.2 2+/3+ ≥75%",
     "comorbidities": "无", "prior_treatment": "未提供", "response": "不适用"},
    {"gender": "男", "age": 63, "primary_site": "胃食管结合部（EGJ）", "pathology": "腺癌",
     "stage": "T3N1M0", "m_sites": "无",
     "biopsy_molecular": "hj_pMMR/MSS,hj_Claudin 18.2 2+/3+ ≥75%,hj_1≤CPS<5,hj_Her2 0",
     "comorbidities": "无", "prior_treatment": "4周期sox+pd1", "response": "SD"},
    {"gender": "男", "age": 40, "primary_site": "胃下部（L）", "pathology": "腺癌,印戒细胞癌",
     "stage": "ycT3N1M0", "m_sites": "无",
     "biopsy_molecular": "hj_pMMR/MSS,hj_Claudin 18.2 2+/3+ ≥75%,hj_1≤CPS<5,hj_Her2 1+,hj_5≤CPS<10",
     "comorbidities": "心梗", "prior_treatment": "3周期sox", "response": "缩小SD"},
    {"gender": "男", "age": 54, "primary_site": "乙状结肠（SC）", "pathology": "腺癌",
     "stage": "cT3N1M0", "m_sites": "无",
     "biopsy_molecular": "hj_pMMR/MSS",
     "comorbidities": "无", "prior_treatment": "未提供", "response": "不适用"},
    {"gender": "男", "age": 74, "primary_site": "胃下部（L）", "pathology": "腺癌",
     "stage": "cT4aN3aM0", "m_sites": "无",
     "biopsy_molecular": "hj_pMMR/MSS,hj_1≤CPS<5,hj_Claudin 18.2 2+/3+ <25%",
     "comorbidities": "糖尿病 高血压", "prior_treatment": "未提供", "response": "不适用"},
    {"gender": "男", "age": 68, "primary_site": "胃上部（U）", "pathology": "腺癌",
     "stage": "cT1bN0M0", "m_sites": "无",
     "biopsy_molecular": "hj_pMMR/MSS,hj_CPS<1,hj_Her2 0,hj_Claudin 18.2 2+/3+ 50%~74%",
     "comorbidities": "无", "prior_treatment": "未提供", "response": "不适用"},
    {"gender": "女", "age": 77, "primary_site": "胃下部（L）", "pathology": "腺癌",
     "stage": "cT4aN2M1", "m_sites": "16组",
     "biopsy_molecular": "hj_dMMR/MSI未做,hj_CPS≥10,hj_Her2 0",
     "comorbidities": "无", "prior_treatment": "未提供", "response": "不适用"},
    {"gender": "男", "age": 73, "primary_site": "胃食管结合部（EGJ）", "pathology": "腺癌",
     "stage": "cT3N2M0", "m_sites": "无",
     "biopsy_molecular": "hj_pMMR/MSS,hj_Her2 0,hj_CPS≥10,hj_Her2 3+,hj_Claudin 18.2 2+/3+ <25%",
     "comorbidities": "无", "prior_treatment": "未提供", "response": "不适用"},
    {"gender": "男", "age": 67, "primary_site": "升结肠（AC）,胃下部（L）", "pathology": "腺癌",
     "stage": "ycT3N2M0", "m_sites": "无",
     "biopsy_molecular": "hj_pMMR/MSS,hj_Her2 0,hj_Claudin 18.2 2+/3+ <25%,hj_CPS<1",
     "comorbidities": "支气管扩张", "prior_treatment": "sox 6周期", "response": "缩小SD"},
    {"gender": "男", "age": 51, "primary_site": "胃中部（M）", "pathology": "腺癌",
     "stage": "ycT3N2M0", "m_sites": "无",
     "biopsy_molecular": "hj_CPS<1,hj_pMMR/MSS",
     "comorbidities": "无", "prior_treatment": "sox+ak104/安慰剂3周期", "response": "缩小SD"},
]

_STAGE_RE = re.compile(r"^(yc|c|p|ycp|cp)?T(\d[a-z]?)N(\d[a-z]?)M(\d[a-z]?)")


def parse_stage(stage: str):
    m = _STAGE_RE.match(stage)
    if not m:
        return None, None, None, None
    prefix = m.group(1) or None
    return prefix, f"T{m.group(2)}", f"N{m.group(3)}", f"M{m.group(4)}"


def build():
    patients = []
    for i, c in enumerate(CASES, 1):
        pid = f"CASE{i:03d}"
        prefix, t, n, m = parse_stage(c["stage"])
        patients.append({
            "patient_id": pid,
            "patient_name": f"案例{i}",
            "gender": c["gender"],
            "age": c["age"],
            "primary_site": c["primary_site"],
            "siewert_type": None,
            "pathology": c["pathology"],
            "patient_type": None,
            "prior_treatment": c["prior_treatment"],
            "lesion_count": None,
            "biopsy_molecular": c["biopsy_molecular"],
            "gross_molecular": None,
            "abnormal_markers": None,
            "marker_change": None,
            "staging_prefix": prefix,
            "t_stage": t,
            "t4b_invasion": None,
            "n_stage": n,
            "m_stage": m,
            "m_sites": c["m_sites"] if c["m_sites"] != "无" else None,
            "staging_notes": None,
            "symptom_change": None,
            "response": c["response"] if c["response"] != "不适用" else None,
            "tumor_emergency": "无",
            "comorbidities": c["comorbidities"],
            "clinical_narrative": None,
        })
    data = {
        "input_format": "structured",
        "input_file": "10 真实案例（用户提供 2026-07-03）",
        "parsed_at": str(date.today()),
        "patient_count": len(patients),
        "patients": patients,
    }
    out = Path("Output/patients_new.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {out}（{len(patients)} 患者）")
    for p in patients:
        print(f"  [{p['patient_id']}] {p['patient_name']} {p['gender']}{p['age']}岁 "
              f"{p['primary_site']} {p['pathology']} {p['staging_prefix'] or ''}{p['t_stage']}{p['n_stage']}{p['m_stage']}")


if __name__ == "__main__":
    build()
