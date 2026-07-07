"""L1 / L2 / L3 Gate 编排。

成本控制在这里落地：
  - L1 消费全量候选，只用低成本字段（不触发差评/多平台分析）；
  - L2 只处理 L1 入围 S/A/B，且有单次上限（layer_gates_v0.yaml: max_candidates）；
  - L3 只处理 L2 后 S/A，同样有上限。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from .cross_platform import CrossPlatformComparer
from .models import AnalysisResult, CandidateDataPacket, Claim
from .packet_builder import DEMAND_SIGNAL_FIELDS, KEY_FIELDS, packet_get
from .review_tags import cluster_review_tags
from .scoring import GRADE_ORDER, PriorityScorer

DECISION_BY_GRADE = {
    "S": ("recommend_research", "进入立项讨论；补齐供应链输入后人审"),
    "A": ("research_more", "补齐关键缺字段后复评"),
    "B": ("watchlist", "低成本跟踪价格/榜单/趋势变化"),
    "C": ("drop", "暂不推进，保留证据与淘汰理由"),
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class GateRunner:
    def __init__(self, scorer: PriorityScorer, gate_cfg: Dict[str, Any], market: str = "US"):
        self.scorer = scorer
        self.cfg = gate_cfg
        self.market = market

    # ================= L1 =================

    def run_l1(self, packets: List[CandidateDataPacket]) -> Dict[str, AnalysisResult]:
        results: Dict[str, AnalysisResult] = {}
        for p in packets:
            r = AnalysisResult(p.candidate_id, p.market)
            r.created_at = _now()

            blocked = []
            if not p.primary_product_id and not p.canonical_url:
                blocked.append("no_product_id_and_no_url")
            if p.market != self.market:
                blocked.append("market_mismatch")
            if blocked:
                r.blocked_reasons = blocked
                r.layer_results["L1"].passed = False
                r.layer_results["L1"].gate_reason = "阻断：" + "、".join(blocked)
                r.recommendation["decision"] = "blocked"
                r.missing_fields = list(p.missing_fields)
                results[p.candidate_id] = r
                continue

            scored = self.scorer.score(p)
            grade = scored["grade"]

            # 无需求信号 -> 强制 C（layer_gates_v0.yaml: demote_if_no_demand_signal）
            has_demand = any(packet_get(p, path) not in (None, [], {})
                             for path in DEMAND_SIGNAL_FIELDS)
            demote_note = ""
            if not has_demand:
                forced = self.cfg["l1"]["demote_if_no_demand_signal"]
                if GRADE_ORDER.index(forced) > GRADE_ORDER.index(grade):
                    grade = forced
                demote_note = "；无任何需求信号，降为 C 并列入补采"

            required = []
            if p.basic_facts.get("price") is None:
                required.append("basic_facts.price（L2 补采）")
            if not has_demand:
                required.append("market_metrics.<any_demand_signal>（补采需求信号）")
            if p.basic_facts.get("rating") is None:
                required.append("basic_facts.rating")

            r.priority_score.update({
                "total": scored["total"], "achievable_max": scored["achievable_max"],
                "pct": scored["pct"], "grade": grade,
                "track": scored["track"], "track_label": scored["track_label"],
                "components": scored["components"],
            })
            r.track_tags = scored["track_tags"]
            r.confidence = scored["confidence"]
            r.missing_fields = list(p.missing_fields)
            l1 = r.layer_results["L1"]
            l1.passed = grade in ("S", "A", "B")
            l1.score = scored["pct"]
            l1.grade = grade
            l1.gate_reason = (f"PScore {scored['total']}/{scored['achievable_max']}"
                              f"（{scored['pct']}%）-> {grade}{demote_note}")
            l1.required_next_data = required

            self._build_claims(p, r, scored)
            decision, action = DECISION_BY_GRADE[grade]
            r.recommendation["decision"] = decision
            r.recommendation["next_actions"] = [action] + required
            r.recommendation["reason_summary"] = "；".join(
                c.claim for c in r.claims if c.claim_type != "risk")[:500]
            r.recommendation["risk_summary"] = "；".join(
                c.claim for c in r.claims if c.claim_type == "risk")[:500]
            results[p.candidate_id] = r
        return results

    def _build_claims(self, p: CandidateDataPacket, r: AnalysisResult, scored):
        for comp in scored["components"]:
            if comp.score is None or comp.max_score <= 0 or not comp.evidence_refs:
                continue
            if comp.score <= 0:
                continue
            r.claims.append(Claim(
                claim=f"{comp.name}：{comp.reason}",
                claim_type={"市场需求": "demand", "竞争可突破": "competition",
                            "产品机会": "product"}.get(comp.name, "other"),
                evidence_refs=comp.evidence_refs,
                confidence=r.confidence))
        for hit in scored["risk_hits"]:
            if not hit["evidence_refs"]:
                continue
            r.claims.append(Claim(
                claim=hit["reason"] + ("（人工待确认）" if hit["human_confirm"] else ""),
                claim_type="risk",
                evidence_refs=hit["evidence_refs"],
                confidence="low" if hit["human_confirm"] else r.confidence))

    # ================= L2 =================

    def run_l2(self, packets: List[CandidateDataPacket],
               results: Dict[str, AnalysisResult]) -> List[str]:
        cfg = self.cfg["l2"]
        eligible = [p for p in packets
                    if results[p.candidate_id].layer_results["L1"].grade in cfg["eligible_grades"]
                    and not results[p.candidate_id].blocked_reasons]
        eligible.sort(key=lambda p: -(results[p.candidate_id].priority_score["pct"] or 0))
        selected = eligible[: cfg["max_candidates"]]

        for p in selected:
            r = results[p.candidate_id]
            l2 = r.layer_results["L2"]
            raw_tags = p.context.get("review_tags_raw")
            required = []
            if raw_tags:
                ev = p.evidence_for("context.review_tags_raw")
                clustered = cluster_review_tags(raw_tags, ev)
                p.product_opportunity["review_tag_clusters"] = clustered["clusters"] + [
                    {"cluster_name": "正面标签", "tags": clustered["positive_tags"]}]
                p.product_opportunity["negative_review_clusters"] = clustered["clusters"]
                p.product_opportunity["improvement_points"] = [
                    {"point": c["improvement_opportunity"],
                     "from_cluster": c["cluster_name"],
                     "evidence_refs": c["evidence_refs"]}
                    for c in clustered["clusters"]]
                gate_note = (f"评价标签聚类：{len(clustered['clusters'])} 个负面聚类 / "
                             f"{clustered['total_mentions']} 次标签提及")
                if clustered["clusters"]:
                    for c in clustered["clusters"]:
                        r.claims.append(Claim(
                            claim=(f"负面评价聚类「{c['cluster_name']}」提及 {c['mention_count']} 次，"
                                   f"改良方向：{c['improvement_opportunity']}"),
                            claim_type="product",
                            evidence_refs=c["evidence_refs"],
                            confidence="medium"))
            else:
                required.append("reviews_or_review_tags")
                gate_note = cfg["missing_review_note"]
                p.mark_missing("product_opportunity.reviews")
            if p.basic_facts.get("rating") is None:
                required.append("basic_facts.rating")

            # 用 L2 证据重打分（负面聚类会刷新“产品机会”维度）
            rescored = self.scorer.score(p)
            l2.passed = rescored["grade"] in ("S", "A")
            l2.score = rescored["pct"]
            l2.grade = rescored["grade"]
            l2.gate_reason = gate_note
            l2.required_next_data = required
            r.priority_score.update({
                "total": rescored["total"], "achievable_max": rescored["achievable_max"],
                "pct": rescored["pct"], "grade": rescored["grade"],
                "track": rescored["track"], "track_label": rescored["track_label"],
                "components": rescored["components"],
            })
            r.track_tags = rescored["track_tags"]
            r.missing_fields = list(p.missing_fields)
        return [p.candidate_id for p in selected]

    # ================= L3 =================

    def run_l3(self, packets: List[CandidateDataPacket],
               results: Dict[str, AnalysisResult],
               comparer: CrossPlatformComparer) -> List[str]:
        cfg = self.cfg["l3"]
        pool = [p for p in packets
                if results[p.candidate_id].layer_results["L2"].grade in cfg["eligible_grades"]]
        pool.sort(key=lambda p: -(results[p.candidate_id].priority_score["pct"] or 0))
        selected = pool[: cfg["max_candidates"]]

        for p in selected:
            r = results[p.candidate_id]
            l3 = r.layer_results["L3"]
            cmp = comparer.analyze(p)
            reasons = []
            if cmp["matched_products"]:
                srcs = "、".join(m["source_id"] for m in cmp["matched_products"])
                reasons.append(f"{len(cmp['matched_products'])} 个数据源按商品ID互验（{srcs}）")
                r.claims.append(Claim(
                    claim=f"多源互验：{srcs} 按商品ID匹配到同一商品",
                    claim_type="cross_platform",
                    evidence_refs=p.evidence_for("basic_facts.title") or [
                        e.evidence_id for e in p.evidence_pack[:2]],
                    confidence="high"))
            same_style = cmp["same_style_signals"]
            if same_style:
                top = same_style[0]
                reasons.append(f"同风格信号：{top['signal']}")
                red_ocean = sum(s["count"] for s in same_style)
                level = "高" if red_ocean >= 50 else ("中" if red_ocean >= 10 else "低")
                r.claims.append(Claim(
                    claim=f"同风格红海程度：{level}（语料内同风格候选 {red_ocean} 款，词表确定性匹配）",
                    claim_type="cross_platform",
                    evidence_refs=p.evidence_for("basic_facts.title"),
                    confidence="medium"))
            if cmp["priority_evidence"]:
                r.claims.append(Claim(
                    claim=f"平台优先级：{cmp['platform_priority']}（{cmp['priority_reason']}）",
                    claim_type="cross_platform",
                    evidence_refs=cmp["priority_evidence"],
                    confidence="medium"))
                reasons.append(cmp["priority_reason"])

            l3.passed = True
            l3.score = r.priority_score["pct"]
            l3.grade = r.priority_score["grade"]
            l3.gate_reason = "；".join(reasons) if reasons else "无跨平台证据，仅输出单平台结论"
            l3.required_next_data = ["owned_supply_inputs（供应链人工输入）"]
            r.recommendation["next_actions"] = list(dict.fromkeys(
                r.recommendation["next_actions"] + ["L3 完成，等待供应链输入与人审"]))
        return [p.candidate_id for p in selected]
