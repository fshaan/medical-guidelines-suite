# Medical Guidelines Suite v2.3.0

Clinical guidelines knowledge base builder, retrieval engine, and batch patient report generator.

## Installation

### Option A: Claude Code Ecosystem Skill

```bash
npx skills add fshaan/medical-guidelines-suite -g -y
```

### Option B: Manual Installation

```bash
# Clone to Claude Code skills directory
git clone https://github.com/fshaan/medical-guidelines-suite \
  ~/.claude/skills/medical-guidelines-suite

# Install Python dependencies
pip install openpyxl python-docx python-pptx pdfplumber
# or: brew install poppler  (for pdftotext)
```

### Option C: Project-Local Installation

```bash
# Copy to project's .agent/skills/ directory
cp -r medical-guidelines-suite /path/to/project/.agent/skills/

# Or symlink
ln -s $(pwd) /path/to/project/.agent/skills/medical-guidelines-suite
```

## Quick Start

### 1. Build Knowledge Base

Place guideline PDFs/DOCXs in a directory and run:

```bash
# Create knowledge base structure
mkdir -p guidelines/NCCN/extracted
cp NCCN_Gastric_2026.pdf guidelines/NCCN/

# Extract text
python scripts/extract_all.py --force
```

Then ask Claude: "构建知识库索引"

### 2. Single-Patient Query

Ask Claude: "HER2阳性晚期胃癌一线治疗，各指南推荐什么？"

### 3. Batch Patient Processing

```bash
# Parse patient Excel
python scripts/batch_pipeline.py parse --input patients.xlsx --output Output/patients.json

# Orchestrate: auto-scan KB, extract features, generate grep commands + batch prompts
python scripts/batch_pipeline.py orchestrate \
  --patients Output/patients.json --kb-root ./guidelines --batch-size 5

# (Claude executes each batch prompt → Output/batches/rag_batch_*.json)

# Verify execution evidence + merge batch results + validate quality
python scripts/batch_pipeline.py verify-batch --input-dir Output/batches/ --kb-root ./guidelines
python scripts/batch_pipeline.py merge --input-dir Output/batches/ --output Output/rag_results.json
python scripts/batch_pipeline.py validate --input Output/rag_results.json --patients Output/patients.json

# Generate reports
python scripts/batch_pipeline.py generate --input Output/rag_results.json --format all
```

Or simply ask Claude: "对 patients.xlsx 中的患者，批量检索指南推荐"

The `orchestrate` command replaces manual splitting — it automatically scans the knowledge base, extracts 9 clinical dimensions from each patient, and generates self-contained batch prompts with pre-built grep commands. Supports checkpoint recovery for interrupted processing.

## Output Deliverables

| File | Description |
|------|-------------|
| `批量推荐汇总表.xlsx` | Summary table with all patients and guideline recommendations |
| `reports/{ID}_{name}_推荐意见书.docx` | Per-patient recommendation report (landscape) |
| `批量推荐幻灯片.pptx` | Presentation slides using branded template |

## File Structure

```
medical-guidelines-suite/
├── SKILL.md                    # Main skill definition (build + query + batch)
├── skill.json                  # Package metadata
├── CHANGELOG.md                # Version history
├── README.md                   # This file
├── references/
│   ├── pdf_reading.md          # PDF processing guide
│   ├── pdf_extraction.md       # PDF extraction methods
│   ├── docx_reading.md         # DOCX processing guide
│   ├── docx_extraction.md      # DOCX extraction methods
│   ├── index_generation.md     # Index template guide
│   └── input_format.md         # Batch input Excel spec
├── templates/
│   ├── data_structure_root.md  # Root index template
│   ├── data_structure_org.md   # Organization index template
│   └── report_template.pptx   # PowerPoint slide template
├── scripts/
│   ├── extract_pdf.py          # PDF text extraction
│   ├── extract_docx.py         # DOCX text extraction
│   ├── extract_all.py          # Batch extraction
│   └── batch_pipeline.py       # Batch patient pipeline (8 subcommands incl. verify-batch)
├── tests/                      # pytest test suite (70 tests)
├── docs/
│   ├── v2.3-anti-laziness-spec.md  # v2.3 execution evidence spec
│   ├── v2.2-fix-plan.md       # v2.2 design spec
│   ├── v2.2-decisions.md      # Confirmed design decisions (D1-D9)
│   └── architecture.md        # Engineering review report
└── examples/
    └── sample_queries.md       # Example clinical questions
```

## Requirements

- Python 3.9+
- `openpyxl` — Excel read/write
- `python-docx` — Word document generation
- `python-pptx` — PowerPoint generation
- `pdftotext` (poppler) — PDF text extraction (optional, for build phase)

## Acknowledgments

This project was inspired by [ConardLi/rag-skill](https://github.com/ConardLi/rag-skill), which demonstrated the hierarchical index + progressive retrieval pattern for local knowledge bases using Claude Code Skills. We adopted and extended its core architectural ideas — `data_structure.md` layered indexing, grep-based search, and the "learn before process" constraint — into the medical guidelines domain, adding cross-guideline comparison, batch patient processing, and multi-format report generation.

## License

This work is licensed under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).

[![CC BY-NC-SA 4.0](https://licensebuttons.net/l/by-nc-sa/4.0/88x31.png)](https://creativecommons.org/licenses/by-nc-sa/4.0/)
