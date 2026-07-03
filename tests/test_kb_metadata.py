"""Tests for scripts/kb_metadata.py — synonym map / normalize / filters / build_sidecar.

Covers KBM-03..06 + D-09 双向规则化 + KBM-06 保守兜底 + chunks.json schema.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.kb_metadata import (
    _SYNONYM_SEED,
    _extract_year,
    build_sidecar,
    filter_chunks_by_disease,
    filter_orgs_by_disease,
    infer_chunk_tags,
    load_synonym_map,
    normalize_disease,
    seed_synonym_map,
)


# ── _extract_year (IN-02) ───────────────────────────────────────────────

def test_extract_year_takes_max_in_range():
    """IN-02: 取最大年份，避免被历史 cohort / ICD 编号污染。"""
    assert _extract_year("# Gastric Cancer 2026 — from 1999 cohort") == "2026"


def test_extract_year_filters_out_of_range():
    """1990-2100 之外的 4 位数字（如 ICD 编号、化合物号）必须丢弃。"""
    assert _extract_year("# NCCN 2026 ICD-10 C16.9 (1234)") == "2026"
    assert _extract_year("# Compound 9999 protocol") == ""


def test_extract_year_empty_when_no_match():
    assert _extract_year("") == ""
    assert _extract_year("# No year here") == ""


# ── 词表 seed 完整性 ──────────────────────────────────────────────────

def test_seed_covers_ten_canonical_keys():
    expected = {
        "gastric", "colorectal", "neuroendocrine", "esophageal", "hepatic",
        "pancreatic", "breast", "lung", "cervical", "lymphoma",
    }
    assert expected.issubset(_SYNONYM_SEED.keys()), (
        f"missing canonical keys: {expected - set(_SYNONYM_SEED.keys())}"
    )

def test_seed_each_key_has_min_aliases():
    for canonical, aliases in _SYNONYM_SEED.items():
        assert len(aliases) >= 5, f"{canonical} only has {len(aliases)} aliases"


# ── normalize_disease ────────────────────────────────────────────────

def test_normalize_hit_zh():
    assert normalize_disease("胃腺癌", _SYNONYM_SEED) == "gastric"

def test_normalize_hit_en_case_insensitive():
    assert normalize_disease("Gastric Cancer", _SYNONYM_SEED) == "gastric"

def test_normalize_strip_suffix():
    # "胃肿瘤" → 剥 "肿瘤" → "胃" → 命中 alias "胃"
    assert normalize_disease("胃肿瘤", _SYNONYM_SEED) == "gastric"

def test_normalize_miss_returns_none():
    assert normalize_disease("罕见癌种", _SYNONYM_SEED) is None

def test_normalize_empty_or_none():
    assert normalize_disease("", _SYNONYM_SEED) is None
    assert normalize_disease(None, _SYNONYM_SEED) is None  # type: ignore[arg-type]


# ── infer_chunk_tags ──────────────────────────────────────────────────

def test_infer_chunk_tags_from_filename():
    # 模拟 batch_pipeline 剥离 org 前缀后的 stem
    tags = infer_chunk_tags("gastric-2026.v2_en", _SYNONYM_SEED)
    assert "gastric" in tags

def test_infer_chunk_tags_empty_when_no_match():
    tags = infer_chunk_tags("rarecancer-2026", _SYNONYM_SEED)
    assert tags == []


def test_infer_chunk_tags_known_false_positive_on_organ_token():
    """WR-04 boundary doc: 歧义器官 alias 与非疾病 token 混排会误命中。

    这是 Phase 1 已知限制（见 infer_chunk_tags docstring + Conventions.md）。
    Phase 2 计划用 stop-token list 修复。此测试 documenting 当前行为，
    一旦未来代码引入 stop-token 过滤，应将断言反转。
    """
    # `pancreas` 是 pancreatic 的器官 alias，与 `research` 这种非疾病 token
    # 混排时会被切分后命中——KB 命名约定要求避免此类 stem，否则触发误归类。
    tags = infer_chunk_tags("pancreas-research-2026", _SYNONYM_SEED)
    assert "pancreatic" in tags, (
        "WR-04 known limit changed: review Conventions.md and Phase 2 stop-token plan"
    )


def test_infer_chunk_tags_splits_on_whitespace():
    """2026-07-02 回归：真实 KB 里大量文件用空格分隔（如 ESMO/JGCA 的
    "esmo gastric cancer 2022.md"），旧的 _FILENAME_TOKEN_RE 只切
    -_. 不切空白，整段 stem 留成一个 token，exact-match 永远打不上
    单词级 alias。真实数据验证：org_disease_coverage.json 里 esmo/jgca
    此前恒为空 []，修复后正确打上 gastric。
    """
    assert infer_chunk_tags("esmo gastric cancer 2022", _SYNONYM_SEED) == ["gastric"]
    assert infer_chunk_tags(
        "jgca_japanese gastric cancer treatment guidelines 2021 (6th edition)",
        _SYNONYM_SEED,
    ) == ["gastric"]


def test_infer_chunk_tags_cjk_substring_match_for_unbroken_chinese_filenames():
    """2026-07-02 回归：CSCO/CACA 的中文文件名（如 "csco_胃癌诊疗指南2021"）
    病种词前后完全没有分隔符，整段留成一个 token，exact-token match 恒 miss
    （真实数据：67/97 个 CSCO 文件此前 disease_tags 全是空 []——CSCO 是 KB
    里最大的中文指南来源）。子串匹配修复必须用轻量归一化（不做后缀剥离）的
    原始 alias 做长度判断——"胃癌"是 2 字符，但 _normalize_token 的后缀
    剥离会把它退化成单字"胃"，如果长度检查用的是剥离后的形式，"胃癌"这个
    最常见的疾病名反而会被误判成单字器官别名而被排除（实测踩过这个坑）。
    """
    assert infer_chunk_tags("csco_胃癌诊疗指南2021", _SYNONYM_SEED) == ["gastric"]
    assert infer_chunk_tags("csco_结直肠癌诊疗指南2021", _SYNONYM_SEED) == ["colorectal"]
    assert infer_chunk_tags(
        "caca_中国肿瘤整合诊治指南（caca) 胃癌2025版", _SYNONYM_SEED
    ) == ["gastric"]
    # 单字器官别名（"胃"单独一个字）刻意不参与子串匹配——WR-04 记录的中文
    # 歧义器官误命中风险边界不应放大。
    assert infer_chunk_tags("csco_胃肠间质瘤诊疗指南2022", _SYNONYM_SEED) == []


def test_infer_chunk_tags_cjk_substring_rejects_bare_organ_names():
    """2026-07-02 回归（codex 对抗式审查）：子串匹配的边界必须是"alias 本身
    带疾病后缀（癌/瘤/cancer/tumor...）"，不能只看长度。synonym_map 里同时
    收了裸器官名 alias（"胰腺"/"结肠"/"直肠"/"乳腺"/"宫颈"，2+ 字符但不带
    疾病后缀）——如果只按长度放行，会把下面这些支持治疗/良性疾病/炎症指南
    误标成对应癌症相关，污染检索：
    - 胰腺炎指南 → pancreatic（错的，胰腺炎不是胰腺癌）
    - 乳腺良性疾病指南 → breast（错的）
    - 结肠息肉指南 → colorectal（错的，息肉不是癌）
    真实数据实测：修复前这三条全部假阳性，修复后全部正确返回 []。
    """
    assert infer_chunk_tags("胰腺炎诊疗指南2022", _SYNONYM_SEED) == []
    assert infer_chunk_tags("乳腺良性疾病诊疗指南2022", _SYNONYM_SEED) == []
    assert infer_chunk_tags("结肠息肉诊疗指南2022", _SYNONYM_SEED) == []
    assert infer_chunk_tags("宫颈筛查指南2022", _SYNONYM_SEED) == []


# ── filter_orgs_by_disease ────────────────────────────────────────────

def test_filter_orgs_drop_unmatched():
    coverage = {
        "NCCN": ["gastric", "colorectal"],
        "ESMO": ["gastric"],
        "JGCA": ["gastric"],
    }
    assert filter_orgs_by_disease(coverage, "colorectal") == ["NCCN"]

def test_filter_orgs_none_returns_all():
    coverage = {"NCCN": ["gastric"], "ESMO": ["gastric"]}
    assert filter_orgs_by_disease(coverage, None) == ["ESMO", "NCCN"]

def test_filter_orgs_all_miss_returns_all_as_fallback():
    coverage = {"NCCN": ["gastric"], "ESMO": ["gastric"]}
    # neuroendocrine 在两个 org 都没覆盖 → 返回全集（保守兜底）
    assert filter_orgs_by_disease(coverage, "neuroendocrine") == ["ESMO", "NCCN"]


# ── filter_chunks_by_disease ──────────────────────────────────────────

def test_filter_chunks_keep_when_tags_empty():
    hits = [{"path": "qmd://nccn/f.md", "content": "...", "score": 0.9}]
    meta = {"qmd://nccn/f.md": {"disease_tags": [], "org": "NCCN", "guideline_version": ""}}
    kept = filter_chunks_by_disease(hits, meta, "gastric")
    assert kept == hits  # KBM-06 兜底

def test_filter_chunks_drop_mismatch():
    hits = [{"path": "qmd://nccn/colorectal.md", "score": 0.9}]
    meta = {"qmd://nccn/colorectal.md": {"disease_tags": ["colorectal"], "org": "NCCN", "guideline_version": ""}}
    kept = filter_chunks_by_disease(hits, meta, "gastric")
    assert kept == []

def test_filter_chunks_keep_when_meta_missing():
    # chunks.json 没覆盖到该 hit → 保留（KBM-06 兜底）
    hits = [{"path": "qmd://nccn/unknown.md", "score": 0.9}]
    kept = filter_chunks_by_disease(hits, {}, "gastric")
    assert kept == hits

def test_filter_chunks_none_returns_all():
    hits = [{"path": "qmd://nccn/a.md"}, {"path": "qmd://esmo/b.md"}]
    kept = filter_chunks_by_disease(hits, {}, None)
    assert kept == hits


def test_filter_chunks_fuzzy_matches_real_qmd_path_format():
    """2026-07-02 回归：真实 AsyncQMDService.query() 返回的 hit["path"] 是
    QMD 自己生成的文件名 slug（连字符），跟 build_sidecar() 落盘 chunks.json
    时用 Path.name 原样拼出的 key（下划线/空格/全角括号）不是同一套表示，
    精确 dict.get(path) 恒 miss，导致 chunk 级过滤对真实数据完全形同虚设
    （这正是 v3.1 milestone 最初要解决的"结直肠癌命中胃癌 chunk"问题的直接
    成因）。真实样本：CACA 一份文件的两种表示已验证模糊匹配后完全一致。
    """
    # QMD 返回的真实 hit path（连字符 slug）
    hits = [{"path": "CACA/caca-中国肿瘤整合诊治指南-caca-胃癌2025版.md", "score": 0.9}]
    # build_sidecar() 落盘的真实 chunks.json key（下划线+空格+全角括号）
    meta = {
        "qmd://caca/caca_中国肿瘤整合诊治指南（caca) 胃癌2025版.md": {
            "disease_tags": ["colorectal"], "org": "caca", "guideline_version": "2025",
        }
    }
    # gastric 患者查询命中这份被标为 colorectal 的文件 → 必须被剔除
    # （精确匹配 bug 存在时：meta lookup 恒 None，KBM-06 兜底"保留"，
    # 这条断言在旧代码下会失败——证明模糊匹配确实生效了）
    kept = filter_chunks_by_disease(hits, meta, "gastric")
    assert kept == []


# ── load / seed synonym_map ──────────────────────────────────────────

def test_load_synonym_map_returns_seed_when_missing(tmp_path):
    # tmp_path 下没有 .metadata/synonym_map.yaml → 返回内置 seed 拷贝
    sm = load_synonym_map(tmp_path)
    assert "gastric" in sm
    # 必须是拷贝（修改不影响 _SYNONYM_SEED）
    sm["gastric"].append("modified")
    assert "modified" not in _SYNONYM_SEED["gastric"]

def test_seed_synonym_map_creates_file_when_missing(tmp_path):
    p = seed_synonym_map(tmp_path)
    assert p.exists()
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert "gastric" in data
    assert "胃腺癌" in data["gastric"]

def test_seed_synonym_map_skips_when_exists(tmp_path):
    # 先手工写一个自定义 yaml
    meta = tmp_path / ".metadata"
    meta.mkdir()
    custom = meta / "synonym_map.yaml"
    custom.write_text("gastric:\n  - 自定义别名\n", encoding="utf-8")

    p = seed_synonym_map(tmp_path)
    assert p == custom
    # 内容必须不变（保护人工 overrides）
    data = yaml.safe_load(custom.read_text(encoding="utf-8"))
    assert data == {"gastric": ["自定义别名"]}


# ── build_sidecar 端到端（用 mock_kb fixture）────────────────────────

def test_build_sidecar_creates_three_files(mock_kb):
    # mock_kb 已构造 NCCN/ESMO/CSCO/extracted/<org>_GastricCancer.md
    orgs_found = []
    for org_name in sorted(["NCCN", "ESMO", "CSCO"]):
        ext = mock_kb / org_name / "extracted"
        md_files = sorted(ext.glob("*.md"))
        orgs_found.append((org_name, mock_kb / org_name, md_files))

    summary = build_sidecar(mock_kb, orgs_found)

    assert (mock_kb / ".metadata" / "chunks.json").exists()
    assert (mock_kb / ".metadata" / "org_disease_coverage.json").exists()
    assert (mock_kb / ".metadata" / "synonym_map.yaml").exists()

    # schema sanity
    chunks = json.loads((mock_kb / ".metadata" / "chunks.json").read_text(encoding="utf-8"))
    assert isinstance(chunks, dict)
    for key, meta in chunks.items():
        assert key.startswith("qmd://")
        assert "disease_tags" in meta
        assert "org" in meta
        assert "guideline_version" in meta

    coverage = json.loads(
        (mock_kb / ".metadata" / "org_disease_coverage.json").read_text(encoding="utf-8")
    )
    # 文件名是 NCCN_GastricCancer.md → token 切分含 "gastriccancer"
    # 规则化后 "gastriccancer" → 剥 "cancer" → "gastric" → 命中
    # 因此 nccn/esmo/csco 三个 org 的 coverage 都应含 "gastric"
    # (WR-03 PHI 防御：coverage keys 与 chunks[*].org 一致，全部 lowercase)
    assert "gastric" in coverage.get("nccn", [])
    assert summary["n_chunks"] == 3
    assert summary["n_orgs"] == 3

def test_build_sidecar_chunk_key_uses_qmd_url_lowercase(mock_kb):
    orgs_found = [("NCCN", mock_kb / "NCCN", sorted((mock_kb / "NCCN" / "extracted").glob("*.md")))]
    build_sidecar(mock_kb, orgs_found)
    chunks = json.loads((mock_kb / ".metadata" / "chunks.json").read_text(encoding="utf-8"))
    keys = list(chunks.keys())
    assert all(k.startswith("qmd://nccn/") for k in keys), keys
    assert all(k == k.lower() for k in keys), "chunks.json keys must be lowercased"

def test_build_sidecar_org_field_is_lowercase(mock_kb):
    """WR-03 PHI 防御：chunks[*].org 与 coverage keys 必须 lowercase，
    避免生产 KB 目录名中可能含的中文/PHI 进入 git-trackable JSON。
    """
    orgs_found = [(
        "NCCN", mock_kb / "NCCN", sorted((mock_kb / "NCCN" / "extracted").glob("*.md"))
    )]
    build_sidecar(mock_kb, orgs_found)

    chunks = json.loads((mock_kb / ".metadata" / "chunks.json").read_text(encoding="utf-8"))
    for key, meta in chunks.items():
        assert meta["org"] == meta["org"].lower(), (
            f"chunks[{key}].org='{meta['org']}' contains uppercase — PHI risk"
        )

    coverage = json.loads(
        (mock_kb / ".metadata" / "org_disease_coverage.json").read_text(encoding="utf-8")
    )
    for org_key in coverage:
        assert org_key == org_key.lower(), (
            f"coverage key '{org_key}' contains uppercase — PHI risk"
        )


def test_build_sidecar_writes_atomically_no_tmp_residue(mock_kb):
    """WR-05 回归：build_sidecar 走 tmp + rename 路径，正常完成后 .tmp 不残留。"""
    orgs_found = [(
        "NCCN", mock_kb / "NCCN", sorted((mock_kb / "NCCN" / "extracted").glob("*.md"))
    )]
    build_sidecar(mock_kb, orgs_found)
    meta = mock_kb / ".metadata"
    residue = list(meta.glob("*.tmp")) + list(meta.glob("*.json.tmp"))
    assert residue == [], f".tmp residue not cleaned up: {residue}"


def test_build_sidecar_skips_existing_synonym_map(mock_kb):
    # 先手工写 overrides
    meta = mock_kb / ".metadata"
    meta.mkdir(exist_ok=True)
    (meta / "synonym_map.yaml").write_text("gastric:\n  - 用户自定义\n", encoding="utf-8")

    orgs_found = [("NCCN", mock_kb / "NCCN", sorted((mock_kb / "NCCN" / "extracted").glob("*.md")))]
    build_sidecar(mock_kb, orgs_found)

    data = yaml.safe_load((meta / "synonym_map.yaml").read_text(encoding="utf-8"))
    assert data == {"gastric": ["用户自定义"]}  # 不被覆盖
