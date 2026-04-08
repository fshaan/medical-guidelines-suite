#!/usr/bin/env python3
"""
批量患者指南检索管道工具

子命令:
  parse       - 解析输入 xlsx → patients.json
  split       - 将 patients.json 分成多个批次文件
  orchestrate - 自动编排批处理流程（扫描知识库+生成 prompt）
  merge       - 合并多个 rag_batch_*.json 为 rag_results.json
  validate    - 检查 rag_results.json 质量与完整性
  generate    - 从 RAG 结果 JSON 生成 Markdown 报告
"""

from __future__ import annotations

import argparse
import json
import locale
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path


# ─── Profile 配置 ────────────────────────────────────────────────────────────


MIN_CITATION_COVERAGE = 0.5
MIN_REC_LENGTH = 50


# ─── parse 子命令 ─────────────────────────────────────────────────────────────


STRUCTURED_ID_COL = "患者ID号"
NARRATIVE_COL = "病情总结"

# 结构化表 26 列 → JSON 字段映射
STRUCTURED_FIELD_MAP = {
    "患者ID号": "patient_id",
    "患者姓名": "patient_name",
    "性别": "gender",
    "年龄": "age",
    "原发部位": "primary_site",
    "Siewert分型": "siewert_type",
    "病理类型": "pathology",
    "患者类型": "patient_type",
    "既往治疗说明": "prior_treatment",
    "原发灶数量": "lesion_count",
    "活检样本-分子分型": "biopsy_molecular",
    "大体样本-分子分型": "gross_molecular",
    "异常肿瘤标记物": "abnormal_markers",
    "治疗后血清肿瘤标记物变化": "marker_change",
    "分期前缀": "staging_prefix",
    "T分期": "t_stage",
    "T4b受侵脏器": "t4b_invasion",
    "N分期": "n_stage",
    "M分期": "m_stage",
    "M转移脏器": "m_sites",
    "分期备注（多病灶可在此补充说明）": "staging_notes",
    "治疗后症状变化": "symptom_change",
    "评效": "response",
    "是否合并肿瘤急症": "tumor_emergency",
    "关键合并症（如有影响诊疗决策的重大合并症，请在此处进行描述）": "comorbidities",
}


def detect_format(headers: list[str]) -> str:
    """根据列名自动检测输入格式"""
    header_set = set(h for h in headers if h)
    if STRUCTURED_ID_COL in header_set and len(headers) >= 10:
        return "structured"
    if NARRATIVE_COL in header_set and len(headers) <= 5:
        return "narrative"
    raise ValueError(
        f"无法识别输入格式。列名: {headers[:5]}...\n"
        f"支持的格式:\n"
        f"  结构化: 需要 '{STRUCTURED_ID_COL}' 列且 >=10 列\n"
        f"  半结构化: 需要 '{NARRATIVE_COL}' 列且 <=5 列"
    )


def parse_structured(ws) -> list[dict]:
    """解析结构化数据表 (26 列)"""
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h) if h else "" for h in rows[0]]

    # 建立列索引
    col_idx = {}
    for i, h in enumerate(headers):
        for src, dst in STRUCTURED_FIELD_MAP.items():
            if src in h:  # 容忍列名略有不同
                col_idx[dst] = i
                break

    patients = []
    for row in rows[1:]:
        if not row or not row[col_idx.get("patient_id", 0)]:
            continue
        p = {}
        for field, idx in col_idx.items():
            val = row[idx] if idx < len(row) else None
            p[field] = str(val).strip() if val is not None else None
        # age 转整数
        if p.get("age"):
            try:
                p["age"] = int(float(p["age"]))
            except (ValueError, TypeError):
                pass
        p["clinical_narrative"] = None
        patients.append(p)
    return patients


def parse_narrative(ws) -> list[dict]:
    """解析半结构化病情总结 (3 列)"""
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h) if h else "" for h in rows[0]]

    # 找到各列位置
    id_idx = next((i for i, h in enumerate(headers) if "ID" in h.upper()), 0)
    name_idx = next((i for i, h in enumerate(headers) if "姓名" in h), 1)
    narrative_idx = next(
        (i for i, h in enumerate(headers) if "病情" in h or "总结" in h), 2
    )

    patients = []
    for row in rows[1:]:
        if not row or not row[id_idx]:
            continue
        p = {
            "patient_id": str(row[id_idx]).strip() if row[id_idx] else None,
            "patient_name": str(row[name_idx]).strip() if row[name_idx] else None,
            "clinical_narrative": str(row[narrative_idx]).strip()
            if row[narrative_idx]
            else None,
        }
        # 其他字段置 null，由 Claude 从 narrative 推断
        for field in STRUCTURED_FIELD_MAP.values():
            if field not in p:
                p[field] = None
        patients.append(p)
    return patients


def cmd_parse(args):
    """parse 子命令入口"""
    import openpyxl

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"输入文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)

    wb = openpyxl.load_workbook(input_path, read_only=True, data_only=True)
    ws = wb.active

    headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    fmt = detect_format(headers)
    print(f"检测到输入格式: {fmt}")

    if fmt == "structured":
        patients = parse_structured(ws)
    else:
        patients = parse_narrative(ws)

    wb.close()

    result = {
        "input_format": fmt,
        "input_file": str(input_path),
        "parsed_at": str(date.today()),
        "patient_count": len(patients),
        "patients": patients,
    }

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已解析 {len(patients)} 位患者 → {output_path}")


# ─── split 子命令 ─────────────────────────────────────────────────────────────


def _split_patients(patients: list[dict], batch_size: int) -> list[list[dict]]:
    """将患者列表分成多个批次（纯函数，供 split 和 orchestrate 共用）"""
    if not patients:
        return []
    return [patients[i : i + batch_size] for i in range(0, len(patients), batch_size)]


def cmd_split(args):
    """split 子命令入口 — 将 patients.json 分成多个批次文件"""
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"输入文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(input_path.read_text(encoding="utf-8"))
    patients = data.get("patients", [])
    batch_size = args.batch_size

    if not patients:
        print("患者列表为空，无需分批", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    batches = _split_patients(patients, batch_size)

    for idx, batch in enumerate(batches, 1):
        batch_data = {
            "input_format": data.get("input_format"),
            "input_file": data.get("input_file"),
            "parsed_at": data.get("parsed_at"),
            "batch_index": idx,
            "batch_count": len(batches),
            "patient_count": len(batch),
            "patients": batch,
        }
        batch_file = output_dir / f"batch_{idx:03d}.json"
        batch_file.write_text(
            json.dumps(batch_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(
        f"已将 {len(patients)} 位患者分为 {len(batches)} 批（每批 {batch_size} 人）→ {output_dir}/"
    )


# ─── orchestrate 子命令 ──────────────────────────────────────────────────────


def resolve_kb_root(explicit_path: str | None) -> Path:
    """按优先级解析知识库根路径。

    优先级: --kb-root > MEDICAL_GUIDELINES_DIR > ./guidelines/ > ./knowledge/
    验证: 目标路径下存在 data_structure.md
    """
    candidates = []

    if explicit_path:
        candidates.append(Path(explicit_path).resolve())
    else:
        env = os.environ.get("MEDICAL_GUIDELINES_DIR")
        if env:
            candidates.append(Path(env).resolve())
        candidates.append(Path("guidelines").resolve())
        candidates.append(Path("knowledge").resolve())

    for p in candidates:
        if p.is_dir() and (p / "data_structure.md").exists():
            return p

    tried = ", ".join(str(c) for c in candidates)
    print(f"无法找到知识库（需含 data_structure.md）。已尝试: {tried}", file=sys.stderr)
    sys.exit(1)


def scan_knowledge_base(kb_root: Path) -> dict:
    """自动扫描知识库结构，返回 kb_profile 字典。

    解析策略 (D1): 正则匹配表头定位 markdown 表格。
    Fallback: 解析失败时通过目录枚举发现 org。
    """
    profile = {
        "orgs": [],
        "org_files": {},
        "org_keywords": {},
        "clinical_question_map": {},
        "root_index_content": "",
    }

    root_ds = kb_root / "data_structure.md"
    root_text = ""
    if root_ds.exists():
        root_text = root_ds.read_text(encoding="utf-8")
        profile["root_index_content"] = root_text

    parsed_orgs = _parse_org_names_from_root(root_text)
    if not parsed_orgs:
        print("  ⚠ 根 data_structure.md 解析失败，fallback 到目录枚举", file=sys.stderr)
        parsed_orgs = _enumerate_org_dirs(kb_root)

    for org in parsed_orgs:
        org_dir = kb_root / org
        extracted_dir = org_dir / "extracted"
        if not extracted_dir.is_dir():
            print(f"  ⚠ {org}/ 无 extracted/ 子目录，跳过", file=sys.stderr)
            continue
        md_files = sorted(extracted_dir.glob("*.md"))
        if not md_files:
            print(f"  ⚠ {org}/extracted/ 无 .md 文件，跳过", file=sys.stderr)
            continue

        profile["orgs"].append(org)
        profile["org_files"][org] = [
            {
                "file": f.name,
                "path": str(f),
                "lines": sum(1 for _ in f.open(encoding="utf-8")),
            }
            for f in md_files
        ]

        org_ds = org_dir / "data_structure.md"
        if org_ds.exists():
            profile["org_keywords"][org] = _parse_keywords_from_ds(
                org_ds.read_text(encoding="utf-8")
            )

    profile["clinical_question_map"] = _parse_clinical_question_map(root_text)

    print(
        f"  知识库扫描完成: {len(profile['orgs'])} 个组织, "
        f"{sum(len(v) for v in profile['org_files'].values())} 个文件"
    )
    return profile


def _parse_org_names_from_root(text: str) -> list[str]:
    """从根 data_structure.md 解析组织名（### OrgName/ 模式）"""
    orgs = []
    for m in re.finditer(r"^###\s+(\w[\w-]*)/", text, re.MULTILINE):
        orgs.append(m.group(1))
    return orgs


def _enumerate_org_dirs(kb_root: Path) -> list[str]:
    """Fallback: 枚举知识库根目录下的子目录"""
    return sorted(
        d.name for d in kb_root.iterdir() if d.is_dir() and not d.name.startswith(".")
    )


def _parse_keywords_from_ds(text: str) -> dict[str, list[str]]:
    """从 data_structure.md 解析'常用检索关键词'区块"""
    keywords = {}
    current_category = None
    in_keyword_section = False

    for line in text.split("\n"):
        if "检索关键词" in line and line.startswith("#"):
            in_keyword_section = True
            continue
        if in_keyword_section:
            if line.startswith("---") or (line.startswith("#") and "检索" not in line):
                break
            cat_match = re.match(r"^###\s+(.+)", line)
            if cat_match:
                current_category = cat_match.group(1).strip()
                keywords[current_category] = []
                continue
            if current_category and line.startswith("- "):
                items = [k.strip() for k in line[2:].split(",")]
                keywords[current_category].extend(items)

    return keywords


def _parse_clinical_question_map(text: str) -> dict:
    """从根 data_structure.md 解析'临床问题→指南映射'表格"""
    cq_map = {}
    in_table = False

    for line in text.split("\n"):
        if "临床问题" in line and "指南映射" in line:
            in_table = True
            continue
        if in_table:
            if line.startswith("|") and "---" not in line and "临床问题" not in line:
                cols = [c.strip() for c in line.split("|")[1:-1]]
                if len(cols) >= 3:
                    question = cols[0]
                    primary = [g.strip() for g in cols[1].split(",") if g.strip()]
                    supplementary = [g.strip() for g in cols[2].split(",") if g.strip()]
                    cq_map[question] = {
                        "primary": primary,
                        "supplementary": supplementary,
                    }
            elif not line.startswith("|") and line.strip():
                break

    return cq_map




# 疾病类型 → 搜索关键词映射
_DISEASE_KEYWORD_MAP = {
    "胃": ["gastric", "stomach", "胃"],
    "肺": ["lung", "pulmonary", "肺"],
    "乳腺": ["breast", "乳腺"],
    "结直肠": ["colorectal", "colon", "rectal", "结直肠", "结肠", "直肠"],
    "肝": ["liver", "hepat", "肝"],
    "食管": ["esophag", "食管"],
    "胰腺": ["pancrea", "胰腺"],
}


def _extract_disease_keywords(disease_type: str) -> list[str]:
    """从 disease_type 提取中英文疾病关键词。"""
    keywords = []
    for cn_key, kw_list in _DISEASE_KEYWORD_MAP.items():
        if cn_key in (disease_type or ""):
            keywords.extend(kw_list)
    if not keywords and disease_type:
        keywords.append(disease_type)
    return keywords


def filter_orgs_by_disease(kb_profile: dict, disease_type: str) -> list[str]:
    """根据 disease_type 过滤 KB 中相关的 org。"""
    disease_kws = _extract_disease_keywords(disease_type)
    if not disease_kws:
        return kb_profile["orgs"]
    relevant = []
    for org in kb_profile["orgs"]:
        files = kb_profile["org_files"].get(org, [])
        if any(kw.lower() in f["file"].lower() for f in files for kw in disease_kws):
            relevant.append(org)
    return relevant or kb_profile["orgs"]


def build_queries(patient: dict, features: dict) -> list[str]:
    """Build QMD queries from patient features. One query per clinical dimension.

    Args:
        patient: Patient data dict (with disease_type etc.)
        features: Output of extract_patient_features()

    Returns:
        List of natural language query strings (1-N)
    """
    queries = []
    disease = patient.get("disease_type", "")

    staging = features.get("staging_keywords", [])
    if staging:
        queries.append(
            f"{disease} {' '.join(staging)} diagnosis staging treatment"
        )

    molecular = features.get("molecular_keywords", [])
    if molecular:
        queries.append(
            f"{disease} {' '.join(molecular)} targeted therapy immunotherapy"
        )

    treatment = features.get("treatment_keywords", [])
    if treatment:
        queries.append(
            f"{disease} {' '.join(treatment)} recommended regimen evidence level"
        )

    return queries or [f"{disease} treatment recommendation"]


# ─── 临床特征提取 ──────────────────────────────────────────────────────────────

_MOLECULAR_PATTERNS = re.compile(
    r"(HER2|Her2|her2|MSI-H|MSS|dMMR|pMMR|PD-L1|CPS[≥<>\d]+|EGFR|ALK|ROS1|"
    r"NTRK|BRAF|KRAS|NRAS|PIK3CA|FGFR2|Claudin[\s-]?18)",
    re.IGNORECASE,
)
_STAGING_PATTERNS = re.compile(
    r"((?:yc|c|p)?T[1-4][a-d]?|N[0-3][a-b]?|M[01]|"
    r"stage\s*(?:I{1,3}V?|IV)|[IⅠⅡⅢⅣ]+[A-C]?期)",
    re.IGNORECASE,
)
_METASTASIS_SITES = {
    "腹膜": ["腹膜", "peritoneal", "peritoneum"],
    "肝": ["肝", "liver", "hepatic"],
    "肺": ["肺", "lung", "pulmonary"],
    "骨": ["骨", "bone", "osseous"],
    "脑": ["脑", "brain", "cerebral"],
    "卵巢": ["卵巢", "ovarian", "Krukenberg"],
    "淋巴结": ["远处淋巴结", "distant lymph", "Virchow"],
}
_TREATMENT_PATTERNS = re.compile(
    r"(SOX|XELOX|CAPOX|FOLFOX|FLOT|S-1|替吉奥|卡培他滨|奥沙利铂|"
    r"PD-1|PD-L1|pembrolizumab|nivolumab|trastuzumab|"
    r"曲妥珠单抗|帕博利珠单抗|纳武利尤单抗|信迪利单抗|替雷利珠单抗|"
    r"化疗|靶向|免疫|放疗|内镜|手术|"
    r"\d+C\s+\w+)",
    re.IGNORECASE,
)
_EMERGENCY_KEYWORDS = [
    "出血",
    "bleeding",
    "梗阻",
    "obstruction",
    "穿孔",
    "perforation",
    "急症",
]
_COMORBIDITY_PATTERNS = re.compile(
    r"(糖尿病|diabetes|高血压|hypertension|肾功能不全|renal|心[脏功]|cardiac|"
    r"肝硬化|cirrhosis|COPD|肺功能|elderly|高龄)",
    re.IGNORECASE,
)


def extract_patient_features(patient: dict) -> dict:
    """从患者数据提取 9 维临床特征关键词 (D2: 正则扫描 + confidence)。"""
    features = {
        "diagnosis_keywords": [],
        "staging_keywords": [],
        "metastasis_keywords": [],
        "molecular_keywords": [],
        "treatment_keywords": [],
        "marker_keywords": [],
        "event_keywords": [],
        "comorbidity_keywords": [],
        "special_keywords": [],
    }

    narrative = patient.get("clinical_narrative")
    is_narrative = narrative and not patient.get("primary_site")

    if is_narrative:
        _extract_from_narrative(narrative, features)
    else:
        _extract_from_structured(patient, features)

    # 去重合并
    all_kw = []
    seen = set()
    for key in features:
        for kw in features[key]:
            if kw.lower() not in seen:
                seen.add(kw.lower())
                all_kw.append(kw)
    features["all_keywords"] = all_kw

    # D2: confidence 标记
    dimensions_hit = sum(
        1
        for k, v in features.items()
        if k.endswith("_keywords") and k != "all_keywords" and v
    )
    features["confidence"] = "low" if dimensions_hit <= 2 else "high"

    return features


def _extract_from_structured(p: dict, features: dict):
    """从结构化字段提取关键词"""
    if p.get("primary_site"):
        features["diagnosis_keywords"].append(p["primary_site"])
        features["diagnosis_keywords"].extend(
            _extract_disease_keywords(p["primary_site"])
        )
    if p.get("pathology"):
        features["diagnosis_keywords"].append(p["pathology"])

    for field in ("staging_prefix", "t_stage", "n_stage", "m_stage"):
        val = p.get(field)
        if val:
            features["staging_keywords"].append(val)
    prefix = p.get("staging_prefix", "")
    t = p.get("t_stage", "")
    n = p.get("n_stage", "")
    m = p.get("m_stage", "")
    if t and n:
        features["staging_keywords"].append(f"{prefix}{t}{n}{m}")

    if p.get("m_sites"):
        sites = re.split(r"[,，、/]", p["m_sites"])
        for s in sites:
            s = s.strip()
            if s:
                features["metastasis_keywords"].append(s)
                for en_name, aliases in _METASTASIS_SITES.items():
                    if any(a in s for a in aliases):
                        features["metastasis_keywords"].extend(aliases)
                        break
    if p.get("t4b_invasion"):
        features["metastasis_keywords"].append(p["t4b_invasion"])

    for field in ("biopsy_molecular", "gross_molecular"):
        val = p.get(field)
        if val:
            items = re.split(r"[,，]", val.replace("hj_", ""))
            features["molecular_keywords"].extend(i.strip() for i in items if i.strip())

    if p.get("prior_treatment"):
        matches = _TREATMENT_PATTERNS.findall(p["prior_treatment"])
        features["treatment_keywords"].extend(matches)
    if p.get("patient_type"):
        if "初治" in p["patient_type"]:
            features["treatment_keywords"].extend(
                ["初治", "treatment-naive", "first-line"]
            )
        elif "术前" in p["patient_type"] or "sq_" in p["patient_type"]:
            features["treatment_keywords"].extend(["术前治疗后", "post-neoadjuvant"])
    if p.get("response"):
        r = p["response"]
        if r not in ("不适用", None, ""):
            features["treatment_keywords"].append(r)

    if p.get("abnormal_markers"):
        markers = re.split(r"[,，、]", p["abnormal_markers"])
        features["marker_keywords"].extend(m.strip() for m in markers if m.strip())
    if p.get("marker_change"):
        features["marker_keywords"].append(p["marker_change"])

    if p.get("tumor_emergency") and p["tumor_emergency"] != "无":
        features["event_keywords"].append(p["tumor_emergency"])
        for ek in _EMERGENCY_KEYWORDS:
            if ek in p["tumor_emergency"]:
                features["event_keywords"].append(ek)

    if p.get("comorbidities"):
        features["comorbidity_keywords"].append(p["comorbidities"])
        matches = _COMORBIDITY_PATTERNS.findall(p["comorbidities"])
        features["comorbidity_keywords"].extend(matches)

    if p.get("siewert_type"):
        features["special_keywords"].extend(
            [
                f"Siewert {p['siewert_type']}",
                "EGJ",
                "食管胃结合部",
            ]
        )
    age = p.get("age")
    if age and isinstance(age, int) and age >= 75:
        features["special_keywords"].extend(["高龄", "elderly"])


def _extract_from_narrative(text: str, features: dict):
    """从 narrative 文本正则扫描所有维度"""
    site_patterns = [
        "胃",
        "食管",
        "结肠",
        "直肠",
        "gastric",
        "esophag",
        "colon",
        "rectal",
    ]
    for sp in site_patterns:
        if sp in text or sp in text.lower():
            features["diagnosis_keywords"].append(sp)
    path_types = ["腺癌", "印戒", "鳞癌", "adenocarcinoma", "signet", "squamous"]
    for pt in path_types:
        if pt in text or pt in text.lower():
            features["diagnosis_keywords"].append(pt)

    features["staging_keywords"].extend(_STAGING_PATTERNS.findall(text))

    for cn_name, aliases in _METASTASIS_SITES.items():
        for a in aliases:
            if a in text:
                features["metastasis_keywords"].extend(aliases)
                break

    features["molecular_keywords"].extend(_MOLECULAR_PATTERNS.findall(text))
    features["treatment_keywords"].extend(_TREATMENT_PATTERNS.findall(text))

    marker_pats = ["CEA", "CA199", "CA724", "CA125", "AFP"]
    for mp in marker_pats:
        if mp in text.upper():
            features["marker_keywords"].append(mp)

    for ek in _EMERGENCY_KEYWORDS:
        if ek in text or ek in text.lower():
            features["event_keywords"].append(ek)

    features["comorbidity_keywords"].extend(_COMORBIDITY_PATTERNS.findall(text))

    siewert_match = re.search(
        r"Siewert\s*(?:type\s*)?([IⅠⅡⅢ123]+)", text, re.IGNORECASE
    )
    if siewert_match:
        features["special_keywords"].extend(["Siewert", "EGJ", "食管胃结合部"])
    age_match = re.search(r"(\d{2,3})\s*岁", text)
    if age_match and int(age_match.group(1)) >= 75:
        features["special_keywords"].extend(["高龄", "elderly"])


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中英文混合按 1 token ≈ 4 字符）"""
    return len(text) // 4


def generate_batch_prompt(
    batch: list[dict],
    kb_profile: dict,
    kb_root: str,
    batch_idx: int,
    total_batches: int,
    output_file: str = "",
) -> str:
    """Generate self-contained batch prompt (based on pre-retrieval results)."""
    lines = []

    lines.append(f"# Batch {batch_idx:03d}/{total_batches:03d} Analysis Task\n")
    lines.append("<CONTEXT_RESET>")
    lines.append("Ignore all retrieval results and patient data before this message.")
    lines.append("This is a fresh, independent batch task. Start from zero.")
    lines.append("Do not reference any other batch results.")
    lines.append("</CONTEXT_RESET>\n")

    lines.append("<MANDATORY_RULES>")
    lines.append("1. Carefully read each patient's pre-retrieved results and extract recommendations, evidence levels, and sources")
    lines.append("2. Each patient must have results for every guideline organization")
    lines.append('3. If pre-retrieval has no content for a guideline, record: "This guideline does not cover this clinical question"')
    lines.append("4. Output must be in Simplified Chinese")
    lines.append("5. Recommendations must be based on pre-retrieved results, not fabricated")
    lines.append("6. Must cite which pre-retrieved chunks were used in retrieval_sources")
    lines.append("7. Citation coverage requirement: cite at least 50% of pre-retrieved results")
    lines.append("</MANDATORY_RULES>\n")

    lines.append(f"## Knowledge Base\nPath: {kb_root}\n")
    lines.append("Root index:\n---")
    lines.append(kb_profile.get("root_index_content", ""))
    lines.append("---\n")

    lines.append(f"## Patient List (this batch: {len(batch)} patients)\n")

    for pi, patient in enumerate(batch, 1):
        pid = patient.get("patient_id", "?")
        pname = patient.get("patient_name", "?")
        features = patient.get("features", {})
        retrieval_results = patient.get("retrieval_results", [])

        lines.append(f"### Patient {pi}: {pname} ({pid})\n")

        lines.append("**Clinical info:**")
        for k, v in patient.items():
            if k in ("features", "retrieval_results"):
                continue
            if v is None:
                continue
            lines.append(f"- {k}: {v}")

        confidence = features.get("confidence", "high")
        lines.append(f"\n**Feature extraction confidence**: {confidence}")

        lines.append("\n**Extracted keywords:**")
        for dim_key in sorted(features.keys()):
            if dim_key.endswith("_keywords") and dim_key != "all_keywords":
                kws = features[dim_key]
                if kws:
                    dim_name = dim_key.replace("_keywords", "")
                    lines.append(f"- {dim_name}: {', '.join(kws)}")

        if retrieval_results:
            lines.append(
                f"\n#### Pre-retrieved Results ({len(retrieval_results)} chunks)\n"
            )
            by_org: dict[str, list] = {}
            for ri, hit in enumerate(retrieval_results, 1):
                path = hit.get("path", "")
                org = path.split("/")[0] if "/" in path else "unknown"
                by_org.setdefault(org, []).append((ri, hit))

            for org, hits in by_org.items():
                lines.append(f"**{org}:**\n")
                for ri, hit in hits:
                    score = hit.get("score", 0)
                    context = hit.get("context", "")
                    content = hit.get("content", "")
                    path = hit.get("path", "")
                    chunk_id = f"R{pi:03d}-{ri:02d}"
                    lines.append(f"[{chunk_id}] (score={score:.2f}) {context}")
                    lines.append(f"  file: {path}")
                    lines.append(f"  content: {content}")
                    lines.append("")

            min_citations = max(1, len(retrieval_results) // 2)
            lines.append(f"#### Citation Requirement [Patient {pi}: {pname}]")
            lines.append(
                f"Cite chunk IDs (e.g. R{pi:03d}-01) in retrieval_sources. "
                f"Coverage >= {MIN_CITATION_COVERAGE:.0%} (at least {min_citations} chunks)."
            )
        else:
            lines.append("\n#### Pre-retrieved Results\n")
            lines.append("No pre-retrieved results available.")
            lines.append('You MUST record: "该指南未检索到与本患者相关的内容" for each guideline.')
            lines.append("Do NOT fabricate recommendations without retrieved evidence.\n")

        lines.append("")

    lines.append("## Output Requirements\n")
    lines.append(f"- File path: {output_file}")
    lines.append("- Format: JSON (strict template below)")
    lines.append("- Output language: Simplified Chinese")
    lines.append('- Top-level key must be `"results"` (not `"patients"`)')
    lines.append("")
    lines.append("JSON template:")

    template = {
        "batch_id": f"batch_{batch_idx:03d}",
        "processed_at": "2026-04-08T10:00:00",
        "results": [
            {
                "patient_id": "P001",
                "patient_name": "Patient Name",
                "clinical_question": "Clinical question summary",
                "guideline_results": [
                    {
                        "guideline": "NCCN",
                        "version": "2026.V2",
                        "recommendation": "Recommendation (Chinese, >=50 chars)",
                        "evidence_level": "Category 1",
                        "source_file": "NCCN/extracted/NCCN_Gastric_2026.md",
                        "retrieval_sources": [
                            {
                                "chunk_id": "R001-01",
                                "score": 0.85,
                                "snippet": "First 40 chars of cited chunk...",
                            }
                        ],
                    }
                ],
                "citation_coverage": 0.75,
                "consensus": "Cross-guideline consensus analysis",
                "differences": "Cross-guideline difference analysis",
            }
        ],
    }

    lines.append("```json")
    lines.append(json.dumps(template, ensure_ascii=False, indent=2))
    lines.append("```\n")

    return "\n".join(lines)


def cmd_orchestrate(args):
    """orchestrate subcommand -- batch processing with QMD pre-retrieval."""
    from scripts.retriever import QMDService

    kb_root = resolve_kb_root(getattr(args, "kb_root", None))
    print(f"Knowledge base: {kb_root}")

    kb_profile = scan_knowledge_base(kb_root)
    if not kb_profile["orgs"]:
        print("Knowledge base is empty", file=sys.stderr)
        sys.exit(1)

    patients_path = Path(args.patients).resolve()
    if not patients_path.exists():
        print(f"Patients file not found: {patients_path}", file=sys.stderr)
        sys.exit(1)
    patients_data = json.loads(patients_path.read_text(encoding="utf-8"))
    patients = patients_data.get("patients", [])
    if not patients:
        print("Patient list is empty", file=sys.stderr)
        sys.exit(1)

    enriched_patients = []
    total_results = 0
    total_queries = 0

    with QMDService() as qmd:
        for p in patients:
            features = extract_patient_features(p)
            queries = build_queries(p, features)
            total_queries += len(queries)

            retrieval_results = []
            for q in queries:
                hits = qmd.query(q, top_k=10, min_score=0.3)
                retrieval_results.extend(hits)

            seen = set()
            unique_results = []
            for hit in retrieval_results:
                key = (hit.get("path", ""), hash(hit.get("content", "")))
                if key not in seen:
                    seen.add(key)
                    unique_results.append(hit)

            total_results += len(unique_results)
            enriched = {
                **p,
                "features": features,
                "retrieval_results": unique_results,
            }
            enriched_patients.append(enriched)

    print(
        f"Pre-retrieval done: {len(patients)} patients, "
        f"{total_queries} queries, {total_results} results\n"
    )

    batch_size = args.batch_size
    max_tokens = args.max_prompt_tokens
    batches = _split_patients(enriched_patients, batch_size)

    final_batches = []
    for batch in batches:
        prompt = generate_batch_prompt(
            batch, kb_profile, str(kb_root),
            len(final_batches) + 1, len(batches),
        )
        tokens = estimate_tokens(prompt)
        if tokens > max_tokens and len(batch) > 1:
            sub_batches = _auto_split_batch(
                batch, kb_profile, str(kb_root), max_tokens,
            )
            final_batches.extend(sub_batches)
        else:
            final_batches.append(batch)

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    for i, batch in enumerate(final_batches, 1):
        output_file = output_dir / f"rag_batch_{i:03d}.json"
        prompt = generate_batch_prompt(
            batch, kb_profile, str(kb_root),
            i, len(final_batches),
            output_file=str(output_file),
        )
        prompt_file = output_dir / f"batch_{i:03d}_prompt.md"
        prompt_file.write_text(prompt, encoding="utf-8")
        print(f"  batch {i:03d}: {len(batch)} patients -> {prompt_file.name}")

    plan = {
        "total_patients": len(patients),
        "total_batches": len(final_batches),
        "total_queries": total_queries,
        "total_retrieval_results": total_results,
        "kb_profile": {
            "orgs": kb_profile["orgs"],
            "kb_root": str(kb_root),
        },
        "batches": [
            {
                "batch_id": f"batch_{i:03d}",
                "patient_count": len(b),
                "prompt_file": f"batch_{i:03d}_prompt.md",
                "output_file": f"rag_batch_{i:03d}.json",
                "status": "pending",
            }
            for i, b in enumerate(final_batches, 1)
        ],
    }
    plan_path = output_dir / "orchestration_plan.json"
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nOrchestration complete: {plan_path}")


def _auto_split_batch(
    batch: list[dict],
    kb_profile: dict,
    kb_root: str,
    max_tokens: int,
) -> list[list[dict]]:
    """D3: 递归拆分超限批次"""
    if len(batch) <= 1:
        return [batch]

    mid = len(batch) // 2
    left, right = batch[:mid], batch[mid:]
    result = []

    for sub in (left, right):
        prompt = generate_batch_prompt(sub, kb_profile, kb_root, 1, 999)
        if estimate_tokens(prompt) > max_tokens and len(sub) > 1:
            result.extend(
                _auto_split_batch(sub, kb_profile, kb_root, max_tokens)
            )
        else:
            result.append(sub)

    return result


# ─── 共用 helper ──────────────────────────────────────────────────────────────


def _is_flat_format(results: list[dict]) -> bool:
    """检测 slim 扁平格式（result 含 guideline 键且无 guideline_results）。"""
    return (
        bool(results)
        and "guideline" in results[0]
        and "guideline_results" not in results[0]
    )


def _aggregate_flat_results(flat_results: list[dict]) -> list[dict]:
    """将 slim 扁平 results 按 patient_id 聚合为 full 兼容格式。"""
    if not flat_results:
        return []
    grouped = {}
    for r in flat_results:
        pid = r.get("patient_id", "UNKNOWN")
        if pid not in grouped:
            grouped[pid] = {
                "patient_id": pid,
                "patient_name": r.get("patient_name", ""),
                "clinical_question": r.get("clinical_question", ""),
                "guideline_results": [],
            }
        grouped[pid]["guideline_results"].append(
            {
                "guideline": r.get("guideline", ""),
                "recommendation": r.get("recommendation", ""),
                "evidence_level": r.get("evidence_level", ""),
                "source_file": r.get("source_file", ""),
            }
        )
    return list(grouped.values())


def _generate_consensus(patient: dict) -> tuple[list[str], list[str]]:
    """基于多 guideline 推荐文本生成 consensus/differences。"""
    recs = patient.get("guideline_results", [])
    if len(recs) < 2:
        return [], []

    all_kw_sets = []
    for r in recs:
        text = r.get("recommendation", "")
        kws = set(re.findall(r"[\u4e00-\u9fff]{2,}", text))
        all_kw_sets.append(kws)

    common = set.intersection(*all_kw_sets) if all_kw_sets else set()
    consensus = [f"各指南均提及: {'、'.join(sorted(common)[:5])}"] if common else []

    diffs = []
    for i, r in enumerate(recs):
        unique = all_kw_sets[i] - common
        if unique:
            diffs.append(f"{r['guideline']}独有: {'、'.join(sorted(unique)[:3])}")

    return consensus, diffs


def _deduplicate_guideline_results(patient: dict) -> dict:
    """去除小模型重复输出的 guideline_results / consensus / differences。"""
    pid = patient.get("patient_id", "?")
    total_removed = 0

    for cq in patient.get("clinical_questions", []):
        grs = cq.get("guideline_results", [])
        if grs:
            seen = set()
            unique = []
            for gr in grs:
                key = (gr.get("guideline", ""), gr.get("recommendation", ""))
                if key not in seen:
                    seen.add(key)
                    unique.append(gr)
            removed = len(grs) - len(unique)
            if removed:
                total_removed += removed
                cq["guideline_results"] = unique

        if cq.get("consensus"):
            cq["consensus"] = list(dict.fromkeys(cq["consensus"]))
        if cq.get("differences"):
            cq["differences"] = list(dict.fromkeys(cq["differences"]))

    if total_removed:
        print(f"  ⚠ 患者 {pid}: 去除 {total_removed} 条重复推荐", file=sys.stderr)
    return patient


def _extract_patient_list(batch_data: dict) -> list[dict]:
    """从 batch JSON 提取患者列表。

    兼容 "results"/"patients" 两种 key，自动将扁平结构包装为 clinical_questions 嵌套。
    注意：原地修改 batch_data 中的 dict（pop 操作）。
    """
    patients = (
        batch_data.get("results")
        if "results" in batch_data
        else batch_data.get("patients", [])
    )
    if not patients and batch_data.get("batch_id"):
        print(
            f"  ⚠ batch {batch_data['batch_id']} 未找到 results/patients 键",
            file=sys.stderr,
        )

    # Slim flat format: aggregate and generate consensus
    if patients and _is_flat_format(patients):
        patients = _aggregate_flat_results(patients)
        for p in patients:
            consensus, diffs = _generate_consensus(p)
            p["clinical_questions"] = [
                {
                    "guideline_results": p.pop("guideline_results"),
                    "consensus": consensus,
                    "differences": diffs,
                }
            ]
        return [_deduplicate_guideline_results(p) for p in patients]

    for p in patients:
        if not p.get("clinical_questions") and p.get("guideline_results"):
            p["clinical_questions"] = [
                {
                    "guideline_results": p.pop("guideline_results"),
                    "consensus": p.pop("consensus", []),
                    "differences": p.pop("differences", []),
                }
            ]
    return [_deduplicate_guideline_results(p) for p in patients]


# ─── index 子命令 ─────────────────────────────────────────────────────────────


def cmd_index(args):
    """index subcommand -- build QMD index and inject Context metadata."""
    kb_root = resolve_kb_root(getattr(args, "kb_root", None))
    print(f"知识库路径: {kb_root}")
    force = getattr(args, "force", False)

    orgs_found = []
    for org_dir in sorted(kb_root.iterdir()):
        if not org_dir.is_dir() or org_dir.name.startswith("."):
            continue
        extracted_dir = org_dir / "extracted"
        if not extracted_dir.exists():
            continue
        md_files = sorted(extracted_dir.glob("*.md"))
        if not md_files:
            continue
        orgs_found.append((org_dir.name, org_dir, md_files))

    if not orgs_found:
        print("No extracted/*.md files found", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(orgs_found)} organizations\n")

    for org_name, org_dir, md_files in orgs_found:
        extracted_dir = org_dir / "extracted"
        cmd = [
            "qmd", "collection", "add",
            str(extracted_dir),
            "--name", org_name,
            "--mask", "**/*.md",
        ]
        if force:
            cmd.append("--force")
        print(f"  collection: {org_name}")
        subprocess.run(cmd, check=True)

    print("\nGenerating embeddings (Qwen3-Embedding)...")
    embed_env = {**os.environ, "QMD_EMBED_MODEL": "Qwen3-Embedding"}
    embed_cmd = ["qmd", "embed"]
    if force:
        embed_cmd.append("-f")
    subprocess.run(embed_cmd, check=True, env=embed_env)

    print("\nInjecting contexts...")
    for org_name, org_dir, md_files in orgs_found:
        ds_path = org_dir / "data_structure.md"
        if ds_path.exists():
            first_line = ds_path.read_text(encoding="utf-8").split("\n")[0]
            org_desc = first_line.lstrip("# ").strip() or org_name
        else:
            org_desc = org_name

        subprocess.run(
            ["qmd", "context", "add", f"qmd://{org_name}", org_desc],
            check=True,
        )
        print(f"  context: {org_name} = {org_desc}")

        for md_file in md_files:
            file_desc = f"{org_name} {md_file.stem.replace('_', ' ')}"
            subprocess.run(
                ["qmd", "context", "add",
                 f"qmd://{org_name}/{md_file.stem}", file_desc],
                check=True,
            )

    print(f"\nIndex complete: {len(orgs_found)} organizations")


# ─── merge 子命令 ─────────────────────────────────────────────────────────────


def cmd_merge(args):
    """merge 子命令入口 — 合并多个 rag_batch_*.json 为 rag_results.json"""
    input_dir = Path(args.input_dir).resolve()

    # 加载患者元数据 lookup（可选）
    patient_lookup = {}
    if getattr(args, "patients", None):
        patients_path = Path(args.patients).resolve()
        if patients_path.exists():
            pdata = json.loads(patients_path.read_text(encoding="utf-8"))
            for p in pdata.get("patients", []):
                pid = p.get("patient_id")
                if pid:
                    patient_lookup[pid] = p
            print(f"已加载患者元数据: {len(patient_lookup)} 位患者")
        else:
            print(f"  ⚠ patients.json 不存在: {patients_path}", file=sys.stderr)

    batch_files = sorted(input_dir.glob("rag_batch_*.json"))

    if not batch_files:
        print(f"未找到批次结果文件 (rag_batch_*.json) → {input_dir}", file=sys.stderr)
        sys.exit(1)

    all_results = []
    patient_ids_seen = set()

    for bf in batch_files:
        batch_data = json.loads(bf.read_text(encoding="utf-8"))
        for result in _extract_patient_list(batch_data):
            pid = result.get("patient_id")
            if pid in patient_ids_seen:
                print(f"  ⚠ 跳过重复患者: {pid} (来自 {bf.name})")
                continue
            patient_ids_seen.add(pid)

            # 注入 batch_source（用于跨批次分析）
            if "batch_source" not in result:
                result["batch_source"] = bf.stem.replace("rag_", "")  # "batch_001"

            # _extract_patient_list 已完成扁平→嵌套包装
            # 清理根级别的冗余字段（兼容 LLM 同时输出两种结构的情况）
            for key in ("consensus", "differences", "guideline_results"):
                result.pop(key, None)

            all_results.append(result)

            # 回注患者元数据（仅填充缺失字段）
            if patient_lookup:
                source = patient_lookup.get(pid)
                if source:
                    for field in (
                        "patient_name",
                        "primary_site",
                        "disease_type",
                        "diagnosis_summary",
                    ):
                        if not result.get(field):
                            val = source.get(field)
                            if val:
                                result[field] = val
                elif pid:
                    print(
                        f"  ⚠ 患者 {pid} 未在 patients.json 中找到，跳过元数据回注",
                        file=sys.stderr,
                    )

    merged = {
        "generated_at": str(date.today()),
        "patient_count": len(all_results),
        "source_batches": [bf.name for bf in batch_files],
        "results": all_results,
    }

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"已合并 {len(batch_files)} 个批次，{len(all_results)} 位患者 → {output_path}"
    )


# ─── validate 子命令 ──────────────────────────────────────────────────────────


def _char_bigrams(text: str) -> set:
    text = text.strip()
    if len(text) < 2:
        return set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _bigram_jaccard(a: str, b: str) -> float:
    sa, sb = _char_bigrams(a), _char_bigrams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _check_cross_batch_similarity(
    results: list[dict], threshold: float = 0.8
) -> list[str]:
    warnings = []
    patient_recs = []
    for r in results:
        pid = r.get("patient_id", "?")
        batch = r.get("batch_source", "?")
        text_parts = []
        for q in r.get("clinical_questions", []):
            for gr in q.get("guideline_results", []):
                text_parts.append(gr.get("recommendation", ""))
        full_text = " ".join(text_parts)
        if full_text.strip():
            patient_recs.append((pid, batch, full_text))

    for i in range(len(patient_recs)):
        for j in range(i + 1, len(patient_recs)):
            pid_i, batch_i, text_i = patient_recs[i]
            pid_j, batch_j, text_j = patient_recs[j]
            if batch_i == batch_j:
                continue
            score = _bigram_jaccard(text_i, text_j)
            if score > threshold:
                warnings.append(
                    f"[{pid_i}] 与 [{pid_j}] 推荐文本高度相似 "
                    f"(Jaccard={score:.2f}, 批次 {batch_i} vs {batch_j}) "
                    f"— 可能存在上下文污染"
                )
    return warnings


def _check_batch_depth_decay(results: list) -> list:
    """检测后续批次执行深度是否显著衰减。"""
    warnings = []
    batch_stats = {}  # batch_source -> total_matches

    for r in results:
        batch = r.get("batch_source", "unknown")
        if batch not in batch_stats:
            batch_stats[batch] = 0
        for q in r.get("clinical_questions", []):
            for gr in q.get("guideline_results", []):
                batch_stats[batch] += len(gr.get("retrieval_sources", []))

    sorted_batches = sorted(batch_stats.items())
    if len(sorted_batches) < 3:
        return warnings

    depths = [count for _, count in sorted_batches]
    mid = len(depths) // 2
    first_half_avg = sum(depths[:mid]) / mid if mid else 0
    second_half_avg = (
        sum(depths[mid:]) / (len(depths) - mid) if (len(depths) - mid) else 0
    )

    if first_half_avg > 0 and second_half_avg < first_half_avg * 0.4:
        warnings.append(
            f"批次深度衰减: 前半段平均匹配数 {first_half_avg:.0f}, "
            f"后半段 {second_half_avg:.0f} (衰减 {(1 - second_half_avg / first_half_avg) * 100:.0f}%)"
        )

    # 连续 3 批单调递减
    for i in range(2, len(depths)):
        if depths[i] < depths[i - 1] < depths[i - 2]:
            batch_names = [sorted_batches[j][0] for j in (i - 2, i - 1, i)]
            warnings.append(
                f"连续衰减趋势: {', '.join(batch_names)} "
                f"(匹配数 {depths[i - 2]} → {depths[i - 1]} → {depths[i]})"
            )
            break

    return warnings


def _check_org_coverage(results: list[dict], known_orgs: list[str]) -> list[str]:
    warnings = []
    for r in results:
        pid = r.get("patient_id", "?")
        covered_orgs = set()
        for q in r.get("clinical_questions", []):
            for gr in q.get("guideline_results", []):
                covered_orgs.add(gr.get("guideline", ""))
        missing = set(known_orgs) - covered_orgs
        if missing:
            coverage = len(covered_orgs) / len(known_orgs) * 100 if known_orgs else 0
            warnings.append(
                f"[{pid}] 组织覆盖率 {coverage:.0f}% ({len(covered_orgs)}/{len(known_orgs)}), "
                f"缺失: {', '.join(sorted(missing))}"
            )
    return warnings


def _verify_batch_results(
    prompt_text: str,
    batch_data: dict,
    kb_root: str = "",
) -> tuple:
    """Verify batch results quality.

    V3: Citation coverage (>= 50% of pre-retrieved chunks cited)
    V4: Contradiction detection (no retrieval sources but has recommendation)

    Returns: (errors: list[str], warnings: list[str])
    """
    errors = []
    warnings = []

    for result in _extract_patient_list(batch_data):
        pid = result.get("patient_id", "?")

        # V3: Citation coverage
        citation_coverage = result.get("citation_coverage", None)
        if citation_coverage is not None and citation_coverage < MIN_CITATION_COVERAGE:
            warnings.append(
                f"[{pid}] Low citation coverage "
                f"({citation_coverage:.0%}, require >= {MIN_CITATION_COVERAGE:.0%})"
            )

        for q in result.get("clinical_questions", []):
            for gr in q.get("guideline_results", []):
                org = gr.get("guideline", "")
                rec = gr.get("recommendation", "")
                sources = gr.get("retrieval_sources", [])

                # V4: No retrieval sources but has recommendation
                if not sources and len(rec) > 50:
                    warnings.append(
                        f"[{pid}] {org} no retrieval sources cited "
                        f"but has recommendation ({len(rec)} chars)"
                    )

                if not rec:
                    errors.append(f"[{pid}] {org} empty recommendation")

                src_file = gr.get("source_file", "")
                if not src_file:
                    warnings.append(f"[{pid}] {org} missing source file")

            if not q.get("consensus"):
                warnings.append(f"[{pid}] missing consensus analysis")
            if not q.get("differences"):
                warnings.append(f"[{pid}] missing difference analysis")

    return errors, warnings


def cmd_verify_batch(args):
    """verify-batch 子命令入口 — 验证批次执行证据的真实性"""
    input_dir = Path(args.input_dir).resolve()
    kb_root = ""
    if hasattr(args, "kb_root") and args.kb_root:
        kb_root = str(resolve_kb_root(args.kb_root))

    batch_files = sorted(input_dir.glob("rag_batch_*.json"))

    if not batch_files:
        print(f"未找到批次结果文件: {input_dir}/rag_batch_*.json", file=sys.stderr)
        sys.exit(1)

    total_pass = 0
    total_fail = 0
    total_warn = 0
    failed_batches = []

    print(f"验证批次: {input_dir}\n")

    for bf in batch_files:
        batch_num = bf.stem.replace("rag_batch_", "")
        prompt_file = input_dir / f"batch_{batch_num}_prompt.md"

        if not prompt_file.exists():
            print(f"  {bf.stem}: ⚠ 未找到对应 prompt 文件 {prompt_file.name}")
            total_warn += 1
            continue

        prompt_text = prompt_file.read_text(encoding="utf-8")
        try:
            batch_data = json.loads(bf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            total_fail += 1
            failed_batches.append(bf.stem)
            print(f"  {bf.stem}: ✗ FAIL (文件损坏: {e})")
            continue

        errors, warns = _verify_batch_results(
            prompt_text, batch_data, kb_root)

        if errors:
            total_fail += 1
            failed_batches.append(bf.stem)
            print(f"  {bf.stem}: ✗ FAIL")
            for e in errors:
                print(f"    ✗ {e}")
            for w in warns:
                print(f"    ⚠ {w}")
        elif warns:
            total_warn += 1
            print(f"  {bf.stem}: ⚠ WARN")
            for w in warns:
                print(f"    ⚠ {w}")
        else:
            total_pass += 1
            print(f"  {bf.stem}: ✓ PASS")

    print(f"\n总结: {total_pass} PASS, {total_fail} FAIL, {total_warn} WARN")
    if failed_batches:
        print(f"建议重新执行: {', '.join(failed_batches)}")

    sys.exit(1 if total_fail > 0 else 0)


def cmd_validate(args):
    """validate 子命令入口 — 检查 rag_results.json 质量与完整性"""
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(input_path.read_text(encoding="utf-8"))
    results = data.get("results", [])

    errors = []
    warnings = []

    # 与 patients.json 对比完整性
    if args.patients:
        patients_path = Path(args.patients).resolve()
        if patients_path.exists():
            patients_data = json.loads(patients_path.read_text(encoding="utf-8"))
            expected_ids = {p["patient_id"] for p in patients_data.get("patients", [])}
            actual_ids = {r.get("patient_id") for r in results}
            missing = expected_ids - actual_ids
            extra = actual_ids - expected_ids
            if missing:
                errors.append(
                    f"缺失患者 ({len(missing)}): {', '.join(sorted(missing))}"
                )
            if extra:
                warnings.append(f"多余患者 ({len(extra)}): {', '.join(sorted(extra))}")

    # 逐患者检查
    rec_lengths = []
    for r in results:
        pid = r.get("patient_id", "?")

        # 必要字段
        for field in ("diagnosis_summary", "clinical_questions", "disease_type"):
            if not r.get(field):
                errors.append(f"[{pid}] 缺失字段: {field}")

        questions = r.get("clinical_questions", [])
        if not questions:
            errors.append(f"[{pid}] 无临床问题")
            rec_lengths.append((pid, 0))
            continue

        total_len = 0
        for qi, q in enumerate(questions, 1):
            grs = q.get("guideline_results", [])
            if not grs:
                warnings.append(f"[{pid}] Q{qi} 无指南检索结果")

            for g in grs:
                rec = g.get("recommendation", "")
                total_len += len(rec)
                if len(rec) < MIN_REC_LENGTH:
                    warnings.append(
                        f"[{pid}] Q{qi} {g.get('guideline', '')} 推荐过短 ({len(rec)}字)"
                    )
                if not g.get("evidence_level"):
                    warnings.append(
                        f"[{pid}] Q{qi} {g.get('guideline', '')} 缺失证据等级"
                    )
                if not g.get("source_file"):
                    warnings.append(
                        f"[{pid}] Q{qi} {g.get('guideline', '')} 缺失来源文件"
                    )
                if not g.get("retrieval_sources"):
                    warnings.append(
                        f"[{pid}] Q{qi} {g.get('guideline', '')} 缺失检索来源"
                    )

            if not q.get("consensus"):
                warnings.append(f"[{pid}] Q{qi} 缺失共识分析")
            if not q.get("differences"):
                warnings.append(f"[{pid}] Q{qi} 缺失差异分析")

        # citation_coverage check
        cov = r.get("citation_coverage")
        if cov is not None and cov < MIN_CITATION_COVERAGE:
            warnings.append(
                f"[{pid}] 引用覆盖率过低 ({cov:.0%}, 要求 >= 50%)"
            )

        rec_lengths.append((pid, total_len))

    # 跨患者一致性：检测质量下降
    if len(rec_lengths) >= 3:
        lengths = [l for _, l in rec_lengths if l > 0]
        if lengths:
            avg_len = sum(lengths) / len(lengths)
            for pid, length in rec_lengths:
                if length > 0 and length < avg_len * 0.3:
                    warnings.append(
                        f"[{pid}] 推荐总长度异常偏短 ({length}字 vs 平均 {avg_len:.0f}字)"
                    )

    # 跨批次相似度检测 (D9)
    cross_warnings = _check_cross_batch_similarity(results)
    warnings.extend(cross_warnings)

    # 批次深度衰减检测 (L4)
    depth_warnings = _check_batch_depth_decay(results)
    warnings.extend(depth_warnings)

    # 组织覆盖率检测 (§1.8)
    kb_profile_path = getattr(args, "kb_profile", None)
    if kb_profile_path:
        plan_path = Path(kb_profile_path).resolve()
        if plan_path.exists():
            try:
                plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
                known_orgs = plan_data.get("kb_profile", {}).get("orgs", [])
                if known_orgs:
                    org_warnings = _check_org_coverage(results, known_orgs)
                    warnings.extend(org_warnings)
            except (json.JSONDecodeError, KeyError):
                pass

    # 输出报告
    print(f"验证结果: {len(results)} 位患者")
    if errors:
        print(f"\n  ✗ {len(errors)} 个错误:")
        for e in errors:
            print(f"    ✗ {e}")
    if warnings:
        print(f"\n  ⚠ {len(warnings)} 个警告:")
        for w in warnings:
            print(f"    ⚠ {w}")
    if not errors and not warnings:
        print(f"  ✓ 验证通过，数据完整")

    sys.exit(1 if errors else 0)


# ─── generate 子命令 ──────────────────────────────────────────────────────────


def _sort_results_by_name(results: list[dict]) -> list[dict]:
    """按患者姓名排序（使用 locale.strxfrm，中文环境下近似拼音序）"""
    try:
        locale.setlocale(locale.LC_COLLATE, "zh_CN.UTF-8")
    except locale.Error:
        try:
            locale.setlocale(locale.LC_COLLATE, "")
        except locale.Error:
            pass
    return sorted(results, key=lambda r: locale.strxfrm(r.get("patient_name", "")))


def load_rag_results(path: Path) -> dict:
    """加载 RAG 结果 JSON"""
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


def md_escape(text: str) -> str:
    """转义 Markdown 特殊字符"""
    if not text:
        return ""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace("\\", "\\\\")
    text = text.replace("\n", " ").replace("\r", "")
    text = text.replace("|", "\\|")
    text = text.replace("*", "\\*")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace("`", "\\`")
    text = text.replace("_", "\\_")
    text = text.replace("#", "\\#")
    text = text.replace("~", "\\~")
    return text


def _prepare_patient_rows(data: dict) -> list[dict]:
    """从 rag_results 中提取患者行数据，返回纯 POD 结构。"""
    rows = []
    for result in data.get("results", []):
        questions = []
        for q in result.get("clinical_questions", []):
            guidelines = []
            for gr in q.get("guideline_results", []):
                guidelines.append(
                    {
                        "name": gr.get("guideline", ""),
                        "version": gr.get("version", ""),
                        "recommendation": gr.get("recommendation", ""),
                        "evidence_level": gr.get("evidence_level", ""),
                        "source_file": gr.get("source_file", ""),
                        "source_lines": gr.get("source_lines", ""),
                    }
                )
            questions.append(
                {
                    "question": q.get("question", ""),
                    "guidelines": guidelines,
                    "evidence_table": [],
                    "consensus": q.get("consensus", []),
                    "differences": q.get("differences", []),
                }
            )
        rows.append(
            {
                "patient_id": result.get("patient_id", ""),
                "patient_name": result.get("patient_name", ""),
                "primary_site": result.get("primary_site", ""),
                "disease_type": result.get("disease_type", ""),
                "diagnosis_summary": result.get("diagnosis_summary", ""),
                "questions": questions,
            }
        )
    return rows


def _slugify(text: str) -> str:
    """生成 Markdown 锚点 slug（中文保留，空格转 -，去掉特殊字符）"""
    slug = text.strip().lower()
    slug = re.sub(r"[^\w\u4e00-\u9fff\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    return slug


def generate_md(data: dict, output_path: Path):
    """生成单一 Markdown 报告文件。"""
    rows = _prepare_patient_rows(data)
    generated_at = data.get("generated_at", str(date.today()))
    patient_count = data.get("patient_count", len(rows))

    seen_slugs: dict[str, int] = {}

    def _unique_slug(text: str) -> str:
        base = _slugify(text)
        if base in seen_slugs:
            seen_slugs[base] += 1
            return f"{base}-{seen_slugs[base]}"
        seen_slugs[base] = 1
        return base

    lines = []
    lines.append("# 批量指南推荐报告")
    lines.append("")
    lines.append(f"> 生成日期: {md_escape(generated_at)} | 患者数: {patient_count}")
    lines.append("")
    lines.append("## 目录")

    slug_map: dict[str, str] = {}
    for row in rows:
        pid = row["patient_id"]
        name = md_escape(row["patient_name"])
        slug = _unique_slug(f"{pid} {row['patient_name']}")
        slug_map[pid] = slug
        lines.append(f"- [{name}](#{slug})")

    lines.append("")
    lines.append("---")
    lines.append("")

    all_evidence_entries = []

    for row in rows:
        pid = row["patient_id"]
        name = md_escape(row["patient_name"])
        lines.append(f"## {pid} {name}")
        lines.append("")
        lines.append("### 基本信息")
        lines.append("")
        lines.append("| 字段 | 内容 |")
        lines.append("|------|------|")
        info_fields = [
            ("患者ID", pid),
            ("肿瘤部位", md_escape(row["primary_site"])),
            ("病种诊断", md_escape(row["disease_type"])),
            ("诊断摘要", md_escape(row["diagnosis_summary"])),
        ]
        for label, value in info_fields:
            lines.append(f"| {label} | {value or '—'} |")
        lines.append("")

        for qi, q in enumerate(row["questions"], 1):
            question_text = md_escape(q["question"])
            lines.append(f"### 临床问题 {qi}: {question_text}")
            lines.append("")

            for g in q["guidelines"]:
                gname = md_escape(g["name"])
                gver = md_escape(g["version"])
                lines.append(f"#### {gname} (v{gver})")
                lines.append("")
                source = g["source_file"]
                slines = g["source_lines"]
                source_display = (
                    f"{md_escape(source)} L{slines}" if slines else md_escape(source)
                )
                lines.append("| 属性 | 内容 |")
                lines.append("|------|------|")
                lines.append(f"| 推荐意见 | {md_escape(g['recommendation'])} |")
                lines.append(f"| 证据等级 | {md_escape(g['evidence_level'])} |")
                lines.append(f"| 来源 | {source_display} |")
                lines.append("")

                if g["evidence_level"]:
                    all_evidence_entries.append((g["name"], g["evidence_level"]))

            if any(g["evidence_level"] for g in q["guidelines"]):
                lines.append("#### 证据等级对照")
                lines.append("")
                lines.append("| 指南 | 证据等级 | 含义 |")
                lines.append("|------|----------|------|")
                for g in q["guidelines"]:
                    if g["evidence_level"]:
                        lines.append(
                            f"| {md_escape(g['name'])} | {md_escape(g['evidence_level'])} | — |"
                        )
                lines.append("")

            lines.append("#### 共识与差异")
            lines.append("")
            if q["consensus"]:
                lines.append("**共识点:**")
                for c in q["consensus"]:
                    lines.append(f"- {md_escape(c)}")
                lines.append("")
            if q["differences"]:
                lines.append("**主要差异:**")
                for d in q["differences"]:
                    lines.append(f"- {md_escape(d)}")
                lines.append("")

        lines.append("---")
        lines.append("")

    if all_evidence_entries:
        lines.append("## 附录：证据等级参考")
        lines.append("")
        lines.append("以下汇总本报告中出现的所有证据等级体系及其含义。")
        lines.append("")
        seen = set()
        lines.append("| 体系 | 等级 | 含义 |")
        lines.append("|------|------|------|")
        for gname, level in all_evidence_entries:
            key = (gname, level)
            if key not in seen:
                seen.add(key)
                lines.append(f"| {md_escape(gname)} | {md_escape(level)} | — |")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append(
        "*本文档由医学指南RAG系统自动生成，仅供临床参考，不替代专业医学判断。*"
    )
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  ✓ Markdown 报告: {output_path}")


def cmd_generate(args):
    """generate 子命令入口"""
    import warnings

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"RAG 结果文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)

    data = load_rag_results(input_path)
    patient_count = data.get("patient_count", len(data.get("results", [])))
    print(f"加载 RAG 结果: {patient_count} 位患者\n")

    if data.get("results"):
        data["results"] = _sort_results_by_name(data["results"])

    output_dir = Path(args.output_dir).resolve()
    fmt = args.format

    if fmt in ("all", "xlsx", "docx", "pptx"):
        warnings.warn(
            f"--format {fmt} 已废弃，已降级为 Markdown 输出。将在未来版本移除。",
            FutureWarning,
            stacklevel=2,
        )

    generated_at = data.get("generated_at", str(date.today()))
    safe_date = re.sub(r"[^\w-]", "", generated_at)
    filename = f"批量指南推荐报告_{safe_date}.md"
    generate_md(data, output_dir / filename)

    print(f"\n生成完成 → {output_dir}/")


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="批量患者指南检索管道工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # parse
    p_parse = sub.add_parser("parse", help="解析输入 xlsx → patients.json")
    p_parse.add_argument("--input", required=True, help="输入 xlsx 文件路径")
    p_parse.add_argument(
        "--output", default="Output/patients.json", help="输出 JSON 路径"
    )

    # split
    p_split = sub.add_parser("split", help="将 patients.json 分成多个批次文件")
    p_split.add_argument("--input", required=True, help="patients.json 路径")
    p_split.add_argument(
        "--batch-size", type=int, default=5, help="每批患者数 (默认 5)"
    )
    p_split.add_argument(
        "--output-dir", default="Output/batches", help="批次文件输出目录"
    )

    # orchestrate
    p_orch = sub.add_parser(
        "orchestrate", help="自动编排批处理流程（扫描知识库+生成 prompt）"
    )
    p_orch.add_argument("--patients", required=True, help="patients.json 路径")
    p_orch.add_argument("--kb-root", default=None, help="知识库根路径（可选）")
    p_orch.add_argument("--output-dir", default="Output/batches", help="输出目录")
    p_orch.add_argument("--batch-size", type=int, default=5, help="每批患者数 (默认 5)")
    p_orch.add_argument(
        "--max-prompt-tokens",
        type=int,
        default=80000,
        help="单个 prompt 最大 token 数 (默认 80000)",
    )

    # merge
    p_merge = sub.add_parser("merge", help="合并批次结果为 rag_results.json")
    p_merge.add_argument("--input-dir", required=True, help="批次结果所在目录")
    p_merge.add_argument(
        "--output", default="Output/rag_results.json", help="合并输出路径"
    )
    p_merge.add_argument(
        "--patients",
        default=None,
        help="patients.json 路径（可选，用于回注患者元数据）",
    )

    # validate
    p_validate = sub.add_parser("validate", help="验证 RAG 结果质量与完整性")
    p_validate.add_argument("--input", required=True, help="rag_results.json 路径")
    p_validate.add_argument(
        "--patients", help="patients.json 路径（可选，用于完整性对比）"
    )
    p_validate.add_argument(
        "--kb-profile", help="orchestration_plan.json 路径（可选，用于组织覆盖率检查）"
    )

    # index
    p_index = sub.add_parser("index", help="Build QMD index and inject Context metadata")
    p_index.add_argument("--kb-root", help="Knowledge base root directory")
    p_index.add_argument("--force", action="store_true", help="Force rebuild index")

    # verify-batch
    p_verify = sub.add_parser("verify-batch", help="验证批次执行证据的真实性")
    p_verify.add_argument("--input-dir", required=True, help="批次结果所在目录")
    p_verify.add_argument(
        "--kb-root", default=None, help="知识库根路径（可选，启用 snippet 校验）"
    )

    # generate
    p_gen = sub.add_parser("generate", help="从 RAG 结果生成 Markdown 报告")
    p_gen.add_argument("--input", required=True, help="RAG 结果 JSON 路径")
    p_gen.add_argument("--output-dir", default="Output", help="输出目录")
    p_gen.add_argument(
        "--format",
        choices=["all", "md", "xlsx", "docx", "pptx"],
        default="md",
        help="输出格式 (默认 md；xlsx/docx/pptx 已废弃)",
    )

    args = parser.parse_args()
    if args.command == "parse":
        cmd_parse(args)
    elif args.command == "split":
        cmd_split(args)
    elif args.command == "orchestrate":
        cmd_orchestrate(args)
    elif args.command == "merge":
        cmd_merge(args)
    elif args.command == "validate":
        cmd_validate(args)
    elif args.command == "index":
        cmd_index(args)
    elif args.command == "verify-batch":
        cmd_verify_batch(args)
    elif args.command == "generate":
        cmd_generate(args)


if __name__ == "__main__":
    main()
