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
    LLMFailure,
    LLMProfile,
    PATIENT_RECOMMENDATION_SCHEMA,
)
from scripts.retriever import AsyncQMDService

# 模块级正则：提取 recommendation 文本中的 [n] 引用编号
_CITATION_RE = re.compile(r"\[(\d+)\]")


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
        "- consensus / differences 各列出 2-4 条\n"
        "- 全部 user-facing 文本使用简体中文"
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
            "total": ok + partial + failed,
            "ok": ok,
            "partial": partial,
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
    return ""
