# Phase 1: Async Retriever + KB Metadata Sidecar — 模式映射

**Mapped:** 2026-05-11
**Files analyzed:** 5（2 修改 + 3 新增，含 1 个运行时种子 YAML）
**Analogs found:** 5 / 5（其中 1 个无完全异步类比，已专项说明）

---

## 1. File Classification（角色 / 数据流 / 类比匹配度）

| New/Modified File | Type | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|------|-----------|----------------|---------------|
| `scripts/retriever.py` | MODIFY 全文 | service (HTTP MCP 客户端 + 子进程 lifecycle) | request-response（async + sync shim） | 自身 `QMDService:43-216`（同步版） | **role-match**（同 role 不同 data flow）。仓库无任何 `async def` / `httpx` 先例，异步部分必须建立新模式 |
| `scripts/kb_metadata.py` | CREATE（~150 LOC） | utility（纯函数 + 单次副作用 build_sidecar） | transform / file-I/O（聚合落盘） | `scripts/extraction/postprocess.py` | **partial**（同 "纯函数小模块" 风格，但 postprocess 完全无 I/O，build_sidecar 有受控 I/O） |
| `scripts/batch_pipeline.py:cmd_index` | MODIFY 局部（lines 1249–1335 后插入 1 行调用） | controller（CLI 子命令） | request-response | 自身 cmd_index 上半段（reuse `orgs_found` 扫描结果） | **exact**（只追加一次 `kb_metadata.build_sidecar(kb_root, orgs_found)` 调用） |
| `tests/test_retriever_async.py` | CREATE | test（async 单元测试） | request-response（mocked） | `tests/test_retriever.py`（同步 mock 风格） + `tests/test_qmd_integration.py`（env-gated 集成） | **role-match**（mock 结构同源，需 `pytest-asyncio` + `AsyncMock`） |
| `tests/test_kb_metadata.py` | CREATE | test（纯函数 + tmp_path I/O） | transform | `tests/test_features.py` / `tests/test_resolve_kb.py` | **exact**（同纯函数单元测试模式 + `tmp_path` fixture） |
| `$MEDICAL_GUIDELINES_DIR/.metadata/synonym_map.yaml` | CREATE（运行时） | config（seed 数据文件，不入仓） | static-config | `docs/refactor_plan_2026-05-11.md:192-218` 样例 | **exact**（文档样例直接搬用 + 扩展到 10 癌种） |

---

## 2. Pattern Assignments（per-file 代码切片）

### 2.1 `scripts/retriever.py`（MODIFY 全文）— role: service, data flow: request-response (async)

**Analog 1：自身同步 `QMDService`（`scripts/retriever.py:43-216`）作为结构骨架**

**协议常量复用**（lines 26-40，原样移到模块顶层即可被 `AsyncQMDService` 直接 import）：

```python
_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

_INIT_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "medical-guidelines-suite", "version": "3.0.0"},
    },
}
```

**子进程 lifecycle pattern**（lines 63-93，`__enter__/__exit__`）— async 版必须保留**完全相同**的"启动失败立刻 kill + 退出时优雅 terminate→kill"防御逻辑：

```python
def __enter__(self) -> "QMDService":
    self._check_port_available()
    self.process = subprocess.Popen(
        ["qmd", "mcp", "--http", "--port", str(self.port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        self._wait_for_ready(timeout=30)
    except Exception:
        if self.process and self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=3)
        self.process = None
        raise
    return self

def __exit__(self, exc_type, exc_val, exc_tb) -> None:
    self._session_id = None
    if self.process is None:
        return
    if self.process.poll() is not None:
        self.process = None
        return
    self.process.terminate()
    try:
        self.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        self.process.kill()
        self.process.wait(timeout=3)
    self.process = None
```

**Session 初始化 + header 抓取**（lines 163-186）— 必须保持 invariant：`__aenter__` / `__enter__` 返回时 `self._session_id` 已写入。注意 `mcp-session-id` header 是**小写**：

```python
def _wait_for_ready(self, timeout: int = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if self.process.poll() is not None:
            raise QMDStartupError(
                f"QMD exited during startup (exit code: {self.process.returncode})"
            )
        try:
            resp = requests.post(
                self.base_url,
                headers=_MCP_HEADERS,
                json=_INIT_PAYLOAD,
                timeout=5,
            )
            if resp.status_code == 200:
                if self.process.poll() is not None:
                    raise QMDStartupError("QMD exited immediately after health check")
                self._session_id = resp.headers.get("mcp-session-id")
                return
        except requests.ConnectionError:
            pass
        time.sleep(0.5)
    raise QMDStartupError(f"QMD not ready after {timeout}s")
```

**MCP 响应解析（无 I/O 纯函数，原样复用）**（lines 188-216）：

```python
@staticmethod
def _parse_mcp_response(data: dict) -> list[dict]:
    if "error" in data:
        err = data["error"]
        raise RuntimeError(
            f"QMD error {err.get('code', '?')}: {err.get('message', '')}"
        )
    result = data.get("result", {})
    structured = result.get("structuredContent", {})
    raw_results = structured.get("results")
    if raw_results is not None:
        return [
            {
                "content": r.get("snippet", ""),
                "path": r.get("file", ""),
                "score": r.get("score", 0.0),
                "context": r.get("context", ""),
            }
            for r in raw_results
        ]
    for block in result.get("content", []):
        if block.get("type") == "text":
            return [{"content": block["text"], "path": "", "score": 0.0, "context": ""}]
    return []
```

**Sync shim 改写要点**（lines 95-153，`query` / `search` 改为）— 必须保持**字节级 API 兼容**，即返回 list[dict]、签名 `(text, top_k=10, min_score=0.3)` 不变；现有 `tests/test_retriever.py` 与 `test_qmd_integration.py` 不允许修改：

```python
# 改写目标形态（示意）：
class QMDService:
    def __enter__(self):
        self._async = AsyncQMDService(port=self.port)
        asyncio.run(self._async.__aenter__())
        self.process = self._async.process       # 暴露给现有 test 的 .process 属性
        return self

    def __exit__(self, *exc):
        asyncio.run(self._async.__aexit__(*exc))

    def query(self, text, top_k=10, min_score=0.3):
        return asyncio.run(self._async.query(text, top_k, min_score))

    def search(self, text, top_k=10):
        return asyncio.run(self._async.search(text, top_k))
```

> **测试基线兼容性硬要求**：`tests/test_retriever.py:14-22` 断言 `svc.process is not None` 与 `proc.terminate.assert_called_once()`；`tests/test_retriever.py:39-53` 用 `mock_post.side_effect = [health_resp, query_resp]` 假设 **2 次** HTTP POST。Shim 必须保证 `subprocess.Popen` 与 `requests.post` 这两个被 patch 的入口仍被调用同样次数（因此 async 内部不能换成 `httpx.AsyncClient` 走自己独立的 mock；必须共享 `requests.post` 作为底层 — 或者 shim 直接走同步路径而不调 `asyncio.run`）。
>
> **D-01 决策歧义点**：CONTEXT.md D-01 写"单一异步实现，同步 `QMDService` 降级为 thin shim"，但若 shim 真的走 `asyncio.run(self._async.method())`，则 mock 目标会从 `scripts.retriever.requests.post` 漂移到 `scripts.retriever.httpx.AsyncClient.post`，旧测试**必然失败**。**Planner 必须解决这个二选一**：(a) 异步内部仍用 `requests` 走 `asyncio.to_thread` 包装（保 mock 不变，但失去并发收益）；或 (b) 改写旧测试为同时 patch `requests` 与 `httpx`（违反 D-01"不改旧测试"）；或 (c) 在 `AsyncQMDService` 内部用 `httpx`，但 shim **不走 async**，直接复用现有 `requests` 同步实现（即同步类与异步类共存，违反 D-01"单一真相"）。**推荐方案**：异步类用 `httpx.AsyncClient`；shim 内部继续走 `requests.post`（保留现有同步代码路径不动），D-01 "单一真相"理解为 "并发 caller 应只使用 AsyncQMDService" 而非 "代码物理去重"。这同时回避 Gotcha §4.2 的 mock 漂移风险。

**Analog 2（异步新模式 — 仓库无先例，需建立）**：

仓库 grep `httpx|asyncio|async def|aiohttp` 在 `scripts/` + `tests/` 下**全部为空**，因此异步部分没有现成代码可抄。落地建议见 §3 「Async-Pattern Recommendations」。

---

### 2.2 `scripts/kb_metadata.py`（CREATE，~150 LOC）— role: utility, data flow: transform + 单次 file-I/O

**Analog：`scripts/extraction/postprocess.py:1-121` — 纯函数小模块风格**

**模块头部样式**（lines 1-6）— 一句 docstring + `from __future__ import annotations` + 顶层模块常量：

```python
"""MinerU 输出 LaTeX 标记后处理 + 内联术语补回。"""

from __future__ import annotations

import re


# ① ② ③ ④ ⑤ ⑥ ⑦ ⑧ ⑨
_CIRCLED_MAP = {str(i): chr(0x2460 + i - 1) for i in range(1, 10)}
```

**函数签名风格**（lines 12-24）— 显式类型注解 + 简短 docstring + 示例输入：

```python
def postprocess_latex(text: str) -> str:
    """清理 MinerU 输出中的 LaTeX 标记，转为纯文本。

    处理的模式:
      $75.7\\%$          → 75.7%
      ...
    """
    if "$" not in text:
        return text
    ...
```

**`kb_metadata.py` 推荐 API 形态**（D-10 已规定）：

```python
"""KB 病种侧车元数据：词表归一化 + chunks/coverage 派生 + 文件落盘。"""

from __future__ import annotations

import json
import re
from pathlib import Path
import yaml


_DISEASE_SUFFIX_RE = re.compile(r"(癌|瘤|肿瘤)$")

# refactor_plan §四.4 样例的内联种子；首次 index 时若 synonym_map.yaml 不存在则落盘
_SYNONYM_SEED: dict[str, list[str]] = {
    "gastric": ["胃癌", "胃腺癌", "胃恶性肿瘤", "gastric", "gastric cancer",
                "gastric adenocarcinoma", "GC", "EGJ", "食管胃结合部腺癌"],
    "colorectal": ["结直肠癌", "结肠癌", "直肠癌", "CRC", "colorectal",
                   "colon cancer", "rectal cancer"],
    "neuroendocrine": ["神经内分泌瘤", "神经内分泌肿瘤", "NEN", "NET", "NEC",
                       "神经内分泌癌"],
    # esophageal / hepatic / pancreatic / breast / lung / cervical / lymphoma
    # 由 planner 在 plan 中补齐至 10 个 canonical_key
}


def load_synonym_map(kb_root: Path) -> dict[str, list[str]]:
    """读取 $KB_ROOT/.metadata/synonym_map.yaml；不存在返回 _SYNONYM_SEED。"""
    ...


def normalize_disease(disease_type: str, synonym_map: dict) -> str | None:
    """patient.disease_type → canonical_key（双向 lower+strip+去癌/瘤/肿瘤后缀）。
    未命中返回 None（保守兜底由调用方负责保留全集）。"""
    ...


def filter_chunks_by_disease(
    hits: list[dict],
    chunks_meta: dict,
    canonical_key: str | None,
) -> list[dict]:
    """chunk 级后置过滤：canonical_key ∈ chunk.disease_tags → 保留；
    chunk.disease_tags 为空 → 保留（KBM-06 兜底）；canonical_key 为 None → 全保留。"""
    ...


def filter_orgs_by_disease(
    coverage: dict,
    canonical_key: str | None,
) -> list[str]:
    """org 级前置过滤：canonical_key ∈ coverage[org] → 保留。
    coverage[org] 为空或 canonical_key 为 None → 保留全集。"""
    ...


def build_sidecar(kb_root: Path, orgs_found: list[tuple[str, Path, list[Path]]]) -> None:
    """副作用入口（cmd_index 一次性调用）：
    1. mkdir $KB_ROOT/.metadata/
    2. 扫 orgs_found 中每个 md_files，按文件名 stem 经 synonym_map 推断 disease_tags
    3. 写 chunks.json / 派生 org_disease_coverage.json / 种子化 synonym_map.yaml（若缺失）
    """
    ...
```

**关键规则（来自 D-06 / D-07 / D-09 / D-10）**：
1. `normalize_disease` 双向归一化：输入侧与词表侧都走 `text.lower().strip()` + `_DISEASE_SUFFIX_RE.sub("", ...)`，再做精确字符串比对；不做 fuzzy / 不调用 LLM。
2. `org_disease_coverage` **不要**单独维护；从 `chunks.json` 聚合派生：`coverage[org] = sorted(set(tag for chunk in chunks if chunk.org == org for tag in chunk.disease_tags))`。
3. `synonym_map.yaml` "已存在不覆盖" — `build_sidecar` 内 `if (kb_root / ".metadata/synonym_map.yaml").exists(): skip`。
4. chunk-level `disease_tags` 推断走文件名启发式：解析 `extracted/<org>-<disease>-<version>.md` 的 stem（参考 cmd_index 第 1325-1328 行已有的 `prefix = org_name.lower() + "-"` 剥离逻辑），剥离 org 前缀后查 synonym_map 反向索引；找不到 → `disease_tags: []`。
5. `guideline_version`：读 `extracted/*.md` 第一行 H1 标题，正则提取年份；找不到则空字符串。
6. **纯函数性**：除 `build_sidecar` 外，其余 4 个函数不可有任何 `open()` / `Path.write_*` / `print()` 调用；只读入参 + 返回值。

---

### 2.3 `scripts/batch_pipeline.py:cmd_index`（MODIFY 局部，扩展点）— role: controller

**Analog：自身 cmd_index 函数体（lines 1249-1335），扫描循环已经收集了 `orgs_found`，直接复用作为 `build_sidecar` 的入参**

**已有扫描循环**（lines 1255-1265，整段保持不变，**仅复用其产出**）：

```python
orgs_found = []
for org_dir in sorted(kb_root.iterdir()):
    if not org_dir.is_dir() or org_dir.name.startswith("."):
        continue
    extracted_dir = org_dir / "extracted"
    if not extracted_dir.exists():
        continue
    md_files = sorted(extracted_dir.glob("*.md"))
    if not md_files:
        continue
    orgs_found.append((org_dir.name, org_dir, md_files))
```

**扩展点**（line 1335 之前 — `print(f"\nIndex complete: ...")` 之前插入一次调用）：

```python
# ── 现有最后一行（line 1335）：
# print(f"\nIndex complete: {len(orgs_found)} organizations")

# ── 新增（直接插在该 print 之前）：
import scripts.kb_metadata as kb_metadata        # 模块顶部 import 即可
...
kb_metadata.build_sidecar(kb_root, orgs_found)
print(f"Sidecar metadata: {kb_root / '.metadata'}")
print(f"\nIndex complete: {len(orgs_found)} organizations")
```

**Hard rule**（CONTEXT.md D-05 / D-12 + Phase 1 范围）：
- 不允许动 cmd_index 内现有的 `qmd collection add` / `qmd embed` / `qmd context add` 任一行（Metal exit-code 134 兼容、`qmd ls` URL normalization 都保持原样）。
- 不允许把 `.metadata/` 写到仓库内任意位置 — 落点固定 `$MEDICAL_GUIDELINES_DIR/.metadata/`，由 `kb_metadata.build_sidecar` 自行 `mkdir(parents=True, exist_ok=True)`。
- Phase 1 不动 `batch_pipeline.py:411-432` 现有的 `_DISEASE_KEYWORD_MAP` / `_extract_disease_keywords` / `filter_orgs_by_disease`（D-11：Phase 3 切换调用后再清理）。

---

### 2.4 `tests/test_retriever_async.py`（CREATE）— role: test, data flow: request-response (async, mocked)

**Analog 1：`tests/test_retriever.py`（同步 mock 风格，扩展为 async）**

**Mock 结构**（lines 9-26）— 同样的"patch `subprocess.Popen` + patch HTTP client + MagicMock proc.poll/pid"：

```python
@patch("scripts.retriever.subprocess.Popen")
@patch("scripts.retriever.requests.post")
def test_qmd_service_lifecycle(mock_post, mock_popen):
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 12345
    mock_popen.return_value = proc

    mock_post.return_value = MagicMock(status_code=200, json=lambda: {"result": "ok"})

    from scripts.retriever import QMDService

    with QMDService(port=9999) as svc:
        assert svc.process is not None
        assert svc.port == 9999

    proc.terminate.assert_called_once()
```

**Async 版改写要点**：
- 装饰器加 `@pytest.mark.asyncio`，函数签名 `async def test_...`。
- HTTP patch 目标从 `scripts.retriever.requests.post` 切到 `scripts.retriever.httpx.AsyncClient`（或 patch `AsyncClient.post`）— 用 `AsyncMock` 而非 `MagicMock`。
- 上下文管理改为 `async with AsyncQMDService(port=9999) as svc:`。
- `subprocess.Popen` patch 完全不变（D-02：QMD 子进程仍同步起停）。

**端口冲突检测复用**（test_retriever.py:65-78）— `_check_port_available` 仍是同步 `socket.socket()` 调用，async 类内同名方法直接 reuse，测试无需改：

```python
@patch("scripts.retriever.socket.socket")
def test_qmd_service_detects_port_conflict(mock_socket_cls):
    mock_sock = MagicMock()
    mock_sock.connect_ex.return_value = 0
    mock_sock.__enter__ = lambda s: mock_sock
    mock_sock.__exit__ = MagicMock(return_value=False)
    mock_socket_cls.return_value = mock_sock

    from scripts.retriever import QMDService, QMDStartupError
    with pytest.raises(QMDStartupError, match="already in use"):
        with QMDService(port=9999):
            pass
```

**崩溃进程优雅 exit**（test_retriever.py:81-99）— `proc.poll.side_effect = [None, None, 1]` 模拟"启动后再次轮询发现已挂"，验证 `terminate.assert_not_called()`。Async 版必须复刻此 case。

**新增 case 清单**（CONTEXT.md 已给出范围 — planner 在 PLAN.md 中具体拆解）：
1. `__aenter__` / `__aexit__` lifecycle（含 Popen 调用 + session_id 注入）
2. session 失效 retry：第一次 HTTP 400 → re-initialize → 第二次成功；用 `mock_post.side_effect = [init_resp, 400_resp, init_resp, 200_resp]` 串起来
3. Semaphore 限流：注入 `asyncio.Semaphore(2)`，并发触发 5 个 `query`，断言**任一时刻 in-flight ≤ 2**（用 `MagicMock(side_effect=lambda...: asyncio.sleep(...))` 计数）
4. 并发 query 正确性：`asyncio.gather(svc.query(q1), svc.query(q2), svc.query(q3))` 返回顺序与查询顺序一致

**Analog 2：`tests/test_qmd_integration.py`（env-gated 集成测试模式 — 必须保留为 thin shim 通过路径）**

```python
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("QMD_AVAILABLE"),
        reason="QMD not available (set QMD_AVAILABLE=1 to run)",
    ),
]
```

> 本文件**不修改**（RTR-04 硬约束）。`test_retriever_async.py` 可以新增一个对称的 env-gated `async` 集成测试，建议写在新文件而不污染 `test_qmd_integration.py`。

---

### 2.5 `tests/test_kb_metadata.py`（CREATE）— role: test, data flow: transform

**Analog：`tests/test_features.py` + `tests/test_resolve_kb.py` — 纯函数 + tmp_path 风格**

**`tests/test_features.py:4-12` 风格**（直接 import 函数、构造 dict 入参、assert 返回结构）：

```python
def test_structured_full_fields(sample_patients):
    p = sample_patients[0]
    features = extract_patient_features(p)
    assert "胃" in " ".join(features["diagnosis_keywords"])
    assert any("T1" in k or "t1" in k.lower() for k in features["staging_keywords"])
```

**`tests/test_resolve_kb.py:12-22` 风格**（用 `tmp_path` + `monkeypatch.setenv` 模拟 `$MEDICAL_GUIDELINES_DIR`）：

```python
def test_env_var(mock_kb, monkeypatch):
    monkeypatch.setenv("MEDICAL_GUIDELINES_DIR", str(mock_kb))
    assert resolve_kb_root(None) == mock_kb

def test_local_guidelines(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MEDICAL_GUIDELINES_DIR", raising=False)
    g = tmp_path / "guidelines"
    g.mkdir()
    (g / "data_structure.md").write_text("# test")
    assert resolve_kb_root(None) == g
```

**复用 `tests/conftest.py:82-115` 的 `mock_kb` fixture**（已构造 `NCCN/extracted/NCCN_GastricCancer.md` 等真实文件名结构，正好用于 `build_sidecar` 端到端测试）：

```python
@pytest.fixture
def mock_kb(tmp_path):
    kb = tmp_path / "guidelines"
    for org in ["NCCN", "ESMO", "CSCO"]:
        ext_dir = kb / org / "extracted"
        ext_dir.mkdir(parents=True)
        (ext_dir / f"{org}_GastricCancer.md").write_text(
            f"Line 1: {org} Gastric Cancer Guideline\n" * 100,
            encoding="utf-8",
        )
        ...
```

**推荐 test case 清单**（KBM-01..06 全覆盖 + D-09 / D-10 行为）：

| Case | 函数 | 断言 |
|------|------|------|
| `test_normalize_hit_zh` | `normalize_disease("胃腺癌", seed)` | `== "gastric"` |
| `test_normalize_hit_en_case` | `normalize_disease("Gastric Cancer", seed)` | `== "gastric"`（lower + 去掉 "cancer" 仍命中） |
| `test_normalize_strip_suffix` | `normalize_disease("胃肿瘤", seed)` | `== "gastric"`（去 "肿瘤" 后命中 "胃"，需 seed 含 "胃" alias） |
| `test_normalize_miss` | `normalize_disease("罕见癌种", seed)` | `is None` |
| `test_filter_chunks_keep_when_tags_empty` | `filter_chunks_by_disease([hit], {"f.md":{"disease_tags":[]}}, "gastric")` | 保留（KBM-06） |
| `test_filter_chunks_drop_mismatch` | hit path 是 `colorectal-2026.md`，canonical_key="gastric" | 丢弃 |
| `test_filter_orgs_drop_csco_for_neuroendocrine` | coverage={"NCCN":["neuroendocrine"], "CSCO":["gastric"]}, key="neuroendocrine" | `== ["NCCN"]` |
| `test_filter_orgs_keep_all_when_key_none` | canonical_key=None | 返回 coverage 全集 |
| `test_build_sidecar_creates_three_files` | 用 `mock_kb` fixture 调 `build_sidecar` | `.metadata/chunks.json` / `org_disease_coverage.json` / `synonym_map.yaml` 都存在且 valid JSON/YAML |
| `test_build_sidecar_skips_existing_synonym_map` | 先写一个自定义 yaml，再 build | yaml 内容不被覆盖 |
| `test_coverage_derived_from_chunks` | 构造 chunks.json 含 NCCN×gastric/colorectal, ESMO×gastric | coverage 聚合正确，sorted unique |
| `test_chunk_disease_tags_from_filename` | 文件名 `NCCN-gastric-2026.md` | chunks_meta[path].disease_tags == ["gastric"] |
| `test_chunk_disease_tags_empty_when_no_match` | 文件名 `NCCN-rarecancer-2026.md` | `== []`（KBM-06） |

---

### 2.6 `$MEDICAL_GUIDELINES_DIR/.metadata/synonym_map.yaml`（CREATE，运行时种子）

**Analog：`docs/refactor_plan_2026-05-11.md:192-218` 完整样例**

直接搬用以下 3 个 canonical_key（refactor_plan 文档已给）：

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

**补齐到 10 个 canonical_key**（D-08，planner 在 plan 中具体落定别名列表）：
`esophageal / hepatic / pancreatic / breast / lung / cervical / lymphoma`。每个 ≥5 个中英文同义词，可参考 `scripts/batch_pipeline.py:400-408` 现有 `_DISEASE_KEYWORD_MAP`（虽然词条偏少但方向正确）：

```python
_DISEASE_KEYWORD_MAP = {
    "胃": ["gastric", "stomach", "胃"],
    "肺": ["lung", "pulmonary", "肺"],
    "乳腺": ["breast", "乳腺"],
    "结直肠": ["colorectal", "colon", "rectal", "结直肠", "结肠", "直肠"],
    "肝": ["liver", "hepat", "肝"],
    "食管": ["esophag", "食管"],
    "胰腺": ["pancrea", "胰腺"],
}
```

**D-12 落点约束**：写在 `$MEDICAL_GUIDELINES_DIR/.metadata/synonym_map.yaml`，**不入仓**。`.gitignore` 不需要新增条目（整个 `$MEDICAL_GUIDELINES_DIR` 在仓库外）。

---

## 3. Async-Pattern Recommendations（仓库无先例，需建立）

仓库 `grep "httpx|asyncio|async def|aiohttp" scripts/ tests/` 全为空。Phase 1 是第一次引入异步代码，以下模式由本 PATTERNS.md 锚定，Phase 2/3 继承。

### 3.1 `httpx.AsyncClient` 生命周期

```python
class AsyncQMDService:
    def __init__(
        self,
        port: int | None = None,
        *,
        timeout_s: float = 60.0,
        semaphore: asyncio.Semaphore | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.port = port or int(os.environ.get("QMD_PORT", "8181"))
        self.base_url = f"http://localhost:{self.port}/mcp"
        self._timeout = timeout_s
        self._sem = semaphore or asyncio.Semaphore(8)        # D-03 默认 8，可注入
        self._http = http_client                              # 注入式，None 时 __aenter__ 内建
        self._owns_http = http_client is None
        self.process: subprocess.Popen | None = None
        self._session_id: str | None = None

    async def __aenter__(self) -> "AsyncQMDService":
        self._check_port_available()                          # 同步复用 lines 155-161
        self.process = subprocess.Popen(...)                  # D-02：子进程仍同步起
        if self._owns_http:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        try:
            await self._wait_for_ready_async(timeout=30)
        except Exception:
            if self.process and self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=3)
            self.process = None
            if self._owns_http and self._http is not None:
                await self._http.aclose()
            raise
        return self

    async def __aexit__(self, *exc):
        self._session_id = None
        if self._owns_http and self._http is not None:
            await self._http.aclose()
        # 子进程 terminate 走与同步版一致的 5s + kill 兜底
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.process = None
```

**关键决策**：`http_client` 注入参数让 Phase 3 `pipeline.py` 把 LLM + QMD 共用同一个 `httpx.AsyncClient` 以复用连接池（refactor_plan §四.3 示意）。`_owns_http` 标志保证只有自建的 client 才在 `__aexit__` 关闭。

### 3.2 `asyncio.Semaphore` 注入模式

```python
# 单元测试 / 独立调用：
async with AsyncQMDService() as svc:                          # 内部新建 Semaphore(8)
    ...

# Phase 3 pipeline.py 共享：
sem_qmd = asyncio.Semaphore(opts.concurrency_qmd)
async with AsyncQMDService(semaphore=sem_qmd) as qmd, \
           httpx.AsyncClient() as http:
    llm = AsyncLLMClient(profile, http, sem=asyncio.Semaphore(opts.llm_concurrency))
    ...
```

**query/search 主体内 sem 使用**：

```python
async def query(self, text: str, top_k: int = 10, min_score: float = 0.3) -> list[dict]:
    async with self._sem:
        return await self._post_with_retry({...payload...}, op="query")
```

### 3.3 Session-id 失效 retry（D-04）

```python
async def _post_with_retry(self, payload: dict, op: str) -> list[dict]:
    resp = await self._http.post(self.base_url, headers=self._session_headers, json=payload)
    if resp.status_code == 400 or not resp.headers.get("mcp-session-id"):
        # 失效：重新 initialize，再发原请求一次
        await self._reinitialize_session()
        resp = await self._http.post(self.base_url, headers=self._session_headers, json=payload)
    resp.raise_for_status()
    return QMDService._parse_mcp_response(resp.json())        # staticmethod 直接复用
```

**注意**：D-04 明确"同一 `_async_query()` 内 retry 一次"，不允许无限循环；超时（`httpx.ReadTimeout`，timeout=180s）直接上抛由 Phase 3 调用方处理。

### 3.4 `pytest-asyncio` 配置

**新增依赖**：`pip install pytest-asyncio`（CONTEXT.md Claude's Discretion 已建议）

**conftest.py 或 pyproject.toml 配置**（推荐 strict mode 避免标记泄漏）：

```ini
# pytest.ini 或 pyproject.toml [tool.pytest.ini_options]
asyncio_mode = strict
```

或在 `tests/conftest.py` 加：

```python
import pytest_asyncio
pytestmark = pytest.mark.asyncio
```

**单测装饰器**：

```python
@pytest.mark.asyncio
@patch("scripts.retriever.subprocess.Popen")
async def test_async_qmd_lifecycle(mock_popen):
    ...
    async with AsyncQMDService(port=9999) as svc:
        ...
```

**Mock async HTTP**：用 `unittest.mock.AsyncMock`（Python 3.8+ 标准库），不需额外依赖：

```python
from unittest.mock import AsyncMock, MagicMock, patch

@patch("scripts.retriever.httpx.AsyncClient")
async def test_session_retry(mock_client_cls):
    mock_client = AsyncMock()
    mock_client.post.side_effect = [
        MagicMock(status_code=200, headers={"mcp-session-id": "s1"}, json=lambda: {"result": {}}),
        MagicMock(status_code=400, headers={}, json=lambda: {"error": "expired"}),
        MagicMock(status_code=200, headers={"mcp-session-id": "s2"}, json=lambda: {"result": {}}),  # re-init
        MagicMock(status_code=200, headers={"mcp-session-id": "s2"}, json=lambda: {...results...}), # retry
    ]
    mock_client_cls.return_value.__aenter__.return_value = mock_client
    ...
```

---

## 4. Shared Patterns（跨多个新文件复用）

### 4.1 Path-typed signatures + `from __future__ import annotations`

**Source：`scripts/extraction/postprocess.py:3` + `scripts/retriever.py:12`**

```python
from __future__ import annotations
```

Apply to：`scripts/kb_metadata.py`、`tests/test_retriever_async.py`、`tests/test_kb_metadata.py`、`scripts/retriever.py`（已有，保留）。

### 4.2 KB 路径解析复用 `resolve_kb_root`

**Source：`scripts/batch_pipeline.py` 顶层 `resolve_kb_root` 函数（由 `tests/test_resolve_kb.py` 覆盖）**

`build_sidecar(kb_root, orgs_found)` 接受**已解析的 `Path`**，不要在 kb_metadata.py 内再做 env 解析 — 沿用 cmd_index 上游已调用 `resolve_kb_root(getattr(args, "kb_root", None))` 的结果。

### 4.3 pytest fixture 重用 `mock_kb`

**Source：`tests/conftest.py:82-115`**

`test_kb_metadata.py` 内 `test_build_sidecar_creates_three_files(mock_kb)` 直接接收 fixture，无需再造 KB 目录树。

### 4.4 `subprocess.run(..., check=True)` 错误传播 + Metal exit-code 134 特例

**Source：`scripts/batch_pipeline.py:1297-1299`**

```python
result = subprocess.run(embed_cmd)
if result.returncode not in (0, 134):  # 134 = Metal GPU exit crash (macOS, benign)
    raise subprocess.CalledProcessError(result.returncode, embed_cmd)
```

**Apply to**：cmd_index 修改时**不要触碰这一行**。`build_sidecar` 调用应放在 embed 步骤**之后**且包在 try/except 内并不阻断 index 流程（如果侧车写盘失败，已建索引不应回滚）— 但具体策略留 planner 决定（也可以选择失败即报错，以保 D-05"一次性原子产出"）。

---

## 5. Gotchas / Landmines（读代码时发现的暗坑）

### 5.1 `mcp-session-id` header 是**小写**（retriever.py:181）

```python
self._session_id = resp.headers.get("mcp-session-id")    # 不是 "Mcp-Session-Id"
```

`httpx` 与 `requests` 都对 header 名称大小写不敏感（按 RFC 7230），但**直接字典访问时必须用小写 key**。Async 版抄写时保留小写。

### 5.2 `subprocess.Popen` 启动后**必须 poll 检查存活**（retriever.py:167-170, 179-180）

`_wait_for_ready` 在两处检查 `self.process.poll()`：(a) 每轮询前；(b) 收到 200 之后再确认一次。原因：QMD 在 health 200 后可能 race-condition 立即退出（首次启动加载 embeddinggemma-300M ~313MB GGUF 时偶发）。Async 版必须保留**双重 poll**。

### 5.3 Metal GPU exit-code 134 在 cmd_index 中**特例放过**（batch_pipeline.py:1298）

macOS Apple Silicon 上 `qmd embed` 完成后偶发 `SIGABRT (exit 134)`，是 Metal shader 清理时的良性退出。任何 wrapping `subprocess.CalledProcessError` 都必须显式 allow 134。

### 5.4 `qmd ls` URL 归一化会**小写文件名 + 加 org 前缀**（batch_pipeline.py:1320-1328）

```python
ls_result = subprocess.run(["qmd", "ls", org_name], capture_output=True, text=True)
for line in ls_result.stdout.splitlines():
    parts = line.split()
    if not parts or not parts[-1].startswith("qmd://"):
        continue
    url = parts[-1]                                           # qmd://nccn/nccn-gastric-2026.v2_en.md
    stem = url.split("/")[-1].removesuffix(".md")             # nccn-gastric-2026.v2_en
    prefix = org_name.lower() + "-"                            # "nccn-"
    if stem.lower().startswith(prefix):
        stem = stem[len(prefix):]                             # gastric-2026.v2_en
```

**Implication for `kb_metadata.build_sidecar`**：用文件名启发式推断 `disease_tags` 时，必须**对齐这套归一化**（lower + 剥离 `<org>-` 前缀），否则 chunks.json 的 `file_path` key 与 query 时 QMD 返回的 hit.path 不匹配，下游 filter_chunks_by_disease 全部失效。**推荐**：`build_sidecar` 内复用同一段 stem 剥离逻辑（或直接 import 一个共享 helper）。

### 5.5 `_wait_for_ready` 的 `ConnectionError` 静默吞噬（retriever.py:183-184）

```python
except requests.ConnectionError:
    pass
```

Async 版应 catch `httpx.ConnectError`（注意不是 `ConnectionError`）— 这是 `httpx` 的不同异常体系。漏掉会导致启动等待循环里 ConnectError 直接抛出，覆盖 QMD 真正崩溃的诊断信号。

### 5.6 D-01 「单一异步真相」与 RTR-04 「同步测试不修改」冲突

详见 §2.1 末尾的"决策歧义点"。**Planner 必须在 PLAN.md 中显式选定路线**，否则会同时违反两个硬约束之一。本 PATTERNS.md **推荐**走 "AsyncQMDService 用 httpx；同步 QMDService 保留独立 requests 实现，不互相调用"路线（D-01 释义为"caller 一律用 async"），并在 PLAN.md 中明确这是对 D-01 的实施层取舍。

### 5.7 chunks.json 的 file_path key 选择

文件路径有三种候选作为 chunks.json 的 key：
1. 绝对路径 `$KB_ROOT/NCCN/extracted/NCCN_Gastric_2026.md`
2. 相对 KB_ROOT 的路径 `NCCN/extracted/NCCN_Gastric_2026.md`
3. QMD 归一化后的 URL `qmd://nccn/nccn-gastric-2026.md`

QMD query 返回的 `hit["path"]` 是 **(3) 形式**（参考 retriever.py:205 `r.get("file", "")` 透传 QMD 原值，已在 §5.4 印证）。**推荐**：chunks.json 用 (3) 作为 key，否则 chunk-level filter 必须在每次查询时做 path 转换。Planner 在 PLAN.md 中必须显式决策（refactor_plan §四.4 schema 写的是 `{file_path: ...}` 但没指定哪种形式）。

---

## 6. No-Analog Notes

| File | Reason | 替代来源 |
|------|--------|----------|
| `AsyncQMDService` 的异步 HTTP I/O 部分 | 仓库无 `httpx` / `asyncio` 先例 | refactor_plan §四.2 + §3 本文档 + httpx 官方文档（`AsyncClient` 生命周期） |
| `pytest-asyncio` 配置 | 仓库 `pyproject.toml` 当前无 asyncio_mode | 社区标准 `asyncio_mode = strict` + 装饰器 `@pytest.mark.asyncio` |

---

## 7. Metadata

**Analog search scope**：`scripts/`、`scripts/extraction/`、`tests/`、`docs/refactor_plan_2026-05-11.md`、`.planning/`
**Files scanned**：30+（包括完整 `scripts/retriever.py`、`scripts/batch_pipeline.py` lines 400-432 + 1240-1340、`tests/test_retriever.py`、`tests/test_qmd_integration.py`、`tests/test_features.py`、`tests/test_resolve_kb.py`、`tests/test_index.py`、`tests/conftest.py`、`scripts/extraction/postprocess.py`、`docs/refactor_plan_2026-05-11.md` 全文、`.planning/ROADMAP.md`、`.planning/REQUIREMENTS.md`、`.planning/phases/01-*/01-CONTEXT.md`、`CLAUDE.md`）
**Async grep result**：`scripts/` + `tests/` 下 `httpx|asyncio|async def|aiohttp` **零命中** — Phase 1 是首次引入异步代码
**Pattern extraction date**：2026-05-11
