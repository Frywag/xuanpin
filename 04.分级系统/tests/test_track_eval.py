# -*- coding: utf-8 -*-
"""三赛道独立评估：验收场景测试（交接清单 §12）。"""
from pathlib import Path

import pytest
import yaml

from grading_system.models import CandidateDataPacket, EvidenceRef
from grading_system.scoring import GroupStats
from grading_system.track_eval import TRACK_IDS, TrackEvaluator, assess_freshness

CFG = yaml.safe_load((Path(__file__).resolve().parents[1] /
                      "configs/tracks_v1.yaml").read_text(encoding="utf-8"))


def ev(path, eid, src="s1"):
    return EvidenceRef(eid, src, "frontend", "T", path, "a.xlsx", "sheet=s;row=2")


def base_packet(cid="c1", group="indie_frontend", roles=("discovery",)):
    p = CandidateDataPacket(cid, "US", "site", "p1", "https://x/p1")
    p.source_refs = [{"source_id": group, "envelope_id": "e1"}]
    p.context["source_group"] = group
    p.context["source_roles"] = list(roles)
    p.set_fact("basic_facts", "title", "Lace Maxi Dress", ev("basic_facts.title", f"ev_{cid}_t"))
    return p


def make_evaluator(packets):
    return TrackEvaluator(CFG, GroupStats(packets), "run_t")


class TestInvariants:
    def test_three_records_per_candidate(self):
        """场景/验收：每个候选每个 run 恰好三条候选级 TrackEvaluation。"""
        packets = [base_packet(f"c{i}") for i in range(5)]
        evals, _, _ = make_evaluator(packets).run(packets)
        assert len(evals) == 15
        for p in packets:
            assert {e.track_id for e in evals if e.subject_id == p.candidate_id} == set(TRACK_IDS)

    def test_pending_and_rejected_have_no_grade(self):
        p = base_packet()
        evals, _, _ = make_evaluator([p]).run([p])
        for e in evals:
            if e.admission_status != "ELIGIBLE":
                assert e.grade is None          # 正式 grade 为空
                assert e.legacy_grade == "C"    # 仅旧表兼容

    def test_determinism(self):
        packets = [base_packet(f"c{i}") for i in range(3)]
        r1 = make_evaluator(packets).run(packets)[0]
        packets2 = [base_packet(f"c{i}") for i in range(3)]
        r2 = make_evaluator(packets2).run(packets2)[0]
        assert [(e.subject_id, e.track_id, e.admission_status, e.grade) for e in r1] \
            == [(e.subject_id, e.track_id, e.admission_status, e.grade) for e in r2]

    def test_results_carry_calibration_status(self):
        p = base_packet()
        evals, _, _ = make_evaluator([p]).run([p])
        assert all("calibration" in e.rule_status for e in evals)


class TestTrendNew:
    def test_scene1_no_sales_but_both_evidences_is_eligible(self):
        """场景一：有近期推出+款型新颖度证据、无任何销量 -> ELIGIBLE 并给级。"""
        p = base_packet("cn1")
        p.freshness.update({"freshness_status": "confirmed",
                            "new_arrival_flag": "page_new_arrival",
                            "novelty_status": "confirmed",
                            "freshness_evidence_refs": ["ev_f1"],
                            "novelty_evidence_refs": ["ev_n1"]})
        e = make_evaluator([p]).eval_trend_new(p, None)
        assert e.admission_status == "ELIGIBLE"
        assert e.grade in ("S", "A", "B")   # 无销量绝不淘汰（单源早期可 B）
        assert e.evidence_refs              # 新款证据 100% 绑定

    def test_scene7_first_seen_only_is_pending(self):
        """场景七：只有系统首次采集，无上架/新品/新颖度证据 -> PENDING_DATA。"""
        p = base_packet("cn2")
        assess_freshness(p)
        e = make_evaluator([p]).eval_trend_new(p, None)
        assert e.admission_status == "PENDING_DATA"
        assert e.grade is None

    def test_scene8_confirmed_old_style_rejected(self):
        p = base_packet("cn3")
        p.freshness["novelty_status"] = "old"
        p.market_metrics["sales_growth_rate"] = 1.5   # 销量增长不能覆盖旧款结论
        e = make_evaluator([p]).eval_trend_new(p, None)
        assert e.admission_status == "REJECTED"

    def test_scene5_indie_discovery_not_structurally_rejected(self):
        """场景五：独立站缺交易字段，按发现源参与趋势判断，不被淘汰为 REJECTED。"""
        p = base_packet("cn4", group="indie_frontend")
        assess_freshness(p)
        e = make_evaluator([p]).eval_trend_new(p, None)
        assert e.admission_status == "PENDING_DATA"   # 待补，而非否定

    def test_shein_new_arrival_collection_is_recent_evidence(self):
        p = base_packet("cn5", group="shein_frontend")
        p.set_fact("market_metrics", "bestseller_rank_text",
                   "#8 Bestseller in New Arrival Mini Casual Dresses",
                   ev("market_metrics.bestseller_rank_text", "ev_cn5_b"))
        assess_freshness(p)
        assert p.freshness["freshness_status"] == "inferred"
        assert p.freshness["freshness_evidence_refs"]


class TestHitImprovement:
    def _demand_packet(self, cid, rating, rc, clusters=0):
        p = base_packet(cid, group="shein_frontend", roles=("transaction", "voc"))
        p.set_fact("market_metrics", "sales_floor_units", 800,
                   ev("market_metrics.sales_floor_units", f"ev_{cid}_s"))
        if rating is not None:
            p.set_fact("basic_facts", "rating", rating, ev("basic_facts.rating", f"ev_{cid}_r"))
            p.set_fact("basic_facts", "review_count", rc,
                       ev("basic_facts.review_count", f"ev_{cid}_rc"))
        p.product_opportunity["negative_review_clusters"] = [
            {"cluster_name": f"痛点{i}", "mention_count": 5,
             "evidence_refs": [f"ev_{cid}_c{i}"]} for i in range(clusters)]
        return p

    def test_scene3_joint_admission(self):
        p = self._demand_packet("h1", rating=3.8, rc=120, clusters=3)
        e = make_evaluator([p]).eval_hit_improvement(p)
        assert e.admission_status == "ELIGIBLE"
        assert e.grade in ("S", "A", "B")

    def test_scene4_low_rating_small_sample_not_eligible(self):
        """场景四：低评分但样本很少 -> 不得进入高等级（待补）。"""
        p = self._demand_packet("h2", rating=3.6, rc=5, clusters=0)
        e = make_evaluator([p]).eval_hit_improvement(p)
        assert e.admission_status == "PENDING_DATA"

    def test_normal_rating_rejected(self):
        p = self._demand_packet("h3", rating=4.7, rc=200, clusters=0)
        e = make_evaluator([p]).eval_hit_improvement(p)
        assert e.admission_status == "REJECTED"

    def test_missing_voc_is_pending_not_graded(self):
        p = self._demand_packet("h4", rating=3.8, rc=120, clusters=0)
        e = make_evaluator([p]).eval_hit_improvement(p)
        assert e.admission_status == "PENDING_DATA"
        assert any("聚类" in m or "评论" in m for m in e.missing_fields)


class TestLongTailAndBudgets:
    def test_not_a_catchall(self):
        """未命中前两赛道 ≠ 自动进入长尾：无交易证据 -> PENDING_DATA。"""
        p = base_packet("l1")
        e = make_evaluator([p]).eval_long_tail(p)
        assert e.admission_status == "PENDING_DATA"

    def test_scene6_budgets_independent(self):
        """场景六：三赛道 L2/L3 名额互不挤占（各自独立队列）。"""
        packets = [base_packet(f"b{i}") for i in range(4)]
        packets[0].freshness.update({"freshness_status": "confirmed",
                                     "novelty_status": "confirmed"})
        _, _, budgets = make_evaluator(packets).run(packets)
        assert set(budgets) == set(TRACK_IDS)
        for tid in TRACK_IDS:
            assert set(budgets[tid]) == {"l2_queue", "l3_queue"}
