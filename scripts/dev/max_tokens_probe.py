"""决定性对比测试：同一 prompt，不同 max_tokens，记录 finish_reason / completion_tokens / 耗时。

目的：验证中断前假设——max_tokens=65536 太大，某些患者上模型 JSON 输出退化
（重复），一直生成到上限才停，单请求几十分钟 → ReadTimeout。

方法：
  1. 从 Output/patients.json 选 1 胃癌 + 1 结直肠癌患者
  2. 复用 pipeline 检索 stage（真实 QMD）拿真实 hits → build_patient_prompt
  3. 对每个 prompt，串行打 vLLM（json_schema strict），max_tokens ∈ 5 档
  4. 记录 finish_reason / completion_tokens / 耗时 / 是否合法 JSON / 重复度

串行（非并发）以保证耗时测量干净，复现"串行单请求也超时"。

用法：
  python3 scripts/dev/max_tokens_probe.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.batch_pipeline import build_queries, extract_patient_features, _synthesize_from_patient
from scripts.kb_metadata import filter_chunks_by_disease, filter_orgs_by_disease, load_synonym_map, normalize_disease
from scripts.llm_client import LLMProfile, PATIENT_RECOMMENDATION_SCHEMA
from scripts.pipeline import _dedupe_hits, _hit_org, _load_kb_metadata, _load_patients, build_patient_prompt
from scripts.retriever import AsyncQMDService


def load_env_file(path: Path) -> None:
    """简版 dotenv：只读 KEY=VALUE，不覆盖已存在的 env。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip("'\"")
        if k and k not in os.environ:
            os.environ[k] = v


PROBE_VALUES = [8192, 16384, 24576, 32768, 65536]
READ_TIMEOUT = 240  # 单请求读超时；65536 退化时可能撞此上限


def repetition_score(text: str) -> float:
    """0=无重复，1=高度重复。把文本按 200 字符分块，1 - unique/total。"""
    if len(text) < 400:
        return 0.0
    chunk = 200
    chunks = [text[i : i + chunk] for i in range(0, len(text), chunk)]
    if len(chunks) < 2:
        return 0.0
    return 1.0 - len(set(chunks)) / len(chunks)


async def get_messages(qmd, patient, synonym_map, chunks_meta, coverage):
    """复刻 _run_one_patient 的检索 stage（stage 1-5），返回 (messages, hit_count, canonical)。"""
    disease_type = patient.get("disease_type") or _synthesize_from_patient(patient, "disease_type")
    patient = {**patient, "disease_type": disease_type}
    features = extract_patient_features(patient)
    queries = build_queries(patient, features)
    canonical = normalize_disease(patient.get("disease_type"), synonym_map)
    hits_per_query = await asyncio.gather(*[qmd.query(q) for q in queries])
    hits = _dedupe_hits([h for sub in hits_per_query for h in sub])
    allowed_orgs = filter_orgs_by_disease(coverage, canonical)
    hits = [h for h in hits if _hit_org(h).lower() in allowed_orgs]
    hits = filter_chunks_by_disease(hits, chunks_meta, canonical)
    messages = build_patient_prompt(patient, hits)
    user_chars = len(messages[1]["content"])
    return messages, len(hits), canonical, user_chars


async def probe_one(http, messages, profile, max_tokens):
    """单次 vLLM 调用（绕过 AsyncLLMClient 重试，拿原始响应 + 精确耗时）。"""
    payload = {
        "model": profile.model,
        "messages": messages,
        "temperature": profile.temperature,
        "max_tokens": max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "patient_recommendation",
                "schema": PATIENT_RECOMMENDATION_SCHEMA,
                "strict": True,
            },
        },
    }
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get(profile.api_key_env, "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url = f"{profile.base_url.rstrip('/')}/chat/completions"
    t0 = time.monotonic()
    record = {"max_tokens": max_tokens}
    try:
        resp = await http.post(
            url, json=payload, headers=headers,
            timeout=httpx.Timeout(READ_TIMEOUT, connect=10),
        )
        record["elapsed_s"] = round(time.monotonic() - t0, 1)
        record["http_status"] = resp.status_code
        if resp.status_code != 200:
            record["error"] = resp.text[:300]
            return record
        data = resp.json()
        choice = data["choices"][0]
        content = choice.get("message", {}).get("content", "") or ""
        usage = data.get("usage", {})
        record["finish_reason"] = choice.get("finish_reason")
        record["completion_tokens"] = usage.get("completion_tokens")
        record["prompt_tokens"] = usage.get("prompt_tokens")
        record["content_chars"] = len(content)
        record["repetition"] = round(repetition_score(content), 2)
        try:
            json.loads(content)
            record["json_valid"] = True
        except json.JSONDecodeError:
            record["json_valid"] = False
        record["content_head"] = content[:120].replace("\n", " ")
        record["content_tail"] = content[-120:].replace("\n", " ") if content else ""
    except httpx.ReadTimeout:
        record["elapsed_s"] = round(time.monotonic() - t0, 1)
        record["error"] = f"ReadTimeout (>{READ_TIMEOUT}s)"
    except httpx.RequestError as e:
        record["elapsed_s"] = round(time.monotonic() - t0, 1)
        record["error"] = f"{type(e).__name__}: {e}"
    return record


def pick_patients(patients):
    """选 1 胃癌 + 1 结直肠癌（按合成的 disease_type）。"""
    gastric = None
    colorectal = None
    for p in patients:
        dt = p.get("disease_type") or _synthesize_from_patient(p, "disease_type") or ""
        if "胃" in dt and gastric is None:
            gastric = p
        elif ("结肠" in dt or "直肠" in dt) and colorectal is None:
            colorectal = p
    chosen = [x for x in (gastric, colorectal) if x]
    return chosen


async def main():
    load_env_file(ROOT / ".env")
    kb_root = Path(os.environ.get("MEDICAL_GUIDELINES_DIR", "."))
    patients = _load_patients(Path("Output/patients.json"))
    chosen = pick_patients(patients)
    if not chosen:
        print("[ERROR] 未能从 patients.json 选出代表性患者", file=sys.stderr)
        return 1

    probe = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else PROBE_VALUES
    profile = LLMProfile.from_env()
    synonym_map = load_synonym_map(kb_root)
    chunks_meta, coverage = _load_kb_metadata(kb_root)

    print(f"profile={profile.name} model={profile.model} base={profile.base_url}")
    print(f"read_timeout={READ_TIMEOUT}s  probe_values={probe}\n")

    all_results = []
    async with httpx.AsyncClient() as http:
        async with AsyncQMDService(http_client=http) as qmd:
            for p in chosen:
                pid = p.get("patient_id", "?")
                pname = p.get("patient_name", "?")
                dt = p.get("disease_type") or _synthesize_from_patient(p, "disease_type")
                messages, hit_n, canonical, user_chars = await get_messages(
                    qmd, p, synonym_map, chunks_meta, coverage
                )
                print(f"=== [{pid}] {pname} (disease={dt}, canonical={canonical})")
                print(f"    hits={hit_n}  prompt_user_chars={user_chars}")
                if hit_n == 0:
                    print("    [SKIP] 无检索证据，跳过此患者\n")
                    continue
                for mt in probe:
                    rec = await probe_one(http, messages, profile, mt)
                    rec["patient_id"] = pid
                    rec["patient_name"] = pname
                    rec["disease"] = dt
                    rec["hit_count"] = hit_n
                    all_results.append(rec)
                    fr = rec.get("finish_reason", "—")
                    ct = rec.get("completion_tokens", "—")
                    el = rec.get("elapsed_s", "—")
                    jv = "✓" if rec.get("json_valid") else "✗"
                    rep = rec.get("repetition", "—")
                    err = rec.get("error", "")
                    tag = f"  ERR={err[:40]}" if err else ""
                    print(f"    mt={mt:>5}  finish={fr:<8} comp_tok={ct:>6}  "
                          f"elapsed={el:>5}s  json={jv}  rep={rep}{tag}")
                print()

    out = ROOT / "Output" / f"max_tokens_probe_{int(time.time())}.json"
    out.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
