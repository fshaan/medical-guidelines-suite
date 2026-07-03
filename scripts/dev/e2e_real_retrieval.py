"""离线 E2E：真实 QMD + 真实 KB 元数据 + mock LLM，跑完整 10 例 pipeline。
验证本轮修复的检索/过滤链路在真实数据上端到端产出正确、非空、病种匹配的证据。
"""
import asyncio, json, os, sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import argparse

async def main():
    captured = {"per_patient_evidence": []}

    async def mock_llm_call(messages, schema, **kw):
        user = messages[1]["content"]
        # 统计 prompt 里真实命中的指南文件路径（[n] qmd://... 或 ORG/...）
        import re
        paths = re.findall(r'(?:qmd://|^\[?\d*\]?\s*)([A-Z]+/[^\s]+\.md)', user, re.MULTILINE)
        # 也抓 build_patient_prompt 输出里的 [n] path 行
        cited = re.findall(r'\[\d+\] ([^\n]+) \(score=', user)
        captured["per_patient_evidence"].append({
            "patient_id": kw.get("patient_id"),
            "evidence_paths_in_prompt": cited[:8],
            "evidence_count": len(cited),
            "prompt_chars": len(user),
        })
        # 返回 schema 合法的最小响应（引用第一条证据）
        first_src = cited[0] if cited else "unknown.md"
        return ({
            "guideline_results": [{
                "guideline": "NCCN",
                "guideline_version": "2026.v1",
                "recommendation": "基于检索证据的推荐方案 [1] [2]",
                "evidence_level": "Category 1",
                "source_file": first_src,
                "retrieval_sources": [
                    {"source_file": cited[0] if len(cited)>0 else "a.md", "score": 0.9},
                    {"source_file": cited[1] if len(cited)>1 else "b.md", "score": 0.8},
                ],
            }],
            "consensus": ["共识条目一", "共识条目二"],
            "differences": ["差异条目一", "差异条目二"],
        }, 0.75, "ok")

    mock_llm = MagicMock()
    mock_llm.complete_structured_with_feedback = AsyncMock(side_effect=mock_llm_call)

    with patch("scripts.pipeline.AsyncLLMClient", return_value=mock_llm):
        from scripts.pipeline import run_pipeline
        args = argparse.Namespace(
            patients="Output/patients.json",
            output_dir="Output/",
            llm_profile="qwen3-vllm-lan",
            concurrency_patients=2,
            concurrency_qmd=8,
            resume=False,
            kb_root=os.environ["MEDICAL_GUIDELINES_DIR"],
        )
        os.environ["LLM_BASE_URL"] = "http://mock.local/v1"
        os.environ["LLM_MODEL"] = "mock-model"
        os.environ["LLM_API_KEY"] = "sk-mock"
        exit_code = await run_pipeline(args)

    print("\n" + "="*70)
    print("离线 E2E（真实 QMD + 真实 KB + mock LLM）逐患者证据统计：")
    print("="*70)
    for ev in sorted(captured["per_patient_evidence"], key=lambda x: x["patient_id"] or ""):
        print(f"\n[{ev['patient_id']}] prompt={ev['prompt_chars']}字, 真实证据={ev['evidence_count']}条")
        for p in ev["evidence_paths_in_prompt"][:5]:
            print(f"    {p}")

    rag = json.loads(Path("Output/rag_results.json").read_text(encoding="utf-8"))
    s = rag["summary"]
    print("\n" + "="*70)
    print(f"summary: total={s['total']} ok={s['ok']} partial={s['partial']} "
          f"no_evidence={s['no_evidence']} failed={s['failed']} exit={exit_code}")
    print("="*70)
    return exit_code

ec = asyncio.run(main())
sys.exit(ec)
