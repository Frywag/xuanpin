"""端到端管道：数据源 -> SourceEnvelope -> CandidateDataPacket -> L1/L2/L3 -> XLSX。"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .adapters.base import COLLECTOR_VERSION
from .adapters.frontend_workbook import FrontendWorkbookAdapter
from .adapters.kalodata import KalodataAdapter
from .adapters.tabcut_echotik import TabcutEchotikAdapter
from .cross_platform import CrossPlatformComparer
from .gates import GateRunner
from .models import dump_json
from .packet_builder import PacketBuilder
from .scoring import GroupStats, PriorityScorer

PLUGIN_ADAPTERS = {
    "kalodata": KalodataAdapter,
    "tabcut_echotik": TabcutEchotikAdapter,
}


def load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def collect_envelopes(repo_root: Path, sources_cfg: Dict[str, Any],
                      extra_envelope_paths: List[Path] = None):
    envelopes = []
    market = sources_cfg.get("market", "US")
    # 标准信封直投（docs/05 输入契约）：配置或命令行给目录/文件即可，无需写 Adapter
    from .envelope_io import load_envelope_files
    paths = [repo_root / spec["path"]
             for spec in sources_cfg.get("envelope_inputs", []) or []]
    paths += list(extra_envelope_paths or [])
    for p in paths:
        loaded, problems = load_envelope_files(p, market)
        envelopes.extend(loaded)
        for msg in problems:
            print(f"[envelope_inputs] {msg}")
    for spec in sources_cfg.get("plugin_sources", []):
        cls = PLUGIN_ADAPTERS[spec["adapter"]]
        adapter = cls(repo_root / spec["workbook"], market=market,
                      collection_mode=spec.get("collection_mode", "mixed"),
                      layer_hint=spec.get("layer_hint", "L1"))
        envelopes.extend(adapter.collect())
    for spec in sources_cfg.get("frontend_workbooks", []):
        adapter = FrontendWorkbookAdapter(
            repo_root / spec["workbook"], source_id=spec["source_id"],
            sites=spec["sites"], column_map=spec["column_map"],
            category_label=spec["category_label"],
            seasonal=bool(spec.get("seasonal")), market=market,
            collection_mode="full_then_analyze", layer_hint="L1")
        envelopes.extend(adapter.collect())
    return envelopes


def run_pipeline(repo_root: Path, out_dir: Path,
                 sources_cfg_path: Path, scoring_cfg_path: Path,
                 gates_cfg_path: Path, sample_packets: int = 10,
                 db_path: Path = None,
                 extra_envelope_paths: List[Path] = None) -> Dict[str, Any]:
    started = datetime.now(timezone.utc)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "envelopes").mkdir(exist_ok=True)
    (out_dir / "candidate_packets").mkdir(exist_ok=True)

    sources_cfg = load_yaml(sources_cfg_path)
    scoring_cfg = load_yaml(scoring_cfg_path)
    gates_cfg = load_yaml(gates_cfg_path)
    # 供应链能力档案（品类级，一次性维护；enabled=false 时能力子项记 missing）
    capability_path = scoring_cfg_path.parent / scoring_cfg.get(
        "supply", {}).get("capability_profile", "supply_capability.yaml")
    capability = load_yaml(capability_path) if capability_path.exists() else {}

    # 1. 采集 -> SourceEnvelope
    envelopes = collect_envelopes(repo_root, sources_cfg, extra_envelope_paths)
    for env in envelopes:
        dump_json(env.to_dict(), out_dir / "envelopes" / f"{env.envelope_id}.json")

    # 2. 合并 -> CandidateDataPacket
    builder = PacketBuilder(market=sources_cfg.get("market", "US"))
    packets = builder.build(envelopes)

    # 3. 组内统计 + 打分器
    stats = GroupStats(packets)
    scorer = PriorityScorer(scoring_cfg, stats, capability_profile=capability)
    runner = GateRunner(scorer, gates_cfg, market=sources_cfg.get("market", "US"))

    # 4. L1 全量粗筛
    results = runner.run_l1(packets)
    # 5. L2 入围精调研（成本受控）
    l2_ids = runner.run_l2(packets, results)
    # 6. L3 终选多平台比对
    comparer = CrossPlatformComparer(packets, stats)
    l3_ids = runner.run_l3(packets, results, comparer)

    # 7. 产物落盘
    with open(out_dir / "candidate_packets.jsonl", "w", encoding="utf-8") as f:
        for p in packets:
            f.write(json.dumps(p.to_dict(), ensure_ascii=False, default=str) + "\n")
    with open(out_dir / "analysis_results.jsonl", "w", encoding="utf-8") as f:
        for r in results.values():
            f.write(json.dumps(r.to_dict(), ensure_ascii=False, default=str) + "\n")
    # 样例候选包（P0 验收要求 ≥3 个）：取 L3 入围 + 高分候选
    sample_ids = (l3_ids + [r.candidate_id for r in sorted(
        results.values(), key=lambda r: -(r.priority_score["pct"] or 0))])
    seen = []
    for cid in sample_ids:
        if cid not in seen:
            seen.append(cid)
        if len(seen) >= sample_packets:
            break
    pk_by_id = {p.candidate_id: p for p in packets}
    for cid in seen:
        dump_json(pk_by_id[cid].to_dict(),
                  out_dir / "candidate_packets" / f"{cid}.json")
        dump_json(results[cid].to_dict(),
                  out_dir / "candidate_packets" / f"{cid}.analysis.json")

    grade_dist = Counter(r.priority_score["grade"] for r in results.values())
    track_dist = Counter(r.priority_score.get("track") or "n/a" for r in results.values())
    pcts = sorted((r.priority_score["pct"] or 0) for r in results.values())

    run_meta = {
        "run_id": out_dir.name,
        "collector_version": COLLECTOR_VERSION,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "market": sources_cfg.get("market", "US"),
        "envelopes": len(envelopes),
        "candidates_total": len(packets),
        "l1_processed": len(results),
        "l2_processed": len(l2_ids),
        "l3_processed": len(l3_ids),
        "grade_distribution": dict(grade_dist),
        "track_distribution": dict(track_dist),
        "capability_profile_enabled": bool(capability.get("enabled")),
        "pct_p50": pcts[len(pcts) // 2] if pcts else None,
        "pct_p90": pcts[int(len(pcts) * 0.9)] if pcts else None,
        "pct_max": pcts[-1] if pcts else None,
        "scoring_config": str(scoring_cfg_path),
        "gates_config": str(gates_cfg_path),
        "sources_config": str(sources_cfg_path),
        "group_stats": json.dumps(stats.to_dict(), ensure_ascii=False),
        "envelope_warnings": json.dumps(
            {e.envelope_id: e.warnings for e in envelopes if e.warnings},
            ensure_ascii=False)[:2000],
        "llm_usage": "无（全部为确定性规则；未编造任何数值）",
        "security": "输入工作簿不含 token/cookie；输出未写入任何凭证字段",
    }
    # 8. XLSX 推荐表
    from .xlsx_report import export_report
    export_report(out_dir / "选品推荐表.xlsx", packets, results, run_meta)

    # 10. 为 L2/L3 候选生成 LLM 分析任务包（由 agent CLI 执行后回灌）
    from .llm_tasks import (build_cross_platform_task, build_deep_review_task,
                            build_review_clustering_task, build_reason_writer_task,
                            build_run_report_task)
    tasks_dir = out_dir / "llm_tasks"
    tasks_dir.mkdir(exist_ok=True)
    pk_dict = {p.candidate_id: p.to_dict() for p in packets}
    n_tasks = 0
    for cid in l2_ids:
        t = build_review_clustering_task(pk_dict[cid], out_dir.name)
        if t:
            dump_json(t, tasks_dir / f"{cid}.review_clustering.json")
            n_tasks += 1
    for cid in l3_ids:
        rd = results[cid].to_dict()
        t = build_cross_platform_task(pk_dict[cid], rd, out_dir.name)
        if t:
            dump_json(t, tasks_dir / f"{cid}.cross_platform_compare.json")
            n_tasks += 1
        dump_json(build_reason_writer_task(pk_dict[cid], rd, out_dir.name),
                  tasks_dir / f"{cid}.reason_writer.json")
        n_tasks += 1

    # 10b. 深度评审任务：S 级全部 + 高分 A（LLM 的正式全量分析，非草稿）
    llm_cfg = gates_cfg.get("llm", {})
    dr_cfg = llm_cfg.get("deep_review", {})
    deep_ids = []
    if dr_cfg:
        ranked = sorted(results.values(),
                        key=lambda r: -(r.priority_score["pct"] or 0))
        for r in ranked:
            g, pct = r.priority_score.get("grade"), r.priority_score.get("pct") or 0
            if g in dr_cfg.get("grades", []) or (
                    g == "A" and pct >= dr_cfg.get("a_min_pct", 101)):
                deep_ids.append(r.candidate_id)
            if len(deep_ids) >= dr_cfg.get("max_candidates", 12):
                break
        for cid in deep_ids:
            dump_json(build_deep_review_task(pk_dict[cid], results[cid].to_dict(),
                                             out_dir.name),
                      tasks_dir / f"{cid}.deep_review.json")
            n_tasks += 1

    # 10c. 整轮运行分析报告任务（后置于选品表产出之后）
    if llm_cfg.get("run_report", {}).get("enabled"):
        top_n = llm_cfg["run_report"].get("top_candidates", 15)
        ranked = sorted(results.values(),
                        key=lambda r: -(r.priority_score["pct"] or 0))[:top_n]
        missing_freq = Counter()
        for p in packets:
            for m in p.missing_fields:
                missing_freq[m] += 1
        total = len(packets)
        run_summary = {
            "run_meta": {k: run_meta[k] for k in (
                "run_id", "market", "candidates_total", "l1_processed",
                "l2_processed", "l3_processed", "grade_distribution",
                "track_distribution", "pct_p50", "pct_p90", "pct_max")},
            # 预先算好比例，报告只允许引用这些现成数值（数值封闭性校验）
            "derived": {
                "grade_share_pct": {g: round(n / total * 100, 1)
                                    for g, n in grade_dist.items()},
                "track_share_pct": {t: round(n / total * 100, 1)
                                    for t, n in track_dist.items()},
                "sources_count": len(envelopes),
            },
            "group_price_bands": stats.to_dict()["price_bands"],
            "top_candidates": [{
                "candidate_id": r.candidate_id,
                "title": (pk_dict[r.candidate_id]["basic_facts"].get("title") or "")[:120],
                "platform": pk_dict[r.candidate_id]["platform"],
                "track": r.priority_score.get("track"),
                "grade": r.priority_score.get("grade"),
                "pct": r.priority_score.get("pct"),
                "confidence": r.confidence,
                "source_count": len({s["source_id"] for s in
                                     pk_dict[r.candidate_id]["source_refs"]}),
                "risk_claims": [c.claim for c in r.claims if c.claim_type == "risk"][:4],
                "reason_summary": r.recommendation.get("reason_summary", "")[:300],
            } for r in ranked],
            "top_missing_fields": [
                {"field": f, "count": n, "share_pct": round(n / total * 100, 1)}
                for f, n in missing_freq.most_common(10)],
            "envelope_warnings": {e.envelope_id: e.warnings
                                  for e in envelopes if e.warnings},
        }
        dump_json(build_run_report_task(run_summary, out_dir.name),
                  tasks_dir / "__run__.run_report.json")
        n_tasks += 1
    run_meta["llm_tasks_generated"] = n_tasks
    run_meta["deep_review_candidates"] = deep_ids

    # 11. 写入选品库（SQLite，跨 run 累积，供 agent CLI 查询）+ 运行记录落盘
    from .store import SelectionStore
    if db_path is None:
        db_path = Path(__file__).resolve().parents[2] / "data" / "selection.db"
    store = SelectionStore(db_path)
    store.record_run(run_meta, envelopes, packets, results, dict(track_dist))
    store.close()
    run_meta["db_path"] = str(db_path)
    dump_json(run_meta, out_dir / "run_meta.json")

    return {"packets": packets, "results": results, "run_meta": run_meta,
            "l2_ids": l2_ids, "l3_ids": l3_ids, "stats": stats}
