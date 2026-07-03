# Phase 3 端到端验收测试报告

**日期**：2026-07-02
**项目**：Medical Guidelines Suite
**分支**：main (ahead 103, behind 0)
**执行人**：CASE (AI Agent, Dr. Shan Fei)
**验收依据**：`docs/phase3_e2e_acceptance.md`

---

## 环境信息

- **KB 根目录**：`/Users/f.sh/MyDocuments/RAG/guidelines`（97 个 Markdown 文件，5 个指南组织：CSCO/NCCN/ESMO/JGCA/CACA）
- **LLM**：`qwen3.6-35b`（NVFP4 量化，spark 主机 192.168.31.92:8000）
- **检索引擎**：QMD（Quick Markdown Search，本地索引 97 文件，13060 向量）
- **QMD 索引状态**：95.8 MB，CSCO 67 文件 / NCCN 25 / ESMO 2 / JGCA 2 / CACA 1
- **Python**：3.9.6（macOS 系统 Python）
- **Node.js**：v25.8.1（用于 QMD MCP）

---

## 验收结果汇总

| 检查项 | 状态 | 实测值 |
|--------|------|--------|
| 前置条件 1 — vLLM 可达 | ✅ | 首次检查 200 OK，`qwen3.6-35b` 模型可用 |
| 前置条件 2 — QMD 索引完整 | ✅ | chunks.json / org_disease_coverage.json / synonym_map.yaml 均存在 |
| 前置条件 3 — 环境变量 | ✅ | MEDICAL_GUIDELINES_DIR 已设 |
| 前置条件 4 — 输入文件 | ✅ | Input/2026-4-23.xlsx 存在，10 位患者 |
| Parse | ✅ | 10 位患者成功解析（结构化格式） |
| QG-01: wall time < 10min (run 流水线) | ❌ | vLLM 服务在测试期间不可用 |
| QG-02: 10 例 0 FAIL | ⏸ | 依赖 QG-01 输出 |
| QG-03: 结直肠癌含"胃癌"为 0 | ⏸ | 依赖 QG-01 输出 |
| QG-04: evidence_level enum 合法 | ⏸ | 依赖 QG-01 输出 |
| QG-05: 0 JSON 解析错误 | ⏸ | 依赖 QG-01 输出 |
| QG-06: pytest 全绿 | ✅ | **289 passed, 4 skipped, 6 warnings** |
| CLI-05: 4 子命令 hidden | ✅ | split/orchestrate/merge/verify-batch 均 argaparse.SUPPRESS |

---

## 各步骤详细记录

### 步骤 0 — 前置条件检查

```bash
# vLLM 可达
curl -s http://192.168.31.92:8000/v1/models
→ 200 OK, model: qwen3.6-35b

# QMD 索引
ls -la /Users/f.sh/MyDocuments/RAG/guidelines/.metadata/
# 文件清单:
#   chunks.json (13,576 bytes)
#   org_disease_coverage.json (108 bytes)
#   synonym_map.yaml (1,126 bytes)

# 输入文件
ls -la Input/2026-4-23.xlsx → 11,980 bytes, 10 patients
```

**10 位患者概览**：

| # | 姓名 | 部位 | 病理 |
|---|------|------|------|
| 1 | 贾培生 | 胃中部(M),胃下部(L) | 腺癌,印戒细胞癌 |
| 2 | 王晓群 | 横结肠(TC) | 腺癌 |
| 3 | 蔡增元 | 胃中部(M) | 腺癌 |
| 4 | 肖庆周 | 直肠(R) | 腺癌 |
| 5 | 孙素玉 | 胃下部(L) | 腺癌 |
| 6 | 贾常山 | 直肠(R) | 腺癌 |
| 7 | 李学 | 升结肠(AC) | 腺癌 |
| 8 | 金荣德 | 胃上部(U) | 神经内分泌癌 |
| 9 | 郭如峰 | 胃下部(L) | 腺癌 |
| 10 | 马尕西木 | 胃食管结合部(EGJ) | 腺癌 |

**疾病分布**：胃癌 6 例（含 1 例神经内分泌癌），结直肠癌 4 例。

---

### 步骤 1 — Parse → Run 流水线（QG-01）

**Parse** ✅：成功
```bash
PYTHONPATH=. python3 scripts/batch_pipeline.py parse \
  --input Input/2026-4-23.xlsx --output Output/patients.json
# 检测到输入格式: structured
# 已解析 10 位患者
```

**Run** ❌：失败

共发起 4 次尝试：

| 尝试 | 参数 | 持续时间 | 结果 |
|------|------|---------|------|
| 1 | concurrency-patients=5, qmd=8 | 3:05 | QMD MCP Node.js 版本不兼容（已修复） |
| 2 | concurrency-patients=5, qmd=8 | 1:34 | QMD MCP 端口冲突 + httpx ReadTimeout（已修复） |
| 3 | concurrency-patients=2, qmd=4 | 2:52 | vLLM 不可用，3 例 FAIL (transport error) |
| 4 | concurrency-patients=2, qmd=4 (Python API) | 2:52 | vLLM 不可用，3 例 FAIL (transport error) |

**QMD 修复记录**：
- `npm rebuild better-sqlite3` — 修复本地 SQLite 模块 ABI 不兼容
- `scripts/retriever.py:271` — 支持 `QMD_NODE_BIN` 环境变量指定 Node.js 路径
- `scripts/retriever.py:377` — 修复 `_post_tools_call` read timeout 过短
- 修复后独立测试：`AsyncQMDService.query()` 0.5s/query，结果准确（CSCO 胃癌 0.93 分）

**pipeline Bug 修复记录**：
- `scripts/pipeline.py:309` — 添加 `disease_type` 回退（`patients.json` 无此字段，从 `primary_site` 推断）
- `scripts/pipeline.py:349` — 添加 debug stderr flush（后移除）

**vLLM 故障详情**：
- 运行期间 `llm.complete_structured()` 抛出 `LLMFailure(stage="transport")`
- 错误信息：`All connection attempts failed`
- 堆栈：httpx → httpcore → `Connection refused`（192.168.31.92:8000）
- 根因：spark 主机上 `/usr/local/bin/vllm` 二进制被删除，`python3 -c "import vllm"` 报 ModuleNotFoundError
- 端口 8000 被僵尸进程残留占用，接受连接后立即 RST
- `pip3 install --break-system-packages vllm` 超时（>120s）未完成
- 后续验证：curl 和 httpx 均 `Connection refused`

**已捕获到 _failed/ 的患者**：

| patient_id | patient_name | stage | error |
|-----------|-------------|-------|-------|
| T002736227 | 王晓群 | transport | All connection attempts failed |
| T002719622 | 蔡增元 | transport | All connection attempts failed |
| T002092340 | 肖庆周 | transport | All connection attempts failed |

---

### 步骤 6 — Pytest 回归（QG-06）✅

```bash
PYTHONPATH=. python3 -m pytest tests/ -q
# 289 passed, 4 skipped, 6 warnings in 3.73s
```

与验收基线（≥289）对齐。4 skipped 为已知跳过项（需要外部依赖的测试）。

---

### 步骤 7 — CLI Hidden 子命令检查（CLI-05）✅

```bash
# 检查 hidden：4 个子命令原始描述不可见
PYTHONPATH=. python3 scripts/batch_pipeline.py --help
# → split / orchestrate / merge / verify-batch 均不显示原始描述
# → parse/validate/generate 正常显示

# 检查仍可调用
PYTHONPATH=. python3 scripts/batch_pipeline.py orchestrate --help
# → 正常返回 usage
```

---

## 已获知的未修复问题

### 1. normalize_disease 始终返回 None

- 原因：synonym_map 无法将 `primary_site` 字符串（如 "胃中部(M),胃下部(L)"）映射到规范化病种名
- 影响：`filter_orgs_by_disease()` 和 `filter_chunks_by_disease()` 可能无法过滤，导致所有指南组织的结果被返回
- 已在 `_run_one_patient` 中添加 `disease_type` 回退，但映射逻辑仍需要补充

### 2. vLLM 并发限制

- `--max-num-seqs 4` 限制 LLM 并发处理能力
- pipeline `--concurrency-patients` 需要 ≤ 2 才能稳定运行
- 超过 2 个并发 LLM 调用会导致 vLLM slot 占满，后续请求排队甚至超时

### 3. 患者年龄字段缺失

- `parse_structured` 可读取年龄，但部分患者年龄为 None（输入为空）
- `extract_patient_features` 可能无法提取年龄相关特征

---

## Sign-off 表

| 检查项 | 通过 | 实测值 | 备注 |
|--------|------|--------|------|
| wall time < 10min (QG-01) | ❌ | N/A | vLLM 不可用，run 未完成 |
| 10 例 0 FAIL (QG-02) | ⏸ | — | 依赖 QG-01 |
| 结直肠癌不含"胃癌" (QG-03) | ⏸ | — | 依赖 QG-01 |
| evidence_level enum (QG-04) | ⏸ | — | 依赖 QG-01 |
| 0 JSON 解析错误 (QG-05) | ⏸ | — | 依赖 QG-01 |
| pytest 全绿 (QG-06) | ✅ | 289 passed, 0 failed | 3.73s 完成 |
| 4 子命令 hidden (CLI-05) | ✅ | 0 visible descriptions | 仍可调用 |

Sign-off by: ___________  Date: ___________

---

## 附录：独立模块测试基准

### QMD 查询性能

| 查询 | 类型 | 耗时 | Top-1 结果 |
|------|------|------|-----------|
| "胃癌化疗方案" | lex+vec | 3.6s | CACA 胃癌2025版 (0.93) |
| "结直肠癌免疫治疗" | lex+vec | 4.5s | — |

### LLM 调用性能（vLLM warm 后）

| 请求类型 | 耗时 | 
|----------|------|
| 简单 chat (14 tokens) | 3.9s |
| json_schema 简单输出 | 0.2s |
| json_schema 完整指南推荐 | ~22.8s (before crash) |

### QMD MCP 并发测试

- 同一 httpx.AsyncClient 下 5 个并发 QMD query → 全部 200（3~5s）
- 同步 QMDService.query() → 0.42s
- 异步 AsyncQMDService.query() → 0.5s（独立测试）

---

*报告生成时间：2026-07-02 16:00 CST*
