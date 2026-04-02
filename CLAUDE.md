# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

---

## Project Overview

**Medical Guidelines RAG** is a clinical guidelines retrieval system based on Agent Skills. It uses **hierarchical keyword search** (NOT vector database) to retrieve content from multiple international/domestic medical guidelines.

**Core Use Cases**:
1. **Single-patient retrieval**: Clinical question → cross-guideline comparison table
2. **Batch patient processing**: Patient Excel → orchestrate → per-patient guideline reports (xlsx + docx + pptx)

**Technical Features**:
- No vector database — grep + Agent hierarchical navigation
- Pre-extracted plain text — searches `extracted/*.txt`
- Cross-guideline evidence level mapping
- Domain-agnostic design — adapts to any medical specialty
- **v2.2 orchestrate** — deterministic batch processing with pre-generated grep commands

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
│   ├── batch_pipeline.py       # 8 subcommands: parse/split/orchestrate/merge/validate/verify-batch/generate
│   ├── extract_pdf.py          # PDF text extraction
│   ├── extract_docx.py         # DOCX text extraction
│   └── extract_all.py          # Batch extraction entry point
├── references/
│   ├── pdf_reading.md / pdf_extraction.md
│   ├── docx_reading.md / docx_extraction.md
│   ├── index_generation.md
│   └── input_format.md         # Input xlsx format spec (26 fields)
├── templates/
│   ├── data_structure_root.md  # Root index template
│   └── data_structure_org.md   # Organization index template
├── tests/                      # pytest suite (148 tests)
├── docs/                       # Design documents
│   └── solutions/              # Documented solutions (bugs, patterns), YAML frontmatter searchable by module/tags
├── Input/                      # User input files (xlsx, patients.json)
└── Output/                     # Generated outputs (auto-created)
```

---

## Common Commands

### Text Extraction

```bash
python scripts/extract_all.py          # Incremental extraction
python scripts/extract_all.py --force  # Force re-extraction
```

### Batch Patient Processing (v2.2 orchestrate)

```bash
# Parse input Excel
python scripts/batch_pipeline.py parse --input Input/2026-3-25.xlsx --output Output/patients.json

# Orchestrate: auto-scan KB, extract features, generate batch prompts
python scripts/batch_pipeline.py orchestrate \
  --patients Output/patients.json \
  --output-dir Output/batches \
  --batch-size 5

# (LLM executes each batch prompt → Output/batches/rag_batch_*.json)

# Verify execution evidence + merge + validate + generate
python scripts/batch_pipeline.py verify-batch --input-dir Output/batches/ --kb-root $MEDICAL_GUIDELINES_DIR
python scripts/batch_pipeline.py merge --input-dir Output/batches/ --output Output/rag_results.json
python scripts/batch_pipeline.py validate --input Output/rag_results.json --patients Output/patients.json
python scripts/batch_pipeline.py generate --input Output/rag_results.json --format md
```

### Testing

```bash
python -m pytest tests/ -v          # Run all tests (148)
python -m pytest tests/ -v -k scan  # Run specific tests
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

| Tool | Purpose |
|------|---------|
| `pdftotext` (poppler) | PDF text extraction |
| `openpyxl` | Excel reading (input parsing) |

**NOT required**: Embedding models, vector databases, additional LLM APIs

---

*Last Updated: 2026-04-01*
