---
gsd_state_version: 1.0
milestone: v3.1
milestone_name: milestone
status: phase-complete
last_updated: "2026-05-12T09:03:40.256Z"
progress:
  total_phases: 4
  completed_phases: 2
  total_plans: 5
  completed_plans: 5
  percent: 100
---

# STATE: medical-guidelines-suite

## Project Reference

- **Core value**: 临床医生输入一次患者信息 → 自动跨指南检索 → 生成结构化循证推荐对比，10 例端到端 <10min
- **Current milestone**: v3.1 async-pipeline（异步 LLM + QMD 流水线 + 病种侧车元数据 + CLI 折叠）
- **Source of truth**: `docs/refactor_plan_2026-05-11.md`（grill-me 13 轮闭环）

## Current Position

- **Phase**: 2 — LLM Client + Schema + Unit Tests ✅
- **Status**: Phase 2 complete (all 2 plans executed, 247 tests passing)
- **Progress**: `[█████░░░░░] 2/4 phases · 5/5 plans · 17/37 requirements delivered`

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
- [x] Plan 02-01: AsyncLLMClient + PATIENT_RECOMMENDATION_SCHEMA + 三类重试 + 18 unit tests — ✅
- [x] Plan 02-02: LLMProfile.from_env + env/yaml 三级优先级 + config/llm_profiles.yaml + 11 unit tests — ✅

### Blockers

无。

### Phase 2 交付物

- `scripts/llm_client.py`（~340 LOC）— AsyncLLMClient + LLMProfile + PATIENT_RECOMMENDATION_SCHEMA + 三类重试 + from_env
- `config/llm_profiles.yaml` — qwen3-vllm-lan + deepseek-cloud 两 profile
- `tests/test_llm_client.py`（18 cases）+ `tests/test_llm_profile.py`（11 cases）
- 247 tests passing, 0 failures
- Requirements delivered: LLM-01..05 + CFG-01/02 (7/7)

## Session Continuity

- **本次会话**：2026-05-12 Phase 2 全部 2 plans 执行完成（247 tests, 零回归）
- **下次会话入口**：Phase 3 规划 `/gsd-plan-phase 3` 或人工端到端验证

---
*Phase 2 completed: 2026-05-12*
