---
gsd_state_version: 1.0
milestone: v3.1
milestone_name: milestone
status: phase-planning
last_updated: "2026-05-12T03:00:00Z"
progress:
  total_phases: 4
  completed_phases: 1
  total_plans: 5
  completed_plans: 3
  percent: 60
---

# STATE: medical-guidelines-suite

## Project Reference

- **Core value**: 临床医生输入一次患者信息 → 自动跨指南检索 → 生成结构化循证推荐对比，10 例端到端 <10min
- **Current milestone**: v3.1 async-pipeline（异步 LLM + QMD 流水线 + 病种侧车元数据 + CLI 折叠）
- **Source of truth**: `docs/refactor_plan_2026-05-11.md`（grill-me 13 轮闭环）

## Current Position

- **Phase**: 2 — LLM Client + Schema + Unit Tests（plans created，等待执行）
- **Status**: Phase 1 ✅ complete · Phase 2 plans ready（02-01 + 02-02），通过 plan-checker（2 WARNING 已接受）
- **Progress**: `[███░░░░░░░] 1/4 phases · Phase 2 plans 2/2 created · 10/37 requirements delivered · Phase 2 将交付 7 reqs（LLM-01..05 + CFG-01/02）`

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
- [ ] Plan 02-01: AsyncLLMClient + PATIENT_RECOMMENDATION_SCHEMA + 三类重试 + unit tests（Wave 1，LLM-01/02/04/05）
- [ ] Plan 02-02: LLMProfile.from_env + env/yaml 三级优先级 + config/llm_profiles.yaml + unit tests（Wave 2，LLM-03/CFG-01/02）

### Blockers

无。Phase 2 ready for `/gsd-execute-phase 2`。

### Phase 2 关键决策锁定（planner 在 PLAN.md 中定稿）

- `jsonschema>=4.0,<5.0` 引入（DeepSeek json_object profile 客户端兜底）
- `complete_structured` 与 `complete_structured_with_feedback` 两方法分离
- 退避基数 `base=1.0s` × `2 ** attempt`（3 次重试总等待 7s ≤ 180s timeout）
- `_EVIDENCE_LEVEL_ENUM` 锁定 23 项（ESMO 扩展自 4 → 9，向后兼容 legacy 22 项子集）
- HTTP client 强制注入（与 AsyncQMDService 默认自建差异化）
- 02-02 wave=2 depends_on=["02-01"]（共享 scripts/llm_client.py 文件锁）

## Session Continuity

- **本次会话**：2026-05-12 Phase 2 plan-phase 完成（02-CONTEXT.md + 02-PATTERNS.md + 02-01-PLAN.md + 02-02-PLAN.md + ROADMAP 进度更新）
- **下次会话入口**：`/gsd-execute-phase 2` 启动 Wave 1（02-01 AsyncLLMClient 落地）

---
*Phase 1 completed: 2026-05-12 · Phase 2 plans ready: 2026-05-12*
