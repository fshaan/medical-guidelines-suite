# medical-guidelines-suite

## What This Is

基于 QMD 混合检索（BM25 + 向量 + LLM 重排）的多机构医学指南 RAG 系统，为临床决策提供跨指南（CSCO/NCCN/ESMO/JGCA/CACA）的循证推荐对比。当前 v3.0 支持单患者交互式查询与批量患者 Excel 处理两种工作流，所有输出强制简体中文。

## Current Milestone: v3.1 async-pipeline

**Goal:** 把 10 例患者端到端耗时从 ~60min 降到 <10min，同时消除 JSON 引号转义 bug 和结直肠癌病种错配问题。

**Target features:**
- 异步 QMD 检索（串行 3min → 并发 30-60s）
- 内网 vLLM + Qwen3.5-35B-A3B 替代人工 LLM（50min → 3-5min）
- JSON Schema strict 输出（消除引号转义 bug）
- 病种侧车元数据 + 双层过滤（消除结直肠癌引用胃癌 chunk）
- per-patient shard 输出 + 部分容错 + `--resume`
- CLI 从 7 阶段折叠为 4 阶段（parse / run / validate / generate，删 batch 概念）

**Source of truth:** `docs/refactor_plan_2026-05-11.md`（grill-me 13 轮闭环）

## Core Value

把临床医生从"手工翻 5+ 本指南找对应推荐"中解放出来——一次输入患者临床信息，自动拉取多机构指南的相关章节并生成结构化的循证对比与共识/差异分析。

## Requirements

### Validated

<!-- v3.0 已交付（2026-05-11 主线合并）。这些是后续重构必须保留的能力。 -->

- ✓ **QMD 混合检索**：BM25 + 向量（embeddinggemma-300M 768 维）+ Qwen3-Reranker-0.6B 三路融合 — v3.0
- ✓ **MinerU + Docling + VLM 提取流水线**：PDF/DOCX → Markdown，图片走 LM Studio VLM 描述 — v3.0
- ✓ **97 份指南索引**：5 大机构（CSCO/NCCN/ESMO/JGCA/CACA），13.06k 向量 chunks — v3.0
- ✓ **批量患者处理**：parse → split → orchestrate → verify-batch → merge → validate → generate 7 阶段流水线 — v3.0
- ✓ **跨指南证据等级映射**：Category 1-3 / I-V,A-E / I-III级 / 强弱推荐 互转 — v2.x
- ✓ **测试覆盖 173 条**：pytest 全绿，含 QMD/MinerU/VLM 集成测试 — v3.0
- ✓ **环境变量配置 KB 根路径**：MEDICAL_GUIDELINES_DIR，避免大文件入库 — v2.x

### Active

<!-- v3.1 async-pipeline milestone 目标。来源：docs/refactor_plan_2026-05-11.md grill-me 13 轮闭环。 -->

- [ ] **异步 LLM 调用流水线**：用 vLLM/Qwen3.5-35B-A3B + httpx.AsyncClient 替代当前的人工"复制 prompt → 粘贴 JSON"
- [ ] **QMD 异步检索**：39 次查询从串行 ~3min 降到并发 ~30-60s
- [ ] **JSON Schema strict 输出**：服务端拒绝 schema 违反，消除引号转义 bug
- [ ] **病种侧车元数据 + 双层过滤**：synonym_map.yaml + chunks.json，解决结直肠癌引用胃癌 chunk 的错配
- [ ] **per-patient shard 输出**：`Output/patients/<id>.json` 为 canonical，`rag_results.json` 退为派生
- [ ] **部分容错 + --resume**：失败患者进 `_failed/`，shard 存在跳过
- [ ] **citation_coverage 反馈重试**：<0.5 时带计算结果反馈再调一次，仍不达标接受并标 `partial`
- [ ] **CLI 折叠**：7 阶段 → 4 阶段（parse / run / validate / generate），删除 split/orchestrate/verify-batch/merge

### Out of Scope

<!-- v3.1 milestone 明确排除。 -->

- 托管 LLM API 默认接入（DeepSeek/OpenAI） — 仅作为 fallback profile，主路径走内网 vLLM
- QMD 索引层 metadata filtering — QMD 不支持，改为侧车 JSON + 应用层过滤
- batch 概念保留 — Phase 4 一次性删除，per-patient shard 是新真相
- 多病种混合患者拆分 — 当前一例患者按 `disease_type` 主诊断匹配
- 实时流式输出 — 一次性 JSON Schema 输出更稳，流式留待 v3.2+
- 患者数据持久化数据库 — Output/ 目录文件即结果，不引入 DB

## Context

**项目演进路径**：v1.x docling 单管线 → v2.x 七阶段批量流水线 + 跨指南映射 → v3.0 QMD 混合检索 + MinerU 提取（2026-05 合并到 main）。

**最近基线测试（2026-05-11）**：用 `Input/2026-4-23.xlsx` 跑 10 例患者端到端，发现两个质量缺陷：
1. LLM 自由生成 JSON 偶发引号未转义 → verify-batch FAIL（本次 1 次需人工修复）
2. 结直肠癌患者的 ESMO/JGCA/CACA 查询返回胃癌 chunk（score 0.92）→ QMD 无 metadata filter，只能事后 org 级过滤，chunk 级未拦截

**性能基线**：10 例患者端到端 ~60min（LLM 人工 50min + QMD 串行 3min + 其他 <10s）。

**技术栈**：Python 3.9+, openpyxl, MinerU(uv), Docling, QMD(npm), pytest, LM Studio (VLM)。

**知识库位置**：`$MEDICAL_GUIDELINES_DIR=/Users/f.sh/MyDocuments/RAG/guidelines`（不入仓）。

**强约束**：所有 user-facing 输出简体中文（不论指南原文语言）。

## Constraints

- **Tech stack**: Python 3.9+ + asyncio — 异步重构基础，与现有 retriever.py/batch_pipeline.py 接口对齐
- **LLM 推理**: 内网 vLLM + Qwen3.5-35B-A3B — 共享服务器，LAN 延迟 1-3s，timeout 180s，并发 5 路（留 50% 余量）
- **QMD 并发**: 全局 Semaphore(8) — QMD 是 MCP HTTP 服务，会话粘性需保持
- **Output 语言**: 简体中文 HARD CONSTRAINT — `CLAUDE.md` 显式声明
- **数据隐私**: 患者 PHI 不入 git，Input/ 已 gitignore — 病种归一化在本机完成，词表可入仓
- **测试**: pytest 必须全绿 — 重构每个 Phase ship 前都要回归 173 条现有测试
- **回滚**: Phase 4 删除 batch 概念前 Phase 3 必须 stabilize 一周 — 避免 batch 调用方未迁移完成就被删

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| LLM 推理栈选 vLLM + Qwen3.5-35B-A3B | 内网部署、json_schema strict 原生支持、A3B MoE 激活 3B 中文够用 | — Pending（Phase 2 验证质量） |
| 部署拓扑选内网共享 LAN/VPN | 数据不出网，timeout 180s 容忍同事任务争抢 | — Pending |
| LLM 并发预算 5 路 | 共享服务器谨慎默认，留 50% 余量 | — Pending |
| `evidence_level` 完全枚举 strict | 服务端 sampling 拒绝违反，根除引号 bug | — Pending |
| per-patient shard 为 canonical | 失败隔离 + --resume 粒度 + 调试方便 | — Pending |
| 病种匹配走 synonym_map.yaml 层级 | 标签缺失则保留（保守默认）— 避免误丢 | — Pending |
| 失败语义部分容错 | 进 `_failed/` + 退出码 1 — 全或无太脆弱 | — Pending |
| KB metadata 存侧车 JSON | QMD 不支持索引层 filter，应用层过滤更可控 | — Pending |
| --resume shard 存在跳过 | 与 _failed/ 自动重试组合，幂等推进 | — Pending |
| Phase 4 一次性删除 batch | 保留双写会有 drift，stabilize 一周后清理 | — Pending |
| Prompt 改 system + user 双消息 | 与 vLLM/OpenAI chat 格式一致 | — Pending |
| citation_coverage 反馈重试一次 | <0.5 接受 + 标 partial — 避免无限重试 | — Pending |
| CLI 主参数 `--patients-dir` | 旧 `--input` deprecated 但保留兼容 | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-11 after initialization (brownfield, v3.0 → v3.1 async-pipeline)*
