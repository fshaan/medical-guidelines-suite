"""读取 run 产物，生成查询结果 MD + 任务完成情况 MD。

用法: python3 scripts/dev/gen_report.py <shards_dir> <patients_json>
例:   python3 scripts/dev/gen_report.py Output/new_run Output/patients_new.json
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


def clinical_summary(p: dict) -> str:
    prefix = p.get("staging_prefix") or ""
    stage = f"{prefix}{p.get('t_stage') or ''}{p.get('n_stage') or ''}{p.get('m_stage') or ''}"
    lines = [
        f"**性别/年龄**：{p.get('gender', '?')} {p.get('age', '?')}岁",
        f"**部位**：{p.get('primary_site', '?')}",
        f"**病理**：{p.get('pathology', '?')}",
        f"**分期**：{stage}",
    ]
    if p.get("m_sites"):
        lines.append(f"**转移部位**：{p['m_sites']}")
    if p.get("biopsy_molecular"):
        lines.append(f"**分子分型**：{p['biopsy_molecular']}")
    if p.get("comorbidities") and p["comorbidities"] != "无":
        lines.append(f"**合并症**：{p['comorbidities']}")
    if p.get("prior_treatment") and p["prior_treatment"] != "未提供":
        lines.append(f"**既往治疗**：{p['prior_treatment']}")
    if p.get("response"):
        lines.append(f"**疗效评价**：{p['response']}")
    return "\n".join(lines)


def gen_query_report(shards: list, patients: dict, out: Path) -> None:
    lines = ["# 跨指南检索推荐结果（10 例真实案例）\n"]
    lines.append(f"生成时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    for s in shards:
        pid = s["patient_id"]
        p = patients.get(pid, {})
        status = s.get("status", "?")
        tag = "✅ OK" if status == "ok" else ("⚠️ PARTIAL" if status == "partial" else f"❌ {status}")
        lines.append(f"\n---\n\n## {p.get('patient_name', pid)}（{pid}）  {tag}\n")
        lines.append(clinical_summary(p) + "\n")
        result = s.get("result")
        if not result:
            lines.append(f"\n> 无结果（status={status}）\n")
            continue
        grs = result.get("guideline_results", [])
        if grs:
            lines.append("### 各指南推荐\n")
            for gr in grs:
                g = gr.get("guideline", "?")
                ver = gr.get("guideline_version", "?")
                ev = gr.get("evidence_level", "?")
                rec = gr.get("recommendation", "")
                lines.append(f"**{g}**（{ver}）`{ev}`\n")
                lines.append(f"{rec}\n")
                srcs = gr.get("retrieval_sources", [])
                if srcs:
                    src_str = "、".join(
                        f"{x.get('source_file', '?').split('/')[-1]}(score={x.get('score', 0):.2f})"
                        for x in srcs[:4]
                    )
                    lines.append(f"- 来源：{src_str}\n")
        cons = result.get("consensus", [])
        if cons:
            lines.append("### 共识\n")
            for c in cons:
                lines.append(f"- {c}")
            lines.append("")
        diffs = result.get("differences", [])
        if diffs:
            lines.append("### 差异\n")
            for d in diffs:
                lines.append(f"- {d}")
            lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"查询结果 → {out}")


def gen_stats_report(shards: list, patients: dict, rag: dict, out: Path) -> None:
    lines = ["# 任务完成情况统计\n"]
    s = rag.get("summary", {})
    total = s.get("total", len(shards))
    ok = s.get("ok", 0)
    partial = s.get("partial", 0)
    no_ev = s.get("no_evidence", 0)
    failed = s.get("failed", 0)
    wall = s.get("wall_time_s", 0)
    success = ok + partial + no_ev
    rate = success / total * 100 if total else 0

    lines.append("## 总览\n")
    lines.append(f"- **总患者数**：{total}")
    lines.append(f"- **成功**：{success}（OK {ok}、PARTIAL {partial}、NO_EVIDENCE {no_ev}）")
    lines.append(f"- **失败**：{failed}")
    lines.append(f"- **成功率**：{rate:.1f}%")
    lines.append(f"- **总耗时（wall）**：{wall:.1f}s（{wall/60:.1f} min）")
    if shards:
        wts = [x.get("wall_time_s", 0) for x in shards]
        lines.append(f"- **平均每例**：{statistics.mean(wts):.1f}s")
        lines.append(f"- **中位数**：{statistics.median(wts):.1f}s")
        lines.append(f"- **最快**：{min(wts):.1f}s")
        lines.append(f"- **最慢**：{max(wts):.1f}s")

    lines.append("\n## 逐患者明细\n")
    lines.append("| 案例 | 性别/年龄 | 部位 | 分期 | 状态 | 耗时(s) | coverage | 降级档 |")
    lines.append("|------|----------|------|------|------|---------|----------|--------|")
    for s2 in shards:
        pid = s2["patient_id"]
        p = patients.get(pid, {})
        prefix = p.get("staging_prefix") or ""
        stage = f"{prefix}{p.get('t_stage', '')}{p.get('n_stage', '')}{p.get('m_stage', '')}"
        ga = p.get("gender", "") + str(p.get("age", ""))
        st = s2.get("status", "?")
        w = s2.get("wall_time_s", 0)
        cov = s2.get("citation_coverage")
        cov_str = f"{cov:.2f}" if cov is not None else "—"
        att = s2.get("llm_attempt", {})
        att_str = f"档{att.get('attempt', '?')}({att.get('mode', '?')})" if att else "—"
        lines.append(f"| {p.get('patient_name', pid)} | {ga} | {p.get('primary_site', '')} | {stage} | {st} | {w:.1f} | {cov_str} | {att_str} |")

    lines.append("\n## 成功率与质量分析\n")
    covs = [x.get("citation_coverage") for x in shards if x.get("citation_coverage") is not None]
    if covs:
        lines.append(f"- **citation_coverage**：均值 {statistics.mean(covs):.2f}、中位 {statistics.median(covs):.2f}、min {min(covs):.2f}、max {max(covs):.2f}")
    low_cov = [x for x in shards if (x.get("citation_coverage") or 0) < 0.5]
    if low_cov:
        names = [patients.get(x["patient_id"], {}).get("patient_name", x["patient_id"]) for x in low_cov]
        lines.append(f"- **coverage < 0.5（PARTIAL）**：{', '.join(names)} —— 推荐文本未充分引用检索证据编号，结果可用但引用薄弱")
    atts = [x.get("llm_attempt", {}).get("attempt", 1) for x in shards]
    degen = sum(1 for a in atts if a > 1)
    if degen:
        lines.append(f"- **触发退化降级**：{degen} 例（档2/档3 成功），说明 strict 首档退化时 json_object/penalty 降级链生效")
    else:
        lines.append("- **触发退化降级**：0 例（全部档1 strict/json_object 首次成功）")

    lines.append("\n## 耗时分布\n")
    bins = {"<30s": 0, "30-50s": 0, "50-70s": 0, ">70s": 0}
    for x in shards:
        w = x.get("wall_time_s", 0)
        if w < 30:
            bins["<30s"] += 1
        elif w < 50:
            bins["30-50s"] += 1
        elif w < 70:
            bins["50-70s"] += 1
        else:
            bins[">70s"] += 1
    for k, v in bins.items():
        lines.append(f"- {k}：{v} 例")

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"任务统计 → {out}")


def main():
    shards_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "Output/new_run")
    patients_path = Path(sys.argv[2] if len(sys.argv) > 2 else "Output/patients_new.json")
    pdata = json.loads(patients_path.read_text(encoding="utf-8"))
    patients = {p["patient_id"]: p for p in pdata["patients"]}
    shards = []
    for f in sorted((shards_dir / "patients").glob("*.json")):
        shards.append(json.loads(f.read_text(encoding="utf-8")))
    rag = json.loads((shards_dir / "rag_results.json").read_text(encoding="utf-8"))

    gen_query_report(shards, patients, shards_dir / "查询结果.md")
    gen_stats_report(shards, patients, rag, shards_dir / "任务完成情况.md")


if __name__ == "__main__":
    main()
