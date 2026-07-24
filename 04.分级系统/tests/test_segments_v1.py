# -*- coding: utf-8 -*-
"""segments.v1（2026-07-24 总方案）：三平台独立、L3 硬筛、细分市场对象、人工 Gate。"""
import copy
from pathlib import Path

import yaml

from grading_system.models import CandidateDataPacket, EvidenceRef
from grading_system.segments_v1 import SegmentsV1Pipeline, platform_of

CFG = yaml.safe_load((Path(__file__).resolve().parents[1] /
                      "configs/segments_v1.yaml").read_text(encoding="utf-8"))


def ev(path, eid):
    return EvidenceRef(eid, "s1", "frontend", "T", path, "a.xlsx", "r2")


def pk(cid, group, sales=None, title="Lace Corset Dress", rating=None, rc=None):
    p = CandidateDataPacket(cid, "US", "site", "p1", "https://x/p1")
    p.context["source_group"] = group
    p.set_fact("basic_facts", "title", title, ev("basic_facts.title", f"e_{cid}t"))
    if sales is not None:
        p.set_fact("market_metrics", "sales_30d_units", sales,
                   ev("market_metrics.sales_30d_units", f"e_{cid}s"))
    if rating is not None:
        p.set_fact("basic_facts", "rating", rating, ev("basic_facts.rating", f"e_{cid}r"))
        p.set_fact("basic_facts", "review_count", rc or 0,
                   ev("basic_facts.review_count", f"e_{cid}rc"))
    return p


def cohort():
    ps = [pk(f"s{i}", "shein_frontend", sales=100 * (i + 1)) for i in range(10)]
    ps += [pk(f"t{i}", "tiktok_shop_plugin", sales=50 * (i + 1),
              title="Seamless Shapewear Bodysuit") for i in range(10)]
    ps.append(pk("indie1", "indie_frontend", title="Plaid Wrap Skirt"))
    return ps


class TestSegmentsV1:
    def test_platform_isolation_and_trend_exclusion(self):
        out = SegmentsV1Pipeline(CFG, "r1").run(cohort())
        assert set(out["platform_runs"]) == {"shein", "tiktok"}
        assert out["excluded_to_trend_system"] == 1     # 独立站→趋势体系
        assert out["scope"]["platform_task_status"]["amazon"] == "RUN_PENDING"
        assert out["scope"]["scope_status"] == "INCOMPLETE_SCOPE"
        assert "不发布" in out["scope"]["advantage_labels"]

    def test_l3_sales_is_hard_gate_no_exceptions(self):
        ps = cohort() + [pk("nosale", "shein_frontend", sales=None)]
        out = SegmentsV1Pipeline(CFG, "r1").run(ps)
        l3 = {d["product_id"]: d for d in out["platform_runs"]["shein"]["l3"]}
        assert l3["nosale"]["status"] == "L3_REJECTED"
        assert "无可验证销量" in l3["nosale"]["reason"]
        assert l3["s0"]["status"] == "L3_REJECTED"      # 分位不足
        assert l3["s9"]["status"] == "L3_PASS"

    def test_unique_primary_segment_and_pilot_approval(self):
        out = SegmentsV1Pipeline(CFG, "r1").run(cohort())
        for r in out["platform_runs"].values():
            seen = {}
            for a in r["assignments"]:
                assert a["status"] == "HUMAN_APPROVED"
                assert "pilot" in a["approver"]          # 试点批量确认留痕
                assert a["product_id"] not in seen       # 唯一 primary_segment
                seen[a["product_id"]] = a["primary_segment_id"]

    def test_human_gate_default_queue_when_pilot_off(self):
        cfg = copy.deepcopy(CFG)
        cfg["human_gate"]["pilot_auto_approve"] = False
        out = SegmentsV1Pipeline(cfg, "r1").run(cohort())
        r = out["platform_runs"]["shein"]
        assert not r["assignments"]                      # 无人工确认→不进 L2
        assert r["review_queue"] and not r["evals"]

    def test_segment_is_evaluation_object_with_tier_rules(self):
        out = SegmentsV1Pipeline(CFG, "r1").run(cohort())
        for r in out["platform_runs"].values():
            for e in r["evals"]:
                assert e["segment_id"].startswith("seg_")
                if e["final_tier"] in ("S", "A"):
                    assert e["l1_gate"] == "PASS"        # 仅 L1 PASS 可 A/S
                if e.get("l1_gate") == "PENDING_DATA":
                    assert e["final_tier"] is None       # 待补不伪装低等级

    def test_opportunity_gate_and_strategy_dual_axis(self):
        out = SegmentsV1Pipeline(CFG, "r1").run(cohort())
        for d in out["opportunity_decisions"]:
            assert d["decision"] == "PENDING_REVIEW"     # 入选须人工批准
        sp = SegmentsV1Pipeline(CFG, "r1")
        low = pk("m1", "shein_frontend", sales=900, rating=4.0, rc=120)
        st = sp.product_strategy(low, txn_pct=0.9)
        assert st["development_action"] == "MODIFICATION"
        assert st["operating_type"] == "HIT_POTENTIAL"   # 改款+爆款型可并存
        mid = pk("m2", "shein_frontend", sales=300)
        st2 = sp.product_strategy(mid, txn_pct=0.5)
        assert st2["operating_type"] == "LONG_TAIL"
