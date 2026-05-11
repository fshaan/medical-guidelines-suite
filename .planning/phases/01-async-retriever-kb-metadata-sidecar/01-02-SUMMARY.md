---
phase: 01-async-retriever-kb-metadata-sidecar
plan: 02
subsystem: kb_metadata
tags: [kb_metadata, synonym_map, disease_normalization, sidecar, tdd]
dependency_graph:
  requires: [01-01-async-retriever]
  provides: [kb_metadata_api, synonym_seed, build_sidecar]
  affects: [scripts/kb_metadata.py, tests/test_kb_metadata.py]
tech_stack:
  added: [yaml.safe_load/yaml.safe_dump, json, re]
  patterns: [bidirectional_normalization, reverse_index, conservative_fallback]
key_files:
  created:
    - scripts/kb_metadata.py
    - tests/test_kb_metadata.py
  modified: []
decisions:
  - TDD execution: tests written first (RED e73c607), implementation second (GREEN f34caed)
  - Python 3.9 compat: str().startswith() replaces Path.is_relative_to() for path traversal defense
  - _normalize_token uses while-loop for repeated suffix stripping (e.g. "胃腺癌肿瘤" -> "胃腺")
metrics:
  duration: 110s
  completed: 2026-05-11
  tasks_completed: 2
  tests_added: 22
  files_created: 2
---

# Phase 1 Plan 02: KB Metadata Sidecar Summary

病种侧车元数据工具模块（6 公共函数 + _SYNONYM_SEED 内置词表），TDD 方式交付，22 测试全绿。

## 交付内容

### scripts/kb_metadata.py（287 LOC）

**6 个公共函数：**

| 函数 | 用途 | 类型 |
|------|------|------|
| `load_synonym_map(kb_root)` | 读 YAML 词表；不存在返回 seed 拷贝 | 纯函数 |
| `seed_synonym_map(kb_root)` | 首次落盘 seed；已存在跳过 | 副作用 |
| `normalize_disease(disease_type, synonym_map)` | 病种归一化 → canonical_key | 纯函数 |
| `infer_chunk_tags(file_name, synonym_map)` | 文件名启发式标签推断 | 纯函数 |
| `filter_orgs_by_disease(coverage, canonical_key)` | org 级前置过滤 | 纯函数 |
| `filter_chunks_by_disease(hits, chunks_meta, canonical_key)` | chunk 级后置过滤 | 纯函数 |
| `build_sidecar(kb_root, orgs_found)` | 生成 3 个 .metadata/ 文件 | 副作用入口 |

**内部 helper：** `_normalize_token`, `_build_reverse_index`, `_extract_year`, `_strip_org_prefix`

### _SYNONYM_SEED（10 个 canonical_key）

| Key | Alias 数 | 代表性 alias |
|-----|----------|-------------|
| gastric | 11 | 胃癌, 胃腺癌, EGJ, stomach |
| colorectal | 12 | 结直肠癌, 结肠癌, CRC, colon |
| neuroendocrine | 7 | 神经内分泌瘤, NEN, NET, NEC |
| esophageal | 7 | 食管癌, 食道癌, esophag |
| hepatic | 10 | 肝癌, 肝细胞癌, HCC, liver |
| pancreatic | 7 | 胰腺癌, pancrea, pancreas |
| breast | 5 | 乳腺癌, 乳癌, breast cancer |
| lung | 9 | 肺癌, NSCLC, SCLC, pulmonary |
| cervical | 6 | 宫颈癌, 子宫颈癌, cervix |
| lymphoma | 10 | 淋巴瘤, 霍奇金, NHL, DLBCL |

### chunks.json Schema

```json
{
  "qmd://nccn/nccn-gastriccancer.md": {
    "disease_tags": ["gastric"],
    "org": "NCCN",
    "guideline_version": "2026"
  }
}
```

Key 生成规则：`qmd://{org_name.lower()}/{file_name.lower()}`（对齐 batch_pipeline.py:1316-1328）

## 关键行为锚定

- `normalize_disease('胃腺癌', seed) == 'gastric'` — 双向后缀剥离 + 反向索引匹配
- `normalize_disease('罕见癌种', seed) is None` — 未命中返回 None
- `filter_orgs_by_disease(coverage, None)` → 全保留（保守兜底）
- `filter_chunks_by_disease(hits, meta, key)` — disease_tags 为空时保留（KBM-06）

## KBM-06 兜底覆盖矩阵

| 场景 | canonical_key | 预期行为 | 测试用例 |
|------|--------------|----------|----------|
| org 全 miss | "neuroendocrine" | 返回全集 sorted | test_filter_orgs_all_miss_returns_all_as_fallback |
| chunk tags 为空 | "gastric" | 保留 hit | test_filter_chunks_keep_when_tags_empty |
| meta 缺失 path | "gastric" | 保留 hit | test_filter_chunks_keep_when_meta_missing |
| canonical_key=None | None | 全保留 | test_filter_orgs_none_returns_all, test_filter_chunks_none_returns_all |

## 安全措施

- **yaml.safe_load/yaml.safe_dump** — 禁止 yaml.load/yaml.dump（T-02-01/T-02-04）
- **路径遍历防御** — `str(meta_dir).startswith(str(kb_root.resolve()))`（T-02-02）
- **信息泄露防护** — chunks.json key 用 qmd:// URL 而非绝对路径（T-02-03）
- **类型防御** — load_synonym_map 返回值强转 `{str(k): list(v or [])}`（T-02-08）

## Deviations from Plan

None — plan executed exactly as written.

## Phase 3 接入提示

在 `_run_one_patient` 内的 dedupe 后调：
```python
from scripts.kb_metadata import filter_chunks_by_disease, filter_orgs_by_disease
hits = filter_chunks_by_disease(hits, chunks_meta, canonical_key)
relevant_orgs = filter_orgs_by_disease(coverage, canonical_key)
```
