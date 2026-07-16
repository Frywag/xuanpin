"""命令行入口（人和 agent CLI 共用）。

在仓库根目录执行，PYTHONPATH 指向 04.分级系统/src：

  run        运行端到端分级管道（采集->合并->L1/L2/L3->XLSX->选品库->LLM任务包）
  grades     查看某次运行的等级/赛道分布
  db-query   对选品库执行只读 SQL（SELECT）
  show       输出某个候选的完整 CandidateDataPacket + AnalysisResult JSON
  explain    输出某个候选的证据链（字段 -> 工作簿/sheet/行）
  llm-ingest 校验并回灌 LLM 分析结果（证据白名单 + 数值封闭性校验）

示例：
  python -m grading_system.cli run --repo-root . --out 04.分级系统/data/analysis_runs/run_x
  python -m grading_system.cli db-query --sql "SELECT grade, COUNT(*) FROM results WHERE run_id=(SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1) GROUP BY grade"
  python -m grading_system.cli show tiktok_shop_1731406217255686451
  python -m grading_system.cli llm-ingest --bundle run_x/llm_tasks/xxx.review_clustering.json --output result.json --model claude-fable-5
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_DIR / "data" / "selection.db"


def _print_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_run(args) -> int:
    from .pipeline import run_pipeline
    out = run_pipeline(
        repo_root=Path(args.repo_root), out_dir=Path(args.out),
        sources_cfg_path=Path(args.sources),
        scoring_cfg_path=Path(args.scoring),
        gates_cfg_path=Path(args.gates),
        db_path=Path(args.db) if args.db else None,
        extra_envelope_paths=[Path(p) for p in (args.envelopes or [])],
        run_mode=args.run_mode)
    meta = out["run_meta"]
    print(f"候选总数: {meta['candidates_total']}")
    print(f"运行模式: {meta['run_mode']}（{meta['run_mode_semantics']}）")
    print(f"项目状态: {meta.get('project_status')}")
    # 双口径标注（P0-08）：legacy 与 tracks.v1 并行输出，禁止混称
    print(f"[legacy 基线口径｜非正式生产推荐] 等级分布: {meta['grade_distribution']}")
    print(f"[legacy 基线口径] 赛道分布: {meta['track_distribution']}")
    tv = meta.get("tracks") or meta.get("tracks_v1")
    if tv:
        sv = tv.get("schema_version", "tracks.v1")
        print(f"[{sv} 校准口径｜{tv['rule_status']}] 准入分布: {tv['per_track']}")
        print(f"[{sv} 校准口径] ELIGIBLE 草案等级: {tv['eligible_grades']}")
        if tv.get("layers"):
            print(f"[{sv} 校准口径] 分层到达(L1/L2/L3): {tv['layers']}")
        print(f"[{sv} 校准口径] 其中临时规则准入(占位): {tv.get('provisional_eligible')}")
        print(f"[{sv} 校准口径] 预算队列(l2/l3/补采): {tv.get('budgets')}")
        if meta.get("development_directions"):
            print(f"[开发方向] {meta['development_directions']}")
        if meta.get("trend_words"):
            print(f"[趋势词体系ABCDE] {meta['trend_words']['grades']}"
                  f"（共 {meta['trend_words']['count']} 词）")
    print(f"L2 处理: {meta['l2_processed']}  L3 处理: {meta['l3_processed']}")
    print(f"LLM 任务包: {meta.get('llm_tasks_generated', 0)} 个（{args.out}/llm_tasks/）")
    print(f"选品库: {meta.get('db_path')}")
    print(f"产物目录: {args.out}")
    return 0


def _open_store(args):
    from .store import SelectionStore
    db = Path(args.db) if getattr(args, "db", None) else DEFAULT_DB
    if not db.exists():
        print(f"选品库不存在：{db}（先执行 run）", file=sys.stderr)
        sys.exit(2)
    return SelectionStore(db)


LEGACY_NOTE = "legacy 单赛道基线，非正式生产推荐（唯一业务事实源切换待业务确认）"
TRACKS_NOTE = ("tracks.v1 三赛道校准口径（rule_status=calibration_pending_"
               "business_approval，非正式生产等级）；PENDING_DATA/REJECTED 无正式等级")


def cmd_grades(args) -> int:
    store = _open_store(args)
    run_id = args.run or store.latest_run_id()
    legacy_rows = store.query(
        "SELECT track, grade, COUNT(*) AS n, ROUND(AVG(pct),1) AS avg_pct "
        "FROM results WHERE run_id=? GROUP BY track, grade "
        "ORDER BY track, CASE grade WHEN 'S' THEN 0 WHEN 'A' THEN 1 "
        "WHEN 'B' THEN 2 ELSE 3 END", (run_id,))
    if args.legacy:  # 兼容旧脚本：只看 legacy 口径
        _print_json({"run_id": run_id, "caliber": "legacy_baseline",
                     "note": LEGACY_NOTE, "distribution": legacy_rows})
        store.close()
        return 0
    # 默认双口径标注输出（P0-08）：以 tracks.v1 为主口径，legacy 为基线参考
    track_rows = store.query(
        "SELECT track_id, admission_status, grade, COUNT(*) AS n, "
        "ROUND(AVG(score),1) AS avg_score "
        "FROM track_evaluations WHERE run_id=? "
        "GROUP BY track_id, admission_status, grade "
        "ORDER BY track_id, admission_status, CASE grade WHEN 'S' THEN 0 "
        "WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END", (run_id,))
    _print_json({
        "run_id": run_id,
        "tracks_v1_calibration": {
            "note": TRACKS_NOTE if track_rows
            else TRACKS_NOTE + "；本 run 无 track_evaluations 记录",
            "distribution": track_rows},
        "legacy_baseline": {"note": LEGACY_NOTE, "distribution": legacy_rows},
    })
    store.close()
    return 0


def cmd_db_query(args) -> int:
    store = _open_store(args)
    try:
        rows = store.query(args.sql)
        _print_json(rows[: args.limit])
    finally:
        store.close()
    return 0


def cmd_show(args) -> int:
    store = _open_store(args)
    data = store.get_candidate(args.candidate_id, args.run)
    store.close()
    if not data["packet"]:
        print(f"候选不存在：{args.candidate_id}", file=sys.stderr)
        return 2
    _print_json(data)
    return 0


def cmd_explain(args) -> int:
    store = _open_store(args)
    chain = store.evidence_chain(args.candidate_id, args.run)
    store.close()
    if not chain:
        print(f"无证据记录：{args.candidate_id}", file=sys.stderr)
        return 2
    _print_json({"candidate_id": args.candidate_id, "evidence_chain": chain})
    return 0


def cmd_llm_ingest(args) -> int:
    from .llm_tasks import render_markdown, validate_llm_output
    bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
    output = json.loads(Path(args.output).read_text(encoding="utf-8"))
    errors = validate_llm_output(bundle, output)
    if errors:
        _print_json({"status": "rejected", "errors": errors})
        return 1
    store = _open_store(args)
    store.record_llm_analysis(
        run_id=bundle["run_id"], candidate_id=bundle["candidate_id"],
        task_type=bundle["task_type"], model=args.model,
        ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        payload={"bundle_inputs_digest": sorted(bundle["inputs"].keys()),
                 "output": output})
    store.close()
    rendered = None
    if args.render:
        rendered = Path(args.render)
        rendered.parent.mkdir(parents=True, exist_ok=True)
        rendered.write_text(render_markdown(bundle, output), encoding="utf-8")
    note = ("深度评审/运行报告为正式分析产出；等级与分数仍由确定性引擎产生，"
            "等级变更建议（grade_challenge）需人审确认"
            if bundle["task_type"] in ("deep_review", "run_report")
            else "已入库 llm_analyses，human_review=pending")
    _print_json({"status": "accepted", "candidate_id": bundle["candidate_id"],
                 "task_type": bundle["task_type"],
                 "rendered": str(rendered) if rendered else None,
                 "note": note})
    return 0


def cmd_validate_envelope(args) -> int:
    from .envelope_io import load_envelope_files
    envelopes, problems = load_envelope_files(Path(args.path), args.market)
    report = {
        "accepted_envelopes": [
            {"envelope_id": e.envelope_id, "source_id": e.source_id,
             "source_type": e.source_type, "records": len(e.records)}
            for e in envelopes],
        "problems": problems,
        "status": "ok" if envelopes and not any("拒收" in p for p in problems)
        else ("partial" if envelopes else "rejected"),
    }
    _print_json(report)
    return 0 if envelopes else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="grading_system")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="运行端到端分级管道")
    run.add_argument("--repo-root", default=str(PROJECT_DIR.parent))
    run.add_argument("--out", required=True)
    run.add_argument("--sources", default=str(PROJECT_DIR / "configs/sources_p0.yaml"))
    run.add_argument("--scoring", default=str(PROJECT_DIR / "configs/scoring_v0.yaml"))
    run.add_argument("--gates", default=str(PROJECT_DIR / "configs/layer_gates_v0.yaml"))
    run.add_argument("--db", default=None, help="选品库路径（默认 data/selection.db）")
    run.add_argument("--envelopes", action="append", default=None,
                     help="标准 SourceEnvelope 文件/目录（可重复；见 docs/05 输入契约）")
    run.add_argument("--run-mode", default="full",
                     choices=["full", "strict_snapshot", "enhanced_replay"],
                     help="运行模式（写入 run_meta 供审计）：full=普通全量运行；"
                          "strict_snapshot=严格快照回放（不可变基线目录）；"
                          "enhanced_replay=受控增强回放（补采后）")
    run.set_defaults(func=cmd_run)

    ve = sub.add_parser("validate-envelope",
                        help="校验标准 SourceEnvelope 文件（采集方交付前自检）")
    ve.add_argument("path", help="信封 .json/.jsonl 文件或目录")
    ve.add_argument("--market", default="US")
    ve.set_defaults(func=cmd_validate_envelope)

    grades = sub.add_parser(
        "grades", help="等级/赛道分布（默认双口径标注：tracks.v1 校准 + legacy 基线）")
    grades.add_argument("--run", default=None)
    grades.add_argument("--legacy", action="store_true",
                        help="只输出 legacy 单赛道基线口径（旧脚本兼容）")
    grades.add_argument("--db", default=None)
    grades.set_defaults(func=cmd_grades)

    q = sub.add_parser("db-query", help="只读 SQL 查询选品库")
    q.add_argument("--sql", required=True)
    q.add_argument("--limit", type=int, default=50)
    q.add_argument("--db", default=None)
    q.set_defaults(func=cmd_db_query)

    show = sub.add_parser("show", help="输出候选完整 JSON（packet + result）")
    show.add_argument("candidate_id")
    show.add_argument("--run", default=None)
    show.add_argument("--db", default=None)
    show.set_defaults(func=cmd_show)

    explain = sub.add_parser("explain", help="输出候选证据链")
    explain.add_argument("candidate_id")
    explain.add_argument("--run", default=None)
    explain.add_argument("--db", default=None)
    explain.set_defaults(func=cmd_explain)

    ingest = sub.add_parser("llm-ingest", help="校验并回灌 LLM 分析结果")
    ingest.add_argument("--bundle", required=True, help="任务包 JSON 路径")
    ingest.add_argument("--output", required=True, help="LLM 输出 JSON 路径")
    ingest.add_argument("--model", required=True, help="执行模型标识（审计用）")
    ingest.add_argument("--render", default=None,
                        help="回灌通过后渲染业务可读 Markdown 到该路径"
                             "（deep_review / run_report）")
    ingest.add_argument("--db", default=None)
    ingest.set_defaults(func=cmd_llm_ingest)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
