# Phase 3: Pipeline + run Subcommand + Interface Extension - Discussion Log

**Mode:** `--auto --chain`
**Date:** 2026-05-12
**Auto-mode notice:** No interactive Q&A — Claude selected the recommended option for each gray area based on refactor_plan §四.3 + §五 and Phase 1-2 已落地契约. Each selection logged below for audit.

## Gray Areas Auto-Selected

### Area 1: Pipeline orchestration primitive

Q: 顶层并发原语用什么？ROADMAP 写的 "TaskGroup" 在 Python 3.9.6 不可用。

Options considered:
- (a) `asyncio.gather(*, return_exceptions=True)` ✅ **selected**
- (b) `taskgroup` backport package
- (c) 自实现 task-group-like wrapper

Selected (a). Reason: Phase 1 CLAUDE.md / CONTEXT 明确禁 TaskGroup，不引新依赖。释义 ROADMAP "TaskGroup" 为「顶层并发原语」非 API 绑定。→ D-01

### Area 2: httpx.AsyncClient lifecycle

Q: QMD + LLM 共用一个 client 还是各自创建？

Options:
- (a) 顶层单实例注入 ✅ **selected**
- (b) 两个独立 client
- (c) global client

Selected (a). Reason: Phase 1 D-03 + Phase 2 D-02 都是「注入式」，复用顶层连接池。→ D-03

### Area 3: 单患者异常分级

Q: stage 异常分多细？

Options:
- (a) 三段（transport / schema / build）+ 兜底 ✅ **selected**
- (b) 单段（任何异常都 _failed）
- (c) 按 Phase 2 LLMFailure.stage 透传 + build 阶段单独

Selected (a)（= c 的细化）。Reason: refactor_plan §四.3 明确 `error/stage/last_llm_output` 三字段；planner / runtime debug 都靠 stage 归因。→ D-05

### Area 4: 退出码语义

Q: 部分失败时 exit code = 0 还是 1？

Options:
- (a) 全 PASS（含 partial）→ 0，任一 _failed → 1 ✅ **selected**
- (b) 全 PASS（无 partial）→ 0
- (c) 永远 0（用 stdout 报告失败）

Selected (a). Reason: PIP-05 字面要求；与 CI / cron 集成预期对齐。→ D-06

### Area 5: rag_results.json 派生时机

Q: run 末尾合并 vs 每 patient 完成就增量更新？

Options:
- (a) run 末尾一次性合并 ✅ **selected**
- (b) 增量写
- (c) 不生成（让用户跑 merge 子命令）

Selected (a). Reason: PIP-06 要求「run 末尾合并」；resume 重生成保证最终一致；增量写无并发收益（asyncio 单进程）。→ D-07

### Area 6: Resume 扫描双队列

Q: resume 时 `_failed/` 中的 patient 怎么处理？

Options:
- (a) 自动重试 + 删旧 _failed 文件 ✅ **selected**
- (b) 跳过（保持 _failed 状态）
- (c) 提示用户选

Selected (a). Reason: PIP-04 字面「`_failed/` 中的患者自动重试」；vLLM 临时挂掉是最常见失败模式，自动重试匹配用户直觉。→ D-08

### Area 7: 写 shard 的原子性

Q: 中断时半写文件如何避免？

Options:
- (a) tmp + rename（POSIX 原子） ✅ **selected**
- (b) 文件锁
- (c) 后置完整性检查

Selected (a). Reason: Phase 1 `kb_metadata.build_sidecar` 已用此模式；asyncio 单进程内不需要 inter-process lock。→ D-09

### Area 8: run 子命令 flag 集合

Q: `--llm-profile` 默认值从哪来？

Options:
- (a) env LLM_PROFILE > 默认 "qwen3-vllm-lan" ✅ **selected**
- (b) 必填
- (c) 始终默认 "qwen3-vllm-lan"

Selected (a). Reason: CFG-01/CFG-03 env 优先级；用户单机切换 profile 无需改 CLI。→ D-10

### Area 9: validate --patients-dir vs --input

Q: 两个互斥 vs 共存？

Options:
- (a) 互斥（mutually_exclusive_group），保留 --input 兼容 + DeprecationWarning ✅ **selected**
- (b) 删 --input
- (c) 同时支持（取并集）

Selected (a). Reason: CLI-02 字面「保留 --input 兼容 deprecated」；删除留 Phase 4 一并清理。→ D-11

### Area 10: 旧子命令 hidden 实现

Q: hidden 用 argparse 哪种机制？

Options:
- (a) `help=argparse.SUPPRESS` ✅ **selected**
- (b) 移到 `--advanced` 子命令组
- (c) env flag toggle

Selected (a). Reason: 标准 Python argparse 做法；行为最小侵入，--help 不显示但仍可调用。→ D-13

### Area 11: build_patient_prompt 迁移粒度

Q: 改造现有 `generate_batch_prompt` 还是新写？

Options:
- (a) 迁移为单患者形态新函数，旧函数保留到 Phase 4 ✅ **selected**
- (b) 修改现有函数支持单/批两模式
- (c) 完全新写不参考旧实现

Selected (a). Reason: 隔离改动 blast radius；CLI-05 期间旧 batch path 仍可调用；Phase 4 一次性清理。→ D-14

### Area 12: citation_coverage 落点

Q: 在 llm_client.py 还是 pipeline.py？

Options:
- (a) pipeline.py 私有函数 ✅ **selected**
- (b) llm_client.py 公开函数
- (c) kb_metadata.py（病种相关）

Selected (a). Reason: Phase 2 D-15 已明确 citation_coverage 在调用方实现（依赖注入避免循环）；只 Phase 3 pipeline 使用。→ D-15

### Area 13: 进度报告

Q: 用 rich / tqdm 还是 plain print？

Options:
- (a) plain print，每 patient 一行 ✅ **selected**
- (b) rich progress bar
- (c) tqdm

Selected (a). Reason: 0 新依赖原则；非 TTY 兼容；CI 友好。→ D-16

### Area 14: 测试覆盖路径

Q: 集成测试用 mock vs 真实 vLLM/QMD？

Options:
- (a) mock httpx 双桩（QMD + LLM） ✅ **selected**
- (b) 真实 endpoint（CI 跑）
- (c) 部分 mock + 部分真实

Selected (a). Reason: QG-06 「pytest 全绿」要求 CI 不依赖外部服务；10 例 E2E 走人工验收（D-18）。→ D-17

### Area 15: 双 Semaphore 关系

Q: patient sem 与 QMD sem 嵌套还是独立？

Options:
- (a) 独立计数（不嵌套） ✅ **selected**
- (b) patient sem wraps QMD sem
- (c) 单一 sem 复用

Selected (a). Reason: refactor_plan §十.2 风险段明确「双 sem 独立避免相互饿死」；典型 patient=5 / qmd=8 / llm=5 不互相 cap。→ D-04

### Area 16: 顶层 client timeout 来源

Q: `httpx.AsyncClient(timeout=?)` 取值？

Options:
- (a) profile.timeout_s（env LLM_TIMEOUT 已覆盖） ✅ **selected**
- (b) 硬编码 180s
- (c) 单独 PIPELINE_TIMEOUT env

Selected (a). Reason: Phase 2 D-12 + fix CR-02 已把 timeout 链路打通；不引第二条配置入口。→ D-03

### Area 17: 10 例 E2E 是否进 CI

Q: ROADMAP success criterion #1（10例 <10min）放 pytest 还是手动？

Options:
- (a) 手动验收命令，PLAN.md 列清单 ✅ **selected**
- (b) pytest 标 `slow` mark
- (c) 不验收（信仰 unit test）

Selected (a). Reason: 需真实 vLLM + QMD + 4.3k 图片 KB，CI 环境无法满足；Phase 2 D-* 也是「人工验收不进 CI」。→ D-18

---

## Deferred Ideas Captured

详见 `03-CONTEXT.md` `<deferred>` 段。共 8 项 v3.2+ / Phase 4 待办。

## Canonical Refs Accumulated

详见 `03-CONTEXT.md` `<canonical_refs>` 段：refactor_plan §四.3/§五/§九/§十.2 + PROJECT/REQUIREMENTS/ROADMAP/STATE + Phase 1-2 CONTEXT + Phase 2 REVIEW + 7 个现有代码引用 + 3 个上游约束。

## Scope Creep Redirected

无。所有讨论严格落在 ROADMAP Phase 3 boundary 内。

---

*Auto-mode complete in single pass. 17 decisions locked, 0 user interactions, 0 scope creep events.*
