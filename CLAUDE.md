# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

---

## Project Overview

**Medical Guidelines RAG** is a clinical guidelines retrieval system based on Agent Skills. It uses **QMD hybrid retrieval (BM25 + vector + LLM reranking)** to retrieve content from multiple international/domestic medical guidelines.

**Core Use Cases**:
1. **Single-patient retrieval**: Clinical question → cross-guideline comparison table
2. **Batch patient processing**: Patient Excel → orchestrate → per-patient guideline reports (Markdown)

**Technical Features**:
- QMD hybrid retrieval — BM25 + vector search + LLM reranking
- Docling-extracted Markdown — searches `extracted/*.md` via QMD
- Cross-guideline evidence level mapping
- Domain-agnostic design — adapts to any medical specialty
- **v2.2 orchestrate** — deterministic batch processing with QMD pre-retrieval

---

## CRITICAL RULE: OUTPUT LANGUAGE

<HARD_CONSTRAINT>

**ALL output MUST be in Simplified Chinese (简体中文)**, regardless of source guideline language.

</HARD_CONSTRAINT>

---

## Knowledge Base

The knowledge base is configured via environment variable, **not stored in this repository**:

```
MEDICAL_GUIDELINES_DIR=/Users/f.sh/MyDocuments/RAG/guidelines
```

`resolve_kb_root()` priority: `--kb-root` param > `MEDICAL_GUIDELINES_DIR` env > `./guidelines/` > `./knowledge/`

---

## Architecture

```
medical-guidelines-suite/
├── SKILL.md                    # Skill definition (build + query + batch)
├── AGENTS.md                   # OpenCode platform instructions
├── CLAUDE.md                   # This file
├── skill.json                  # Package metadata
├── README.md / CHANGELOG.md
├── scripts/
│   ├── batch_pipeline.py       # 9 subcommands: parse/split/orchestrate/index/merge/validate/verify-batch/generate
│   ├── retriever.py            # QMD service wrapper (hybrid BM25 + vector + reranking)
│   ├── extract_all.py          # Batch extraction entry point (Docling → .md, legacy)
│   ├── extract_guidelines.py   # v2 extraction pipeline (MinerU + Docling + VLM)
│   └── extraction/             # Extraction modules (pdf_extractor, docx_extractor, postprocess, vlm_describer)
├── references/
│   ├── pdf_reading.md / pdf_extraction.md
│   ├── docx_reading.md / docx_extraction.md
│   ├── index_generation.md
│   └── input_format.md         # Input xlsx format spec (26 fields)
├── templates/
│   ├── data_structure_root.md  # Root index template
│   └── data_structure_org.md   # Organization index template
├── tests/                      # pytest suite (169 tests)
├── docs/                       # Design documents
│   └── solutions/              # Documented solutions (bugs, patterns), YAML frontmatter searchable by module/tags
├── Input/                      # User input files (xlsx, patients.json)
└── Output/                     # Generated outputs (auto-created)
```

---

## Common Commands

### Text Extraction (Docling, legacy)

```bash
python3 scripts/extract_all.py          # Incremental extraction (Docling → extracted/*.md)
python3 scripts/extract_all.py --force  # Force re-extraction
```

### Text Extraction v2 (MinerU + Docling + VLM)

```bash
# 提取单个 PDF（MinerU）或 DOCX（Docling）
python3 scripts/extract_guidelines.py extract --input <file> --output-dir <dir>

# 批量提取整个知识库
python3 scripts/extract_guidelines.py extract-all --kb-root $MEDICAL_GUIDELINES_DIR

# VLM 图片描述（需要 LM Studio 运行）
python3 scripts/extract_guidelines.py describe-images --input-dir <images_dir>

# 全流程（提取 + VLM，VLM 不可用时仍完成提取）
python3 scripts/extract_guidelines.py pipeline --kb-root $MEDICAL_GUIDELINES_DIR
```

### QMD Index

```bash
# Build QMD search index over extracted Markdown files
python3 scripts/batch_pipeline.py index --kb-root $MEDICAL_GUIDELINES_DIR
```

### Batch Patient Processing (v2.2 orchestrate)

```bash
# Parse input Excel
python3 scripts/batch_pipeline.py parse --input Input/2026-3-25.xlsx --output Output/patients.json

# Orchestrate: auto-scan KB, QMD pre-retrieval, generate batch prompts
python3 scripts/batch_pipeline.py orchestrate \
  --patients Output/patients.json \
  --output-dir Output/batches \
  --batch-size 5

# (LLM executes each batch prompt → Output/batches/rag_batch_*.json)

# Verify execution evidence + merge + validate + generate
python3 scripts/batch_pipeline.py verify-batch --input-dir Output/batches/ --kb-root $MEDICAL_GUIDELINES_DIR
python3 scripts/batch_pipeline.py merge --input-dir Output/batches/ --output Output/rag_results.json
python3 scripts/batch_pipeline.py validate --input Output/rag_results.json --patients Output/patients.json
python3 scripts/batch_pipeline.py generate --input Output/rag_results.json --format md
```

### Testing

```bash
python3 -m pytest tests/ -v          # Run all tests (148)
python3 -m pytest tests/ -v -k scan  # Run specific tests
```

---

## Cross-Guideline Evidence Level Comparison

| Guideline | Highest | High | Moderate | Low/Optional |
|-----------|---------|------|----------|--------------|
| NCCN | Category 1 | Category 2A | Category 2B | Category 3 |
| ESMO | I,A | II,B | III,C | IV,D |
| CSCO | I级(1A) | I级(1B/2A) | II级 | III级 |
| Japanese | Strong | Weak | — | — |

---

## Dependencies

| Tool | Install | Purpose |
|------|---------|---------|
| `mineru` | `uv tool install mineru` | PDF to Markdown extraction (v2, recommended) |
| `docling` | `pip install docling` | DOCX to Markdown extraction |
| `qmd` | `npm install -g @tobilu/qmd` | Hybrid BM25 + vector retrieval service |
| `openpyxl` | `pip install openpyxl` | Excel reading (input parsing) |

**NOT required**: pdftotext/poppler, python-docx, separate embedding model APIs

**Optional**: LM Studio (local VLM for image descriptions in extract_guidelines.py)

### QMD Model Configuration

QMD manages its own GGUF models. Configure local paths in `~/.config/qmd/index.yml`:

```yaml
models:
  embed:    /Users/f.sh/.cache/qmd/models/hf_ggml-org_embeddinggemma-300M-Q8_0.gguf
  rerank:   /Users/f.sh/.lmstudio/models/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF/qwen3-reranker-0.6b-q8_0.gguf
  generate: /Users/f.sh/.cache/qmd/models/hf_tobil_qmd-query-expansion-1.7B-q4_k_m.gguf
```

**重要限制**：`qmd embed`（索引构建）硬编码使用 embeddinggemma-300M（768维），
无视 `config.models.embed`。`config.models.embed` 只影响查询时向量编码，
因此 `embed` 字段必须与索引实际使用的模型一致（300M），否则查询时维度不匹配崩溃。

On first run, `embed` and `generate` are auto-downloaded (~313 MB + 1.2 GB). On macOS with
Apple Silicon, a Metal shader compile warning appears but does not affect results.

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MEDICAL_GUIDELINES_DIR` | — | Knowledge base root path (required) |
| `QMD_PORT` | `8181` | Port for QMD service |
| `QMD_AVAILABLE` | — | Set to `1` to enable integration tests gated on QMD |

---

## v3.1 async-pipeline Planning (Active)

GSD 规划文档落在 [`.planning/`](.planning/)：
- `.planning/PROJECT.md` — milestone 范围 + 13 项 key decisions
- `.planning/ROADMAP.md` — 4 phases × success criteria
- `.planning/REQUIREMENTS.md` — 37 个 v1 req 全部 mapping
- `.planning/STATE.md` — 当前位置 + 性能基线表
- `.planning/phases/01-async-retriever-kb-metadata-sidecar/` — Phase 1 CONTEXT + PATTERNS + 3 PLAN

**当前位置**：Phase 1 plans 已就绪并通过 plan-checker，等待 `/gsd-execute-phase 1` 启动 Wave 1（async retriever + kb_metadata 并行）。

**重要：D-01 释义改动** —— CONTEXT.md D-01 字面是「同步类降级为 thin shim」，但实际落地（`01-01-PLAN.md` 的 `<critical_conflict_resolution>`）是「async/sync 物理共存」—— 因为 sync 测试 patch `requests.post`，shim 会让 patch 失效。详见 [`Decisions.md`](Decisions.md) 2026-05 末条。

---

*Last Updated: 2026-05-11*
