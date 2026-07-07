# -*- coding: utf-8 -*-
"""标准信封直投（docs/05 输入契约）与 MCP 服务测试。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from grading_system.envelope_io import load_envelope_files, validate_envelope_dict

PROJECT_DIR = Path(__file__).resolve().parents[1]


def make_envelope(**over):
    d = {
        "schema_version": "source.envelope.v1",
        "envelope_id": "envelope_ext_test_01",
        "source_id": "ext_spider",
        "source_type": "frontend",
        "market": "US",
        "quality_status": "VALID",
        "records": [{
            "record_kind": "product",
            "product_id": "p001",
            "site": "extsite",
            "fields": {
                "title": "External Floral Dress",
                "canonical_url": "https://ext/p001",
                "unit_price": {"amount": 19.9, "currency": "USD"},
                "sales_floor_units": 500,
            },
            "evidence": {
                k: {"evidence_id": f"ev_ext_{i:03d}", "source_id": "ext_spider",
                    "source_type": "frontend", "tool_or_adapter": "ext",
                    "field_path": k, "artifact_path": "oss://raw/p001.json",
                    "record_locator": "item=1", "confidence": "high", "note": ""}
                for i, k in enumerate(
                    ["title", "canonical_url", "unit_price", "sales_floor_units"])
            },
        }],
    }
    d.update(over)
    return d


class TestValidate:
    def test_valid_envelope_passes(self):
        errors, warnings = validate_envelope_dict(make_envelope())
        assert errors == []

    def test_market_mismatch_rejected(self):
        errors, _ = validate_envelope_dict(make_envelope(market="DE"))
        assert any("market" in e for e in errors)

    def test_price_without_currency_rejected(self):
        env = make_envelope()
        env["records"][0]["fields"]["unit_price"] = {"amount": 9.9}
        errors, _ = validate_envelope_dict(env)
        assert any("币种" in e for e in errors)

    def test_no_id_no_url_rejected(self):
        env = make_envelope()
        env["records"][0]["product_id"] = None
        del env["records"][0]["fields"]["canonical_url"]
        errors, _ = validate_envelope_dict(env)
        assert any("product_id" in e for e in errors)

    def test_field_without_evidence_warns(self):
        env = make_envelope()
        env["records"][0]["fields"]["rating"] = 4.2
        errors, warnings = validate_envelope_dict(env)
        assert errors == []
        assert any("rating" in w for w in warnings)


class TestLoadAndPipeline:
    def test_load_from_dir(self, tmp_path):
        (tmp_path / "a.json").write_text(
            json.dumps(make_envelope(), ensure_ascii=False), encoding="utf-8")
        envs, problems = load_envelope_files(tmp_path)
        assert len(envs) == 1
        assert envs[0].source_id == "ext_spider"

    def test_bad_envelope_rejected_whole(self, tmp_path):
        bad = make_envelope(market="DE")
        (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
        envs, problems = load_envelope_files(tmp_path)
        assert envs == []
        assert any("拒收" in p for p in problems)

    def test_envelope_enters_packet_builder(self, tmp_path):
        from grading_system.packet_builder import PacketBuilder
        (tmp_path / "a.json").write_text(
            json.dumps(make_envelope(), ensure_ascii=False), encoding="utf-8")
        envs, _ = load_envelope_files(tmp_path)
        packets = PacketBuilder().build(envs)
        assert len(packets) == 1
        p = packets[0]
        assert p.candidate_id == "extsite_p001"
        assert p.basic_facts["price"] == {"amount": 19.9, "currency": "USD"}
        assert p.market_metrics["sales_floor_units"] == 500
        assert p.evidence_for("basic_facts.title")


class TestMcpServer:
    @pytest.fixture(scope="class")
    def db_path(self, tmp_path_factory):
        from grading_system.store import SelectionStore
        from grading_system.models import AnalysisResult, CandidateDataPacket, EvidenceRef
        db = tmp_path_factory.mktemp("mcp") / "sel.db"
        store = SelectionStore(db)
        p = CandidateDataPacket("c1", "US", "shein", "111", "https://x/111")
        p.source_refs = [{"source_id": "shein_frontend", "envelope_id": "e1"}]
        p.set_fact("basic_facts", "title", "Dress",
                   EvidenceRef("ev_1", "shein_frontend", "frontend", "T",
                               "basic_facts.title", "a.xlsx", "sheet=s;row=2"))
        r = AnalysisResult("c1", "US")
        r.priority_score.update({"total": 30.0, "achievable_max": 65.0, "pct": 46.2,
                                 "grade": "A", "track": "balanced",
                                 "track_label": "均衡款", "components": []})
        store.record_run({"run_id": "run_m", "market": "US", "candidates_total": 1,
                          "l2_processed": 0, "l3_processed": 0,
                          "grade_distribution": {"A": 1}}, [], [p], {"c1": r})
        store.close()
        return db

    def _rpc(self, proc, msg):
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    def test_stdio_roundtrip(self, db_path):
        proc = subprocess.Popen(
            [sys.executable, "-m", "grading_system.mcp_server", "--db", str(db_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            env={"PYTHONPATH": str(PROJECT_DIR / "src"), "PATH": "/usr/bin:/bin"})
        try:
            resp = self._rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                    "params": {"protocolVersion": "2024-11-05",
                                               "capabilities": {}}})
            assert resp["result"]["serverInfo"]["name"] == "xuanpin-selection-store"
            resp = self._rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = {t["name"] for t in resp["result"]["tools"]}
            assert {"get_candidate", "db_query", "query_grades"} <= names
            resp = self._rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                    "params": {"name": "get_candidate",
                                               "arguments": {"candidate_id": "c1"}}})
            payload = json.loads(resp["result"]["content"][0]["text"])
            assert payload["result"]["priority_score"]["grade"] == "A"
            # 只读防护：写语句必须报 isError
            resp = self._rpc(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                    "params": {"name": "db_query",
                                               "arguments": {"sql": "DELETE FROM runs"}}})
            assert resp["result"]["isError"] is True
        finally:
            proc.terminate()
