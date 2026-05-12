# Phase 3 端到端验收命令清单

本文件固化 Phase 3 ship gate（refactor_plan §九 + ROADMAP success criteria 5 条）。所有命令在真实环境（内网 vLLM + 本机 QMD + 完整 KB）执行，不进 CI。完成后填写下方 Sign-off 表。

## 0. 前置条件检查

在执行验收前，确认以下 4 项全部满足：

1. **vLLM 可达**：
   ```bash
   curl -s ${LLM_BASE_URL}/v1/models | head -5
   ```
   预期：返回 200，JSON 含 `data` 数组。

2. **QMD 索引完整**：
   ```bash
   ls -la $MEDICAL_GUIDELINES_DIR/.metadata/
   ```
   预期：`chunks.json`、`org_disease_coverage.json`、`synonym_map.yaml` 三文件存在。

3. **环境变量已设置**：
   ```bash
   echo "LLM_BASE_URL=$LLM_BASE_URL"
   echo "LLM_MODEL=$LLM_MODEL"
   echo "MEDICAL_GUIDELINES_DIR=$MEDICAL_GUIDELINES_DIR"
   ```
   预期：三个变量非空。

4. **患者数据就位**：
   ```bash
   ls -la Input/2026-4-23.xlsx
   ```
   预期：文件存在。

## 1. 执行 parse → run 流水线（QG-01）

```bash
python3 scripts/batch_pipeline.py parse --input Input/2026-4-23.xlsx --output Output/patients.json
time python3 scripts/batch_pipeline.py run \
  --patients Output/patients.json \
  --output-dir Output/ \
  --llm-profile qwen3-vllm-lan \
  --concurrency-patients 5 \
  --concurrency-qmd 8
```

**验收门**：wall time 必须 < 10 min（QG-01）

**失败诊断**：
- wall time 超时但 QMD/LLM 单调用正常 → 并发未生效，检查 semaphore 注入
- `_failed/` 多于预期 → 查看 `_failed/<pid>.json` 的 `error` 字段判断 transport/schema/build
- Ctrl-C 中断 → `rag_results.json` 应在 finally 块仍生成（D-07 保证）

## 2. 批验证 patients/（QG-02 + QG-04 + QG-05）

```bash
python3 scripts/batch_pipeline.py validate \
  --patients-dir Output/patients/ \
  --patients Output/patients.json
```

**验收门**：
- 输出 0 FAIL，所有 10 例 `citation_coverage >= 0.5` 或 `status == "partial"`（QG-02）
- 全部 `evidence_level` 在 enum 内（QG-04）
- 全部 JSON 解析成功无报错（QG-05）

## 3. 结直肠癌反例校验（QG-03）

```bash
for pid in 贾常山 李学 肖庆周; do
  echo "=== $pid ==="
  jq -r '.result.guideline_results[] | select(.guideline | IN("ESMO","JGCA","CACA")) | .recommendation' Output/patients/${pid}.json 2>/dev/null | grep -c "胃癌" || echo 0
done
```

**验收门**：三次 `grep -c` 输出必须全部为 0（QG-03）

**若任一非 0 → 双层过滤失效**：
1. 检查 `KB/.metadata/chunks.json` 是否含 `colorectal` 标签
2. 检查 `normalize_disease` 是否把"结直肠癌"映射到 `canonical_key="colorectal"`
3. 检查 `filter_chunks_by_disease` 是否被 `_run_one_patient` stage 4 调用

## 4. evidence_level enum 校验（QG-04 二次验证）

```bash
jq -r '.patients[].result.guideline_results[].evidence_level' Output/rag_results.json | sort -u
```

**验收门**：输出每行都在 `PATIENT_RECOMMENDATION_SCHEMA` `evidence_level` enum 内（27 个值）。

## 5. 生成报告（generate --patients-dir）

```bash
python3 scripts/batch_pipeline.py generate \
  --patients-dir Output/patients/ \
  --output-dir Output/ \
  --format md
```

**验收门**：`Output/report.md` 存在，10 例 patient 每例一段，跨指南推荐表格非空。

## 6. pytest 回归（QG-06）

```bash
python3 -m pytest tests/ -q
```

**验收门**：全绿，与现有 286 测试基线 + Plan 03 新增 ~3 测试对齐（预期 289+ tests passing）。

## 7. 旧 batch 子命令 hidden 但仍可调用（CLI-05）

```bash
# 检查 hidden
python3 scripts/batch_pipeline.py --help 2>&1 | grep -E "split|orchestrate|verify-batch|merge" || echo "PASS: 0 hidden descriptions visible"

# 检查仍可调用
python3 scripts/batch_pipeline.py orchestrate --help | head -3
```

**验收门**：
- 第一条：hidden 子命令原始描述文案 0 行可见
- 第二条：`orchestrate --help` 返回正常 help（仍可调用）

---

## Sign-off 表

| 检查项 | 通过 | 实测值 | 备注 |
|--------|------|--------|------|
| wall time < 10min (QG-01) | [ ] | _____ s | |
| 10 例 0 FAIL (QG-02) | [ ] | __ FAIL | |
| 结直肠癌不含"胃癌" (QG-03) | [ ] | grep counts: 贾___ 李___ 肖___ | |
| evidence_level enum (QG-04) | [ ] | __ unique values | |
| 0 JSON 解析错误 (QG-05) | [ ] | __ parse errors | |
| pytest 全绿 (QG-06) | [ ] | __ passed, __ failed | |
| 4 子命令 hidden (CLI-05) | [ ] | __ visible descriptions in help | |

Sign-off by: ___________  Date: ___________

所有检查项 `[x]` 后，Phase 3 ship gate 达成，可触发 Phase 4 stabilize 一周倒计时（refactor_plan §七）。
