# Medical Guidelines Suite v3.1

Clinical guidelines knowledge base builder, retrieval engine, and batch patient report generator.

> **Active milestone — v3.1 async-pipeline (Phase 1–3 complete, E2E verified 10/10)**: 端到端从 ~60min 降到 **205.9s**（降 94%）。Phase 1（async retriever + KB sidecar）、Phase 2（vLLM client + schema）、Phase 3（async pipeline + `run` subcommand）全部落地并通过真实 E2E 验收（2026-07-03：10 例真实案例 10/10 成功，wall 205.9s，QG-01..05 全 PASS），**319 tests passing**。关键突破：决定性对比测试定位 max_tokens 超时三层根因（hits 过载诱发退化 / vLLM strict 模式 string 字段写不停 / json_object 丢失 enum 强制）+ codex 对抗审查 → 退化感知三档降级链 + json_object 主路径 + evidence_level 应用层归一化。Phase 4（删除 batch 概念）待 stabilize 一周后启动。Source of truth: [`docs/refactor_plan_2026-05-11.md`](docs/refactor_plan_2026-05-11.md)。规划文档：[`.planning/`](.planning/)。案例报告：[`docs/case_report_2026-07-03/`](docs/case_report_2026-07-03/)。

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
pip install docling openpyxl

# Install QMD hybrid retrieval service
npm install -g @tobilu/qmd
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

# Extract to Markdown (Docling)
python3 scripts/extract_all.py --force

# Build QMD search index
python3 scripts/batch_pipeline.py index --kb-root ./guidelines
```

Then ask Claude: "构建知识库索引"

### 2. Single-Patient Query

Ask Claude: "HER2阳性晚期胃癌一线治疗，各指南推荐什么？"

### 3. Batch Patient Processing

```bash
# Parse patient Excel
python3 scripts/batch_pipeline.py parse --input patients.xlsx --output Output/patients.json

# Run end-to-end async pipeline (v3.1 — single command replaces 4-step orchestrate flow)
python3 scripts/batch_pipeline.py run \
  --patients Output/patients.json \
  --output-dir Output/ \
  --llm-profile qwen3-vllm-lan \
  --concurrency-patients 2 \
  --concurrency-qmd 8

# Validate per-patient results
python3 scripts/batch_pipeline.py validate --patients-dir Output/patients/

# Generate reports
python3 scripts/batch_pipeline.py generate --patients-dir Output/patients/
```

The `run` subcommand (v3.1) is the primary entry point — it runs QMD pre-retrieval + LLM inference + per-patient JSON output in a single async pipeline with N-way patient concurrency. Failed patients are isolated to `Output/_failed/` and can be retried with `--resume`.

Legacy `split / orchestrate / verify-batch / merge` subcommands are hidden (`--help` won't show them) but still callable for backward compatibility during the stabilize period.

## Output Deliverables

| File | Description |
|------|-------------|
| `批量指南推荐报告_YYYYMMDD.md` | Single Markdown report with all patients, TOC navigation, guideline cards, evidence appendix |

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
│   ├── docx_extraction.md      # DOCX processing methods
│   ├── index_generation.md     # Index template guide
│   └── input_format.md         # Batch input Excel spec
├── templates/
│   ├── data_structure_root.md  # Root index template
│   └── data_structure_org.md   # Organization index template
├── scripts/
│   ├── retriever.py            # QMD service wrapper (sync + async, BM25 + vector + reranking)
│   ├── llm_client.py           # AsyncLLMClient + schema + 退化检测(DegenerationError) + evidence_level 归一化 + 三类重试
│   ├── pipeline.py             # Async per-patient orchestrator (run_pipeline + 退化感知三档降级链 + helpers)
│   ├── kb_metadata.py          # KB disease metadata + synonym map + dual-layer filtering
│   ├── extract_all.py          # Legacy batch extraction (Docling → extracted/*.md)
│   ├── extract_guidelines.py   # v2 extraction pipeline (MinerU + Docling + VLM)
│   ├── extraction/             # Extraction modules (pdf/docx/postprocess/vlm_describer)
│   ├── dev/                    # 开发期验证脚本（max_tokens_probe 决定性测试 / e2e_real_retrieval / build_new_patients / gen_report）
│   └── batch_pipeline.py       # CLI entry point (parse/run/validate/generate/index + 4 hidden legacy)
├── config/
│   └── llm_profiles.yaml       # LLM profile definitions (qwen3-vllm-lan, deepseek-cloud)
├── tests/                      # pytest test suite (319 tests, 含退化/归一化/E2E smoke 回归)
├── docs/
│   ├── refactor_plan_2026-05-11.md  # v3.1 async-pipeline source of truth (grill-me 13-round)
│   ├── phase3_e2e_acceptance.md     # Phase 3 E2E verification checklist + sign-off template
│   ├── v2.3-anti-laziness-spec.md  # v2.3 execution evidence spec
│   ├── v2.2-fix-plan.md       # v2.2 design spec
│   ├── v2.2-decisions.md      # Confirmed design decisions (D1-D9)
│   ├── architecture.md        # Engineering review report
│   └── solutions/             # Documented solutions (YAML frontmatter, searchable by module/tag)
├── .planning/                  # GSD planning artifacts (PROJECT/ROADMAP/REQUIREMENTS/STATE/phases)
└── examples/
    └── sample_queries.md       # Example clinical questions
```

## Requirements

- Python 3.9+
- `httpx` — Async HTTP client for QMD + LLM calls (`pip install httpx`)
- `docling` — PDF/DOCX to Markdown extraction (`pip install docling`)
- `openpyxl` — Excel input parsing (`pip install openpyxl`)
- `qmd` — Hybrid BM25 + vector retrieval service (`npm install -g @tobilu/qmd`)
- `pyyaml` — LLM profile YAML parsing (`pip install pyyaml`)

## Acknowledgments

This project was inspired by [ConardLi/rag-skill](https://github.com/ConardLi/rag-skill), which demonstrated the hierarchical index + progressive retrieval pattern for local knowledge bases using Claude Code Skills. We adopted and extended its core architectural ideas — `data_structure.md` layered indexing and the "learn before process" constraint — into the medical guidelines domain, adding QMD hybrid retrieval (BM25 + vector + LLM reranking), cross-guideline comparison, batch patient processing, and Markdown report generation.

## License

This work is licensed under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).

[![CC BY-NC-SA 4.0](https://licensebuttons.net/l/by-nc-sa/4.0/88x31.png)](https://creativecommons.org/licenses/by-nc-sa/4.0/)
