# -*- coding: utf-8 -*-
"""L2 评价标签聚类测试：样例文本为 Shein 实采格式。"""
from grading_system.review_tags import cluster_review_tags, parse_review_tags


REAL_SAMPLE = ("No Smell (3)Beachwear (1)Casual (1)Summer Outfits (1)Soft (2)"
               "Runs Large (24)Wrong Size (16)See-Through (13)Never Received This Item (5)")


class TestParse:
    def test_parse_counts(self):
        tags = parse_review_tags(REAL_SAMPLE)
        d = {t["tag"]: t["count"] for t in tags}
        assert d["Runs Large"] == 24
        assert d["No Smell"] == 3


class TestCluster:
    def test_negative_clusters(self):
        out = cluster_review_tags(REAL_SAMPLE, ["ev_x"])
        names = [c["cluster_name"] for c in out["clusters"]]
        assert "尺码偏大" in names
        assert "面料偏透" in names
        assert "物流履约投诉" in names

    def test_no_smell_is_positive_not_negative(self):
        # "No Smell" 是正面标签，绝不能进负面聚类
        out = cluster_review_tags("No Smell (10)", ["ev_x"])
        assert out["clusters"] == []
        assert out["positive_tags"][0]["tag"] == "No Smell"

    def test_frequency_hint(self):
        out = cluster_review_tags("Runs Large (24)", ["ev_x"])
        assert out["clusters"][0]["frequency_hint"] == "high"

    def test_evidence_bound(self):
        out = cluster_review_tags(REAL_SAMPLE, ["ev_shein_1"])
        for c in out["clusters"]:
            assert c["evidence_refs"] == ["ev_shein_1"]
