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


def _rel(path, root: Path) -> str:
    """路径转仓库相对（可复现审计，P0-07）；不在仓库内则原样返回。"""
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return str(path)


# run 模式语义（P0-07/P0-09 回放可审计性）：run_meta.run_mode 记录本次运行属于哪一种，
# 汇报口径必须与之一致，禁止把普通全量运行表述为「严格快照回放」。
RUN_MODES = {
    "full": "普通全量运行（当前数据可得性下的完整管道，非基线回放）",
    "strict_snapshot": "严格快照回放（不可变基线目录，不补字段，结果可复现比对）",
    "enhanced_replay": "受控增强回放（补采后重放，须声明相对基线新增的数据面）",
}


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
    # 工作簿缺失快速失败（P0-07）：换环境回放时给出明确清单，而不是在
    # openpyxl 深处抛裸异常或静默跳过部分数据源导致结果不可比
    missing_books = [spec["workbook"]
                     for key in ("plugin_sources", "frontend_workbooks")
                     for spec in sources_cfg.get(key, []) or []
                     if not (repo_root / spec["workbook"]).exists()]
    if missing_books:
        raise FileNotFoundError(
            "数据源工作簿缺失（sources 配置中的文件必须全部存在；跨环境回放请使用"
            "不可变基线目录并核对文件清单，见 docs/07）：" + "；".join(missing_books))
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
            track_hint=spec.get("track_hint"),
            source_roles=spec.get("source_roles"),
            collection_mode="full_then_analyze", layer_hint="L1")
        envelopes.extend(adapter.collect())
    return envelopes


def run_pipeline(repo_root: Path, out_dir: Path,
                 sources_cfg_path: Path, scoring_cfg_path: Path,
                 gates_cfg_path: Path, sample_packets: int = 10,
                 db_path: Path = None,
                 extra_envelope_paths: List[Path] = None,
                 run_mode: str = "full") -> Dict[str, Any]:
    if run_mode not in RUN_MODES:
        raise ValueError(f"未知 run_mode：{run_mode}（可选：{sorted(RUN_MODES)}）")
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

    # 6b. 商品画像标注（152 键模板）：深度评审集合（S+高分A）先做代码直取键，
    #     语义键生成 profile_extraction LLM 任务（10b 落盘）
    from .profile import build_direct_profile
    profile_registry_path = scoring_cfg_path.parent / "profile_keys_v1.yaml"
    profile_registry = (load_yaml(profile_registry_path)
                        if profile_registry_path.exists() else {})
    dr_cfg = gates_cfg.get("llm", {}).get("deep_review", {})
    deep_ids: List[str] = []
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
    pk_by_id = {p.candidate_id: p for p in packets}
    # S/A/B 全量画像直取（下游 152 键核对用）；语义任务仍限深评集合控成本
    sab_ids = [r.candidate_id for r in results.values()
               if r.priority_score.get("grade") in ("S", "A", "B")]
    if profile_registry:
        for cid in sab_ids:
            pk_by_id[cid].context["profile"] = build_direct_profile(
                pk_by_id[cid], profile_registry)

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
        # 运行模式与结果语义（P0-07/P0-09）：汇报口径必须按此标注，
        # 普通全量运行不得表述为「严格快照回放」
        "run_mode": run_mode,
        "run_mode_semantics": RUN_MODES[run_mode],
        "project_status": None,  # 由 tracks_v1.yaml 提供；纯 legacy 运行记 legacy_only
        "result_semantics": (
            "双链并行过渡期：legacy 单赛道结果为基线参考（非正式生产推荐）；"
            "tracks.v1 三赛道结果为校准态（rule_status=calibration_pending_"
            "business_approval）。业务金标批准前两者均不得作为正式生产等级；"
            "唯一业务事实源的切换时点待业务拍板（见 tracks_v1.yaml "
            "pending_business_confirmation）"),
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
        "scoring_config": _rel(scoring_cfg_path, repo_root),
        "gates_config": _rel(gates_cfg_path, repo_root),
        "sources_config": _rel(sources_cfg_path, repo_root),
        "group_stats": json.dumps(stats.to_dict(), ensure_ascii=False),
        "envelope_warnings": json.dumps(
            {e.envelope_id: e.warnings for e in envelopes if e.warnings},
            ensure_ascii=False)[:2000],
        "llm_usage": "无（全部为确定性规则；未编造任何数值）",
        "security": "输入工作簿不含 token/cookie；输出未写入任何凭证字段",
    }
    # 6c. 三赛道独立评估（tracks.v1；legacy 单赛道结果保留为旧版兼容）
    tracks_cfg_path = scoring_cfg_path.parent / "tracks_v1.yaml"
    track_evals, clusters, track_budgets = [], [], {}
    run_meta["project_status"] = "legacy_only"
    if tracks_cfg_path.exists():
        from .track_eval import TrackEvaluator
        tracks_cfg = load_yaml(tracks_cfg_path)
        run_meta["project_status"] = tracks_cfg.get("project_status", "legacy_only")
        # 业务确认证据通道（规则 §7.3：业务人员确认是合法准入证据）
        bc_path = scoring_cfg_path.parent / "business_confirmations.yaml"
        if bc_path.exists():
            tracks_cfg["business_confirmations"] = load_yaml(bc_path) or {}
        evaluator = TrackEvaluator(tracks_cfg, stats, out_dir.name)
        track_evals, clusters, track_budgets = evaluator.run(packets)
        with open(out_dir / "track_evaluations.jsonl", "w", encoding="utf-8") as f:
            for e in track_evals:
                f.write(json.dumps(e.to_dict(), ensure_ascii=False, default=str) + "\n")
        dump_json({"clusters": clusters, "budgets": track_budgets},
                  out_dir / "opportunity_clusters.json")
        from .xlsx_report import export_track_report
        export_track_report(out_dir / "三赛道选品推荐表.xlsx", packets,
                            track_evals, clusters, track_budgets, tracks_cfg)

    # 7b. S/A/B 全量产品信息保留（含全部字段/证据/画像，供下游 152 键核对）
    with open(out_dir / "sab_products_full.jsonl", "w", encoding="utf-8") as f:
        for cid in sorted(sab_ids, key=lambda c: -(results[c].priority_score["pct"] or 0)):
            f.write(json.dumps({"packet": pk_by_id[cid].to_dict(),
                                "result": results[cid].to_dict()},
                               ensure_ascii=False, default=str) + "\n")
    run_meta_sab = len(sab_ids)

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

    # 10b. 深度评审 + 画像语义提取任务（对象同为 S+高分A，deep_ids 已在 6b 计算）
    llm_cfg = gates_cfg.get("llm", {})
    from .profile import build_profile_extraction_task
    for cid in deep_ids:
        dump_json(build_deep_review_task(pk_dict[cid], results[cid].to_dict(),
                                         out_dir.name),
                  tasks_dir / f"{cid}.deep_review.json")
        n_tasks += 1
        if profile_registry:
            t = build_profile_extraction_task(pk_by_id[cid], out_dir.name,
                                              profile_registry)
            if t:
                dump_json(t, tasks_dir / f"{cid}.profile_extraction.json")
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
    # 10d. 多平台同款/同款式/同趋势比对任务（独立模块；LLM 综合判定，不用商品 id）
    from .llm_tasks import build_style_match_task
    from .cross_platform import style_tokens_of
    top_for_match = sorted([r for r in results.values()
                            if r.priority_score.get("grade") in ("S", "A")],
                           key=lambda r: -(r.priority_score["pct"] or 0))[:20]
    cards = []
    for r in top_for_match:
        pk = pk_by_id[r.candidate_id]
        cards.append({
            "candidate_id": r.candidate_id, "platform": pk.platform,
            "source_group": pk.context.get("source_group"),
            "title": pk.basic_facts.get("title"),
            "price": pk.basic_facts.get("price"),
            "category": pk.context.get("category_name") or pk.context.get("category_label"),
            "image_url": pk.basic_facts.get("image_url"),
            "trend_tags": pk.context.get("trend_tags"),
            "style_tokens": sorted(style_tokens_of(pk.basic_facts.get("title") or "")),
            "evidence_refs": pk.evidence_for("basic_facts.title")
                             + pk.evidence_for("basic_facts.price")})
    pairs = []
    for i, a in enumerate(cards):
        for b in cards[i + 1:]:
            if (a["source_group"] != b["source_group"]
                    and set(a["style_tokens"]) & set(b["style_tokens"])):
                pairs.append({"candidate_a": a["candidate_id"],
                              "candidate_b": b["candidate_id"],
                              "shared_tokens": sorted(set(a["style_tokens"])
                                                      & set(b["style_tokens"]))})
    if cards:
        dump_json(build_style_match_task(cards, pairs[:40], out_dir.name),
                  tasks_dir / "__style_match__.style_match_report.json")
        n_tasks += 1

    run_meta["llm_tasks_generated"] = n_tasks
    run_meta["deep_review_candidates"] = deep_ids
    run_meta["sab_full_retained"] = run_meta_sab
    if track_evals:
        from collections import Counter as _C
        run_meta["tracks_v1"] = {
            "rule_status": "calibration_pending_business_approval",
            "evaluations": len(track_evals),
            "per_track": {tid: dict(_C(e.admission_status for e in track_evals
                                       if e.track_id == tid))
                          for tid in ("trend_new", "hit_improvement", "long_tail_direct")},
            "eligible_grades": {tid: dict(_C(e.grade for e in track_evals
                                             if e.track_id == tid and e.grade))
                                for tid in ("trend_new", "hit_improvement", "long_tail_direct")},
            # 临时规则准入（占位，业务指示 2026-07-14）：数据源结构性缺失的候选
            # 照常进入筛选流程，缺失照记、补证照排；最终门限待人工确认
            "provisional_eligible": {
                tid: sum(1 for e in track_evals
                         if e.track_id == tid and e.admission_status == "ELIGIBLE"
                         and e.admission_basis == "provisional_rule")
                for tid in ("trend_new", "hit_improvement", "long_tail_direct")},
            "clusters": len(clusters),
            # 预算拆分（P0-03）：l2/l3=已准入深挖，refetch=PENDING_DATA 补证
            "budgets": {tid: {"l2": len(b.get("l2_queue", [])),
                              "l3": len(b.get("l3_queue", [])),
                              "refetch": len(b.get("refetch_queue", []))}
                        for tid, b in track_budgets.items()},
        }

    # 11. 写入选品库（SQLite，跨 run 累积，供 agent CLI 查询）+ 运行记录落盘
    from .store import SelectionStore
    if db_path is None:
        db_path = Path(__file__).resolve().parents[2] / "data" / "selection.db"
    store = SelectionStore(db_path)
    store.record_run(run_meta, envelopes, packets, results, dict(track_dist))
    if track_evals:
        store.record_track_evaluations(out_dir.name, track_evals)
    store.close()
    run_meta["db_path"] = _rel(db_path, repo_root)
    dump_json(run_meta, out_dir / "run_meta.json")

    return {"packets": packets, "results": results, "run_meta": run_meta,
            "l2_ids": l2_ids, "l3_ids": l3_ids, "stats": stats}
