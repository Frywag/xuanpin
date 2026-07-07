"""选品库 MCP 服务（stdio，零依赖实现）。

让其他系统/agent 通过 MCP 协议直接调用分级结果，替代 Excel 转发：

    python -m grading_system.mcp_server [--db 04.分级系统/data/selection.db]

实现 MCP stdio 传输（按行分隔的 JSON-RPC 2.0）：initialize /
notifications/initialized / ping / tools/list / tools/call。
全部工具只读；SQL 工具复用 store.query 的只读防护。

Claude Code 挂载示例（.mcp.json）：
{
  "mcpServers": {
    "xuanpin-selection": {
      "command": "python3",
      "args": ["-m", "grading_system.mcp_server"],
      "env": {"PYTHONPATH": "04.分级系统/src"}
    }
  }
}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .store import SelectionStore

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "xuanpin-selection-store", "version": "0.1.0"}

TOOLS = [
    {
        "name": "list_runs",
        "description": "列出选品库中的分析运行（run_id、时间、等级/赛道分布）",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "query_grades",
        "description": "某次运行的等级×赛道分布；run_id 缺省为最新一次",
        "inputSchema": {"type": "object",
                        "properties": {"run_id": {"type": "string"}}, "required": []},
    },
    {
        "name": "get_candidate",
        "description": "候选的完整 CandidateDataPacket + AnalysisResult（含证据与分层结果）",
        "inputSchema": {"type": "object",
                        "properties": {"candidate_id": {"type": "string"},
                                       "run_id": {"type": "string"}},
                        "required": ["candidate_id"]},
    },
    {
        "name": "get_evidence_chain",
        "description": "候选的证据链：字段 -> 数据工作簿/sheet/行号",
        "inputSchema": {"type": "object",
                        "properties": {"candidate_id": {"type": "string"},
                                       "run_id": {"type": "string"}},
                        "required": ["candidate_id"]},
    },
    {
        "name": "get_llm_analysis",
        "description": "候选的 LLM 分析产出（deep_review 等；__run__ 取整轮报告）",
        "inputSchema": {"type": "object",
                        "properties": {"candidate_id": {"type": "string"},
                                       "task_type": {"type": "string"},
                                       "run_id": {"type": "string"}},
                        "required": ["candidate_id"]},
    },
    {
        "name": "db_query",
        "description": "对选品库执行只读 SQL（表：runs/envelopes/candidates/results/"
                       "evidence/llm_analyses；写语句会被拒绝）",
        "inputSchema": {"type": "object",
                        "properties": {"sql": {"type": "string"},
                                       "limit": {"type": "integer", "default": 50}},
                        "required": ["sql"]},
    },
]


class McpServer:
    def __init__(self, db_path: Path):
        self.store = SelectionStore(db_path)

    # ---------- 工具实现 ----------

    def call_tool(self, name: str, args: dict):
        if name == "list_runs":
            return self.store.query(
                "SELECT run_id, started_at, candidates_total, l2_processed, "
                "l3_processed, grade_distribution, track_distribution "
                "FROM runs ORDER BY started_at DESC")
        if name == "query_grades":
            run_id = args.get("run_id") or self.store.latest_run_id()
            return {"run_id": run_id, "distribution": self.store.query(
                "SELECT track, grade, COUNT(*) AS n, ROUND(AVG(pct),1) AS avg_pct "
                "FROM results WHERE run_id=? GROUP BY track, grade", (run_id,))}
        if name == "get_candidate":
            return self.store.get_candidate(args["candidate_id"], args.get("run_id"))
        if name == "get_evidence_chain":
            return {"candidate_id": args["candidate_id"],
                    "evidence_chain": self.store.evidence_chain(
                        args["candidate_id"], args.get("run_id"))}
        if name == "get_llm_analysis":
            run_id = args.get("run_id") or self.store.latest_run_id()
            sql = ("SELECT candidate_id, task_type, model, ingested_at, "
                   "human_review, json FROM llm_analyses "
                   "WHERE candidate_id=? AND run_id=?")
            params = [args["candidate_id"], run_id]
            if args.get("task_type"):
                sql += " AND task_type=?"
                params.append(args["task_type"])
            rows = self.store.query(sql, params)
            for r in rows:
                r["json"] = json.loads(r["json"])
            return rows
        if name == "db_query":
            rows = self.store.query(args["sql"])
            return rows[: int(args.get("limit", 50))]
        raise ValueError(f"未知工具：{name}")

    # ---------- JSON-RPC 分发 ----------

    def handle(self, msg: dict):
        method = msg.get("method")
        msg_id = msg.get("id")
        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO})
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None  # 通知无响应
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            params = msg.get("params") or {}
            try:
                data = self.call_tool(params.get("name"),
                                      params.get("arguments") or {})
                return self._result(msg_id, {
                    "content": [{"type": "text",
                                 "text": json.dumps(data, ensure_ascii=False,
                                                    default=str)}],
                    "isError": False})
            except Exception as exc:  # 工具错误按 MCP 规范走 isError
                return self._result(msg_id, {
                    "content": [{"type": "text", "text": f"error: {exc}"}],
                    "isError": True})
        if msg_id is not None:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"}}
        return None

    @staticmethod
    def _result(msg_id, result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def serve_stdio(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            resp = self.handle(msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="grading_system.mcp_server")
    default_db = Path(__file__).resolve().parents[2] / "data" / "selection.db"
    parser.add_argument("--db", default=str(default_db))
    args = parser.parse_args(argv)
    McpServer(Path(args.db)).serve_stdio()


if __name__ == "__main__":
    main()
