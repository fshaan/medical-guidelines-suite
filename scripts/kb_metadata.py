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

# 文件名模糊匹配用：去掉一切非字母数字/CJK 字符（连字符、下划线、空格、全/半角
# 括号...）。2026-07-02 修复：真实 AsyncQMDService.query() 返回的 hit["path"]
# 里的文件名是 QMD 自己内部生成的 slug（如 "caca-中国肿瘤整合诊治指南-caca-
# 胃癌2025版.md"），跟 build_sidecar() 落盘 chunks.json 时用 Path.name 原样
# 拼出的 key（如 "caca_中国肿瘤整合诊治指南（caca) 胃癌2025版.md"）是两套独立
# 生成的"同一文件"表示，标点/连字符/下划线/空格/全半角括号全都不对齐，精确
# 字符串匹配恒为 miss。剥掉全部标点后两者字母数字/CJK 序列完全一致（真实数据
# 验证过），用这个做模糊匹配 key。
_NON_ALNUM_CJK_RE = re.compile(r"[^0-9a-zA-Z一-鿿]+")


def _canonical_filename(path: str) -> str:
    """取路径最后两段（org + 文件名），剥掉标点、转小写，用于跨表示形式
    模糊匹配。

    2026-07-02 codex 对抗式审查发现：只取最后一段文件名会丢掉 org 维度——
    如果不同机构恰好有同名/标点差异后撞车的文件名，会被错误合并成同一个
    canonical key，metadata 张冠李戴。带上 org 段可以在真实碰撞发生前提前
    收窄命中范围（真实 97 条 chunks.json + 109 个 extracted 文件已验证过
    当前 KB 无碰撞，这里是防御未来数据集扩张时出现同名文件）。
    """
    p = (path or "")
    if p.startswith("qmd://"):
        p = p[len("qmd://"):]
    parts = [seg for seg in p.split("/") if seg]
    tail = "/".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else "")
    return _NON_ALNUM_CJK_RE.sub("", tail).lower()

# 文件名 token 切分（连字符 / 下划线 / 点 / 空白）
# 2026-07-02 修复：真实 KB 里大量文件用空格分隔（如 "esmo gastric cancer
# 2022.md"、"jgca_japanese gastric cancer treatment guidelines 2021
# (6th edition).md"），此前不切空白导致这些文件名整段留成一个 token，永远
# exact-match 不上 synonym_map 里的单词级别 alias（"gastric"/"colorectal"）。
# 真实数据验证：org_disease_coverage.json 里 esmo/jgca 此前都是空 []。
# 注：CSCO/CACA 的中文文件名（病种词前后完全没有空白/连字符分隔，如
# "csco_胃癌诊疗指南2021.md"）这条修复本身覆盖不到——那是 exact-token 匹配
# 策略本身的结构性限制，另在 infer_chunk_tags() 里用限定长度>=2 的 CJK
# 子串匹配单独修复（见该函数 2026-07-02 注释）。
_FILENAME_TOKEN_RE = re.compile(r"[-_.\s]+")


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


_CJK_RE = re.compile(r"[一-鿿]")


def infer_chunk_tags(file_name: str, synonym_map: dict) -> list[str]:
    """从文件名（含或不含 .md / org 前缀）推断 canonical_keys 集合。

    对齐 batch_pipeline.py:1326-1328 的归一化：lower + token split。

    WR-04 known limit: 歧义器官 alias（pancreas/liver/stomach/pulmonary/cervix
    + 中文 肝/胃/肺）在与非疾病 token 混合的 stem 上会误命中（例如
    `pancreas-research.md` → 归 pancreatic）。Conventions.md 约定 KB 文件
    命名应避免疾病 alias 与非疾病 token 混排；Phase 2 计划引入 stop-token
    过滤做代码层防御。

    2026-07-02 修复：真实 KB 里 CSCO/CACA 的中文文件名（如
    "csco_胃癌诊疗指南2021.md"）病种词前后完全没有分隔符可切——
    "胃癌诊疗指南2021" 整段留成一个 token，exact-token match 恒 miss（真实
    数据验证：67/97 个 CSCO 文件此前 disease_tags 全是空 []，CSCO 是 KB 里
    最大的中文指南来源，系统性排除会直接破坏"跨指南对比"的核心价值）。
    对长度 >= 2 的 CJK alias 做子串匹配作为补充，刻意只限定 CJK 且长度 >= 2
    （排除"胃"/"肝"/"肺"这类单字器官别名）：
    - 不改变英文 token 的既有 exact-match 行为，WR-04 记录的英文歧义器官
      误命中风险不变、不放大
    - 单字 CJK 器官别名仍只走 exact-match，不参与子串匹配，避免引入同等级别
      的中文歧义误命中（"胃"单字出现在无关词组里的概率显著高于"胃癌"整词）
    """
    if not file_name:
        return []
    stem = Path(file_name).stem.lower()
    rev = _build_reverse_index(synonym_map)
    hits: set[str] = set()
    tokens = [_normalize_token(t) for t in _FILENAME_TOKEN_RE.split(stem)]
    for tok in tokens:
        if tok and tok in rev:
            hits.add(rev[tok])
    # 子串匹配用轻量归一化（只 strip+lower，不做后缀剥离）的原始 alias 文本，
    # 不能复用上面 exact-match 用的 rev（_build_reverse_index 对每个 alias
    # 都跑过 _normalize_token 的后缀剥离循环）——"胃癌"这种 2 字符 alias
    # 会被剥掉"癌"字退化成单字"胃"，长度检查会误判成单字器官别名而被排除，
    # 反而让最常见的疾病名（胃癌/肝癌/肺癌）全部匹配不上。
    #
    # 边界必须是"alias 本身带不带疾病后缀"（癌/瘤/cancer/tumor/...），不能
    # 只看长度：synonym_map 里同时收了裸器官名 alias（"胰腺"/"结肠"/"直肠"/
    # "乳腺"/"宫颈"，2+ 字符但不带疾病后缀）——如果只按长度放行，会把
    # "胰腺炎诊疗指南"误标成胰腺癌相关、"乳腺良性疾病指南"误标成乳腺癌相关
    # （codex 对抗式审查实测复现）。只放行本身带疾病后缀的 alias（"胰腺癌"/
    # "乳腺癌"会被单独列为 alias，仍能正常子串匹配），裸器官名 alias 完全
    # 不参与子串匹配。
    for tok in tokens:
        if not tok:
            continue
        for canonical, aliases in synonym_map.items():
            for alias in [canonical, *aliases]:
                alias_lite = (alias or "").strip().lower()
                if (
                    len(alias_lite) >= 2
                    and _CJK_RE.search(alias_lite)
                    and _DISEASE_SUFFIX_RE.search(alias_lite)
                    and alias_lite in tok
                ):
                    hits.add(canonical)
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
    - chunks_meta 无该 path key（模糊匹配文件名后）→ 保留（KBM-06 兜底）
    - chunk.disease_tags 为空 → 保留（KBM-06 兜底）
    - canonical_key ∈ tags → 保留；否则丢弃

    2026-07-02 修复：真实 hit["path"] 用 QMD 自己的文件名 slug（如
    "CACA/caca-....md"），chunks_meta 的 key 是 build_sidecar() 落盘时用
    "qmd://{org}/{Path.name}" 精确拼出来的（如 "qmd://caca/caca_....md"）——
    两者标点/连字符/下划线/空格全不对齐，原来的精确 dict.get(path) 恒为
    None，KBM-06"保留"兜底让这层过滤对真实数据完全形同虚设（这正是本 v3.1
    milestone 最初要解决的"结直肠癌命中胃癌 chunk"问题的直接成因）。改用
    _canonical_filename() 剥标点后按文件名模糊匹配。
    """
    if canonical_key is None:
        return list(hits)
    canon_index = {
        _canonical_filename(k): v for k, v in chunks_meta.items()
    }
    out: list[dict] = []
    for hit in hits:
        path = hit.get("path", "")
        meta = canon_index.get(_canonical_filename(path))
        if meta is None:
            out.append(hit)
            continue
        tags = meta.get("disease_tags") or []
        if not tags or canonical_key in tags:
            out.append(hit)
    return out


def _extract_year(text: str) -> str:
    """从 H1 行抽最大 4 位年份（1990-2100 区间）。

    IN-02: 原实现取「最后一个匹配」会被 H1 行里的 ICD-10 编号 / 化合物号
    / 历史 cohort 年份覆盖（如 "Gastric Cancer 2026 — supplemented from
    1999 cohort" 错误返回 1999；"NCCN 2026 ICD-10 C16.9 (1234)" 返回 1234）。
    改为「最大值 + 合理年份范围过滤」更鲁棒。
    """
    if not text:
        return ""
    years = [int(m) for m in _YEAR_RE.findall(text) if 1990 <= int(m) <= 2100]
    return str(max(years)) if years else ""


def _strip_org_prefix(stem: str, org_name: str) -> str:
    """对齐 batch_pipeline.py:1326-1328 — lower 比较，命中则剥离。"""
    prefix = org_name.lower() + "-"
    if stem.lower().startswith(prefix):
        return stem[len(prefix):]
    return stem


def _atomic_write_json(path: Path, data: dict) -> None:
    """tmp + rename 原子写：同目录写 .tmp 文件再 Path.replace（POSIX rename 原子）。

    避免 Path.write_text 在并发 cmd_index 下被中断而产生半截 JSON——下游
    pipeline.py json.load() 一旦读到坏文件会持续 JSONDecodeError。
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


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
            # WR-03 PHI 防御：org 字段写入 lowercase（与 key 一致）。生产 KB
            # 目录名可能含中文医院名/拼音姓名前缀，原大小写直接写入侧车 JSON
            # 会让 PHI 进入 git-trackable 文件。lowercase + 仅用作内部标识。
            chunks[key] = {
                "disease_tags": tags,
                "org": org_name.lower(),
                "guideline_version": version,
            }

    coverage: dict[str, list[str]] = {}
    for meta in chunks.values():
        coverage.setdefault(meta["org"], set()).update(meta["disease_tags"])
    coverage_out = {org: sorted(tags) for org, tags in coverage.items()}

    chunks_path = meta_dir / "chunks.json"
    coverage_path = meta_dir / "org_disease_coverage.json"
    synonym_path = meta_dir / "synonym_map.yaml"

    # WR-05: tmp + rename 原子写盘。两个 cmd_index 并发跑（脚本化重建场景）
    # 时，Path.write_text 不原子会写出半截 JSON，下游 pipeline.py 加载时
    # JSONDecodeError 且持续读到坏文件。POSIX rename 在 same-filesystem 下原子。
    _atomic_write_json(chunks_path, chunks)
    _atomic_write_json(coverage_path, coverage_out)

    return {
        "chunks_path": chunks_path,
        "coverage_path": coverage_path,
        "synonym_path": synonym_path,
        "n_chunks": len(chunks),
        "n_orgs": len(coverage_out),
    }
