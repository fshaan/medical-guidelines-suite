# Changelog

All notable changes to the Medical Guidelines Suite will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.0] - 2026-03-24

### Added
- **Batch quality assurance pipeline** for large patient sets (30+)
  - `split` subcommand: splits patients.json into batches (default 5 per batch)
  - `merge` subcommand: combines batch results with deduplication and structure normalization
  - `validate` subcommand: checks completeness, field quality, recommendation length, cross-patient consistency
- **Checkpoint recovery**: interrupted batch processing can resume from last completed batch
- **Batch context isolation rules** (HARD_CONSTRAINT) to prevent LLM output quality degradation
- Automatic processing mode selection: direct (N≤5) vs batch (N>5)

### Changed
- Execution workflow (Section 7/8) rewritten with split → batch → merge → validate → generate pipeline
- batch_pipeline.py now has 5 subcommands: parse, split, merge, validate, generate

## [2.0.0] - 2026-03-19

### Added
- **Batch patient processing skill** (medical-guidelines-batch)
  - Parse Excel input (structured 26-column or narrative 3-column format)
  - Auto-infer clinical questions per patient based on disease + staging + biomarkers
  - RAG retrieval loop across all relevant guideline organizations
  - Three output deliverables: xlsx summary, per-patient docx reports, pptx slides
- `scripts/batch_pipeline.py` — parse + generate subcommands
- `templates/report_template.pptx` — branded slide template with 5 layouts
- `references/input_format.md` — input Excel format specification

### Changed
- Excel summary now includes 差异点 (differences) column between 共识点 and guideline columns
- Word reports now in landscape orientation with patient ID in title and generation date
- PowerPoint slides use template layouts with proper placeholder filling:
  - Layout 2: patient info table + clinical questions
  - Layout 3: guideline comparison table (4 cols: 指南/版本/推荐意见/证据等级)
  - Layout 4: two-column consensus/differences view
- Summary overview auto-paginates at 15 rows per slide
- Table font auto-scales when >8 guideline results
- Recommendation text truncated at 150 chars (at nearest punctuation)

### Dependencies
- Added: `openpyxl`, `python-docx`, `python-pptx` (pip packages)

## [1.0.0] - 2026-03-05

### Added
- Initial release of medical-guidelines-suite
- Knowledge base builder skill (PDF/DOCX extraction + index generation)
- Knowledge retrieval skill (hierarchical grep-based search)
- Support for multiple guideline organizations (NCCN, ESMO, CSCO, CACA, Japanese, Korean)
- Cross-guideline evidence level comparison
- Bilingual keyword search (Chinese + English)
- Template files for data_structure.md generation
- Extraction scripts for PDF and DOCX files
- Example clinical queries documentation
- HEARTBEAT.md for portable update checking

### Features
- Domain-agnostic design - adapts to any medical specialty
- No vector database required - uses grep-based hierarchical search
- Pre-extracted text files for faster retrieval
- Output always in Simplified Chinese for Chinese clinicians
- User-configurable knowledge base location
- Support for environment variable configuration
- OpenClaw compatible packaging with clawdis frontmatter
