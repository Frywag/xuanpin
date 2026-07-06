# -*- coding: utf-8 -*-
"""打分引擎与 Gate 规则测试（合成候选，阈值来自 scoring_v0.yaml）。"""
from pathlib import Path

import pytest
import yaml

from grading_system.gates import GateRunner
from grading_system.models import CandidateDataPacket, EvidenceRef
from grading_system.scoring import GroupStats, PriorityScorer, eval_bands

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"


@pytest.fixture(scope="module")
def scoring_cfg():
    return yaml.safe_load((CONFIG_DIR / "scoring_v0.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def gates_cfg():
    return yaml.safe_load((CONFIG_DIR / "layer_gates_v0.yaml").read_text(encoding="utf-8"))


def ev(field_path, source_id="kalodata", eid=None):
    ev.counter = getattr(ev, "counter", 0) + 1
    return EvidenceRef(
        evidence_id=eid or f"ev_t_{ev.counter:04d}", source_id=source_id,
        source_type="plugin", tool_or_adapter="T", field_path=field_path,
        artifact_path="x.xlsx", record_locator="sheet=s;row=2")


def strong_packet() -> CandidateDataPacket:
    """双源、强需求、低集中度、带评分——对应 S/A 头部候选的形态。"""
    p = CandidateDataPacket("c_strong", "US", "tiktok_shop", "123", "https://x/123")
    p.source_refs = [{"source_id": "kalodata", "envelope_id": "e1"},
                     {"source_id": "tabcut", "envelope_id": "e2"}]
    p.context["source_group"] = "tiktok_shop_plugin"
    p.set_fact("basic_facts", "title", "Wireless Bra", ev("basic_facts.title"))
    p.set_fact("basic_facts", "price", {"amount": 108.51, "currency": "CNY"},
               ev("basic_facts.price"))
    p.set_fact("basic_facts", "rating", 4.4, ev("basic_facts.rating"))
    p.set_fact("basic_facts", "review_count", 68609, ev("basic_facts.review_count"))
    p.set_fact("market_metrics", "sales_30d_units", 86499,
               ev("market_metrics.sales_30d_units"))
    p.set_fact("market_metrics", "sales_growth_rate", 0.55,
               ev("market_metrics.sales_growth_rate", source_id="tabcut"))
    p.set_fact("market_metrics", "video_revenue_share", 0.93,
               ev("market_metrics.video_revenue_share"))
    p.set_fact("competition_metrics", "category_top10_shop_ratio", 0.1186,
               ev("competition_metrics.category_top10_shop_ratio"))
    # 第二源需求证据（多源互验）
    p.set_fact("market_metrics", "sales_7d_units", 20000,
               ev("market_metrics.sales_7d_units", source_id="tabcut"))
    return p


def empty_packet() -> CandidateDataPacket:
    p = CandidateDataPacket("c_empty", "US", "unknown_site", "p9")
    p.source_refs = [{"source_id": "patterned_tights_frontend", "envelope_id": "e9"}]
    p.set_fact("basic_facts", "title", "Some Tights",
               ev("basic_facts.title", source_id="patterned_tights_frontend"))
    return p


class TestBands:
    def test_first_match_wins(self):
        bands = [{"gte": 80000, "points": 15}, {"gte": 30000, "points": 12},
                 {"gt": 0, "points": 3}]
        assert eval_bands(86499, bands) == 15
        assert eval_bands(30000, bands) == 12
        assert eval_bands(1, bands) == 3
        assert eval_bands(0, bands) is None


class TestScorer:
    def test_strong_candidate_scores_high(self, scoring_cfg):
        p = strong_packet()
        scorer = PriorityScorer(scoring_cfg, GroupStats([p]))
        out = scorer.score(p)
        demand = next(c for c in out["components"] if c.name == "市场需求")
        assert demand.score >= 20      # 15 量级 + 5 动能 + 广度
        assert out["pct"] > 45
        # 每个得分组件必须有证据
        for c in out["components"]:
            if c.score and c.score > 0 and c.max_score > 0:
                assert c.evidence_refs, f"组件 {c.name} 得分但无证据"

    def test_supply_dimension_missing_not_zero_filled(self, scoring_cfg):
        p = strong_packet()
        scorer = PriorityScorer(scoring_cfg, GroupStats([p]))
        out = scorer.score(p)
        supply = next(c for c in out["components"] if c.name == "自有供应链")
        assert supply.score is None      # 缺失就是缺失，不是 0 分
        assert supply.missing
        assert out["achievable_max"] == 65   # 25+20+20，风险不计入

    def test_no_demand_signal_scores_low(self, scoring_cfg):
        p = empty_packet()
        scorer = PriorityScorer(scoring_cfg, GroupStats([p]))
        out = scorer.score(p)
        assert out["grade"] == "C"
        assert "market_metrics.<any_demand_signal>" in p.missing_fields

    def test_single_source_cannot_reach_s(self, scoring_cfg):
        p = strong_packet()
        p.source_refs = [{"source_id": "kalodata", "envelope_id": "e1"}]
        # 把第二源证据改回单源
        for e in p.evidence_pack:
            e.source_id = "kalodata"
        scorer = PriorityScorer(scoring_cfg, GroupStats([p]))
        out = scorer.score(p)
        assert out["grade"] != "S"


class TestGates:
    def test_l1_blocks_candidate_without_id_and_url(self, scoring_cfg, gates_cfg):
        p = CandidateDataPacket("c_block", "US", "tiktok_shop", None, None)
        p.source_refs = [{"source_id": "kalodata", "envelope_id": "e1"}]
        scorer = PriorityScorer(scoring_cfg, GroupStats([p]))
        runner = GateRunner(scorer, gates_cfg)
        results = runner.run_l1([p])
        r = results["c_block"]
        assert r.blocked_reasons == ["no_product_id_and_no_url"]
        assert r.recommendation["decision"] == "blocked"

    def test_l2_only_processes_eligible_and_capped(self, scoring_cfg, gates_cfg):
        packets = [strong_packet(), empty_packet()]
        scorer = PriorityScorer(scoring_cfg, GroupStats(packets))
        runner = GateRunner(scorer, gates_cfg)
        results = runner.run_l1(packets)
        l2_ids = runner.run_l2(packets, results)
        assert "c_strong" in l2_ids
        assert "c_empty" not in l2_ids   # C 级不进 L2（成本控制）

    def test_l1_reasons_all_have_evidence(self, scoring_cfg, gates_cfg):
        packets = [strong_packet()]
        scorer = PriorityScorer(scoring_cfg, GroupStats(packets))
        runner = GateRunner(scorer, gates_cfg)
        results = runner.run_l1(packets)
        for r in results.values():
            for claim in r.claims:
                assert claim.evidence_refs, "推荐/风险理由必须绑定证据"
