"""选品库：SQLite 持久化（stdlib sqlite3，零额外依赖）。

设计目标（对应「以库的形式存放采集回来的数据源」）：
  1. 每次运行按 run_id 追加，历史运行可对比（同一候选跨 run 的等级变化）；
  2. 关键字段拉平成列（可直接 SQL 筛选/聚合），完整对象存 JSON 列（无损）；
  3. 对 agent CLI 友好：只读查询接口 + 固定的表结构契约。

表结构：
  runs        运行记录（配置快照、等级/赛道分布）
  envelopes   SourceEnvelope 头信息 + 完整 JSON
  candidates  候选包：拉平字段（价格/评分/销量/来源数…）+ 完整 JSON
  results     分析结果：赛道/等级/pct/各层结果 + 完整 JSON
  evidence    证据索引：evidence_id -> 工作簿/sheet/行/字段
  llm_analyses  大模型回灌的分析结果（经校验后入库，标记 human_review）
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT, finished_at TEXT, market TEXT,
    candidates_total INTEGER, l2_processed INTEGER, l3_processed INTEGER,
    grade_distribution TEXT, track_distribution TEXT,
    config_json TEXT
);
CREATE TABLE IF NOT EXISTS envelopes (
    envelope_id TEXT, run_id TEXT,
    source_id TEXT, source_type TEXT, market TEXT,
    quality_status TEXT, records_count INTEGER, artifact_path TEXT,
    json TEXT,
    PRIMARY KEY (envelope_id, run_id)
);
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT, run_id TEXT,
    platform TEXT, source_group TEXT, title TEXT, category TEXT,
    price_amount REAL, price_currency TEXT, rating REAL, review_count REAL,
    sales_30d REAL, sales_floor REAL, favorites REAL, growth_rate REAL,
    source_count INTEGER, quality_status TEXT, missing_count INTEGER,
    json TEXT,
    PRIMARY KEY (candidate_id, run_id)
);
CREATE TABLE IF NOT EXISTS results (
    candidate_id TEXT, run_id TEXT,
    track TEXT, grade TEXT, pct REAL, total REAL, confidence TEXT,
    decision TEXT, l1_grade TEXT, l2_grade TEXT, l3_grade TEXT,
    json TEXT,
    PRIMARY KEY (candidate_id, run_id)
);
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT, run_id TEXT, candidate_id TEXT,
    source_id TEXT, field_path TEXT, artifact_path TEXT,
    record_locator TEXT, confidence TEXT,
    PRIMARY KEY (evidence_id, run_id)
);
CREATE TABLE IF NOT EXISTS llm_analyses (
    candidate_id TEXT, run_id TEXT, task_type TEXT,
    model TEXT, ingested_at TEXT, human_review TEXT DEFAULT 'pending',
    json TEXT,
    PRIMARY KEY (candidate_id, run_id, task_type)
);
CREATE TABLE IF NOT EXISTS track_evaluations (
    run_id TEXT, subject_type TEXT, subject_id TEXT, track_id TEXT,
    admission_status TEXT, grade TEXT, score REAL, confidence TEXT,
    cluster_id TEXT, rule_status TEXT, rule_version TEXT, json TEXT,
    PRIMARY KEY (run_id, subject_type, subject_id, track_id)
);
CREATE INDEX IF NOT EXISTS idx_results_grade ON results (run_id, grade, pct);
CREATE INDEX IF NOT EXISTS idx_candidates_group ON candidates (run_id, source_group);
CREATE INDEX IF NOT EXISTS idx_evidence_cand ON evidence (run_id, candidate_id);
"""


class SelectionStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    # ---------- 写入（pipeline 调用） ----------

    def record_run(self, run_meta: Dict[str, Any], envelopes, packets, results,
                   track_distribution: Optional[Dict[str, int]] = None):
        run_id = run_meta["run_id"]
        c = self.conn
        c.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
        for table in ("envelopes", "candidates", "results", "evidence"):
            c.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
        c.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, run_meta.get("started_at"), run_meta.get("finished_at"),
             run_meta.get("market"), run_meta.get("candidates_total"),
             run_meta.get("l2_processed"), run_meta.get("l3_processed"),
             json.dumps(run_meta.get("grade_distribution"), ensure_ascii=False),
             json.dumps(track_distribution or {}, ensure_ascii=False),
             json.dumps(run_meta, ensure_ascii=False, default=str)))

        c.executemany(
            "INSERT INTO envelopes VALUES (?,?,?,?,?,?,?,?,?)",
            [(e.envelope_id, run_id, e.source_id, e.source_type, e.market,
              e.quality_status, len(e.records),
              e.artifact_paths.get("raw_workbook", ""),
              json.dumps(e.to_dict(), ensure_ascii=False, default=str))
             for e in envelopes])

        cand_rows, ev_rows = [], []
        for p in packets:
            price = p.basic_facts.get("price") or {}
            cand_rows.append((
                p.candidate_id, run_id, p.platform,
                p.context.get("source_group"), p.basic_facts.get("title"),
                p.context.get("category_name") or p.context.get("category_label"),
                price.get("amount"), price.get("currency"),
                p.basic_facts.get("rating"), p.basic_facts.get("review_count"),
                p.market_metrics.get("sales_30d_units"),
                p.market_metrics.get("sales_floor_units"),
                p.market_metrics.get("favorites_count"),
                p.market_metrics.get("sales_growth_rate"),
                p.source_count, p.quality_status, len(p.missing_fields),
                json.dumps(p.to_dict(), ensure_ascii=False, default=str)))
            for ev in p.evidence_pack:
                ev_rows.append((ev.evidence_id, run_id, p.candidate_id,
                                ev.source_id, ev.field_path, ev.artifact_path,
                                ev.record_locator, ev.confidence))
        c.executemany(
            "INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            cand_rows)
        c.executemany("INSERT OR IGNORE INTO evidence VALUES (?,?,?,?,?,?,?,?)", ev_rows)

        c.executemany(
            "INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r.candidate_id, run_id,
              r.priority_score.get("track"), r.priority_score.get("grade"),
              r.priority_score.get("pct"), r.priority_score.get("total"),
              r.confidence, r.recommendation.get("decision"),
              r.layer_results["L1"].grade, r.layer_results["L2"].grade,
              r.layer_results["L3"].grade,
              json.dumps(r.to_dict(), ensure_ascii=False, default=str))
             for r in results.values()])
        c.commit()

    def record_track_evaluations(self, run_id: str, evaluations):
        self.conn.execute("DELETE FROM track_evaluations WHERE run_id=?", (run_id,))
        self.conn.executemany(
            "INSERT INTO track_evaluations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(e.run_id, e.subject_type, e.subject_id, e.track_id,
              e.admission_status, e.grade, e.score, e.confidence,
              e.cluster_id, e.rule_status, e.rule_version,
              json.dumps(e.to_dict(), ensure_ascii=False, default=str))
             for e in evaluations])
        self.conn.commit()

    def record_llm_analysis(self, run_id: str, candidate_id: str, task_type: str,
                            model: str, ingested_at: str, payload: Dict[str, Any]):
        self.conn.execute(
            "INSERT OR REPLACE INTO llm_analyses VALUES (?,?,?,?,?,?,?)",
            (candidate_id, run_id, task_type, model, ingested_at, "pending",
             json.dumps(payload, ensure_ascii=False, default=str)))
        self.conn.commit()

    # ---------- 查询（agent CLI 调用） ----------

    def latest_run_id(self) -> Optional[str]:
        row = self.conn.execute(
            "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def query(self, sql: str, params: Iterable = ()) -> List[Dict[str, Any]]:
        """只读 SQL（拒绝写操作），返回 dict 行。"""
        forbidden = ("insert", "update", "delete", "drop", "alter", "create",
                     "replace", "pragma", "attach", "vacuum")
        head = sql.strip().split(None, 1)[0].lower() if sql.strip() else ""
        if head in forbidden:
            raise ValueError(f"只读接口拒绝执行 {head.upper()} 语句")
        cur = self.conn.execute(sql, tuple(params))
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_candidate(self, candidate_id: str, run_id: Optional[str] = None):
        run_id = run_id or self.latest_run_id()
        row = self.conn.execute(
            "SELECT json FROM candidates WHERE candidate_id=? AND run_id=?",
            (candidate_id, run_id)).fetchone()
        packet = json.loads(row[0]) if row else None
        row = self.conn.execute(
            "SELECT json FROM results WHERE candidate_id=? AND run_id=?",
            (candidate_id, run_id)).fetchone()
        result = json.loads(row[0]) if row else None
        return {"run_id": run_id, "packet": packet, "result": result}

    def evidence_chain(self, candidate_id: str, run_id: Optional[str] = None):
        run_id = run_id or self.latest_run_id()
        return self.query(
            "SELECT evidence_id, source_id, field_path, artifact_path, "
            "record_locator, confidence FROM evidence "
            "WHERE candidate_id=? AND run_id=? ORDER BY field_path",
            (candidate_id, run_id))
