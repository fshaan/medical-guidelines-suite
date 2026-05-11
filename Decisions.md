# Decisions

## 2026-03 | 检索策略：QMD 混合检索而非纯向量搜索

采用 BM25 + 向量检索 + LLM reranking 三级管线（QMD），不用单一向量检索。  
**Why**：医学指南有大量术语、缩写、编号（如"RECIST 1.1"、"T3N2M0"），BM25 对精确词汇的召回率显著优于纯向量；混合后互补，LLM reranking 做最终精排。

## 2026-03 | 知识库注入：环境变量 + 本地目录，不入仓库

指南原文通过 `MEDICAL_GUIDELINES_DIR` 环境变量注入，不存入 git。  
**Why**：指南 PDF 有版权限制，且文件体积大；环境变量方案使同一份代码可在不同机器上指向不同知识库，方便多人协作。

## 2026-04 | 批处理设计：确定性 orchestrate + QMD 预检索

v2.2 引入确定性批处理模式（orchestrate），先对患者列表做 QMD 预检索再逐条生成报告。  
**Why**：纯 LLM 驱动的批处理在大批量患者时失控（幻觉、跳步）；确定性 orchestrate 保证每个患者都走相同的检索-生成管线，结果可复现。

## 2026-03 | 输出语言：硬约束简体中文

无论源指南语言（英文 NCCN、中文 CSCO），所有输出强制使用简体中文。  
**Why**：目标用户为中国临床医生，中文输出直接可用；英文输出需二次翻译增加摩擦。

## 2026-05 | v3.1 async-pipeline milestone 启动

把 10 例患者端到端从 ~60min 降到 <10min，沿 4 个 phase 推进：异步 retriever + KB 元数据 → vLLM 客户端 + strict schema → async 总编排器 + `run` 子命令 → 删除 batch 概念。  
**Why**：v3.0 测试报告（`Output/test_report_2026-05-11.md`）暴露两个瓶颈—— LLM 完全人工占 50min、QMD 串行 39 次查询占 3min；同时发现 JSON 引号未转义与结直肠癌错引胃癌 chunk 两个质量缺陷。grill-me 13 轮闭环后的完整方案在 `docs/refactor_plan_2026-05-11.md`，逐字段决策已落定。

## 2026-05 | LLM 推理栈：内网 vLLM + Qwen3.5-35B-A3B（非托管 API）

主路径用内网共享 vLLM 服务器（LAN/VPN，1-3s 延迟，timeout 180s，并发 5），托管 API（DeepSeek/OpenAI）仅作 fallback profile。  
**Why**：数据合规要求患者信息不出网；vLLM 0.5+ 原生支持 `response_format={"type":"json_schema","strict":true}`，在 sampling 阶段就拒绝违反 schema 的输出，**从根本上消除 JSON 引号转义 bug**——这是托管 API（DeepSeek 只支持 `json_object`，schema 不 strict）做不到的。

## 2026-05 | per-patient shard 为 canonical 输出

`Output/patients/<patient_id>.json` 是真相，`Output/rag_results.json` 退为派生 aggregate。失败患者进 `Output/_failed/<patient_id>.json`（含 error/stage/last_llm_output），不阻塞其他患者。  
**Why**：批处理"全或无"语义太脆弱——10 例里 1 例失败就要重跑整批。shard 化把失败隔离到单患者粒度，配合 `--resume`（shard 存在跳过 + `_failed/` 自动重试）实现幂等推进。退出码：全 PASS（含 partial）→ 0，任一失败 → 1。

## 2026-05 | KB metadata 存侧车 JSON（QMD 不支持索引层过滤）

`$KB_ROOT/.metadata/{chunks,org_disease_coverage}.json` + `synonym_map.yaml` 在 `cmd_index` 一次性产出，运行时只读。双层过滤：org 级前置（结直肠癌患者跳 ESMO/JGCA/CACA）+ chunk 级后置（按 `disease_tags` 过滤命中）。chunk_keys 为空时**保留**（保守兜底）。  
**Why**：QMD 索引层不支持 metadata filtering，事后过滤又只能作用到 org 级——这就是 v3.0 测试里结直肠癌患者命中胃癌 chunk（score 0.92）的根因。侧车 JSON + 应用层双层过滤是 QMD 限制下最可控的方案；词表归一化（"胃腺癌"→"gastric"）走 `synonym_map.yaml`，运维可扩展 `overrides.yaml` 而不动代码。

## 2026-05 | CLI 7 阶段折叠为 4

新版命令：`parse / run / validate / generate / index`（5 个），旧的 `split / orchestrate / verify-batch / merge` 在 Phase 3 期间 hidden（`--help` 不显示但仍可调用），Phase 3 ship 后 stabilize 一周再于 Phase 4 一次性删除。  
**Why**：当前 7 阶段是 v2.x "确定性 orchestrate" 设计——但那是建立在"LLM 必须人工执行"前提下的。引入 async LLM 客户端后，orchestrate 的 batch prompt 中间产物 + verify-batch 的执行证据校验全部不再需要。保留双写会导致 drift；一次性删除前先 stabilize 一周保留回退能力。

## 2026-05 | D-01 释义改动：async/sync 物理共存（非 thin shim）

Phase 1 把 `AsyncQMDService` 作为新真相，但同步 `QMDService` **保留独立 `requests`-based 实现路径**，不走 `asyncio.run(self._async.method())` 包装。D-01 字面"单一异步真相"重新解释为"调用方一律用 async；sync 类仅为 BC stub"。  
**Why**：`tests/test_retriever.py` 与 `tests/test_qmd_integration.py` patch 的是 `scripts.retriever.requests.post`；若 sync 类内部改走异步包装 + `httpx`，patch 失效 → 测试红，违反 RTR-04 "现有测试不修改通过" 约束。代价是 ~80 LOC 重复，换回 173 测试基线零回归。这条改动**没有更新到 CONTEXT.md D-01 字面**——以 `01-01-PLAN.md` 的 `<critical_conflict_resolution>` 块为准。
