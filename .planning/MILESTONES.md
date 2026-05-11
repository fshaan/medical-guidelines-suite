# Milestones: medical-guidelines-suite

记录已完成 milestone 与当前进行中 milestone 的索引。详细工作内容见 `PROJECT.md` / `ROADMAP.md` / git history。

## Shipped

### v3.0 QMD Hybrid Retrieval + MinerU Extraction (2026-05-11)

**Status:** ✅ Shipped to `main` (commit `6052490` Merge feature/qmd-retrieval)

**Delivered:**
- QMD 混合检索（BM25 + 向量 embeddinggemma-300M + Qwen3-Reranker-0.6B）替代旧的 docling-only 检索
- MinerU + Docling + VLM 提取流水线（PDF/DOCX → Markdown，图片走 LM Studio VLM）
- 97 份指南索引（CSCO/NCCN/ESMO/JGCA/CACA），13.06k 向量 chunks
- 批量患者 7 阶段流水线（parse → split → orchestrate → verify-batch → merge → validate → generate）
- 跨指南证据等级映射（Category 1-3 / I-V,A-E / I-III级 / 强弱推荐 互转）
- 测试套件扩展至 173 条（含 QMD/MinerU/VLM gated 集成测试）
- 环境变量配置 KB 根路径（`MEDICAL_GUIDELINES_DIR`），大文件不入仓
- CLI 工作流文档、ci 工作流、提取流水线重设计文档

**Known issues discovered post-ship (2026-05-11 10 例 fixture 测试):**
- LLM 自由生成 JSON 偶发引号未转义 → verify-batch FAIL（1 次需人工修复）
- 结直肠癌患者的 ESMO/JGCA/CACA 查询返回胃癌 chunk（score 0.92）→ QMD 无 metadata filter
- 10 例端到端 ~60min（LLM 人工 50min 占比 83%）

**These issues drive the next milestone (v3.1).**

---

### v2.x Pre-QMD（合并前的批量流水线基线）

**Delivered:**
- 七阶段批量处理流水线（parse/split/orchestrate/verify-batch/merge/validate/generate）
- Docling 单管线提取（PDF/DOCX → Markdown）
- Org-disease 过滤（结直肠癌跳过 ESMO/JGCA/CACA）
- citation_coverage 验证逻辑（兼容旧批次字符串格式）
- 9 个批次合并为 43 名患者的统一结果集（验证场景）

---

## Active

### v3.1 async-pipeline (2026-05-11 → planning)

**Status:** Planning（等待 `/gsd-plan-phase 1`）

**Goal:** 把 10 例患者端到端耗时从 ~60min 降到 <10min，消除两个 v3.0 post-ship 质量缺陷。

**Phases:**
1. Async Retriever + KB Metadata Sidecar (2d)
2. LLM Client + Schema + Unit Tests (2d)
3. Pipeline + run Subcommand + Interface Extension (3d) — **关键 gate**
4. Delete Batch Concept + Rewrite Tests (1d) — Phase 3 ship 后 stabilize 一周再启动

**Total scope:** 37 v1 requirements across 4 phases (see `REQUIREMENTS.md` / `ROADMAP.md`)

**Source of truth:** `docs/refactor_plan_2026-05-11.md`

---

## Future Candidates (v3.2+)

- Streaming：LLM 流式输出 + 患者级 SSE/WebSocket 推送
- Observability：结构化日志 + Prometheus metrics + 失败聚类报告
- Multi-Disease：单患者多病种拆分独立查询并合并

详见 `REQUIREMENTS.md` v2 Requirements 段。

---

*Milestones index initialized: 2026-05-11*
