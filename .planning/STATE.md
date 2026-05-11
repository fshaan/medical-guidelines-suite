---
gsd_state_version: 1.0
milestone: v3.1
milestone_name: milestone
status: executing
last_updated: "2026-05-11T22:41:44Z"
progress:
  total_phases: 4
  completed_phases: 0
  total_plans: 3
  completed_plans: 1
  percent: 33
---

# STATE: medical-guidelines-suite

## Project Reference

- **Core value**: 临床医生输入一次患者信息 → 自动跨指南检索 → 生成结构化循证推荐对比，10 例端到端 <10min
- **Current milestone**: v3.1 async-pipeline（异步 LLM + QMD 流水线 + 病种侧车元数据 + CLI 折叠）
- **Source of truth**: `docs/refactor_plan_2026-05-11.md`（grill-me 13 轮闭环）

## Current Position

- **Phase**: 1 — Async Retriever + KB Metadata Sidecar
- **Plan**: 01-01 ✅ (Async Retriever + KB Metadata Sidecar)
- **Status**: Plan 01 complete, continuing to Plan 02
- **Progress**: `[░░░░░░░░░░] 0/4 phases · 1/3 plans in Phase 1 · 0/37 requirements delivered`

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
- D-01 释义：AsyncQMDService 独立 httpx 路径，同步 QMDService 保留 requests，调用方层面 async-only
- D-03 默认 Semaphore(8)，注入式 semaphore 参数可覆盖
- D-04 session retry 仅一次：400或缺header触发re-initialize+重发

### Todos

- [x] Plan 01-01: Async Retriever + 依赖管理 — ✅ 完成
- [ ] Plan 01-02: KB Metadata Sidecar
- [ ] Plan 01-03: CLI 折叠

### Blockers

无。

## Session Continuity

- **上次会话**：2026-05-11 PROJECT.md + REQUIREMENTS.md 初始化（brownfield）
- **本次会话**：2026-05-11 Plan 01-01 执行完成（AsyncQMDService + 7 tests）
- **下次会话入口**：Plan 01-02（KB Metadata Sidecar）或 `/gsd-execute-phase 1`

---
*State initialized: 2026-05-11 after roadmap creation*
