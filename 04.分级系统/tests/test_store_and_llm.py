# -*- coding: utf-8 -*-
"""选品库（SQLite）与 LLM 任务契约测试。"""
import json

import pytest

from grading_system.llm_tasks import (build_reason_writer_task,
                                      build_review_clustering_task,
                                      validate_llm_output)
from grading_system.models import AnalysisResult, CandidateDataPacket, EvidenceRef
from grading_system.store import SelectionStore


def make_packet():
    p = CandidateDataPacket("c1", "US", "shein", "111", "https://x/111")
    p.source_refs = [{"source_id": "shein_frontend", "envelope_id": "e1"}]
    p.context["source_group"] = "shein_frontend"
    ev = EvidenceRef("ev_a1", "shein_frontend", "frontend", "T",
                     "basic_facts.title", "a.xlsx", "sheet=shein;row=2")
    p.set_fact("basic_facts", "title", "Floral Dress", ev)
    ev2 = EvidenceRef("ev_a2", "shein_frontend", "frontend", "T",
                      "context.review_tags_raw", "a.xlsx", "sheet=shein;row=2")
    p.context["review_tags_raw"] = "Runs Large (24)Soft (2)"
    p.add_evidence(ev2, "context.review_tags_raw")
    return p


def make_result(p):
    r = AnalysisResult(p.candidate_id, "US")
    r.priority_score.update({"total": 30.0, "achievable_max": 65.0, "pct": 46.2,
                             "grade": "A", "track": "balanced",
                             "track_label": "均衡款", "components": []})
    r.layer_results["L1"].grade = "A"
    return r


class TestStore:
    def test_roundtrip_and_query(self, tmp_path):
        store = SelectionStore(tmp_path / "t.db")
        p = make_packet()
        r = make_result(p)
        meta = {"run_id": "run_t", "started_at": "2026-07-06T00:00:00",
                "finished_at": "2026-07-06T00:01:00", "market": "US",
                "candidates_total": 1, "l2_processed": 0, "l3_processed": 0,
                "grade_distribution": {"A": 1}}
        store.record_run(meta, [], [p], {p.candidate_id: r}, {"balanced": 1})
        assert store.latest_run_id() == "run_t"
        rows = store.query("SELECT grade, pct FROM results WHERE run_id='run_t'")
        assert rows == [{"grade": "A", "pct": 46.2}]
        got = store.get_candidate("c1")
        assert got["packet"]["basic_facts"]["title"] == "Floral Dress"
        assert got["result"]["priority_score"]["grade"] == "A"
        chain = store.evidence_chain("c1")
        assert any(e["evidence_id"] == "ev_a1" for e in chain)
        store.close()

    def test_query_is_readonly(self, tmp_path):
        store = SelectionStore(tmp_path / "t.db")
        with pytest.raises(ValueError):
            store.query("DELETE FROM runs")
        store.close()

    def test_rerun_same_run_id_replaces(self, tmp_path):
        store = SelectionStore(tmp_path / "t.db")
        p = make_packet()
        r = make_result(p)
        meta = {"run_id": "run_t", "market": "US", "candidates_total": 1,
                "l2_processed": 0, "l3_processed": 0, "grade_distribution": {}}
        store.record_run(meta, [], [p], {p.candidate_id: r})
        store.record_run(meta, [], [p], {p.candidate_id: r})  # 幂等重跑
        rows = store.query("SELECT COUNT(*) AS n FROM candidates")
        assert rows[0]["n"] == 1
        store.close()


class TestLLMTasks:
    def test_bundle_contains_whitelist_and_rules(self):
        p = make_packet()
        bundle = build_review_clustering_task(p.to_dict(), "run_t")
        assert bundle["task_type"] == "review_clustering"
        assert "ev_a2" in bundle["allowed_evidence_ids"]
        assert any("编造" in r for r in bundle["rules"])

    def test_validate_rejects_foreign_evidence(self):
        p = make_packet()
        bundle = build_review_clustering_task(p.to_dict(), "run_t")
        bad = {"clusters": [{"cluster_name": "尺码偏大",
                             "evidence_refs": ["ev_fake_999"]}],
               "missing_fields": []}
        errors = validate_llm_output(bundle, bad)
        assert any("白名单外" in e for e in errors)

    def test_validate_rejects_fabricated_numbers(self):
        p = make_packet()
        bundle = build_review_clustering_task(p.to_dict(), "run_t")
        bad = {"clusters": [{"cluster_name": "尺码偏大",
                             "evidence_refs": ["ev_a2"],
                             "representative_reviews": ["月销 58,300 件"]}],
               "missing_fields": []}
        errors = validate_llm_output(bundle, bad)
        assert any("编造" in e for e in errors)

    def test_validate_accepts_clean_output(self):
        p = make_packet()
        bundle = build_review_clustering_task(p.to_dict(), "run_t")
        good = {"clusters": [{"cluster_name": "尺码偏大", "frequency_hint": "high",
                              "representative_reviews": ["Runs Large (24)"],
                              "improvement_opportunity": "版型收正",
                              "risk_if_unfixed": "退货率偏高",
                              "evidence_refs": ["ev_a2"]}],
                "missing_fields": ["无评论全文"], "assumptions": [],
                "confidence": "medium", "blocked_reasons": []}
        assert validate_llm_output(bundle, good) == []

    def test_validate_ignores_digits_in_evidence_ids(self):
        """证据 id 里的数字（ev_..._001353）不是事实数值，不得触发编造判定。"""
        p = make_packet()
        bundle = build_review_clustering_task(p.to_dict(), "run_t")
        bundle["allowed_evidence_ids"] = ["ev_shein_frontend_001353"]
        good = {"clusters": [{"cluster_name": "面料偏透", "frequency_hint": "low",
                              "representative_reviews": ["Runs Large (24)"],
                              "improvement_opportunity": "加里衬",
                              "risk_if_unfixed": "退货",
                              "evidence_refs": ["ev_shein_frontend_001353"]}],
                "missing_fields": [], "assumptions": [], "confidence": "medium",
                "blocked_reasons": []}
        assert validate_llm_output(bundle, good) == []

    def test_reason_writer_bundle(self):
        p = make_packet()
        r = make_result(p)
        bundle = build_reason_writer_task(p.to_dict(), r.to_dict(), "run_t")
        assert bundle["task_type"] == "reason_writer"
        assert bundle["inputs"]["grade"] == "A"
