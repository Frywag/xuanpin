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


class TestDeepReview:
    def _bundle(self):
        from grading_system.llm_tasks import build_deep_review_task
        p = make_packet()
        r = make_result(p)
        return build_deep_review_task(p.to_dict(), r.to_dict(), "run_t")

    def _good_output(self, ev):
        sec = {"assessment": "基于证据的判断", "strengths": [], "concerns": [],
               "evidence_refs": [ev], "confidence": "medium"}
        return {
            "executive_summary": "摘要",
            "sections": {k: dict(sec) for k in
                         ("demand", "competition", "product", "supply_chain", "risk")},
            "grade_challenge": {"agrees_with_engine": True,
                                "suggested_grade": None, "rationale": ""},
            "go_recommendation": {"decision": "hold", "conditions": ["补供应链"]},
            "open_questions": [], "next_actions": [],
            "missing_fields": ["无评论全文"], "assumptions": [],
            "confidence": "medium",
        }

    def test_bundle_contains_full_packet_and_engine_analysis(self):
        b = self._bundle()
        assert b["task_type"] == "deep_review"
        assert b["inputs"]["candidate"]["basic_facts"]["title"] == "Floral Dress"
        assert b["inputs"]["engine_analysis"]["grade"] == "A"
        assert b["allowed_evidence_ids"]

    def test_valid_deep_review_accepted(self):
        b = self._bundle()
        out = self._good_output(b["allowed_evidence_ids"][0])
        assert validate_llm_output(b, out) == []

    def test_section_without_evidence_rejected(self):
        b = self._bundle()
        out = self._good_output(b["allowed_evidence_ids"][0])
        out["sections"]["risk"]["evidence_refs"] = []
        errors = validate_llm_output(b, out)
        assert any("sections.risk" in e for e in errors)

    def test_disagree_without_rationale_rejected(self):
        b = self._bundle()
        out = self._good_output(b["allowed_evidence_ids"][0])
        out["grade_challenge"] = {"agrees_with_engine": False,
                                  "suggested_grade": "B", "rationale": ""}
        errors = validate_llm_output(b, out)
        assert any("rationale" in e for e in errors)


class TestRunReport:
    def _bundle(self):
        from grading_system.llm_tasks import build_run_report_task
        summary = {
            "run_meta": {"run_id": "run_t", "candidates_total": 2234,
                         "grade_distribution": {"S": 3, "A": 42}},
            "derived": {"grade_share_pct": {"S": 0.1, "A": 1.9}},
            "top_candidates": [{"candidate_id": "c1", "title": "Floral Dress",
                                "grade": "A", "pct": 46.2}],
            "top_missing_fields": [],
        }
        return build_run_report_task(summary, "run_t")

    def _good_output(self):
        return {
            "title": "选品分析报告", "executive_summary": "摘要",
            "market_landscape": "格局",
            "track_analysis": [{"track": "balanced", "narrative": "叙述",
                                "top_candidate_ids": ["c1"]}],
            "top_candidates_review": [{"candidate_id": "c1", "one_liner": "点评"}],
            "data_quality_and_gaps": "缺口", "risk_overview": "风险",
            "next_collection_plan": ["补采评论"],
            "missing_fields": [], "assumptions": [],
        }

    def test_valid_report_accepted(self):
        assert validate_llm_output(self._bundle(), self._good_output()) == []

    def test_unknown_candidate_rejected(self):
        out = self._good_output()
        out["top_candidates_review"].append(
            {"candidate_id": "c_fabricated", "one_liner": "编造的候选"})
        errors = validate_llm_output(self._bundle(), out)
        assert any("输入之外的候选" in e for e in errors)

    def test_render_markdown(self):
        from grading_system.llm_tasks import render_markdown
        md = render_markdown(self._bundle(), self._good_output())
        assert "# 选品分析报告" in md
        assert "下一轮采集计划" in md

    def test_inline_candidate_id_mention_not_treated_as_number(self):
        """正文里内联提到候选 id（含数字）是合法引用，不得触发编造判定。"""
        out = self._good_output()
        out["market_landscape"] = "头部候选 c1 与 run_t 的整体格局良好"
        assert validate_llm_output(self._bundle(), out) == []

    def test_truly_new_number_in_narrative_still_rejected(self):
        out = self._good_output()
        out["market_landscape"] = "预计月销售额可达 987654 美元"
        errors = validate_llm_output(self._bundle(), out)
        assert any("编造" in e for e in errors)


class TestProfile:
    def _registry(self):
        import yaml
        from pathlib import Path
        p = Path(__file__).resolve().parents[1] / "configs/profile_keys_v1.yaml"
        return yaml.safe_load(p.read_text(encoding="utf-8"))

    def test_direct_profile_fills_with_evidence(self):
        from grading_system.profile import build_direct_profile
        p = make_packet()
        prof = build_direct_profile(p, self._registry())
        assert prof["产品英文标题"]["value"] == "Floral Dress"
        assert prof["产品英文标题"]["evidence_refs"]
        assert prof["起订量"]["source"] == "manual"   # 供应链键待人工
        assert "评分" not in prof                      # 缺失键不出现，不补 0

    def test_semantic_task_uses_womenswear_vocab(self):
        from grading_system.profile import build_profile_extraction_task
        p = make_packet()   # title: Floral Dress -> 女装词表
        b = build_profile_extraction_task(p, "run_t", self._registry())
        assert b["task_type"] == "profile_extraction"
        assert b["inputs"]["key_set"] == "womenswear"
        assert "款式风格" in b["inputs"]["keys"]

    def test_vocab_violation_rejected(self):
        from grading_system.profile import build_profile_extraction_task
        p = make_packet()
        b = build_profile_extraction_task(p, "run_t", self._registry())
        ev = b["allowed_evidence_ids"][0]
        out = {"profile": {"款式风格": {"value": "Cyberpunk",
                                        "evidence_refs": [ev], "confidence": "high"}},
               "missing_fields": []}
        errors = validate_llm_output(b, out)
        assert any("受控词表" in e for e in errors)
        out["profile"]["款式风格"]["value"] = "Boho"
        assert validate_llm_output(b, out) == []
