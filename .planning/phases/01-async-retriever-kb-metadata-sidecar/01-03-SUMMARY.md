---
phase: 01-async-retriever-kb-metadata-sidecar
plan: 03
subsystem: batch_pipeline, metadata
tags: [cmd_index, build_sidecar, kb_metadata, sidecar, QMD]

requires:
  - phase: 01-async-retriever-kb-metadata-sidecar/01-02
    provides: scripts/kb_metadata.py with build_sidecar + 6 public functions
  - phase: 01-async-retriever-kb-metadata-sidecar/01-01
    provides: requirements.txt with pyyaml, httpx, pytest-asyncio

provides:
  - cmd_index 自动产出 .metadata/chunks.json + org_disease_coverage.json + synonym_map.yaml
  - build_sidecar try/except warn-only 策略（侧车失败不阻断 QMD 索引）

affects: [cmd_index, Phase 3 pipeline 病种过滤]

tech-stack:
  added: []
  patterns: [warn-only sidecar generation in cmd_index]

key-files:
  created: []
  modified:
    - scripts/batch_pipeline.py

key-decisions:
  - "build_sidecar 失败策略：warn-only 不 abort，与 Metal 134 容忍精神对齐"
  - "顶部 import from scripts import kb_metadata（无循环依赖风险）"

patterns-established:
  - "侧车元数据生成：cmd_index 在 QMD 索引完成后、最终 print 前插入 build_sidecar 调用"

requirements-completed: [KBM-01, KBM-02]

duration: 3min
completed: 2026-05-12
---

# Phase 1 Plan 03: cmd_index 侧车集成 Summary

**在 cmd_index 末尾接入 kb_metadata.build_sidecar，一次 index 命令同时产出三个 .metadata/ 侧车文件（chunks.json / org_disease_coverage.json / synonym_map.yaml）**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-05-12
- **Completed:** 2026-05-12
- **Tasks:** 1（Task 2 为 human checkpoint，由 orchestrator 后续处理）
- **Files modified:** 1

## Accomplishments
- cmd_index 扩展：index 命令运行后自动生成 .metadata/ 三个侧车文件
- warn-only 策略：build_sidecar 异常仅输出 stderr 警告，不影响 QMD 索引已完成状态
- diff 严格控制在 16 行（1 行 import + 13 行函数体插入）
- 198 测试全部通过，零回归

## Task Commits

1. **Task 1: cmd_index 末尾接入 build_sidecar 调用** - `2d883ea` (feat)

## Files Created/Modified
- `scripts/batch_pipeline.py` — 顶部增加 `from scripts import kb_metadata`；cmd_index 函数末尾插入 build_sidecar 调用 + try/except 包裹

## Decisions Made
- **顶部 import vs lazy import**：选择顶部 import（`from scripts import kb_metadata`），因为 kb_metadata.py 仅依赖 stdlib + pyyaml，无循环依赖风险，且 `pytest tests/ -q` 零回归验证通过
- **warn-only 失败策略**：与 cmd_index 现有的 Metal exit-code 134 容忍策略对齐；侧车可下次 re-run 补齐

## Deviations from Plan

None — plan 按预期执行。

## Issues Encountered

None

## Known Stubs

无 — build_sidecar 已完整实现并集成。

## Next Phase Readiness
- Phase 3 可通过 `kb_metadata.load_synonym_map(kb_root)` + `json.load(chunks.json)` + `json.load(org_disease_coverage.json)` 读取侧车数据
- 调用 `filter_orgs_by_disease` 决定查询哪些 org，调用 `filter_chunks_by_disease` 在 dedupe 后过滤 chunks
- Task 2 human checkpoint 待 orchestrator 统一验证（真实 KB 端到端 index 运行）

---
*Phase: 01-async-retriever-kb-metadata-sidecar*
*Completed: 2026-05-12*
