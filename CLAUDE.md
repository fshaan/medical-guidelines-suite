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
  embed:    ~/.cache/qmd/models/hf_ggml-org_embeddinggemma-300M-Q8_0.gguf
  rerank:   /path/to/Qwen3-Reranker-0.6B-Q8_0-GGUF/qwen3-reranker-0.6b-q8_0.gguf
  generate: ~/.cache/qmd/models/hf_tobil_qmd-query-expansion-1.7B-q4_k_m.gguf
```

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

*Last Updated: 2026-04-23*
