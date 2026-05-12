# Phase 2: LLM Client + Schema + Unit Tests - Context

**Gathered:** 2026-05-12
**Status:** Ready for planning
**Mode:** `--auto`（决策面已由 grill-me 13 轮闭环 + Phase 1 落地的异步范式锁死，本步骤固化 Phase 2 实现层选择并自动推进至 plan-phase）

<domain>
## Phase Boundary

新增 `scripts/llm_client.py`：以 `AsyncLLMClient.complete_structured(messages, schema)` 走 OpenAI 兼容 `response_format={"type":"json_schema","strict":true}`，定义 `PATIENT_RECOMMENDATION_SCHEMA`（`evidence_level` 完全枚举 20+ 变体），并配齐重试 / feedback / failure 策略与多 profile 配置。同时新增可选 `config/llm_profiles.yaml`（env 优先于 yaml）并把 7 个 LLM_* env 变量 wired 进 `LLMProfile.from_env()`。

**不在本 Phase 范围内**（属 Phase 3 / Phase 4）：
- `scripts/pipeline.py:run_pipeline` 顶层 TaskGroup + 患者级 Semaphore（Phase 3）
- `_run_one_patient` 子流水线 / per-patient shard 输出 / `_failed/` / `--resume`（Phase 3）
- `citation_coverage` 的**实际计算**（由 Phase 3 `_run_one_patient` 调用方计算；Phase 2 只暴露"feedback 重试一次"的客户端入口）
- `run` 子命令注册 / CLI 改造 / `--patients-dir` 接口扩展（Phase 3）
- 旧 batch 子命令删除（Phase 4）
- 双层病种过滤接入查询路径（已在 Phase 1 交付数据 + 函数，Phase 3 接入）

</domain>

<decisions>
## Implementation Decisions

### LLM 客户端形态（LLM-01）

- **D-01:** `AsyncLLMClient` 接口签名 = `async def complete_structured(messages, schema, *, schema_name="patient_recommendation") -> dict`
  - 第一位参数 `messages: list[dict]`（OpenAI chat 格式，含 system + user 双消息，prompt 渲染由 Phase 3 调用方负责，本 Phase 不构造 prompt）
  - 第二位 `schema: dict`（JSON Schema 字面量，调用方传入；默认应传 `PATIENT_RECOMMENDATION_SCHEMA`）
  - 返回 `dict`（已解析的 JSON，**不含** `status: "partial"` 标记；status 由调用方根据 citation_coverage 判断）

- **D-02:** HTTP 走 `httpx.AsyncClient`，**注入式**（与 Phase 1 `AsyncQMDService.http_client` 同模式）
  - 构造函数：`AsyncLLMClient(profile: LLMProfile, http: httpx.AsyncClient, sem: asyncio.Semaphore)`
  - `http` 不在 client 内部创建（Phase 3 `pipeline.py` 把 QMD + LLM 共用同一个 `httpx.AsyncClient` 复用连接池）；调用方负责 `async with httpx.AsyncClient()` 生命周期
  - `sem` 同样注入式：单元测试可注入 `asyncio.Semaphore(1)` 验证限流，Phase 3 注入 `Semaphore(5)`

- **D-03:** `response_format` 走 OpenAI 兼容 strict JSON Schema
  ```python
  response_format = {
      "type": "json_schema",
      "json_schema": {
          "name": schema_name,
          "schema": schema,
          "strict": True,
      },
  }
  ```
  - vLLM 0.5+ 在 sampling 阶段就拒绝违反 schema 的输出 → 消除 JSON 引号转义 bug 的可能（refactor_plan §四.1）
  - DeepSeek fallback profile 走 `json_object` 模式（不支持 strict schema），由 `LLMProfile.structured_mode` 字段控制；本 Phase 不需要 DeepSeek 路径走通，但接口必须接受配置

### Schema 设计（LLM-02）

- **D-04:** `PATIENT_RECOMMENDATION_SCHEMA` 顶层结构与 refactor_plan §四.1 完全对齐
  ```python
  PATIENT_RECOMMENDATION_SCHEMA = {
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
                      "evidence_level": {"type": "string", "enum": [...20+ 变体]},
                      "source_file": {"type": "string"},
                      "retrieval_sources": {"type": "array", "items": {...}},
                  },
                  "required": ["guideline","guideline_version","recommendation",
                               "evidence_level","source_file","retrieval_sources"],
              },
          },
          "consensus": {"type": "array", "items": {"type": "string"}},
          "differences": {"type": "array", "items": {"type": "string"}},
      },
      "required": ["guideline_results","consensus","differences"],
  }
  ```
  - **不**加 `additionalProperties: false`（vLLM strict 模式已经强制；额外字段被服务端拒绝）

- **D-05:** `evidence_level` enum 必须**穷举**当前 `_EVIDENCE_LEVEL_MEANINGS` 表（`batch_pipeline.py:1896-1923`）所有变体
  - CSCO：`"1A类","1B类","2A类","2B类","3类","I级推荐","II级推荐","III级推荐"`
  - NCCN：`"Category 1","Category 2A","Category 2B","Category 3"`
  - ESMO：`"I,A","I,B","II,A","II,B","II,C","III,C","IV,C","IV,D","V,E"`
  - JGCA/CACA：`"强推荐","弱推荐","Strong","Weak"`
  - 通用：`"不适用","N/A"`
  - 共 **23 个枚举值**（planner 必须在 PLAN.md 中列出完整 enum 列表，不允许"等等"省略）

- **D-06:** `retrieval_sources` 子 schema 内最少必填 `source_file`（chunk 文件名）+ `score`（QMD 返回的相似度分数）
  - 完整结构与现 `verify-batch` 解析逻辑兼容（`batch_pipeline.py:1597+` 已读 `score` 与 `source_file`）
  - 不要求 schema-level enum 校验文件名（KB 文件列表运行期才知）

### 重试策略（LLM-04, LLM-05）

- **D-07:** 三条独立重试路径
  | Trigger | 策略 | 终态 |
  |---------|------|------|
  | HTTP 429 / 5xx / `httpx.TimeoutException` | 指数退避 `backoff = base * (2 ** attempt)`，base=1s，最多 3 次 | 仍失败抛 `LLMFailure(stage="transport")` |
  | `json.JSONDecodeError` / schema 校验失败 | **不退避**，立即重试 1 次（同一 messages，schema 不变） | 仍失败抛 `LLMFailure(stage="schema")` |
  | `citation_coverage < 0.5` | **带反馈**重试 1 次（messages 追加一条 user message："上轮 citation_coverage = X，请重新检索并补充 retrieval_sources"）；仍 <0.5 接受输出，调用方标 `status: "partial"` | 不抛错（接受为 partial） |

  - **citation_coverage 计算**：本 Phase **不实现**，由 Phase 3 调用方计算后传给 `complete_structured` 的 `feedback_threshold` 参数；本 Phase 的 client 暴露一个 `complete_structured_with_feedback(messages, schema, feedback_check: Callable[[dict], float], threshold: float = 0.5) -> tuple[dict, float]` 高阶方法封装"调用一次 → 算分 → 阈值不足重试一次"循环。

- **D-08:** `LLMFailure` 异常字段固定为 `(patient_id, last_error, stage)`
  ```python
  class LLMFailure(Exception):
      def __init__(self, patient_id: str | None, last_error: Exception, stage: str):
          self.patient_id = patient_id
          self.last_error = last_error
          self.stage = stage      # "transport" | "schema" | "feedback"
  ```
  - `patient_id` 由调用方传入（Phase 3 `_run_one_patient` 把当前 patient.id 当 kwarg 注入 `complete_structured`，client 内部只透传到 exception，不主动从 messages 解析）
  - 本 Phase 的 client 单元测试用 `patient_id="test_001"` 静态占位

- **D-09:** 重试**计数语义**
  - "指数退避 3 次" = 第 1 次失败后等 1s，第 2 次失败等 2s，第 3 次失败等 4s，**总共发起 4 次 HTTP 请求**（初次 + 3 次重试）
  - "schema 失败重试一次" = 总共 2 次 HTTP 请求
  - "feedback 重试一次" = 总共 2 次 HTTP 请求（语义上是 retry，不是 transport-level retry）
  - 三条路径**独立计数**（schema 重试时不计入 transport 重试 budget，反之亦然）

### Profile 与配置（LLM-03, CFG-01, CFG-02）

- **D-10:** `LLMProfile` = frozen dataclass，字段对齐 refactor_plan §四.1
  ```python
  @dataclass(frozen=True)
  class LLMProfile:
      name: str
      base_url: str
      model: str
      api_key_env: str = "LLM_API_KEY"
      max_tokens: int = 4096
      temperature: float = 0.1
      timeout_s: int = 180
      structured_mode: str = "json_schema"   # 或 "json_object"
      concurrency: int = 5
  ```

- **D-11:** Profile 加载优先级 **env > yaml > 内置默认**
  - `LLMProfile.from_env()` 类方法：读 `LLM_PROFILE` 决定 profile 名（默认 `"qwen3-vllm-lan"`），再分字段读 `LLM_BASE_URL / LLM_MODEL / LLM_API_KEY / LLM_TIMEOUT / LLM_STRUCTURED_MODE / LLM_CONCURRENCY`；env 缺失字段则 fallback 到 yaml（若存在）；yaml 缺失字段 fallback 到 dataclass 默认值
  - yaml 文件位置：`./config/llm_profiles.yaml`（项目根相对路径）；找不到则跳过
  - `LLM_API_KEY` 单独走 `os.environ[profile.api_key_env]`（即 profile 字段指向 env 名，**值**到调用时才解析；frozen dataclass 不存明文 key）

- **D-12:** `config/llm_profiles.yaml` 内置两个 profile（refactor_plan §六 样例）
  ```yaml
  profiles:
    qwen3-vllm-lan:
      base_url: http://10.0.x.x:8000/v1
      model: Qwen3.5-35B-A3B
      api_key_env: LLM_API_KEY
      timeout_s: 180
      structured_mode: json_schema
      concurrency: 5
    deepseek-cloud:
      base_url: https://api.deepseek.com/v1
      model: deepseek-chat
      api_key_env: DEEPSEEK_API_KEY
      timeout_s: 60
      structured_mode: json_object
      concurrency: 10
  ```
  - `base_url` 占位符 `10.0.x.x` 由运维侧实际部署后通过 env `LLM_BASE_URL` 覆盖；yaml 本身不入仓时填占位即可（**入仓**：本 Phase 把该文件 commit 进 `config/llm_profiles.yaml`，不放 secret）

### Claude's Discretion

- 重试退避基数：D-07 选 `base=1s`，planner 可在 PLAN 中放宽到 `0.5s` 或 `2s`，但必须保证 3 次重试总等待 ≤7s（180s timeout 下不会把单请求 budget 吃光）
- jitter：是否在指数退避里加随机抖动（防 thundering herd）→ Phase 2 单 client 单 patient，无必要；留 v3.2 多 patient 并发再加
- httpx `Retry` 中间件 vs 手写 `for attempt in range(4)` 循环 → **手写**（更易测试、不引入 `httpx-retry` 额外依赖）
- 是否把 `complete_structured` 与 `complete_structured_with_feedback` 合并为单方法（带 `feedback_check=None` 默认参数）→ planner 决定；优先**两方法分离**，feedback 是高阶组合不污染基础接口
- 单元测试是否调用真实 vLLM endpoint：**否**。本 Phase 全部用 `AsyncMock(httpx.AsyncClient.post)` 桩；ROADMAP Phase 2 Success Criteria #1 的"跑 1 例 fixture"是 manual 验收（用户执行），不进 CI

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents (planner / executor) MUST read these before planning or implementing.**

### Source of Truth（必读）

- `docs/refactor_plan_2026-05-11.md`
  - §四.1 `scripts/llm_client.py` 设计 — `LLMProfile` dataclass、`AsyncLLMClient.complete_structured` 签名、`PATIENT_RECOMMENDATION_SCHEMA` 完整字面量、retry policy 描述
  - §六 配置（env vars + 可选 yaml） — 7 个 LLM_* env、`llm_profiles.yaml` 双 profile 样例
  - §十.1 / §十.5 风险与权衡 — Qwen3.5-35B-A3B 中文质量、JSON schema 与 Qwen 输出兼容

### Project-Level

- `.planning/PROJECT.md` — v3.1 milestone 范围、key decisions（`evidence_level` 完全枚举 strict / 部分容错 / 内网 vLLM 主路径）
- `.planning/REQUIREMENTS.md` — Phase 2 范围：LLM-01..05（5 条）+ CFG-01..02（2 条），共 7 条
- `.planning/ROADMAP.md` §Phase 2 — Goal、Success Criteria（3 条）、Duration（2d）
- `.planning/STATE.md` — performance baseline 表（LLM 50min 人工 → 3-5min vLLM 并发 5）

### Phase 1 落地的异步范式（必读，直接复用）

- `.planning/phases/01-async-retriever-kb-metadata-sidecar/01-CONTEXT.md` §D-02 / §D-03 — 子进程 vs HTTP I/O 异步化的边界划分（LLM 无子进程，但 sem 注入式模式直接搬）
- `.planning/phases/01-async-retriever-kb-metadata-sidecar/01-PATTERNS.md` §3 「Async-Pattern Recommendations」 — `httpx.AsyncClient` 生命周期 / `asyncio.Semaphore` 注入 / `pytest-asyncio` 配置（已在 conftest 启用 strict mode）
- `scripts/retriever.py:AsyncQMDService`（Phase 1 交付） — frozen dataclass-style 构造参数、`async with self._sem` 限流、注入式 `http_client` 默认 None 时自建 + `_owns_http` 标志、`__aexit__` 关闭策略

### 现有代码（Phase 2 复用 / 引用）

- `scripts/batch_pipeline.py:1880-1930` — `_EVIDENCE_LEVEL_MEANINGS` 正则表（D-05 enum 来源；planner 必须把 23 个变体逐一列入 schema enum）
- `scripts/batch_pipeline.py:743-887` — 现 `generate_batch_prompt` 函数（Phase 3 改造为 `build_patient_prompt`；Phase 2 client 不构造 prompt，但 `messages` 参数形状必须兼容此函数的 system+user 双消息输出）
- `tests/conftest.py` — pytest fixtures（Phase 1 已添加 `pytest-asyncio` strict mode；Phase 2 直接复用 `@pytest.mark.asyncio` 装饰器与 `AsyncMock` 模式）
- `requirements.txt` — 已含 `httpx>=0.27,<1.0` + `pytest-asyncio>=0.23,<1.0`（Phase 1 添加；Phase 2 不需新增依赖，除非 yaml 解析用到 pyyaml — `pyyaml>=6.0` 已在）

### 上游约束

- `CLAUDE.md` — Python 3.9.6 约束（**禁** `asyncio.TaskGroup` / `asyncio.timeout` / `except*`；用 `await asyncio.wait_for()` + `asyncio.gather(...)` 替代）
- `~/.config/qmd/index.yml` — QMD 模型配置（与 Phase 2 无关，但说明 yaml-based config pattern 在本仓已有先例）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **Phase 1 `AsyncQMDService` 注入模式** (`scripts/retriever.py:AsyncQMDService.__init__`) — `http_client / semaphore / timeout_s` 三个关键字参数风格直接复制到 `AsyncLLMClient.__init__`；`_owns_http` 标志同模式（LLM 客户端默认 `http_client` 必填 = 调用方提供，本 Phase 单元测试可注入 mock）
- **Phase 1 测试 fixture 风格** (`tests/test_retriever_async.py`) — `@pytest.mark.asyncio` + `AsyncMock` + `mock_client.post.side_effect = [resp1, resp2, ...]` 模拟序列响应直接复用于 LLM retry 测试
- **`_EVIDENCE_LEVEL_MEANINGS` 正则表** (`scripts/batch_pipeline.py:1896-1923`) — 23 个 evidence_level 变体的权威清单；schema enum 从这里逐项摘取字面量（去掉正则元字符）

### Established Patterns

- **从 env 读 int with fallback**（仓库目前无统一 helper，但 `scripts/retriever.py:55` 用 `int(os.environ.get("QMD_PORT", "8181"))` 模式）— `LLMProfile.from_env` 同款，但需补 `try/except ValueError`（用户写 `LLM_TIMEOUT=abc` 时不静默掉到默认值）
- **yaml 加载**（仓库目前 yaml 出现在 `synonym_map.yaml` / `~/.config/qmd/index.yml`）— `scripts/kb_metadata.py:load_synonym_map` 是已落地的 yaml 读取参考；Phase 2 `load_profiles_yaml` 直接搬同款 `yaml.safe_load` + `FileNotFoundError` 兜底
- **frozen dataclass 字段顺序**（Phase 1 未使用 dataclass）— Phase 2 是仓库首个 `@dataclass(frozen=True)`；planner 在 PLAN.md 中需要显式声明 `from dataclasses import dataclass, field`

### Integration Points

- **`AsyncLLMClient` ↔ Phase 3 `_run_one_patient`**：Phase 3 调用 `await llm.complete_structured_with_feedback(messages, PATIENT_RECOMMENDATION_SCHEMA, feedback_check=compute_citation_coverage)`；Phase 2 client 暴露的接口必须接受 `feedback_check: Callable[[dict], float]` 这种依赖注入形式（避免循环依赖 Phase 3 的 citation_coverage 计算逻辑回流到 Phase 2）
- **`LLMProfile.from_env` ↔ `batch_pipeline.py:main`**：Phase 3 `run` 子命令调用 `LLMProfile.from_env(name=args.llm_profile)`；Phase 2 只保证 `from_env` 函数本身在 unit test 下工作正常
- **`config/llm_profiles.yaml` ↔ git**：本文件**入仓**（不含 secrets，仅 base_url / model 等元数据）；planner 必须在 PLAN.md 中显式列入 `git add config/llm_profiles.yaml`

</code_context>

<specifics>
## Specific Ideas

- **`messages` 参数形态**：与 OpenAI chat 完全一致，`[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]`；Phase 2 client 直接透传给 vLLM，不做任何 prompt 工程
- **feedback retry 的反馈消息模板**（refactor_plan 未明示，planner 在 PLAN.md 定稿）：
  ```
  上轮输出 citation_coverage = {prev:.2f} 低于阈值 {threshold:.2f}。
  请重新检索并补充 retrieval_sources，确保每条 guideline_results 至少引用 2 个独立 source_file，
  且 recommendation 文本中引用 [n] 编号与 retrieval_sources[n-1].source_file 对应。
  保持原 schema 不变。
  ```
- **timeout 实现**：在 `httpx.AsyncClient(timeout=profile.timeout_s)` 构造时设置（调用方持有 client，所以 timeout 也由调用方按 profile 配置）；client 内部**不再**额外 `asyncio.wait_for`（双层 timeout 会让错误归因混乱）
- **测试 fixture：`tests/fixtures/patient_001.json`** —— 内容由 planner 在 PLAN.md 中指定 schema-valid 的 1 例患者，用于 ROADMAP Success Criteria #1 的人工验收（不在自动 pytest 范围内）
- **测试 fixture：`tests/fixtures/mock_llm_response.json`** —— schema-valid 的 1 个 guideline_result 数组样例，用于 retry 路径单元测试的 `mock_client.post.return_value.json.return_value`

</specifics>

<deferred>
## Deferred Ideas

- **流式输出**（`stream=True`） — `STR-01`（v3.2 范围），strict schema 需一次完整输出，Phase 2 不支持流式
- **多 profile 同时实例化 + 自动 failover**（vLLM 挂了切 DeepSeek） — refactor_plan §十.1 提及，但实施级别 deferred 到 v3.2；Phase 2 单 profile per AsyncLLMClient 实例
- **token usage tracking / cost accounting** — `OBS-01..03`（v3.2 范围），Phase 2 不暴露 metrics
- **prompt caching / system prompt 预热** — vLLM 0.5+ 支持，但 Phase 2 单患者请求间没有缓存价值；留 v3.2 性能优化
- **citation_coverage 实际计算** — Phase 3 `_run_one_patient` 范围；Phase 2 只暴露 `feedback_check: Callable` 注入点

### Reviewed Todos (not folded)

None — `gsd-sdk query todo.match-phase 2` 返回 0 matches。

</deferred>

---

*Phase: 02-llm-client-schema-unit-tests*
*Context gathered: 2026-05-12*
*Auto-mode: `--auto`（recommended options per refactor_plan §四.1 + §六 决策已闭环）*
