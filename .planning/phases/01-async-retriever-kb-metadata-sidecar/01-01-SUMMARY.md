---
phase: 01
plan: 01
subsystem: retriever
tags: [async, httpx, retriever, mcp, qmd]
dependency_graph:
  requires: []
  provides: [AsyncQMDService, test_retriever_async.py]
  affects: [scripts/retriever.py]
tech_stack:
  added: [httpx>=0.27, pytest-asyncio>=0.23]
  patterns: [async-context-manager, semaphore-gating, session-retry]
key_files:
  created:
    - requirements.txt
    - tests/test_retriever_async.py
  modified:
    - scripts/retriever.py
decisions:
  - "D-01 释义：AsyncQMDService 独立 httpx 路径，同步 QMDService 保留 requests；不做物理代码合并"
  - "D-03 默认并发 Semaphore(8)，注入式 semaphore 参数可覆盖"
  - "D-04 session retry 仅一次：400 或缺 header 触发 re-initialize + 重发"
  - "_parse_mcp_response 作为 QMDService staticmethod 被 AsyncQMDService 复用"
metrics:
  duration: 149s
  completed: 2026-05-11
  tasks: 3
  files_changed: 3
  tests_added: 7
  tests_total: 176
---

# Phase 1 Plan 01: Async Retriever + 依赖管理 Summary

httpx.AsyncClient 异步 QMD 客户端，asyncio.Semaphore 并发门控，与同步 QMDService 双路径共存。

## Commits

| Commit  | Message                                                    |
|---------|------------------------------------------------------------|
| 56bb85e | chore(01-01): add requirements.txt with httpx and pytest-asyncio |
| ab867d3 | feat(01-01): add AsyncQMDService with httpx + asyncio.Semaphore |
| 5f73693 | test(01-01): add 7 async retriever tests covering B-1..B-7 |

## AsyncQMDService 公开契约

```python
class AsyncQMDService:
    def __init__(self, port=None, *, timeout_s=60.0, semaphore=None, http_client=None): ...
    async def __aenter__(self) -> AsyncQMDService: ...
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None: ...
    async def query(self, text: str, top_k=10, min_score=0.3) -> list[dict]: ...
    async def search(self, text: str, top_k=10) -> list[dict]: ...
```

## 与同步 QMDService 并存策略

- **AsyncQMDService**：`httpx.AsyncClient` 真异步，Semaphore 门控并发
- **QMDService**：`requests` 同步路径，4 个方法体零修改（字节级一致）
- 两条独立 HTTP 路径，互不调用
- 协议常量 `_MCP_HEADERS` / `_INIT_PAYLOAD` + `_parse_mcp_response` staticmethod 跨类共享
- D-01 释义：调用方层面 async-only（Phase 3 pipeline.py），同步类作为 BC stub

## 测试覆盖矩阵

| Case | 行为                                   | 覆盖点                                  |
|------|----------------------------------------|----------------------------------------|
| B-1  | 生命周期                               | Popen 启动 + session_id 抓取 + terminate |
| B-2  | 端口冲突                               | QMDStartupError，Popen 不被调用         |
| B-3  | 启动失败回滚                           | process.kill + http_client.aclose       |
| B-4  | Semaphore 限流                         | Semaphore(2) 限制 5 并发 <= 2 in-flight |
| B-5  | Session 失效重试                       | 400 → re-initialize → 重发一次          |
| B-6  | 并发顺序                               | gather(q1,q2,q3) 保序                   |
| B-7  | 已崩溃进程退出                         | __aexit__ 不调 terminate                |

## 已知 Trade-off

- ~80 LOC sync/async lifecycle 代码重复（`__aexit__`、`_wait_for_ready` 骨架）
- 保留同步类的唯一理由是 tests/test_retriever.py 的 `@patch("scripts.retriever.requests.post")` 契约
- Phase 3 后若全调用方已迁到 async，可删除同步类

## Phase 3 接入提示

```python
shared_http = httpx.AsyncClient(timeout=60)
sem = asyncio.Semaphore(opts.concurrency_qmd)
async with AsyncQMDService(port=8181, semaphore=sem, http_client=shared_http) as svc:
    results = await asyncio.gather(*[svc.query(q) for q in queries])
```

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check: PASSED

- requirements.txt: FOUND
- scripts/retriever.py: FOUND (contains `class AsyncQMDService`)
- tests/test_retriever_async.py: FOUND
- Commit 56bb85e: FOUND
- Commit ab867d3: FOUND
- Commit 5f73693: FOUND
- git diff tests/test_retriever.py tests/test_qmd_integration.py: EMPTY
- pytest tests/ -q: 176 passed, 4 skipped
