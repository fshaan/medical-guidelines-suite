# Medical Guidelines Suite — 异步 LLM 批量流水线重构方案

> 起草日期：2026-05-11 | 关联测试报告：`Output/test_report_2026-05-11.md`
> 状态：决策已闭环，待 Phase 1 启动

---

## 一、Context

`medical-guidelines-suite` 当前运行 10 例患者端到端耗时约 1 小时，瓶颈分布如下：

| 阶段 | 耗时 | 根因 |
|------|------|------|
| LLM 执行 | ~50min | **完全人工**：用户把 batch_prompt.md 复制到 LLM 界面，再把 JSON 粘贴回 rag_batch_*.json — `batch_pipeline.py` 没有任何 LLM API 调用代码 |
| QMD 预检索 | ~3min | `cmd_orchestrate` (batch_pipeline.py:889-1032) 嵌套 for 循环，39 次查询**串行执行**，`retriever.py` 是同步 requests-based |
| 其他 (parse/merge/validate/generate) | <10s | 无问题 |

此外有两个质量缺陷：

1. **JSON 引号转义 bug**：LLM 自由生成 JSON 时 ASCII 双引号未转义导致 verify-batch FAIL（本次测试已发生 1 次需人工修复）
2. **病种错配**：结直肠癌患者的 ESMO/JGCA/CACA 查询返回胃癌 chunk（score 0.92），因为 QMD 索引无 metadata filtering，事后过滤仅作用于 org 级别

**目标**：10 例患者端到端从 1 小时降到 <10 分钟，并消除上述两个质量缺陷。

---

## 二、决策清单（已通过 grill-me 闭环）

| # | 决策项 | 选定方案 |
|---|--------|----------|
| 1 | LLM 推理栈 | **vLLM + Qwen3.5-35B-A3B** |
| 2 | 部署拓扑 | **内网共享服务器**（LAN/VPN，1-3s 延迟，timeout 180s） |
| 3 | 并发预算 | **5 路**（LLM_CONCURRENCY=5, --concurrency-patients=5） |
| 4 | Schema 严格度 | **`evidence_level` 完全枚举**，json_schema strict=true |
| 5 | 输出 canonical | **per-patient shard 是真相**，aggregate `rag_results.json` 是派生 |
| 6 | 病种匹配 | **词表驱动 synonym_map.yaml + 层级匹配**（"标签缺失则保留"为保守默认） |
| 7 | 失败语义 | **部分容错**：失败者进 `_failed/<id>.json`，整体退出码 1 |
| 8 | KB 元数据存储 | **侧车 JSON 文件** `$KB_ROOT/.metadata/{chunks,org_disease_coverage}.json` |
| 9 | --resume 粒度 | **shard 存在即跳过**；`_failed/` 中的患者自动重试 |
| 10 | 旧测试过渡 | **Phase 4 一次性删除 + 重写 per-patient 版** |
| 11 | Prompt 格式 | **system + user 双消息**，user 以 markdown 渲染患者数据与 chunks |
| 12 | citation 后验失败 | **带反馈重试一次**，仍 <0.5 则接受并标 `status: "partial"` |
| 13 | validate/generate CLI | **新增 `--patients-dir`，保留 `--input` 作为 deprecated 兼容** |

---

## 三、新架构

**流水线由 7 阶段折叠为 4 阶段**：

```
旧：parse → split → orchestrate → [人工 LLM] → verify-batch → merge → validate → generate
新：parse → run → validate → generate
```

`run` 是一个 async 总编排器（新增 `scripts/pipeline.py`），对每个患者并发执行子流水线：

```
patient
  → extract_features
  → build_queries
  → asyncio.gather(qmd.query × N)             # 单患者内查询并发
  → dedupe + org_prefilter + chunk_meta_filter # 双层病种过滤
  → build_patient_prompt（system + user 双消息）
  → llm.complete_structured(schema, strict)    # JSON Schema 强约束
  → 计算 citation_coverage
  → if <0.5: 带反馈再调一次
  → 写 Output/patients/<patient_id>.json
```

顶层 `asyncio.TaskGroup` + 患者级 `asyncio.Semaphore(5)`；全局 `asyncio.Semaphore(8)` 限 QMD 在飞请求；LLM 客户端独立 `asyncio.Semaphore(5)`。

**CLI 变化**：
- 保留：`parse / validate / generate / index`
- 新增：`run`（核心）
- 删除（Phase 4）：`split / orchestrate / verify-batch / merge`
- Phase 3 期间旧子命令 deregistered（仍可调用但 `--help` 不显示）

---

## 四、关键模块设计

### 1. `scripts/llm_client.py`（新增）— LLM 抽象层

```python
@dataclass(frozen=True)
class LLMProfile:
    name: str                  # 例 "qwen3-vllm-lan"
    base_url: str              # 例 "http://10.0.x.x:8000/v1"
    model: str                 # "Qwen3.5-35B-A3B"
    api_key_env: str = "LLM_API_KEY"
    max_tokens: int = 4096
    temperature: float = 0.1
    timeout_s: int = 180        # LAN 延迟下的安全值
    structured_mode: str = "json_schema"   # vLLM ≥0.5 原生支持
    concurrency: int = 5         # 共享服务器谨慎默认

class AsyncLLMClient:
    def __init__(self, profile, http: httpx.AsyncClient, sem: asyncio.Semaphore): ...
    async def complete_structured(messages, schema, *, schema_name) -> dict
```

**Schema**（`PATIENT_RECOMMENDATION_SCHEMA`）关键约束：

```json
{
  "type": "object",
  "properties": {
    "guideline_results": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "guideline": {"type": "string", "enum": ["CSCO","NCCN","ESMO","JGCA","CACA"]},
          "guideline_version": {"type": "string", "minLength": 3},
          "recommendation": {"type": "string", "minLength": 30},
          "evidence_level": {
            "type": "string",
            "enum": [
              "Category 1","Category 2A","Category 2B","Category 3",
              "I,A","I,B","II,A","II,B","II,C","III,C","IV,C","IV,D","V,E",
              "I级推荐","II级推荐","III级推荐",
              "1A类","1B类","2A类","2B类","3类",
              "强推荐","弱推荐","N/A"
            ]
          },
          "source_file": {"type": "string"},
          "retrieval_sources": {"type": "array", "items": {...}}
        },
        "required": ["guideline","guideline_version","recommendation","evidence_level","source_file","retrieval_sources"]
      }
    },
    "consensus": {"type": "array", "items": {"type": "string"}},
    "differences": {"type": "array", "items": {"type": "string"}}
  },
  "required": ["guideline_results","consensus","differences"]
}
```

**调用模式**：`response_format={"type":"json_schema","json_schema":{"name":"patient_recommendation","schema":SCHEMA,"strict":true}}`。vLLM 0.5+ 在 sampling 阶段就拒绝违反 schema 的输出，**消除引号转义 bug 的可能**。

**重试**：429/5xx/timeout 指数退避 3 次；schema 失败重试一次；citation_coverage<0.5 时**带计算结果反馈再调一次**，仍不达标则接受并写 `status: "partial"`。最终失败抛 `LLMFailure(patient_id, last_error, stage)`。

### 2. `scripts/retriever.py`（重构）— 异步 QMD

新增 `AsyncQMDService`：
- `httpx.AsyncClient` 处理 HTTP I/O；QMD 子进程启动/停止仍然同步（一个 pipeline run 一个 QMD server）
- `asyncio.Semaphore(M=8)` 限全局 QMD 在飞请求；CLI `--concurrency-qmd` 覆盖
- session header `Mcp-Session-Id` 在 `__aenter__` 一次性初始化；中途失效（HTTP 400 或缺 session header）时 retry 一次 re-`initialize`
- 同步 `QMDService` 保留为 thin shim（内部 `asyncio.run`），现有 `tests/test_retriever.py` / `test_qmd_integration.py` 不动

### 3. `scripts/pipeline.py`（新增）— 按患者并发流水线

```python
async def run_pipeline(patients, kb_profile, opts):
    async with AsyncQMDService(...) as qmd, httpx.AsyncClient() as http:
        llm = AsyncLLMClient(profile, http, sem=asyncio.Semaphore(opts.llm_concurrency))
        sem_pat = asyncio.Semaphore(opts.concurrency_patients)
        results = []
        async with asyncio.TaskGroup() as tg:
            for p in patients:
                if opts.resume and shard_exists(p):
                    continue
                tg.create_task(_run_one_patient(p, qmd, llm, sem_pat, kb_meta, opts))
        return _summarize(opts.output_dir)
```

**`_run_one_patient` 内部**：try/except 整段包；任一阶段异常捕获为 `Output/_failed/<patient_id>.json`，**不影响其他患者**。LLM 重试在 `AsyncLLMClient` 内；QMD 重试一次 re-init。

**输出布局（per-patient canonical）**：
- 成功：`Output/patients/<patient_id>.json`
- 失败：`Output/_failed/<patient_id>.json`（含 error/stage/last_llm_output）
- Partial（citation 后验失败）：`Output/patients/<patient_id>.json` + `status: "partial"`
- Aggregate（派生）：`run` 末尾合并产出 `Output/rag_results.json`

**退出码**：所有 patient PASS（含 partial）→ 0；任一 patient 进 `_failed/` → 1。

### 4. `scripts/kb_metadata.py`（新增）— 病种侧车元数据

`cmd_index` 在构建 QMD 索引时额外产出：

```
$KB_ROOT/.metadata/
├── chunks.json              # {file_path: {disease_tags:[canonical_keys], org, guideline_version}}
├── org_disease_coverage.json  # {"ESMO": ["gastric"], "NCCN": ["gastric","colorectal","rectal","esophageal"]}
├── synonym_map.yaml         # 词表，源于规则 + 手工维护
└── overrides.yaml           # 可选：边角 case 手工补丁
```

**synonym_map.yaml** 示例：

```yaml
gastric:
  - 胃癌
  - 胃腺癌
  - 胃恶性肿瘤
  - gastric
  - gastric cancer
  - gastric adenocarcinoma
  - GC
  - EGJ              # 食管胃结合部纳入胃癌
  - 食管胃结合部腺癌
colorectal:
  - 结直肠癌
  - 结肠癌
  - 直肠癌
  - CRC
  - colorectal
  - colon cancer
  - rectal cancer
neuroendocrine:
  - 神经内分泌瘤
  - 神经内分泌肿瘤
  - NEN
  - NET
  - NEC
  - 神经内分泌癌
```

**层级匹配算法**（kb_metadata.py 中）：
1. 把 `patient.disease_type` 经 synonym_map 归一化到 canonical_key（如 "胃腺癌" → "gastric"）
2. 把 chunk 的 `disease_tags`（也是 canonical_keys）求并集
3. **patient_key ∈ chunk_keys** → 保留；否则丢弃
4. chunk_keys 为空（标签缺失）→ **保留**（保守默认）

**双层过滤**：
- **Org 级前置**：`filter_orgs_by_disease()` 复用现有逻辑（batch_pipeline.py:422-432），把 patient.disease_type 经词表归一化后查 `org_disease_coverage.json`，结直肠癌患者跳过 ESMO/JGCA/CACA。
- **Chunk 级后置**：QMD 返回 hits 后查 `chunks.json`，不匹配丢弃。

---

## 五、CLI 变化细节

### `parse`（保持）

```
parse --input Input/*.xlsx --output Output/patients.json
```

### `run`（新增）

```
run \
  --patients Output/patients.json \
  --output-dir Output/ \
  --llm-profile qwen3-vllm-lan \           # 默认 env LLM_PROFILE
  --concurrency-patients 5 \                # 默认 5
  --concurrency-qmd 8 \                     # 默认 8
  --resume                                  # shard 存在则跳过
```

### `validate`（接口调整）

```
# 新（首选）
validate --patients-dir Output/patients/ --patients Output/patients.json

# 旧（deprecated 但保留）
validate --input Output/rag_results.json --patients Output/patients.json
```

### `generate`（接口调整）

```
# 新（首选）
generate --patients-dir Output/patients/ --format md

# 旧（deprecated 但保留）
generate --input Output/rag_results.json --format md
```

### `index`（扩展）

```
index --kb-root $MEDICAL_GUIDELINES_DIR
# 现：构建 QMD index
# 新：同时产出 .metadata/{chunks,org_disease_coverage}.json
```

---

## 六、配置（env vars + 可选 yaml）

```bash
# LLM
export LLM_PROFILE=qwen3-vllm-lan
export LLM_BASE_URL=http://<lan-host>:8000/v1
export LLM_MODEL=Qwen3.5-35B-A3B
export LLM_API_KEY=EMPTY                     # vLLM 一般不校验
export LLM_TIMEOUT=180
export LLM_STRUCTURED_MODE=json_schema
export LLM_CONCURRENCY=5

# QMD
export QMD_PORT=8181
export MEDICAL_GUIDELINES_DIR=/Users/f.sh/MyDocuments/RAG/guidelines

# Pipeline
export PIPELINE_CONCURRENCY_PATIENTS=5
export PIPELINE_CONCURRENCY_QMD=8
```

可选 `config/llm_profiles.yaml`（env 优先级 > yaml）：

```yaml
profiles:
  qwen3-vllm-lan:
    base_url: http://10.0.x.x:8000/v1
    model: Qwen3.5-35B-A3B
    api_key_env: LLM_API_KEY
    timeout_s: 180
    structured_mode: json_schema
    concurrency: 5
  deepseek-cloud:                            # 备选：托管 API fallback
    base_url: https://api.deepseek.com/v1
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
    timeout_s: 60
    structured_mode: json_object             # DeepSeek 不支持 json_schema strict
    concurrency: 10
```

---

## 七、实施分阶段

| Phase | 内容 | 工期 | Success Criteria |
|-------|------|------|------------------|
| **1** | Async retriever + sync shim + kb_metadata.py（索引侧车） | 2d | `pytest tests/` 全绿；`.metadata/*.json` 在 $KB_ROOT 中生成；同步 QMDService 现有测试不变 |
| **2** | `llm_client.py` + schema + 单元测试 | 2d | 对内网 vLLM 跑 1 例 fixture，stdout 是 schema-valid JSON；retry/feedback 路径覆盖 |
| **3** | `pipeline.py` + `run` 子命令 + validate/generate 接口扩展 | 3d | 在 10 例 fixture 上 `run --concurrency-patients 5` <10min；rag_results.json 与 现产物结构等价；所有 10 例 PASS validate；零 JSON 解析错误；结直肠癌 3 例不再引用胃癌 chunk |
| **4** | 删除 batch 概念 + 重写 4 个测试文件 | 1d | `pytest tests/` 全绿；`batch_pipeline.py --help` 只剩 5 个子命令；CHANGELOG 更新 |

Phase 3 ship 后 stabilize 一周再启动 Phase 4。

---

## 八、文件改动清单

**新增**：
- `scripts/llm_client.py`、`scripts/pipeline.py`、`scripts/kb_metadata.py`
- `config/llm_profiles.yaml`（env 优先，yaml 可选）
- `tests/test_llm_client.py`、`test_pipeline.py`、`test_retriever_async.py`、`test_kb_metadata.py`
- `tests/fixtures/patient_001.json`、`mock_qmd_response.json`、`mock_llm_response.json`
- `$KB_ROOT/.metadata/synonym_map.yaml`（首次 `index` 时种子化，运维可扩展）

**修改**：
- `scripts/retriever.py` (1-217) — 新增 `AsyncQMDService`，同步类降级为 shim
- `scripts/batch_pipeline.py`：
  - 删除（Phase 4）：`cmd_orchestrate` (889-1032)、`_auto_split_batch` (1034-1061)、`cmd_split` (201-242)、`cmd_verify_batch` (1597-1660)
  - `generate_batch_prompt` (742-887) → 改造为单患者 `build_patient_prompt`，迁到 `pipeline.py`
  - `main()` (2242+) 注册 `run`，Phase 3 把旧命令注册为 hidden，Phase 4 删除
  - 保留并复用：`extract_patient_features` / `build_queries` (435-491) / `filter_orgs_by_disease` (411-433) / `_is_reference_chunk`
  - `cmd_validate` (1662-) / `cmd_generate` (2205-) — 添加 `--patients-dir` 路径；保留 `--input` 兼容
  - `cmd_index` (1249-1339) 扩展为写侧车元数据
  - 净变化：2346 → ~1100 行
- `SKILL.md`、`docs/*`、`CHANGELOG.md` — 流程从 7 阶段更新为 4 阶段

**删除**（Phase 4）：
- `tests/test_orchestrate.py`、`test_split.py`、`test_verify_batch.py`、`test_prompt.py`

---

## 九、端到端验证方案

**Phase 1 验证**：

```bash
pytest tests/test_retriever_async.py tests/test_retriever.py tests/test_kb_metadata.py -v
PYTHONPATH=. python3 scripts/batch_pipeline.py index --kb-root $MEDICAL_GUIDELINES_DIR
ls $MEDICAL_GUIDELINES_DIR/.metadata/chunks.json   # 必须存在
ls $MEDICAL_GUIDELINES_DIR/.metadata/org_disease_coverage.json
```

**Phase 2 验证**：

```bash
# 先启动内网 vLLM（运维侧），假设 base_url 已配
LLM_PROFILE=qwen3-vllm-lan python -m scripts.llm_client \
  --fixture tests/fixtures/patient_001.json
# 期望 stdout 是 schema-valid JSON，含 evidence_level 在 enum 内
```

**Phase 3 端到端验证**（关键 gate）：

```bash
PYTHONPATH=. python3 scripts/batch_pipeline.py parse \
  --input Input/2026-4-23.xlsx --output Output/patients.json

time PYTHONPATH=. python3 scripts/batch_pipeline.py run \
  --patients Output/patients.json \
  --output-dir Output/ \
  --concurrency-patients 5 \
  --llm-profile qwen3-vllm-lan

# 关键断言：
# - wall time <10 min
# - ls Output/patients/*.json | wc -l == 10
# - ls Output/_failed/ 为空（或人工确认接受）
# - jq '.results[].citation_coverage' Output/rag_results.json 全部 ≥ 0.5
# - 贾常山/李学/肖庆周 的 ESMO/JGCA/CACA guideline_results 不含 "胃癌" 字样
# - jq '.results[].guideline_results[].evidence_level' 全部在 schema enum 内

PYTHONPATH=. python3 scripts/batch_pipeline.py validate \
  --patients-dir Output/patients/ --patients Output/patients.json
# 期望 0 FAIL

PYTHONPATH=. python3 scripts/batch_pipeline.py generate \
  --patients-dir Output/patients/ --format md
```

**对照基线**：`Output/test_report_2026-05-11.md` 的 §四「各患者输出质量评估」表 — 重构后所有 10 例的 citation_coverage 应 ≥ 当前值（0.50-0.65），且结直肠癌 3 例的非相关 org 推荐应记为"该指南未检索到相关内容"而非引用胃癌 chunk。

---

## 十、风险与权衡

1. **Qwen3.5-35B-A3B 中文临床推荐质量**：A3B MoE 激活 3B 参数，在中文临床综合分析上弱于 Claude。缓解：(a) schema 强枚举 + min_length 兜底；(b) `--llm-profile deepseek-cloud` 一键切换托管 API；(c) Phase 3 success gate 含与现 10 例 golden 输出的质量 diff，不只是 JSON 合法性。
2. **内网共享 vLLM 资源争抢**：同事在跑别的任务时本项目延迟可能升到 10s+。缓解：concurrency 5 留出 50% 余量；timeout 180s；带指数退避重试。
3. **QMD MCP session stickiness**：QMD session 是 per-TCP-client，共享一个 `httpx.AsyncClient` 保持粘性；中途失效 retry 一次 re-initialize。
4. **病种词表覆盖不全**：首次 `index` 时种子化覆盖 KB 现有 ~10 种癌种，但多病种文件（如 NCCN 综合指南）需手工 `overrides.yaml`。"标签缺失则保留"作为保守兜底，避免误丢。
5. **JSON schema 与 Qwen 输出兼容性**：少数 evidence_level 变体（如 "Category 2A/2B"）可能 vLLM strict 模式下被拒。缓解：枚举列出常见 20+ 种；首次 dry-run 后根据真实输出扩展。
6. **测试冲击**：4 个 batch-targeted 测试文件需重写或删除。缓解：Phase 4 与 Phase 3 分离，pipeline 先稳定再清理。

---

## 十一、Critical Files

- `/Users/f.sh/Workspace/devs/medical-guidelines-suite/scripts/batch_pipeline.py`
- `/Users/f.sh/Workspace/devs/medical-guidelines-suite/scripts/retriever.py`
- `/Users/f.sh/Workspace/devs/medical-guidelines-suite/scripts/pipeline.py`（新增）
- `/Users/f.sh/Workspace/devs/medical-guidelines-suite/scripts/llm_client.py`（新增）
- `/Users/f.sh/Workspace/devs/medical-guidelines-suite/scripts/kb_metadata.py`（新增）
- `/Users/f.sh/Workspace/devs/medical-guidelines-suite/config/llm_profiles.yaml`（新增）
- `$MEDICAL_GUIDELINES_DIR/.metadata/synonym_map.yaml`（首次 index 产出）

---

## 十二、预期性能改进

| 指标 | 当前 | 重构后（预期） | 改进 |
|------|------|--------------|------|
| 10 例患者端到端耗时 | ~60 min | <10 min | **6×** |
| QMD 预检索（39 次查询） | ~3 min（串行） | ~30-60s（并发 8） | **3-6×** |
| LLM 推理（10 例 × 5 org） | ~50 min（人工） | ~3-5 min（vLLM Qwen 并发 5） | **10×** |
| JSON 解析错误率 | 每批次 ~0.5 次 | 0（schema strict 服务端强制） | **质变** |
| 结直肠癌病种错配 chunk 占比 | ~30% | <5%（双层过滤 + 词表归一） | **6×** |
| 单患者 prompt 大小 | 单患者切片自 90KB 多患者捆绑 | ~15-20KB 独立 | **5×** |

---

*方案作者：Claude Code | 测试基线：2026-05-11 测试报告 | 决策闭环：grill-me 13 轮*
