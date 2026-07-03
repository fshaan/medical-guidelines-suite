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

## 2026-05 | LLM 推理栈：内网 vLLM + Qwen3.6-35B-A3B（非托管 API）

主路径用内网共享 vLLM 服务器（LAN/VPN，1-3s 延迟，timeout 180s，并发 5），托管 API（DeepSeek/OpenAI）仅作 fallback profile。  
**Why**：数据合规要求患者信息不出网；vLLM 0.5+ 原生支持 `response_format={"type":"json_schema","strict":true}`，在 sampling 阶段就拒绝违反 schema 的输出，**从根本上消除 JSON 引号转义 bug**——这是托管 API（DeepSeek 只支持 `json_object`，schema 不 strict）做不到的。

## 2026-07-02 | 修正：spark 主机实际部署型号是 Qwen3.6-35B-A3B-NVFP4，非 Qwen3.5-35B-A3B

SSH 登录 spark（192.168.31.92）核实：容器 `qwen36` 跑的是 `vllm/vllm-openai:nightly-aarch64`，权重路径 `/models/nvidia/Qwen3.6-35B-A3B-NVFP4`，`--served-model-name qwen3.6-35b`（NVFP4 量化 + fp8 kv-cache + 投机解码），`/v1/models` 返回 id 与之一致。此前 Phase 2 review（`02-REVIEW.md` IN-03）已标记 "Qwen3.5-35B-A3B" 疑似笔误/占位符；本次为实地核验后的最终修正。  
**Why**：`config/llm_profiles.yaml` 的 `model` 字段会原样进入 API 请求体，vLLM OpenAI Server 按 `served-model-name` 精确匹配，值不对会直接 404。已同步修正 `config/llm_profiles.yaml`、`tests/test_llm_profile.py`、`.planning/STATE.md`、`.planning/PROJECT.md`；`docs/refactor_plan_2026-05-11.md` 与已归档的 Phase 1-3 `.planning/phases/**` 执行文档保持原样不改（历史存档，不是活文档）。

## 2026-05 | per-patient shard 为 canonical 输出

`Output/patients/<patient_id>.json` 是真相，`Output/rag_results.json` 退为派生 aggregate。失败患者进 `Output/_failed/<patient_id>.json`（含 error/stage/last_llm_output），不阻塞其他患者。  
**Why**：批处理"全或无"语义太脆弱——10 例里 1 例失败就要重跑整批。shard 化把失败隔离到单患者粒度，配合 `--resume`（shard 存在跳过 + `_failed/` 自动重试）实现幂等推进。退出码：全 PASS（含 partial）→ 0，任一失败 → 1。

## 2026-05 | KB metadata 存侧车 JSON（QMD 不支持索引层过滤）

`$KB_ROOT/.metadata/{chunks,org_disease_coverage}.json` + `synonym_map.yaml` 在 `cmd_index` 一次性产出，运行时只读。双层过滤：org 级前置（结直肠癌患者跳 ESMO/JGCA/CACA）+ chunk 级后置（按 `disease_tags` 过滤命中）。chunk_keys 为空时**保留**（保守兜底）。  
**Why**：QMD 索引层不支持 metadata filtering，事后过滤又只能作用到 org 级——这就是 v3.0 测试里结直肠癌患者命中胃癌 chunk（score 0.92）的根因。侧车 JSON + 应用层双层过滤是 QMD 限制下最可控的方案；词表归一化（"胃腺癌"→"gastric"）走 `synonym_map.yaml`，运维可扩展 `overrides.yaml` 而不动代码。

## 2026-05 | CLI 7 阶段折叠为 4

新版命令：`parse / run / validate / generate / index`（5 个），旧的 `split / orchestrate / verify-batch / merge` 在 Phase 3 期间 hidden（`--help` 不显示但仍可调用），Phase 3 ship 后 stabilize 一周再于 Phase 4 一次性删除。  
**Why**：当前 7 阶段是 v2.x "确定性 orchestrate" 设计——但那是建立在"LLM 必须人工执行"前提下的。引入 async LLM 客户端后，orchestrate 的 batch prompt 中间产物 + verify-batch 的执行证据校验全部不再需要。保留双写会导致 drift；一次性删除前先 stabilize 一周保留回退能力。

## 2026-05 | WR-04 短词误命中：先治理命名约定，Phase 2 再做代码级 stop-token

Phase 1 review 发现 `infer_chunk_tags("pancreas-research", ...)` 会把研究文档错误归入 pancreatic 病种 tags——根因是 `_SYNONYM_SEED` 含歧义器官 alias（`pancreas`、`liver`、`stomach`、`肝`、`胃`、`肺`），与非疾病 token 在 stem 中混排时 token split 命中。**Phase 1 收尾不动 seed**：通过 Conventions.md 增补 KB 命名约定（建议 `<ORG>_<Disease>_<Year>.md` 形式、研究文档独立子目录或 `_meta_`/`_protocol_` 前缀）+ `infer_chunk_tags` docstring 警示 + 一个 documenting 边界测试（`pancreas-research` 仍归 pancreatic）。  
**Why**：直接移除歧义器官 alias 会破坏正向命中（`liver-cancer-2026.md` 也命中 `liver`）；引入 stop-token 列表要枚举 `research/study/protocol/version/...` 数十词，外溢到 seed 数据治理范围。Phase 2 计划引入 alias 长度分层（短 alias 仅精确匹配，长 alias 参与 token split）+ stop-token 黑名单做代码级防御；当前 Phase 1 用 documenting 测试锁死边界，未来代码引入过滤时该测试反转即可发现 regression。

## 2026-05 | D-01 释义改动：async/sync 物理共存（非 thin shim）

Phase 1 把 `AsyncQMDService` 作为新真相，但同步 `QMDService` **保留独立 `requests`-based 实现路径**，不走 `asyncio.run(self._async.method())` 包装。D-01 字面"单一异步真相"重新解释为"调用方一律用 async；sync 类仅为 BC stub"。  
**Why**：`tests/test_retriever.py` 与 `tests/test_qmd_integration.py` patch 的是 `scripts.retriever.requests.post`；若 sync 类内部改走异步包装 + `httpx`，patch 失效 → 测试红，违反 RTR-04 "现有测试不修改通过" 约束。代价是 ~80 LOC 重复，换回 173 测试基线零回归。这条改动**没有更新到 CONTEXT.md D-01 字面**——以 `01-01-PLAN.md` 的 `<critical_conflict_resolution>` 块为准。

## 2026-05 | D-05 异常分级：LLMFailure → KeyError/ValueError → CancelledError(raise)

pipeline.py `_run_one_patient` 中异常处理分三级：`LLMFailure` 写 `_failed/` 并继续其他患者；`KeyError/ValueError` 为代码 bug 写 `_failed/` 并 log stacktrace；`CancelledError` 直接 raise 不捕获（让 TaskGroup 取消所有 sibling）。禁止裸 `except Exception`。  
**Why**：裸 except 会吞掉 `CancelledError`（用户 Ctrl-C），导致 pipeline 拒绝退出。分级后 `CancelledError` 正确传播到 event loop，`try/finally` 中 `_merge_rag_results` 保证已成功患者的结果不丢。

## 2026-05 | D-07 try/finally 强制写 rag_results.json（Ctrl-C 安全）

`run_pipeline` 在 `try/finally` 中调用 `_merge_rag_results`，保证即使 Ctrl-C 中断也能把已完成的 patients/ 汇总为 `rag_results.json`。  
**Why**：10 例患者跑到第 8 例被 Ctrl-C，前 7 例结果不应丢。`finally` 块确保 aggregate 文件总是最新的。

## 2026-05 | D-08 _scan_resume 两段扫描 + _failed unlink

`--resume` 模式先扫 `patients/*.json`（跳过已完成），再扫 `_failed/*.json`（unlink 旧失败文件后重跑）。  
**Why**：单纯跳 `_failed/` 会让失败患者永远卡住。两段扫描让 `_failed/` 患者自动重试（前提是失败原因已修复），`patients/` 患者不重跑（幂等保证）。

## 2026-05 | D-10 run 子命令 7 个参数

`batch_pipeline.py run` 接受 `--patients / --output-dir / --llm-profile / --concurrency-patients / --concurrency-qmd / --resume / --kb-root` 共 7 个参数，全部有 env var 默认值（`LLM_PROFILE / PIPELINE_CONCURRENCY_PATIENTS / PIPELINE_CONCURRENCY_QMD / MEDICAL_GUIDELINES_DIR`），CLI flag 优先级 > env。  
**Why**：env var 支持无参数一键运行（CI/production），CLI flag 支持临时覆盖（调试）。

## 2026-05 | D-11 validate/generate --patients-dir 互斥组 + --input deprecated

`validate` 和 `generate` 新增 `--patients-dir`（v3.1 主路径）与 `--input`（deprecated）互斥组，只允许选一个。选 `--input` 时 emit DeprecationWarning。  
**Why**：v3.0 的 `--input Output/rag_results.json` 单文件模式在 per-patient shard 架构下语义不清（聚合文件是派生的）。`--patients-dir Output/patients/` 直接读 shard，是 v3.1 的 canonical 入口。保留 `--input` 是 stabilize 期 BC。

## 2026-05 | D-13 4 旧子命令 hidden via argparse.SUPPRESS

`split / orchestrate / merge / verify-batch` 四个 subparser 的 `help=argparse.SUPPRESS`，`--help` 不显示但仍可调用。  
**Why**：Phase 3 stabilize 期间旧调用方可能仍在用 hidden 子命令。Phase 4 stabilize 一周后一次性删除源码和对应测试。SUPPRESS 而非删除保留了回退能力。

## 2026-05 | D-14 build_patient_prompt 迁移自 generate_batch_prompt 单患者形态

`pipeline.py:build_patient_prompt` 从 `batch_pipeline.py:generate_batch_prompt` 提取单患者逻辑，不再生成 batch prompt，而是直接返回单患者完整上下文字符串（包含 QMD 预检索结果 + clinical question）。  
**Why**：v3.0 的 batch prompt 是给人工 LLM 阅读的中间产物；v3.1 的 `run_pipeline` 直接把 prompt 传入 `AsyncLLMClient.complete_structured`，不需要 batch 文件落盘。提取单患者形态消除 batch 概念依赖。

## 2026-07-02 | Phase 3 E2E 验收暴露的检索链路多层 bug 修复（3 轮 codex 对抗式审查）

真实 E2E 跑 10 例患者（vLLM@spark + 本机 QMD）连续暴露一串此前单元测试没覆盖到的 bug，逐层定位、逐轮请 codex 做对抗式审查后修复。按发现顺序：

1. **org 过滤大小写不一致（P0）**：`_hit_org()` 按测试契约返回大写 `"NCCN"`，但 `build_sidecar()` 写入 `org_disease_coverage.json` 的 key 是小写 `"nccn"`——`_run_one_patient` 比较前不归一化，导致不管 canonical 是不是 None，QMD 检索结果**永远被过滤成空**。加 `.lower()` 归一化。
2. **异常静默吞掉（P0）**：`run_pipeline` 的 `asyncio.gather(..., return_exceptions=True)` 返回值没接收，Stage 3 QMD 抛出的 `httpx.RequestError` 不在 `_run_one_patient` 的任何 except 分支里，直接穿透被 gather 静默丢弃——患者既不进 `patients/` 也不进 `_failed/`，summary 全 0 但 exit code 0，看起来"什么都没发生"。新增 `QMDQueryError` + Stage 3 局部 catch + 顶层 `isinstance(r, Exception)` 安全网。
3. **QMD session 级并发限制（P0）**：真实 spark 复现——两个并发 `tools/call` 打到同一 MCP session，一个正常返回，另一个**永远收不到响应**（不是变慢，是卡死）。两个独立 `AsyncQMDService` 实例（各自独立 session/进程）并发完全没问题，证实是 session 级限制。加 `self._session_lock` 串行化同一 session 的 HTTP 往返，保留 `self._sem` 的准入控制语义。
4. **`_hit_org()` 不认真实 QMD 路径格式（P0）**：真实 `hit["path"]` 是裸 `"CACA/foo.md"`（无 `qmd://` 前缀、无 `/extracted/` 段），旧实现只认前两种格式，对真实数据恒返回 `""`——org 过滤再次清空所有结果。加第三段 fallback：≥2 段路径取首段。
5. **chunk 级过滤路径 key 不匹配（P0）**：`chunks.json` key 是 `qmd://{org}/{Path.name}`（下划线/空格/全角括号），真实 hit path 是 QMD 内部 slug（连字符）——两套"同一文件"的独立表示，精确 `dict.get(path)` 恒 miss，chunk 级病种过滤对真实数据完全形同虚设（这正是 milestone 最初要解决的"结直肠癌命中胃癌 chunk"的直接成因）。新增 `_canonical_filename()` 剥标点模糊匹配，key 带上 org 段防跨机构碰撞。
6. **CJK 文件名打标失效（P1）**：`_FILENAME_TOKEN_RE` 不切空格（ESMO/JGCA 空格分隔文件名全部 miss）+ 中文文件名（CSCO/CACA 占 67/97）病种词前后无分隔符 exact-token 恒 miss。前者加 `\s`，后者对"本身带疾病后缀（癌/瘤/cancer）"的 CJK alias 做子串匹配（裸器官名"胰腺/结肠"不参与，防"胰腺炎→胰腺癌"误标）。
7. **零检索证据静默幻觉风险（P0）**：即使 `hits=[]`，旧代码照常调 LLM——模型在无真实指南内容下仍生成引用 CSCO/NCCN 的 JSON（凭训练知识编造），`compute_citation_coverage` 对空 sources 返回 1.0 满分，被当 "ok" 写出。改为 LLM 调用前拦截，写独立 `status="no_evidence"` shard（不计入 failed、不影响 exit code——该病种在 KB 无指南是合法结果，不是 bug）。
8. **`max_tokens` 调低过头**：曾把默认值 65536 调到 8192（无实测依据），真实 10/10 患者全在 ~24000 字符处截断成非法 JSON。实测证据证明 8192 严重不够，改回 65536 + 加 `finish_reason=="length"` 截断检测标注。

**Why（共性根因）**：Phase 1-3 的单元测试 mock 了 QMD/LLM 的返回结构，但 mock 的 path 格式、chunks_meta key 格式、并发行为都跟真实部署不一致——三层过滤（org 级、chunk 级、零证据）对 mock 数据是 no-op，对真实数据全部失效，导致"单元测试全绿 + 真实 E2E 完全跑不出真实结果"。修复后新增的回归测试全部用真实 KB 文件名 / 真实 path 格式 / 真实并发复现场景。302 tests passing（+13 条针对本轮 bug 的回归测试）。

**未修复、已记录**：vLLM `--max-num-seqs 4` 限制下 pipeline 并发需 ≤2 才稳定（已在 `docs/phase3_e2e_acceptance.md` 标注）；`max_tokens` 尚未 profile 化（`LLMProfile.from_env` 不读 yaml/env 的 max_tokens，改默认值影响所有 profile，留给后续）。

## 2026-07-03 | max_tokens 超时根因三层定位 + Phase 3 E2E 验收通过（决定性测试 + codex 对抗审查）

中断前假设"max_tokens=65536 太大导致退化"。决定性对比测试（`scripts/dev/max_tokens_probe.py`，同 prompt 不同 max_tokens，3 轮）+ codex 对抗式审查纠正了认知，定位**三层相互独立的问题**，各自治本：

1. **退化（重复生成撞 max_tokens）**：随机触发，max_tokens 不决定是否退化、只影响爆炸半径（vLLM ~120 tok/s：8192→68s 快速失败 vs 65536→546s ReadTimeout）。27 hits 全文 prompt（11046 字）信息过载是诱因。胃癌 5/5 稳定退化，盲重试无效。
   - 治本：`_select_diverse_hits` 按 org 保底精简到 ≤15（控上下文规模）+ 退化感知三档降级链（strict→精简hits+frequency_penalty→json_object 降级）+ `DegenerationError` 独立失败语义（不伪装 partial，QG-02 诚实）+ 全局 finish_reason=length/重复度检测。
2. **strict 模式 string 字段"写不停"**：vLLM json_schema strict 下 recommendation（string，无强制闭合）持续生成到 max_tokens，finish=length 截断成非法 JSON。schema `maxLength` 在 xgrammar 后端**不强制闭合**（实测：简单 prompt 模型恰好写短"看起来生效"，复杂 prompt 仍写到 max_tokens；对比脚本 + E2E 双重验证）。这是中断前"截断 vs 退化"判断混淆的根源——2026-07-02 误判为"8192 截断"的，实际部分是 strict string 写不停。
   - 治本：`structured_mode` 改 `json_object`（模型自由闭合，实测 13-20s 合法输出 ~1500 token），prompt 加 JSON 结构描述（json_object 模式必需）。
3. **json_object 丢失 enum 强制**：模型输出 evidence_level 简写（"1A" 而非 "1A类"、"1类证据"）。
   - 治本：`_normalize_evidence_level` 应用层归一化（前缀/去空白匹配 + 兜底"不适用"），`_parse_and_validate` 在 jsonschema.validate 前调用。

**vLLM grammar 缓存坑**：vLLM 按 schema **name** 缓存编译后的 grammar，schema 内容变了（加 maxLength）但 name 不变会命中旧缓存。`_build_payload` 让 schema_name 带 schema 内容 hash 后缀（`patient_recommendation_<md5[:8]>`），schema 变则 name 变，强制重编译。

**max_tokens profile 化**（2026-07-02 留待项已落地）：`LLMProfile.from_env` 接入 `LLM_MAX_TOKENS` env > yaml `max_tokens` > 默认 8192 三级优先级。默认从 65536 降到 8192（正常输出 ~1500 token 有余量，退化 68s 可控）。

**结果**：E2E 10/10 OK，wall **205.9s**（<10min QG-01 达成，较 v3.0 的 ~60min 降 94%），36 条指南覆盖全部 5 组织，QG-01..05 全 PASS。配置：max_tokens 8192、timeout 90s、structured_mode json_object、concurrency-patients 2（vLLM `--max-num-seqs 4`）。
