"""KB 病种侧车元数据：词表归一化 + chunks/coverage 派生 + 文件落盘。

Phase 1 (KBM-03..06): 纯函数 API + 一个副作用入口 build_sidecar。
Phase 3 pipeline.py 通过 normalize_disease / filter_orgs_by_disease /
filter_chunks_by_disease 三个函数接入查询路径。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml


# ── 病种归一化常量 ─────────────────────────────────────────────────────

# 通用后缀（中文癌瘤系列 + 英文 cancer/carcinoma）— normalize_disease 双向剥离
_DISEASE_SUFFIX_RE = re.compile(
    r"(癌症|肿瘤|恶性肿瘤|癌|瘤|cancer|carcinoma|tumor|tumour|neoplasm)$",
    re.IGNORECASE,
)

# 提取 guideline_version 的年份正则（取 H1 文本里最后一个 4 位数字）
_YEAR_RE = re.compile(r"\b(\d{4})\b")

# 文件名 token 切分（连字符 / 下划线 / 点）
_FILENAME_TOKEN_RE = re.compile(r"[-_.]")


# ── 内置 seed 词表（D-08，10 个 canonical_key）─────────────────────────

_SYNONYM_SEED: dict[str, list[str]] = {
    "gastric": [
        "胃癌", "胃腺癌", "胃恶性肿瘤", "胃", "EGJ", "食管胃结合部腺癌",
        "gastric", "gastric cancer", "gastric adenocarcinoma", "GC", "stomach",
    ],
    "colorectal": [
        "结直肠癌", "结肠癌", "直肠癌", "结直肠", "结肠", "直肠",
        "CRC", "colorectal", "colon cancer", "rectal cancer", "colon", "rectal",
    ],
    "neuroendocrine": [
        "神经内分泌瘤", "神经内分泌肿瘤", "神经内分泌癌",
        "NEN", "NET", "NEC", "neuroendocrine",
    ],
    "esophageal": [
        "食管癌", "食道癌", "食管", "esophageal", "esophagus",
        "oesophageal", "esophag",
    ],
    "hepatic": [
        "肝癌", "原发性肝癌", "肝细胞癌", "肝", "HCC",
        "hepatic", "hepatocellular", "liver", "liver cancer", "hepat",
    ],
    "pancreatic": [
        "胰腺癌", "胰癌", "胰腺", "pancreatic", "pancreatic cancer",
        "pancrea", "pancreas",
    ],
    "breast": [
        "乳腺癌", "乳癌", "乳腺", "breast", "breast cancer",
    ],
    "lung": [
        "肺癌", "非小细胞肺癌", "小细胞肺癌", "肺", "NSCLC", "SCLC",
        "lung", "lung cancer", "pulmonary",
    ],
    "cervical": [
        "宫颈癌", "子宫颈癌", "宫颈", "cervical", "cervical cancer", "cervix",
    ],
    "lymphoma": [
        "淋巴瘤", "霍奇金淋巴瘤", "非霍奇金淋巴瘤", "B 细胞淋巴瘤",
        "lymphoma", "Hodgkin", "non-Hodgkin", "NHL", "HL", "DLBCL",
    ],
}


# ── 内部 helper ────────────────────────────────────────────────────────

def _normalize_token(text: str) -> str:
    """lower + strip + 去通用后缀。返回 ''（若全部被剥光，避免空匹配）。"""
    t = (text or "").strip().lower()
    # 反复剥离后缀（"胃腺癌肿瘤" → "胃腺癌" → "胃腺"）
    prev = None
    while t and t != prev:
        prev = t
        t = _DISEASE_SUFFIX_RE.sub("", t).strip()
    return t


def _build_reverse_index(synonym_map: dict) -> dict[str, str]:
    """构造 {normalized_alias: canonical_key} 反向索引（lazy 重算，纯函数）。"""
    rev: dict[str, str] = {}
    for canonical, aliases in synonym_map.items():
        # canonical 本身也算 alias
        for alias in [canonical, *aliases]:
            norm = _normalize_token(alias)
            if norm:
                rev.setdefault(norm, canonical)
    return rev


# ── 公开 API ───────────────────────────────────────────────────────────

def load_synonym_map(kb_root: Path) -> dict[str, list[str]]:
    """读取 $KB_ROOT/.metadata/synonym_map.yaml；不存在返回内置 seed 拷贝。"""
    p = Path(kb_root) / ".metadata" / "synonym_map.yaml"
    if not p.exists():
        return {k: list(v) for k, v in _SYNONYM_SEED.items()}
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    # 防御：确保返回类型 dict[str, list[str]]
    return {str(k): list(v or []) for k, v in data.items()}


def seed_synonym_map(kb_root: Path) -> Path:
    """若 $KB_ROOT/.metadata/synonym_map.yaml 不存在则落盘 seed。返回路径。"""
    meta_dir = Path(kb_root) / ".metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    p = meta_dir / "synonym_map.yaml"
    if p.exists():
        return p
    with p.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            _SYNONYM_SEED, fh,
            allow_unicode=True,
            sort_keys=True,
            default_flow_style=False,
        )
    return p


def normalize_disease(
    disease_type: str | None,
    synonym_map: dict,
) -> str | None:
    """patient.disease_type → canonical_key。未命中返回 None."""
    if not disease_type:
        return None
    norm = _normalize_token(disease_type)
    if not norm:
        return None
    rev = _build_reverse_index(synonym_map)
    # 完整规则化串先精确匹配
    if norm in rev:
        return rev[norm]
    # 兜底：把规则化串按 token 切分，任一 token 命中即返回 canonical
    for tok in _FILENAME_TOKEN_RE.split(norm):
        tok = tok.strip()
        if tok and tok in rev:
            return rev[tok]
    return None


def infer_chunk_tags(file_name: str, synonym_map: dict) -> list[str]:
    """从文件名（含或不含 .md / org 前缀）推断 canonical_keys 集合。

    对齐 batch_pipeline.py:1326-1328 的归一化：lower + token split。
    """
    if not file_name:
        return []
    stem = Path(file_name).stem.lower()
    rev = _build_reverse_index(synonym_map)
    hits: set[str] = set()
    for tok in _FILENAME_TOKEN_RE.split(stem):
        tok = _normalize_token(tok)
        if tok and tok in rev:
            hits.add(rev[tok])
    return sorted(hits)


def filter_orgs_by_disease(
    coverage: dict,
    canonical_key: str | None,
) -> list[str]:
    """org 级前置过滤。None / 全 miss → 返回 sorted(全集)。"""
    all_orgs = sorted(coverage.keys())
    if canonical_key is None:
        return all_orgs
    kept = [org for org in all_orgs if canonical_key in (coverage.get(org) or [])]
    return kept or all_orgs  # 全 miss 时保守保留


def filter_chunks_by_disease(
    hits: list[dict],
    chunks_meta: dict,
    canonical_key: str | None,
) -> list[dict]:
    """chunk 级后置过滤。
    - canonical_key=None → 全保留
    - chunks_meta 无该 path key → 保留（KBM-06 兜底）
    - chunk.disease_tags 为空 → 保留（KBM-06 兜底）
    - canonical_key ∈ tags → 保留；否则丢弃
    """
    if canonical_key is None:
        return list(hits)
    out: list[dict] = []
    for hit in hits:
        path = hit.get("path", "")
        meta = chunks_meta.get(path)
        if meta is None:
            out.append(hit)
            continue
        tags = meta.get("disease_tags") or []
        if not tags or canonical_key in tags:
            out.append(hit)
    return out


def _extract_year(text: str) -> str:
    """从 H1 行抽末位 4 位数字年份。"""
    if not text:
        return ""
    matches = _YEAR_RE.findall(text)
    return matches[-1] if matches else ""


def _strip_org_prefix(stem: str, org_name: str) -> str:
    """对齐 batch_pipeline.py:1326-1328 — lower 比较，命中则剥离。"""
    prefix = org_name.lower() + "-"
    if stem.lower().startswith(prefix):
        return stem[len(prefix):]
    return stem


def build_sidecar(
    kb_root: Path,
    orgs_found: list[tuple[str, Path, list[Path]]],
) -> dict:
    """副作用入口：写三个 .metadata/ 文件，返回 summary."""
    kb_root = Path(kb_root).resolve()
    meta_dir = kb_root / ".metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)

    # 路径遍历防御：确保 meta_dir 在 kb_root 子树下
    if not str(meta_dir).startswith(str(kb_root)):
        raise ValueError(
            f".metadata path escapes kb_root: {meta_dir} not under {kb_root}"
        )

    seed_synonym_map(kb_root)
    sm = load_synonym_map(kb_root)

    chunks: dict[str, dict] = {}
    for org_name, _org_dir, md_files in orgs_found:
        for md_file in md_files:
            # chunks.json key 对齐 hit.path（qmd:// URL，全小写）
            key = f"qmd://{org_name.lower()}/{md_file.name.lower()}"
            stem_no_prefix = _strip_org_prefix(md_file.stem, org_name)
            tags = infer_chunk_tags(stem_no_prefix, sm)
            # 读 H1 抽年份；文件不存在/读失败 → ""
            version = ""
            try:
                with md_file.open("r", encoding="utf-8") as fh:
                    first_line = fh.readline()
                version = _extract_year(first_line)
            except OSError:
                version = ""
            chunks[key] = {
                "disease_tags": tags,
                "org": org_name,
                "guideline_version": version,
            }

    coverage: dict[str, list[str]] = {}
    for meta in chunks.values():
        coverage.setdefault(meta["org"], set()).update(meta["disease_tags"])
    coverage_out = {org: sorted(tags) for org, tags in coverage.items()}

    chunks_path = meta_dir / "chunks.json"
    coverage_path = meta_dir / "org_disease_coverage.json"
    synonym_path = meta_dir / "synonym_map.yaml"

    chunks_path.write_text(
        json.dumps(chunks, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    coverage_path.write_text(
        json.dumps(coverage_out, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return {
        "chunks_path": chunks_path,
        "coverage_path": coverage_path,
        "synonym_path": synonym_path,
        "n_chunks": len(chunks),
        "n_orgs": len(coverage_out),
    }
