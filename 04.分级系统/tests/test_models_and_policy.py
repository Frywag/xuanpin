# -*- coding: utf-8 -*-
"""数据契约与缺失数据策略测试。"""
import pytest

from grading_system.models import CandidateDataPacket, Claim, EvidenceRef


def make_ev(field_path="basic_facts.title", eid="ev_test_000001"):
    return EvidenceRef(
        evidence_id=eid, source_id="kalodata", source_type="plugin",
        tool_or_adapter="Test", field_path=field_path,
        artifact_path="02.插件数据源/Kalodata最终交付原数据.xlsx",
        record_locator="sheet=x;row=2")


class TestClaimEvidenceRequired:
    def test_claim_without_evidence_raises(self):
        # 硬性规则：无证据的结论不允许存在
        with pytest.raises(ValueError):
            Claim(claim="该款很好", claim_type="demand", evidence_refs=[])

    def test_claim_with_evidence_ok(self):
        c = Claim(claim="有需求", claim_type="demand", evidence_refs=["ev_1"])
        assert c.evidence_refs == ["ev_1"]


class TestPacketFacts:
    def test_set_fact_binds_evidence(self):
        p = CandidateDataPacket("c1", "US", "tiktok_shop")
        ev = make_ev("basic_facts.title")
        p.set_fact("basic_facts", "title", "Test Bra", ev)
        assert p.basic_facts["title"] == "Test Bra"
        assert p.evidence_for("basic_facts.title") == ["ev_test_000001"]

    def test_none_value_not_written(self):
        p = CandidateDataPacket("c1", "US", "tiktok_shop")
        p.set_fact("basic_facts", "rating", None, make_ev())
        assert p.basic_facts["rating"] is None
        assert p.evidence_pack == []

    def test_missing_fields_registry(self):
        p = CandidateDataPacket("c1", "US", "shein")
        p.mark_missing("basic_facts.rating")
        p.mark_missing("basic_facts.rating")  # 去重
        assert p.missing_fields == ["basic_facts.rating"]

    def test_field_coverage(self):
        p = CandidateDataPacket("c1", "US", "shein")
        p.set_fact("basic_facts", "title", "X", make_ev())
        cov = p.field_coverage(["basic_facts.title", "basic_facts.rating"])
        assert cov == pytest.approx(0.5)
