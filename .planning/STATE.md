---
gsd_state_version: 1.0
milestone: v3.1
milestone_name: milestone
status: in-progress
last_updated: "2026-05-12T10:22:00.000Z"
progress:
  total_phases: 4
  completed_phases: 2
  total_plans: 8
  completed_plans: 7
  percent: 88
---

# STATE: medical-guidelines-suite

## Project Reference

- **Core value**: 临床医生输入一次患者信息 → 自动跨指南检索 → 生成结构化循证推荐对比，10 例端到端 <10min
- **Current milestone**: v3.1 async-pipeline（异步 LLM + QMD 流水线 + 病种侧车元数据 + CLI 折叠）
- **Source of truth**: `docs/refactor_plan_2026-05-11.md`（grill-me 13 轮闭环）

## Current Position

- **Phase**: 3 — Pipeline + run Subcommand + Interface Extension
- **Status**: Phase 3 executing (Plan 01+02 complete, Plan 03 next)
- **Progress**: `[████████░░] 2/4 phases · 7/8 plans · 29/37 requirements delivered`

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
- D-05 异常分级：LLMFailure → KeyError/ValueError → CancelledError(raise)，禁止 except Exception
- D-07 try/finally 强制写 rag_results.json（Ctrl-C 安全）
- D-08 _scan_resume 两段扫描 + _failed unlink
- D-14 build_patient_prompt 迁移自 generate_batch_prompt 单患者形态
- D-10 run 子命令 7 个参数（patients/output-dir/llm-profile/concurrency-patients/concurrency-qmd/resume/kb-root）
- D-11 validate/generate --patients-dir 互斥组 + --input deprecated
- D-13 4 旧子命令 hidden via argparse.SUPPRESS
- Python 3.9: argparse.SUPPRESS 在 subparser 中只隐藏描述文本，名字仍在 {choices} 中

### Todos

- [x] Plan 01-01: AsyncQMDService + requirements.txt — ✅
- [x] Plan 01-02: kb_metadata.py + 22 tests — ✅
- [x] Plan 01-03: cmd_index 接入 build_sidecar — ✅
- [x] Plan 02-01: AsyncLLMClient + PATIENT_RECOMMENDATION_SCHEMA + 三类重试 + 18 unit tests — ✅
- [x] Plan 02-02: LLMProfile.from_env + env/yaml 三级优先级 + config/llm_profiles.yaml + 11 unit tests — ✅
- [x] Plan 03-01: pipeline.py 核心 (run_pipeline + _run_one_patient + helpers) — ✅ 275 tests
- [x] Plan 03-02: batch_pipeline.py CLI 改造 (run 子命令 + hidden + --patients-dir) — ✅ 286 tests
- [ ] Plan 03-03: E2E smoke + 真实环境验收清单

### Blockers

无。

### Phase 3 Plan 02 交付物

- `scripts/batch_pipeline.py` — run 子命令 + 4 hidden + 2 互斥组 + dispatch 分支 + cmd_validate/generate 分支 + 2 新私有函数
- `tests/test_cli_subcommands.py`（11 cases）
- 286 tests passing, 0 failures
- Requirements delivered: CLI-01..05 + CFG-03 (6/18 Phase 3 reqs)

## Session Continuity

- **本次会话**：2026-05-12 Phase 3 执行中
- **当前状态**：Phase 3 Plan 01+02 完成（286 tests），Plan 03 待执行（E2E 验收需真实环境）

---
*Phase 3 Plan 02 completed: 2026-05-12*
