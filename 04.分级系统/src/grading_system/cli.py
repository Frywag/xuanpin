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
        db_path=Path(args.db) if args.db else None)
    meta = out["run_meta"]
    print(f"候选总数: {meta['candidates_total']}")
    print(f"等级分布: {meta['grade_distribution']}")
    print(f"赛道分布: {meta['track_distribution']}")
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


def cmd_grades(args) -> int:
    store = _open_store(args)
    run_id = args.run or store.latest_run_id()
    rows = store.query(
        "SELECT track, grade, COUNT(*) AS n, ROUND(AVG(pct),1) AS avg_pct "
        "FROM results WHERE run_id=? GROUP BY track, grade "
        "ORDER BY track, CASE grade WHEN 'S' THEN 0 WHEN 'A' THEN 1 "
        "WHEN 'B' THEN 2 ELSE 3 END", (run_id,))
    _print_json({"run_id": run_id, "distribution": rows})
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
    from .llm_tasks import validate_llm_output
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
    _print_json({"status": "accepted", "candidate_id": bundle["candidate_id"],
                 "task_type": bundle["task_type"],
                 "note": "已入库 llm_analyses，human_review=pending；等级/分数仍由确定性引擎决定"})
    return 0


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
    run.set_defaults(func=cmd_run)

    grades = sub.add_parser("grades", help="等级/赛道分布")
    grades.add_argument("--run", default=None)
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
    ingest.add_argument("--db", default=None)
    ingest.set_defaults(func=cmd_llm_ingest)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
