---
gsd_state_version: 1.0
milestone: v3.1
milestone_name: milestone
status: phase-complete
last_updated: "2026-05-12T00:15:00Z"
progress:
  total_phases: 4
  completed_phases: 1
  total_plans: 3
  completed_plans: 3
  percent: 100
---

# STATE: medical-guidelines-suite

## Project Reference

- **Core value**: 临床医生输入一次患者信息 → 自动跨指南检索 → 生成结构化循证推荐对比，10 例端到端 <10min
- **Current milestone**: v3.1 async-pipeline（异步 LLM + QMD 流水线 + 病种侧车元数据 + CLI 折叠）
- **Source of truth**: `docs/refactor_plan_2026-05-11.md`（grill-me 13 轮闭环）

## Current Position

- **Phase**: 1 — Async Retriever + KB Metadata Sidecar ✅
- **Status**: Phase 1 complete (all 3 plans executed)
- **Progress**: `[██░░░░░░░░] 1/4 phases · 3/3 plans in Phase 1 · 10/37 requirements delivered`

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

- [x] Plan 01-01: AsyncQMDService + requirements.txt — ✅
- [x] Plan 01-02: kb_metadata.py + 22 tests — ✅
- [x] Plan 01-03: cmd_index 接入 build_sidecar — ✅

### Blockers

无。

## Session Continuity

- **本次会话**：2026-05-12 Phase 1 全部 3 plans 执行完成（198 tests, 零回归）
- **下次会话入口**：Phase 2 规划 `/gsd-plan-phase 2` 或人工端到端验证 `batch_pipeline.py index --kb-root $MEDICAL_GUIDELINES_DIR`

---
*Phase 1 completed: 2026-05-12*
