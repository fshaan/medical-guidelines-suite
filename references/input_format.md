# Input Format Specification

Two independent input formats are supported. The user provides **one** Excel file in either format.

---

## Format A: Structured Data Table (26 columns)

**Auto-detection**: Column count ≥ 10 AND contains "患者ID号" header.

### Column List

| # | Column Header | JSON Field | Required | Description |
|---|--------------|------------|----------|-------------|
| 0 | 自动编号 | (ignored) | No | Auto-increment ID |
| 1 | 患者ID号 | patient_id | **Yes** | Unique patient identifier |
| 2 | 患者姓名 | patient_name | **Yes** | Patient name |
| 3 | 性别 | gender | Yes | 男/女 |
| 4 | 年龄 | age | Yes | Integer |
| 5 | 原发部位 | primary_site | **Yes** | Tumor location, e.g., "胃中部（M）" |
| 6 | Siewert分型 | siewert_type | No | For EGJ tumors: I/II/III |
| 7 | 病理类型 | pathology | **Yes** | e.g., 腺癌, 印戒细胞癌, 鳞癌 |
| 8 | 患者类型 | patient_type | **Yes** | "zq_初治" or "sq_术前治疗后（包括外院治疗）" |
| 9 | 既往治疗说明 | prior_treatment | Yes | e.g., "4C SOX+PD-1" |
| 10 | 原发灶数量 | lesion_count | No | 单发/多发 |
| 11 | 活检样本-分子分型 | biopsy_molecular | **Yes** | e.g., "hj_pMMR/MSS", "hj_Her2 0,hj_CPS≥10" |
| 12 | 大体样本-分子分型 | gross_molecular | No | Post-surgical pathology molecular typing |
| 13 | 异常肿瘤标记物 | abnormal_markers | No | e.g., "CEA,CA199,CA724" |
| 14 | 治疗后血清肿瘤标记物变化 | marker_change | No | 升高/降低/不变 |
| 15 | 分期前缀 | staging_prefix | Yes | "c" (clinical) or "yc" (post-treatment clinical) |
| 16 | T分期 | t_stage | **Yes** | T1-T4b |
| 17 | T4b受侵脏器 | t4b_invasion | No | Invaded organs for T4b |
| 18 | N分期 | n_stage | **Yes** | N0-N3 |
| 19 | M分期 | m_stage | **Yes** | M0/M1 |
| 20 | M转移脏器 | m_sites | Yes (if M1) | e.g., "腹膜", "肝" |
| 21 | 分期备注 | staging_notes | No | Multi-lesion notes |
| 22 | 治疗后症状变化 | symptom_change | No | Post-treatment symptom change |
| 23 | 评效 | response | Yes | PD/SD/PR/CR or "不适用" |
| 24 | 是否合并肿瘤急症 | tumor_emergency | No | 出血/梗阻/穿孔/无 |
| 25 | 关键合并症 | comorbidities | No | Free text describing major comorbidities |

### Molecular Typing Conventions

The `biopsy_molecular` field uses a prefix convention:
- `hj_` = 活检 (biopsy) result
- `pMMR/MSS` = mismatch repair proficient / microsatellite stable
- `dMMR/MSI-H` = mismatch repair deficient / microsatellite instability high
- `Her2 0/1+/2+/3+` = HER2 IHC score
- `CPS≥1/≥5/≥10` = PD-L1 Combined Positive Score

### Patient Type Conventions

- `zq_初治` = Treatment-naive patient
- `sq_术前治疗后（包括外院治疗）` = Post-neoadjuvant treatment (including treatment at other hospitals)

---

## Format B: Semi-Structured Narrative (3 columns)

**Auto-detection**: Column count ≤ 5 AND contains "病情总结" header.

| # | Column Header | JSON Field | Description |
|---|--------------|------------|-------------|
| 0 | ID | patient_id | Patient identifier |
| 1 | 姓名 | patient_name | Patient name |
| 2 | 病情总结 | clinical_narrative | Free-text clinical summary |

### Narrative Format

A compressed clinical narrative following Chinese oncology conventions:

```
男，69岁，胃中部（M）印戒细胞癌，ycT4aN1M1腹膜转移，活检hj_pMMR/MSS，
2019直肠术后、2025消化道出血胃癌，4C SOX+PD-1，PD。
```

Key elements typically present: gender, age, tumor site, pathology, staging, molecular typing, treatment history, response.

For this format, Claude extracts clinical dimensions from the narrative text during the clinical question inference step (SKILL.md Section 4).

---

## Output JSON Structure

Both formats produce the same `patients.json`:

```json
{
  "input_format": "structured",
  "input_file": "/path/to/input.xlsx",
  "parsed_at": "2026-03-19",
  "patient_count": 5,
  "patients": [
    {
      "patient_id": "T001587071",
      "patient_name": "杨永富",
      "gender": "男",
      "age": 69,
      "primary_site": "胃中部（M）",
      "pathology": "印戒细胞癌",
      "...": "...",
      "clinical_narrative": null
    }
  ]
}
```

For narrative format, structured fields are null and `clinical_narrative` contains the original text.

---

*Last Updated: 2026-03-19*
