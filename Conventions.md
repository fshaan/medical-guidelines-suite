# Conventions

## 目录结构

```
medical-guidelines-suite/
├── SKILL.md                    # Skill 定义（build + query + batch 三个触发词）
├── scripts/
│   ├── retriever.py            # QMD 服务封装（sync QMDService + async AsyncQMDService）
│   ├── llm_client.py           # AsyncLLMClient + PATIENT_RECOMMENDATION_SCHEMA + 三类重试
│   ├── pipeline.py             # async per-pipeline 编排器（run_pipeline + 12 helpers）
│   ├── kb_metadata.py          # KB 病种元数据 + synonym_map + 双层过滤
│   ├── extract_all.py          # Legacy batch extraction (Docling → extracted/*.md)
│   ├── extract_guidelines.py   # v2 extraction pipeline (MinerU + Docling + VLM)
│   ├── extraction/             # Extraction modules (pdf/docx/postprocess/vlm_describer)
│   ├── batch_pipeline.py       # CLI 入口（parse/run/validate/generate/index + 4 hidden legacy）
│   └── dev/                    # 开发期验证脚本（不入正式 CLI；如 e2e_real_retrieval.py 离线 E2E）
├── config/
│   └── llm_profiles.yaml       # LLM profile 定义（qwen3-vllm-lan, deepseek-cloud）
├── templates/                  # 报告 Markdown 模板
├── examples/                   # 示例输入/输出
├── docs/                       # 设计文档
├── tests/                      # 测试套件（302 tests）
├── references/                 # 参考资料
├── Input/                      # 患者 Excel 输入（不入 git）
├── Output/                     # 生成报告输出（不入 git）
├── logs/                       # 运行日志（不入 git）
└── _meta.json                  # skill 元数据
```

## 约定

- `Input/`、`Output/`、`logs/` 均列入 `.gitignore`，不存储患者数据
- 知识库目录（`MEDICAL_GUIDELINES_DIR` 指向）完全在仓库外，不做任何引用
- `extracted/*.md` 是 Docling 从 PDF 提取的中间产物，由脚本生成，不手动编辑
- Skill 的三个入口词（build / query / batch）在 `SKILL.md` 头部声明，不在脚本里硬编码
- `config/llm_profiles.yaml` 定义 LLM 推理 profile，env var 优先级 > yaml
- `scripts/pipeline.py` 是 v3.1 async 编排器，所有异步调用方通过 `run_pipeline(args)` 入口
- `Output/patients/<pid>.json` 是 per-patient canonical shard，`Output/rag_results.json` 是其派生 aggregate
- `Output/_failed/<pid>.json` 存储失败患者（error + stage + last_llm_output），`--resume` 时自动重试
- 测试命名：`test_<module>.py` 对应 `scripts/<module>.py`；E2E smoke 在 `tests/test_pipeline_e2e_smoke.py`
- `batch_pipeline.py` 的 4 个旧子命令（split/orchestrate/merge/verify-batch）已 hidden（`help=argparse.SUPPRESS`），Phase 4 删除

## KB 目录与文件命名（v3.1 sidecar 约定）

`scripts/kb_metadata.infer_chunk_tags` 通过文件名 token 切分（连字符/下划线/点）匹配
`_SYNONYM_SEED` 词表来推断 `chunks[*].disease_tags`。**KB 目录与 `extracted/*.md`
文件名应避免在 stem 中混入非疾病 token**（如 `pancreas-research.md`、
`liver-cohort-2026.md`、`肝-科研笔记.md`），否则歧义器官 alias（`pancreas`、`liver`、
`stomach`、`pulmonary`、`cervix`、`肝`、`胃`、`肺`）会让该文件被错误归入 pancreatic /
hepatic / gastric / lung / cervical 病种 tags。

推荐命名形式：`<ORG>_<DiseaseName>_<Year>.md` 或 `<DiseaseName>-<Year>-v<N>.md`，
即疾病 token 出现在 stem 中**仅当**该文件确实是疾病专属指南。研究、综述、议程类
文档建议放进独立子目录（如 `research/`、`drafts/`）或加 `_meta_` / `_protocol_`
前缀以避免被 `extract-all` / `cmd_index` 当作指南扫描。

Phase 2 计划引入 stop-token 过滤减少误命中；当前 Phase 1 依赖命名约定。

## v3.1 Async Pipeline 约定

- `AsyncQMDService` 和 `AsyncLLMClient` 共享 `httpx.AsyncClient`（由 `run_pipeline` 创建并注入）
- 患者并发受 `Semaphore(concurrency_patients)` 控制，默认 5
- QMD 并发受 `Semaphore(concurrency_qmd)` 控制，默认 8
- QMD 单 session 不支持并发 tools/call（真实 spark 实测：同 session 第二个请求永久卡死）→ `AsyncQMDService._session_lock` 串行化 HTTP 往返，semaphore 仅做准入控制
- 每个患者的 pipeline 流程：load metadata → QMD 检索 → 病种双层过滤（org 级 + chunk 级）→ 零证据拦截 → build prompt → LLM inference → validate → write shard
- `PATIENT_RECOMMENDATION_SCHEMA` 的 `evidence_level` 为 27 变体完全枚举（strict），根除 JSON 引号转义 bug
- 异常分级（D-05）：LLMFailure → _failed/，KeyError/ValueError → _failed/ + log，CancelledError → raise
- shard status 三态：`ok`（正常）/ `partial`（citation_coverage<0.5 被接受）/ `no_evidence`（检索过滤后无证据，合法结果不计入 failed、不影响 exit code——该病种在 KB 无指南不是 bug）/ 失败进 `_failed/`
- 真实 hit path 与 chunks.json key 跨表示形式（QMD slug vs Path.name）经 `_canonical_filename()` 剥标点模糊匹配对齐
- Python 3.9 兼容：不使用 `match/case`、`X | Y` type union、`StrEnum` 等 3.10+ 语法
