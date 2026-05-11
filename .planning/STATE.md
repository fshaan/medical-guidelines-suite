---
milestone: v3.1
name: async-pipeline
status: planning
current_phase: 1
current_plan: null
phases_total: 4
phases_complete: 0
plans_total: 0
plans_complete: 0
nodes_total: 0
nodes_complete: 0
requirements_total: 37
requirements_mapped: 37
last_updated: 2026-05-11
---

# STATE: medical-guidelines-suite

## Project Reference

- **Core value**: 临床医生输入一次患者信息 → 自动跨指南检索 → 生成结构化循证推荐对比，10 例端到端 <10min
- **Current milestone**: v3.1 async-pipeline（异步 LLM + QMD 流水线 + 病种侧车元数据 + CLI 折叠）
- **Source of truth**: `docs/refactor_plan_2026-05-11.md`（grill-me 13 轮闭环）

## Current Position

- **Phase**: 1 — Async Retriever + KB Metadata Sidecar
- **Plan**: 未规划（等待 `/gsd-plan-phase 1`）
- **Status**: Not started (planning)
- **Progress**: `[░░░░░░░░░░] 0/4 phases · 0/37 requirements delivered`

## Performance Metrics

| Metric | Baseline (v3.0) | Target (v3.1) |
|--------|-----------------|---------------|
| 10 例端到端耗时 | ~60 min | <10 min |
| QMD 预检索（39 次查询） | ~3 min 串行 | ~30-60s 并发 8 |
| LLM 推理（10 例 × 5 org） | ~50 min 人工 | ~3-5 min vLLM 并发 5 |
| JSON 解析错误率 | ~0.5 次/批 | 0（schema strict） |
| 结直肠癌病种错配 chunk | ~30% | <5% |

## Accumulated Context

### Decisions (from PROJECT.md Key Decisions)

- LLM 推理栈：vLLM + Qwen3.5-35B-A3B（内网共享 LAN）
- 并发预算：LLM 5 路 / QMD 8 路 / patients 5 路
- `evidence_level` 完全枚举（strict）以根除引号转义 bug
- per-patient shard 为 canonical，`rag_results.json` 退为派生
- 病种侧车 JSON + synonym_map.yaml（QMD 不支持索引层 metadata filter）
- 失败语义：部分容错（`_failed/` + 退出码 1）
- Phase 4 一次性删除 batch 概念（Phase 3 stabilize 一周后）

### Todos

- [ ] `/gsd-plan-phase 1` — 拆解 Phase 1 至 plans

### Blockers

无。

## Session Continuity

- **上次会话**：2026-05-11 PROJECT.md + REQUIREMENTS.md 初始化（brownfield）
- **本次会话**：2026-05-11 ROADMAP.md 创建（4 phases，37 requirements 全覆盖）
- **下次会话入口**：`/gsd-plan-phase 1` 启动 Phase 1 规划

---
*State initialized: 2026-05-11 after roadmap creation*
