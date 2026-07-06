"""命令行入口。

用法（在仓库根目录执行）：

    python -m grading_system.cli run \
        --repo-root . \
        --out "04.分级系统/data/analysis_runs/run_$(date +%Y%m%d)_001"

可选参数 --sources/--scoring/--gates 指向自定义配置。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import run_pipeline

PROJECT_DIR = Path(__file__).resolve().parents[2]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="grading_system")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="运行端到端分级管道")
    run.add_argument("--repo-root", default=str(PROJECT_DIR.parent))
    run.add_argument("--out", required=True)
    run.add_argument("--sources", default=str(PROJECT_DIR / "configs/sources_p0.yaml"))
    run.add_argument("--scoring", default=str(PROJECT_DIR / "configs/scoring_v0.yaml"))
    run.add_argument("--gates", default=str(PROJECT_DIR / "configs/layer_gates_v0.yaml"))
    args = parser.parse_args(argv)

    if args.command == "run":
        out = run_pipeline(
            repo_root=Path(args.repo_root), out_dir=Path(args.out),
            sources_cfg_path=Path(args.sources),
            scoring_cfg_path=Path(args.scoring),
            gates_cfg_path=Path(args.gates))
        meta = out["run_meta"]
        print(f"候选总数: {meta['candidates_total']}")
        print(f"等级分布: {meta['grade_distribution']}")
        print(f"L2 处理: {meta['l2_processed']}  L3 处理: {meta['l3_processed']}")
        print(f"产物目录: {args.out}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
