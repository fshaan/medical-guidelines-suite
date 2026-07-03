"""Patient-level concurrent pipeline orchestrator.

Phase 3 (PIP-01..06): 顶层 asyncio.gather + 患者级 Semaphore，共享 httpx.AsyncClient
注入到 AsyncQMDService 与 AsyncLLMClient。

工具层（Task 1）: compute_citation_coverage / build_patient_prompt / _atomic_write_json /
_merge_rag_results / _scan_resume / _write_failed / _dedupe_hits / _load_patients /
_load_kb_metadata / _hit_org。

编排层（Task 2）: run_pipeline / _run_one_patient。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from scripts.kb_metadata import (
    filter_chunks_by_disease,
    filter_orgs_by_disease,
    load_synonym_map,
    normalize_disease,
)
from scripts.llm_client import (
    AsyncLLMClient,
    DegenerationError,
    LLMFailure,
    LLMProfile,
    PATIENT_RECOMMENDATION_SCHEMA,
)
from scripts.retriever import AsyncQMDService, QMDQueryError

# 模块级正则：提取 recommendation 文本中的 [n] 引用编号
_CITATION_RE = re.compile(r"\[(\d+)\]")


def _format_exc(exc: Exception) -> str:
    """把异常格式化成非空、可诊断的字符串。

    2026-07-03 修复：真实 E2E 里 vLLM 中途重启，`httpx.ConnectError` 的
    message 是空字符串（`str(httpx.ConnectError('')) == ''`），导致
    `_write_failed` 存进 _failed/ 的 error 字段是空的，`FAIL (transport: )`
    完全看不出失败原因。始终带上异常类型名，message 为空时也能定位。
    """
    name = type(exc).__name__
    msg = str(exc)
    return f"{name}: {msg}" if msg else name


def _atomic_write_json(path: Path, data: dict) -> None:
    """tmp + rename 原子写：同目录写 .tmp 文件再 Path.replace（POSIX rename 原子）。

    复制自 kb_metadata.py:236-247，保持一致。
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def compute_citation_coverage(result: dict) -> float:
    """扫 guideline_results[*].recommendation 中的 [n] → unique set / retrieval_sources 总数。

    边界（D-15）：
    - recommendation 无 [n] → coverage = 0
    - retrieval_sources 为空 → coverage = 1（无可引用即视为不缺）
    - guideline_results 缺失或空 → coverage = 0.0
    """
    cited: set = set()
    total_sources = 0
    guideline_results = result.get("guideline_results")
    if not guideline_results:
        return 0.0
    for gr in guideline_results:
        rec = gr.get("recommendation") or ""
        for m in _CITATION_RE.finditer(rec):
            cited.add(int(m.group(1)))
        total_sources += len(gr.get("retrieval_sources") or [])
    if total_sources == 0:
        return 1.0
    return len(cited) / total_sources


def build_patient_prompt(patient: dict, retrieval_hits: List[dict]) -> List[dict]:
    """构造单患者的 system + user 双消息（D-14）。

    - system: 医学 persona + 简体中文约束 + 引用编号约束
    - user: 患者信息 + 检索结果编号 [1] [2] ...
    """
    system = (
        "你是一位资深肿瘤科医生 + 临床指南专家。基于以下指南检索结果，"
        "为该患者生成跨指南循证推荐对比。\n"
        "- 输出 strict JSON，schema 由 response_format 强制\n"
        "- 每条 guideline_results 至少引用 2 个 retrieval_sources 编号 [n]\n"
        "- evidence_level 必须落在 schema enum 内\n"
        "- 每条 recommendation ≤500 字，简明给出核心推荐与关键依据（schema maxLength=600 强制闭合）\n"
        "- consensus / differences 各 2-3 条，每条 ≤120 字\n"
        "- 每条推荐一次性写完，禁止重复同一内容或循环生成\n"
        "- 全部 user-facing 文本使用简体中文\n"
        "- JSON 结构：{\"guideline_results\":[{\"guideline\":\"CSCO|NCCN|ESMO|JGCA|CACA\",\"guideline_version\":\"版本\",\"recommendation\":\"≤500字推荐\",\"evidence_level\":\"证据级别\",\"source_file\":\"来源\",\"retrieval_sources\":[{\"source_file\":\"来源\",\"score\":0.9}]}],\"consensus\":[\"共识\"],\"differences\":[\"差异\"]}"
    )
    user_parts: List[str] = []
    user_parts.append(
        f"## 患者 {patient.get('patient_name', '?')} ({patient.get('patient_id', '?')})\n"
    )
    for k, v in patient.items():
        if k in ("features", "retrieval_results") or v is None:
            continue
        user_parts.append(f"- {k}: {v}")
    user_parts.append("\n## 检索结果\n")
    for i, hit in enumerate(retrieval_hits, 1):
        path = hit.get("path", "")
        score = hit.get("score", 0.0)
        content = hit.get("content", "")
        user_parts.append(f"[{i}] {path} (score={score:.2f})\n{content}\n")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def _write_failed(
    output_dir: Path,
    patient_id: str,
    error: str,
    stage: str,
    last_llm_output: Optional[str],
) -> None:
    """落到 output_dir/_failed/<patient_id>.json，含 5 字段。"""
    failed_dir = output_dir / "_failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    shard = {
        "patient_id": patient_id,
        "error": error,
        "stage": stage,
        "last_llm_output": last_llm_output,
        "attempted_at": datetime.utcnow().isoformat() + "Z",
    }
    _atomic_write_json(failed_dir / f"{patient_id}.json", shard)


def _merge_rag_results(output_dir: Path, wall_time_s: float) -> dict:
    """合并 patients/*.json + _failed/*.json → rag_results.json schema（D-07）。"""
    patients_dir = output_dir / "patients"
    failed_dir = output_dir / "_failed"
    patients: List[dict] = []
    failures: List[dict] = []
    ok = 0
    partial = 0
    failed = 0
    # 2026-07-02：no_evidence 是合法结果（该患者病种在当前 KB 里没有相关
    # 指南），不是故障——单独计数，不计入 failed，不影响 exit_code。
    no_evidence = 0

    for p in sorted(patients_dir.glob("*.json")):
        shard = json.loads(p.read_text(encoding="utf-8"))
        patients.append({
            "id": shard["patient_id"],
            "status": shard["status"],
            "citation_coverage": shard.get("citation_coverage"),
            "result": shard.get("result"),
        })
        if shard["status"] == "ok":
            ok += 1
        elif shard["status"] == "partial":
            partial += 1
        elif shard["status"] == "no_evidence":
            no_evidence += 1

    for p in sorted(failed_dir.glob("*.json")):
        shard = json.loads(p.read_text(encoding="utf-8"))
        failures.append({
            "id": shard["patient_id"],
            "error": shard.get("error"),
            "stage": shard.get("stage"),
        })
        failed += 1

    return {
        "patients": patients,
        "failures": failures,
        "summary": {
            "total": ok + partial + no_evidence + failed,
            "ok": ok,
            "partial": partial,
            "no_evidence": no_evidence,
            "failed": failed,
            "wall_time_s": round(wall_time_s, 2),
        },
    }


def _scan_resume(
    patients: List[dict],
    output_dir: Path,
    resume: bool,
) -> List[dict]:
    """--resume 模式两段扫描（D-08）。

    - resume=False → 返回原 patients 列表
    - resume=True:
      - patients/<pid>.json 存在 → 跳过
      - _failed/<pid>.json 存在 → 加入 to_run 并删除旧 _failed 文件
      - 否则 → 加入 to_run
    """
    if not resume:
        return patients

    patients_dir = output_dir / "patients"
    failed_dir = output_dir / "_failed"
    to_run: List[dict] = []
    for p in patients:
        pid = p.get("patient_id", "")
        done_path = patients_dir / f"{pid}.json"
        fail_path = failed_dir / f"{pid}.json"
        if done_path.exists():
            continue  # 已完成，跳过
        if fail_path.exists():
            fail_path.unlink()  # 删除旧 _failed 文件，准备重跑
        to_run.append(p)
    return to_run


def _dedupe_hits(hits: List[dict]) -> List[dict]:
    """去重：key = hit.get("path","") + hit.get("content","")[:50]，保留首次出现。"""
    seen: set = set()
    out: List[dict] = []
    for hit in hits:
        key = hit.get("path", "") + hit.get("content", "")[:50]
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


def _load_patients(path: Path) -> List[dict]:
    """读 patients.json，返回 data["patients"] 列表。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return data.get("patients", [])


def _load_kb_metadata(kb_root: Path) -> tuple:
    """读 chunks.json + org_disease_coverage.json，缺失返回空 dict。"""
    kb_root = Path(kb_root)
    chunks_meta: dict = {}
    coverage: dict = {}
    chunks_path = kb_root / ".metadata" / "chunks.json"
    coverage_path = kb_root / ".metadata" / "org_disease_coverage.json"
    if chunks_path.exists():
        try:
            chunks_meta = json.loads(chunks_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    if coverage_path.exists():
        try:
            coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return chunks_meta, coverage


def _hit_org(hit: dict) -> str:
    """从 hit["path"] 提取组织名（uppercase）。

    启发式：
    - qmd://<org>/... → 取第二段
    - /path/to/<ORG>/extracted/... → 取 extracted 前一段
    - <ORG>/filename.md（无协议前缀、无 extracted 段）→ 取第一段
      （2026-07-02 修复：真实 AsyncQMDService.query() 返回的 hit["path"] 就是
      这种裸格式，例如 "CACA/caca_....md"——此前只认前两种格式，对真实数据
      恒返回 ""，导致 Stage 4 org 过滤把所有检索结果清空，不管 canonical 是
      什么。真实 E2E 运行验证：修复前 hits 从 31 条被过滤到 0 条）
    - 无法提取 → ""
    """
    path = hit.get("path", "")
    if not path:
        return ""
    # qmd:// protocol
    if path.startswith("qmd://"):
        parts = path[len("qmd://"):].split("/")
        if parts:
            return parts[0].upper()
    # filesystem path with extracted/
    parts = path.split("/")
    for i, seg in enumerate(parts):
        if seg == "extracted" and i >= 1:
            return parts[i - 1].upper()
    # 裸 "<ORG>/filename" 格式（真实 QMD 返回的常见形态）
    if len(parts) >= 2 and parts[0]:
        return parts[0].upper()
    return ""


def _select_diverse_hits(
    hits: List[dict], per_org: int = 3, max_total: int = 15
) -> List[dict]:
    """按组织保底选取 hits，控制喂给 LLM 的上下文规模。

    2026-07-03（codex 审查采纳）：真实 E2E 里单患者过滤后常剩 ~27 hits，全文
    拼进 prompt（11000+ 字）信息过载，是 vLLM/Qwen3.6 结构化输出退化（重复生
    成到 max_tokens 上限）的诱因之一。按 _hit_org 分组、每 org 取 score top-K
    保底，保证 5 个指南组织都有代表（前提是该 org 有命中），再合并按 score 截
    断到 max_total。这样既控总量又避免高分 CSCO/NCCN 挤掉 ESMO/JGCA/CACA。
    """
    if len(hits) <= max_total:
        return hits
    by_org: Dict[str, List[dict]] = {}
    for h in hits:
        by_org.setdefault(_hit_org(h).lower(), []).append(h)
    picked: List[dict] = []
    for org_hits in by_org.values():
        org_hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
        picked.extend(org_hits[:per_org])
    picked.sort(key=lambda h: h.get("score", 0.0), reverse=True)
    return picked[:max_total]


# ---------------------------------------------------------------------------
# 编排层（Task 2）
# ---------------------------------------------------------------------------

# 模块顶部 import（避免每个 patient 重复 import 开销）
from scripts.batch_pipeline import build_queries, extract_patient_features


async def _llm_with_degeneration_fallback(
    llm: AsyncLLMClient, patient: dict, hits: List[dict], pid: str,
) -> tuple[dict, float, str, dict]:
    """三档退化感知降级链（2026-07-03 codex 审查采纳）。

    决定性对比测试证明退化随机触发、max_tokens 不决定是否退化；胃癌 5/5 稳定
    退化说明盲重试是抽奖——必须逐档换策略。三档：
      档1 strict json_schema + 完整 hits（已 _select_diverse_hits 精简到 ≤15）
      档2 strict + 进一步精简 hits(per_org=2) + frequency_penalty=0.3
      档3 json_object 降级（应用层 jsonschema.validate）+ 精简 hits + penalty
    任一档成功即返回 (result, score, status, attempt_meta)；三档全退化抛
    DegenerationError 让上层写 _failed/stage=degeneration。LLMFailure(transport)
    不在此捕获——网络错误换策略无意义，直接穿透到 _run_one_patient 的 except。
    """
    schema = PATIENT_RECOMMENDATION_SCHEMA
    fb_kw = dict(feedback_check=compute_citation_coverage, threshold=0.5, patient_id=pid)
    # 档1: strict + 完整精简 hits
    try:
        r, s, st = await llm.complete_structured_with_feedback(
            build_patient_prompt(patient, hits), schema, **fb_kw,
        )
        return r, s, st, {"attempt": 1, "mode": "strict", "hits": len(hits), "penalty": 0.0}
    except DegenerationError:
        pass
    # 档2: 精简 hits + frequency_penalty 抑制重复
    hits2 = _select_diverse_hits(hits, per_org=2, max_total=8)
    try:
        r, s, st = await llm.complete_structured_with_feedback(
            build_patient_prompt(patient, hits2), schema,
            frequency_penalty=0.3, **fb_kw,
        )
        return r, s, st, {"attempt": 2, "mode": "strict", "hits": len(hits2), "penalty": 0.3}
    except DegenerationError:
        pass
    # 档3: strict→json_object 降级（应用层 jsonschema.validate 兜底）
    r, s, st = await llm.complete_structured_with_feedback(
        build_patient_prompt(patient, hits2), schema,
        frequency_penalty=0.3, structured_mode_override="json_object", **fb_kw,
    )
    return r, s, st, {"attempt": 3, "mode": "json_object", "hits": len(hits2), "penalty": 0.3}


async def _run_one_patient(
    patient: dict,
    *,
    qmd: AsyncQMDService,
    llm: AsyncLLMClient,
    output_dir: Path,
    pat_sem: asyncio.Semaphore,
    synonym_map: dict,
    chunks_meta: dict,
    coverage: dict,
) -> None:
    """单患者子流水线：6 stages 串行（D-02）。

    异常分级（D-05）：
    - LLMFailure → _write_failed (stage=e.stage，一般 "transport"/"schema")
    - QMDQueryError → _write_failed (stage="retrieval")
      （2026-07-02 修复 + codex 对抗式审查后收紧：Stage 3 QMD 查询此前没有
      专属 except，httpx.RequestError 会穿透 _run_one_patient，被 run_pipeline
      里没接收返回值的 gather(..., return_exceptions=True) 静默吞掉——患者
      既不进 patients/ 也不进 _failed/，summary 全 0 但 exit code 仍是 0。
      分类边界现在绑定在 Stage 3 代码位置本身——只在那段 gather 调用外层
      catch httpx.RequestError 并立刻转成 QMDQueryError，而不是在这个共享
      except 块里裸 catch httpx.RequestError；后者理论上会跟 Stage 6 LLM
      调用产生的同类异常混淆，即使 llm_client.py 当前总是把自己的
      RequestError 包成 LLMFailure、实际不会泄漏）
    - KeyError/ValueError → _write_failed (stage="build")
    - asyncio.CancelledError → raise（透传，不写 shard）
    - 无 except Exception 通配（WR-08）
    """
    async with pat_sem:  # D-04: patient-level 限流
        pid = patient.get("patient_id", "unknown")
        pname = patient.get("patient_name", "?")
        t_start = time.monotonic()
        try:
            # Phase 3 fix: patients.json 无 disease_type，从 primary_site 关键词合成
            # （复用 batch_pipeline._synthesize_from_patient 的 _SITE_TO_DISEASE 子串
            # 匹配逻辑，而不是把 primary_site 原始自由文本直接当 disease_type——后者
            # 含全角括号/逗号，normalize_disease 的 tokenizer 只切 -_. 无法匹配）
            from scripts.batch_pipeline import _synthesize_from_patient
            disease_type = patient.get("disease_type") or _synthesize_from_patient(patient, "disease_type")
            patient = {**patient, "disease_type": disease_type}

            # Stage 1: 特征提取（纯函数，从 batch_pipeline 复用）
            features = extract_patient_features(patient)
            queries = build_queries(patient, features)

            # Stage 2: 病种归一化（Phase 1 函数）
            canonical = normalize_disease(patient.get("disease_type"), synonym_map)

            # Stage 3: QMD 并发查询（Phase 1 D-03 sem 自动生效）
            # 局部转换为 QMDQueryError（而不是让裸 httpx.RequestError 传到下面
            # 共享的 except 块）：codex 对抗式审查指出，如果共享 except 直接catch
            # httpx.RequestError，理论上会跟 Stage 6 LLM 调用产生的同类异常混淆
            # （虽然当前 llm_client.py 已把自己的 RequestError 包成 LLMFailure，
            # 不会真的泄漏到这里，但把分类逻辑绑定到"代码位置"而不是"猜异常类型
            # 的来源"更稳，不依赖 llm_client.py 未来不变）。
            try:
                hits_per_query = await asyncio.gather(
                    *[qmd.query(q) for q in queries]
                )
            except httpx.RequestError as e:
                raise QMDQueryError(f"QMD query stage failed: {e}") from e
            hits = _dedupe_hits([h for sub in hits_per_query for h in sub])

            # Stage 4: 双层过滤（Phase 1 D-10）
            # _hit_org() 按既有测试契约返回大写（"NCCN"），侧车 org_disease_coverage.json
            # 的 key 是 build_sidecar() 写入时 .lower() 过的（"nccn"）——比较前必须归一化，
            # 否则不管 canonical 是不是 None，这行永远把所有 hits 过滤成空。
            allowed_orgs = filter_orgs_by_disease(coverage, canonical)
            hits = [h for h in hits if _hit_org(h).lower() in allowed_orgs]
            hits = filter_chunks_by_disease(hits, chunks_meta, canonical)

            # 2026-07-03（codex 审查）：按 org 保底精简，控制喂给 LLM 的上下文
            # 规模，降低结构化输出退化触发（见 _select_diverse_hits）。
            hits = _select_diverse_hits(hits)

            # 2026-07-02 修复（codex 对抗式审查 P0）：零检索证据不能静默喂给
            # LLM。此前即使 hits=[]，Stage 5/6 依然照常执行——LLM 在没有任何
            # 真实指南内容的 prompt 下仍会生成看起来言之有据、引用 CSCO/NCCN
            # 的 JSON（凭训练知识编造，不是真的检索到的内容），而
            # compute_citation_coverage() 对空 retrieval_sources 返回 1.0（满
            # 分），会被当成正常 "ok" shard 写出——对医学指南系统这是不可接受
            # 的静默幻觉风险。改为在真正调用 LLM 前就拦截。
            #
            # 2026-07-02 第二轮修复（codex 对抗式审查）：不应该把这种情况当
            # QMDQueryError/写进 _failed/ 计入 failed——QMD 本身没有失败，只是
            # 这个患者的病种在当前 KB 里确实没有相关指南，这是合法的正常结果
            # （比如罕见病，不是 bug）。原实现会让 QG-02"10 例 0 FAIL"验收门
            # 对这种患者产生假阳性失败。改为写一个独立 status="no_evidence"
            # 的 patients/ shard（不进 _failed/，不计入 failed 计数，退出码
            # 不受影响），跟 LLMFailure/QMDQueryError 等真实故障区分开。
            if not hits:
                wall = time.monotonic() - t_start
                shard = {
                    "patient_id": pid,
                    "status": "no_evidence",
                    "citation_coverage": None,
                    "wall_time_s": round(wall, 2),
                    "result": None,
                    "note": (
                        f"no relevant retrieval hits after org/chunk filtering "
                        f"(canonical={canonical!r}, allowed_orgs={allowed_orgs})"
                    ),
                }
                _atomic_write_json(output_dir / "patients" / f"{pid}.json", shard)
                print(f"[{pid}] {pname} ... NO_EVIDENCE (canonical={canonical!r})", flush=True)
                return

            # Stage 5+6: 退化感知 LLM 调用。prompt 构造（D-14 build_patient_prompt）
            # 内置于 _llm_with_degeneration_fallback 每档，按退化情况切换 hits 规模。
            result, score, status, attempt_meta = await _llm_with_degeneration_fallback(
                llm, patient, hits, pid,
            )

            wall = time.monotonic() - t_start
            shard = {
                "patient_id": pid,
                "status": status,  # "ok" or "partial"
                "citation_coverage": score,
                "wall_time_s": round(wall, 2),
                "result": result,
                "llm_attempt": attempt_meta,  # 哪档成功（审计退化降级路径）
            }
            _atomic_write_json(output_dir / "patients" / f"{pid}.json", shard)
            print(
                f"[{pid}] {pname} ... {status.upper()} ({wall:.1f}s, coverage={score:.2f})",
                flush=True,
            )

        except LLMFailure as e:
            err_str = _format_exc(e.last_error)
            _write_failed(output_dir, pid, err_str, e.stage, last_llm_output=None)
            print(f"[{pid}] {pname} ... FAIL ({e.stage}: {err_str})", flush=True)
        except DegenerationError as e:
            # 2026-07-03（codex 审查）：三档降级链全退化 → 独立失败语义，不伪装
            # partial（QG-02 诚实）。保留 content_preview 供审计退化原文。
            _write_failed(
                output_dir, pid, str(e), stage="degeneration",
                last_llm_output=e.content_preview,
            )
            print(
                f"[{pid}] {pname} ... FAIL (degeneration: "
                f"finish={e.finish_reason} rep={e.repetition:.2f} "
                f"tok={e.completion_tokens})",
                flush=True,
            )
        except QMDQueryError as e:
            _write_failed(output_dir, pid, str(e), stage="retrieval", last_llm_output=None)
            print(f"[{pid}] {pname} ... FAIL (retrieval: {e})", flush=True)
        except (KeyError, ValueError) as e:
            _write_failed(output_dir, pid, str(e), stage="build", last_llm_output=None)
            print(f"[{pid}] {pname} ... FAIL (build: {e})", flush=True)
        except asyncio.CancelledError:
            raise  # D-05: CancelledError 必须向上传播，不写 shard


async def run_pipeline(args: argparse.Namespace) -> int:
    """顶层编排器（PIP-01 + PIP-06）。

    返回退出码：0=全部成功（含 partial），1=有 failure。
    """
    profile = LLMProfile.from_env(name=args.llm_profile)
    patients = _load_patients(args.patients)
    output_dir = Path(args.output_dir).resolve()
    (output_dir / "patients").mkdir(parents=True, exist_ok=True)
    (output_dir / "_failed").mkdir(parents=True, exist_ok=True)

    # Resume 扫描（D-08）
    to_run = _scan_resume(patients, output_dir, resume=args.resume)

    kb_root = Path(args.kb_root or os.environ.get("MEDICAL_GUIDELINES_DIR", "."))
    synonym_map = load_synonym_map(kb_root)
    chunks_meta, coverage = _load_kb_metadata(kb_root)

    wall_start = time.monotonic()

    # D-03: 顶层共享 httpx.AsyncClient —— QMD + LLM 共连接池
    # D-04: 三 sem 互不嵌套
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(profile.timeout_s)
        ) as http:
            qmd_sem = asyncio.Semaphore(args.concurrency_qmd)
            pat_sem = asyncio.Semaphore(args.concurrency_patients)
            async with AsyncQMDService(
                http_client=http,
                semaphore=qmd_sem,
            ) as qmd:
                llm = AsyncLLMClient(
                    profile,
                    http=http,
                    semaphore=asyncio.Semaphore(profile.concurrency),
                )

                results = await asyncio.gather(
                    *[
                        _run_one_patient(
                            p,
                            qmd=qmd,
                            llm=llm,
                            output_dir=output_dir,
                            pat_sem=pat_sem,
                            synonym_map=synonym_map,
                            chunks_meta=chunks_meta,
                            coverage=coverage,
                        )
                        for p in to_run
                    ],
                    return_exceptions=True,  # D-01: 单 patient 失败不击垮 gather
                )
                # 2026-07-02 修复：此前 gather 的返回值没被接收，任何没被
                # _run_one_patient 内部 except 分类到的异常（旧例：QMD 查询
                # 超时）会直接消失——不写 _failed/、不打日志，summary 全 0
                # 但 exit code 仍是 0，看起来像"什么都没发生"。这是保底安全网：
                # isinstance(r, Exception) 天然排除 asyncio.CancelledError
                # （Python 3.8+ 继承自 BaseException 而非 Exception），
                # 保留 D-05 "CancelledError 不写 shard" 的设计。
                for p, r in zip(to_run, results):
                    if isinstance(r, Exception):
                        pid = p.get("patient_id", "unknown")
                        print(
                            f"[{pid}] UNEXPECTED exception escaped _run_one_patient "
                            f"(未被内部 except 分类，视为 bug): {r!r}",
                            file=sys.stderr, flush=True,
                        )
                        _write_failed(output_dir, pid, repr(r), stage="unexpected", last_llm_output=None)
    finally:
        # D-07: Ctrl-C 也要尽力写 rag_results.json
        wall = time.monotonic() - wall_start
        aggregate = _merge_rag_results(output_dir, wall)
        _atomic_write_json(output_dir / "rag_results.json", aggregate)

    # D-06: 退出码
    exit_code = 0 if aggregate["summary"]["failed"] == 0 else 1
    s = aggregate["summary"]
    print(
        f"Total: {s['total']}  OK: {s['ok']}  Partial: {s['partial']}  "
        f"No_evidence: {s['no_evidence']}  Failed: {s['failed']}  "
        f"Wall: {s['wall_time_s']:.1f}s  Exit: {exit_code}",
        flush=True,
    )
    return exit_code
