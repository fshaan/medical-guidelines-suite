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
    build_sidecar,
    filter_chunks_by_disease,
    filter_orgs_by_disease,
    infer_chunk_tags,
    load_synonym_map,
    normalize_disease,
    seed_synonym_map,
)


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
    # 因此 NCCN/ESMO/CSCO 三个 org 的 coverage 都应含 "gastric"
    assert "gastric" in coverage.get("NCCN", [])
    assert summary["n_chunks"] == 3
    assert summary["n_orgs"] == 3

def test_build_sidecar_chunk_key_uses_qmd_url_lowercase(mock_kb):
    orgs_found = [("NCCN", mock_kb / "NCCN", sorted((mock_kb / "NCCN" / "extracted").glob("*.md")))]
    build_sidecar(mock_kb, orgs_found)
    chunks = json.loads((mock_kb / ".metadata" / "chunks.json").read_text(encoding="utf-8"))
    keys = list(chunks.keys())
    assert all(k.startswith("qmd://nccn/") for k in keys), keys
    assert all(k == k.lower() for k in keys), "chunks.json keys must be lowercased"

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
