# -*- coding: utf-8 -*-
"""三赛道主链 v2（2026-07-16 重构）：开发方向、销量形态赛道、分层淘汰、趋势词。"""
from pathlib import Path

import yaml

from grading_system.models import CandidateDataPacket, EvidenceRef
from grading_system.scoring import GroupStats
from grading_system.tracks_v2 import (TRACK_IDS_V2, TracksV2Evaluator,
                                      build_amazon_word_freq)

CFG = yaml.safe_load((Path(__file__).resolve().parents[1] /
                      "configs/tracks_v2.yaml").read_text(encoding="utf-8"))


def ev(path, eid):
    return EvidenceRef(eid, "s1", "frontend", "T", path, "a.xlsx", "sheet=s;row=2")


def pk(cid, group="shein_frontend", roles=("transaction",), sales=None,
       rating=None, rc=None, title="Lace Maxi Dress"):
    p = CandidateDataPacket(cid, "US", "site", "p1", "https://x/p1")
    p.source_refs = [{"source_id": group, "envelope_id": "e1"}]
    p.context["source_group"] = group
    p.context["source_roles"] = list(roles)
    p.set_fact("basic_facts", "title", title, ev("basic_facts.title", f"ev_{cid}_t"))
    if sales is not None:
        p.set_fact("market_metrics", "sales_30d_units", sales,
                   ev("market_metrics.sales_30d_units", f"ev_{cid}_s"))
    if rating is not None:
        p.set_fact("basic_facts", "rating", rating, ev("basic_facts.rating", f"ev_{cid}_r"))
    if rc is not None:
        p.set_fact("basic_facts", "review_count", rc,
                   ev("basic_facts.review_count", f"ev_{cid}_rc"))
    return p


def cohort(n=10, base=100):
    """同组 n 个候选，销量递增（保证组内分位可算）。"""
    return [pk(f"c{i}", sales=base * (i + 1)) for i in range(n)]


def run_v2(packets):
    return TracksV2Evaluator(CFG, GroupStats(packets), "run_t").run_v2(packets)


class TestDirection:
    def test_puhuo_unconditional_and_gaikuan_requires_reviews(self):
        packets = cohort()
        packets[0].set_fact("basic_facts", "rating", 4.0, ev("basic_facts.rating", "ev_r0"))
        packets[0].set_fact("basic_facts", "review_count", 200,
                            ev("basic_facts.review_count", "ev_rc0"))
        _, _, _, _, directions = run_v2(packets)
        d0 = directions["c0"]
        assert "铺货" in d0["options"] and "改款" in d0["options"]
        assert d0["basis"] == "provisional_rule"     # 差评比例缺失→评分代理占位
        d1 = directions["c1"]                        # 无评价数据：只有铺货
        assert d1["options"] == ["铺货"]

    def test_gaikuan_not_offered_for_good_rating(self):
        packets = cohort()
        packets[0].set_fact("basic_facts", "rating", 4.8, ev("basic_facts.rating", "ev_r0"))
        packets[0].set_fact("basic_facts", "review_count", 500,
                            ev("basic_facts.review_count", "ev_rc0"))
        _, _, _, _, directions = run_v2(packets)
        assert directions["c0"]["options"] == ["铺货"]


class TestSalesFormTracks:
    def test_invariant_three_records(self):
        packets = cohort(5)
        evals, _, _, _, _ = run_v2(packets)
        assert len(evals) == 15
        for p in packets:
            assert {e.track_id for e in evals
                    if e.subject_id == p.candidate_id} == set(TRACK_IDS_V2)

    def test_hit_requires_high_sales(self):
        packets = cohort()
        evals, _, _, _, _ = run_v2(packets)
        top = next(e for e in evals if e.subject_id == "c9" and e.track_id == "hit")
        low = next(e for e in evals if e.subject_id == "c0" and e.track_id == "hit")
        assert top.admission_status == "ELIGIBLE"
        assert low.admission_status == "REJECTED"

    def test_gaikuan_direction_relaxes_sales_requirement(self):
        """业务指示：需要改款的产品（有差评）销量要求适当放低。"""
        packets = cohort()
        mid = packets[6]                       # 分位 0.7 < 爆款门限 0.75
        ev_hit_strict = TracksV2Evaluator(CFG, GroupStats(packets), "r").run_v2(
            [p for p in packets])[0]
        strict = next(e for e in ev_hit_strict
                      if e.subject_id == "c6" and e.track_id == "hit")
        assert strict.admission_status == "REJECTED"
        packets2 = cohort()
        packets2[6].set_fact("basic_facts", "rating", 4.0,
                             ev("basic_facts.rating", "ev_r6"))
        packets2[6].set_fact("basic_facts", "review_count", 200,
                             ev("basic_facts.review_count", "ev_rc6"))
        evals2, _, _, _, _ = run_v2(packets2)
        relaxed = next(e for e in evals2
                       if e.subject_id == "c6" and e.track_id == "hit")
        assert relaxed.admission_status == "ELIGIBLE"   # 0.7 ≥ 0.75*0.7

    def test_long_tail_low_rating_needs_gaikuan(self):
        packets = cohort()
        packets[5].set_fact("basic_facts", "rating", 3.5,
                            ev("basic_facts.rating", "ev_r5"))
        evals, _, _, _, _ = run_v2(packets)
        e = next(x for x in evals if x.subject_id == "c5" and x.track_id == "long_tail")
        assert e.admission_status == "REJECTED"          # 低分且无改款方向
        packets2 = cohort()
        packets2[5].set_fact("basic_facts", "rating", 3.5,
                             ev("basic_facts.rating", "ev_r5"))
        packets2[5].set_fact("basic_facts", "review_count", 200,
                             ev("basic_facts.review_count", "ev_rc5"))
        evals2, _, _, _, _ = run_v2(packets2)
        e2 = next(x for x in evals2 if x.subject_id == "c5" and x.track_id == "long_tail")
        assert e2.admission_status == "ELIGIBLE"         # 有改款方向不阻断

    def test_no_sales_is_pending_not_rejected(self):
        packets = cohort() + [pk("nx", group="indie_frontend", roles=("discovery",))]
        evals, _, _, _, _ = run_v2(packets)
        e = next(x for x in evals if x.subject_id == "nx" and x.track_id == "hit")
        assert e.admission_status == "PENDING_DATA"


class TestLayeredElimination:
    def test_s_requires_l3(self):
        """分层淘汰：L1 只能到 B，L2 到 A，只有走完 L3 才可能 S。"""
        packets = cohort(30, base=50)
        evals, _, budgets, _, _ = run_v2(packets)
        for e in evals:
            if e.admission_status != "ELIGIBLE":
                continue
            if e.grade == "S":
                assert e.layer_reached == "L3"
            if e.layer_reached == "L1":
                assert e.grade == "B"
        for tid in TRACK_IDS_V2:
            assert set(budgets[tid]) == {"l2_queue", "l3_queue", "refetch_queue"}
            assert len(budgets[tid]["l2_queue"]) <= CFG["tracks"][tid]["l2_budget_draft"]


class TestTrendWordSystem:
    def test_words_from_discovery_sources_graded_abcde(self):
        packets = cohort()   # 交易源：不参与词体系
        for i, t in enumerate(["Ruched Corset Maxi Dress", "Ruched Corset Top",
                               "Plaid Corset Dress"]):
            packets.append(pk(f"w{i}", group=f"indie{i}", roles=("discovery",), title=t))
        _, _, _, words, _ = run_v2(packets)
        assert words, "发现源标题必须产出趋势词"
        by_word = {w["word"]: w for w in words}
        assert "corset" in by_word
        assert by_word["corset"]["grade"] in "ABCDE"
        assert len(by_word["corset"]["sites"]) == 3
        # 交易源的词不进入体系（只针对头部独立站/社媒）
        assert all(set(w["sites"]) & {"indie0", "indie1", "indie2"} for w in words)

    def test_amazon_freq_corpus(self):
        packets = [pk("a1", group="amazon_us", title="Basic Corset Dress"),
                   pk("a2", group="amazon_us", title="Corset Belt")]
        freq = build_amazon_word_freq(packets)
        assert freq["corset"] == 2
