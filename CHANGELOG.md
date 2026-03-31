# Changelog

All notable changes to the Medical Guidelines Suite will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v2.4.0 (2026-04-01)

### Added
- `--profile slim` mode for small model (27B) compatibility
  - Reduces grep commands from ~36 to ~12 per patient via dimension grouping
  - Flattened 2-layer JSON output (vs 5-layer in full mode)
  - Micro-checkpoints in prompts for better instruction following
  - Dynamic org filtering by disease type
  - Auto-generated consensus/differences in merge stage
  - Relaxed validation (skip anti-laziness checks, lower thresholds)

### Fixed
- slim prompt 未写入磁盘 — `cmd_orchestrate` 写 prompt 文件时遗漏 `config=config` 参数
- `_auto_split_batch` 不透传 config — slim 模式下 token 估算使用错误模板
- `verify-batch` 的 V1/V2 检查对 slim 永远失败 — slim 不要求 execution_log，现已跳过
- `_extract_patient_list` 重复调用 — `cmd_verify_batch` 中消除冗余调用
- `_aggregate_flat_results` KeyError — `patient_id` 缺失时使用 `.get()` 防护

## [2.3.1] - 2026-03-27

### Fixed
- **merge 输出 0 患者** — 修复 prompt 未指定完整 JSON 顶层结构导致 LLM 输出 `"patients"` 键而代码读 `"results"` 键的 schema drift 问题
- **扁平→嵌套结构兼容** — 新增 `_extract_patient_list` helper，自动识别 `results`/`patients` 键并将扁平 `guideline_results` 包装为 `clinical_questions` 嵌套结构
- **`[] or fallback` 布尔短路陷阱** — 使用显式 `"results" in batch_data` 检查替代 `or` 短路，避免空列表错误回退

### Added
- **完整 JSON 输出模板** — batch prompt 现在包含完整的顶层 JSON 结构模板，明确 `"results"` 键名和扁平结构要求
- **Anti-subagent 指令** — batch prompt 的 MANDATORY_RULES 新增规则 6-7，禁止 LLM 使用 Agent/Task tool 或编写脚本批量执行 grep
- **静默失败警告** — 当 batch 文件有 `batch_id` 但无法提取患者时，输出 stderr 警告
- **10 个新测试** (80 total) — _extract_patient_list helper (5)、prompt 模板 (2)、merge 兼容性 (3)

## [2.3.0] - 2026-03-25

### Added
- **`verify-batch` subcommand** — 在合并前自动验证 LLM 是否真正执行了每条 grep 命令，防止偷懒
  - V1: 命令覆盖率检查（prompt 中的 CMD-ID vs JSON 中的 execution_log）
  - V2: 计数一致性检查（声称的命令数 vs 实际命令数）
  - V3: snippet 真实性校验（从知识库文件中验证 first_match_snippet 是否存在）
  - V4: 空匹配矛盾检测（match_count=0 却有具体推荐内容）
- **批次深度衰减检测** — validate 自动检测后续批次的检索深度是否显著低于前序批次（前后半段对比 + 连续 3 批单调递减）
- **CMD-ID numbering** in batch prompts — each grep command gets a unique `CMD-P{n}-{org}-{seq}` identifier
- **Per-patient checkpoints** in batch prompts — execution_summary verification points after each patient's grep commands
- **execution_log JSON schema** in batch prompt output requirements — complete example with cmd_id, match_count, first_match_snippet
- **19 new pytest tests** (70 total) covering prompt hardening, verify-batch, and depth decay detection

### Changed
- batch_pipeline.py now has 8 subcommands (added verify-batch)
- Batch workflow: verify-batch required between LLM execution and merge step
- SKILL.md updated with execution evidence recording rules and 3 new Forbidden Actions
- Prompt "补充检索" section tightened: supplemental grep only after all CMD-* commands complete

## [2.2.0] - 2026-03-25

### Added
- **`orchestrate` subcommand** — deterministic batch processing that auto-scans the knowledge base, extracts 9 clinical dimensions per patient, and pre-generates all grep commands and self-contained batch prompts
- **Three-layer batch isolation**: CONTEXT_RESET text instructions + validate cross-batch similarity detection (Jaccard > 0.8) + SKILL.md HARD_CONSTRAINT rules
- **Organization coverage check** in validate: detects when guideline results miss known organizations
- **Patient name sorting** in generate: outputs ordered by pinyin approximation
- **51 pytest tests** covering all new orchestrate functions
- Design decision documentation (`docs/v2.2-decisions.md`) and engineering review (`docs/architecture.md`)

### Changed
- SKILL.md Step 3 rewritten: all batch processing now uses orchestrate-driven workflow (no more direct/batch mode split)
- 2 new HARD_CONSTRAINTs: execution mode constraints (no subagents, no custom scripts) + full-coverage retrieval rules
- 7 new Forbidden Actions for batch processing discipline
- validate now accepts `--kb-profile` for organization coverage checking
- batch_pipeline.py now has 7 subcommands: parse, split, orchestrate, merge, validate, generate

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
