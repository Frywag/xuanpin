"""segments.v1 主链（2026-07-24 总方案）：三平台独立 -> L3 商品硬筛 ->
细分市场人工确认 -> L2/L1（细分市场为对象）-> C/B/A/S -> 多平台综合骨架 ->
商品策略双轴。趋势体系独立（独立站不进主链）。红线与校准态语义不变。"""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any, Dict, List, Optional

from .models import CandidateDataPacket
from .track_eval import SIGNAL_REGISTRY, _get, _has

PLATFORM_KEYS = ("amazon", "tiktok", "shein")


def platform_of(p: CandidateDataPacket, cfg) -> Optional[str]:
    g = ((p.context.get("source_group") or "") + " " + (p.platform or "")).lower()
    for pk, spec in cfg["platforms"].items():
        if any(pat in g for pat in spec["source_group_patterns"]):
            return pk
    return None          # 独立站等 -> 趋势体系，不进主链（§2.3/§15）


def _txn_value(p) -> Optional[float]:
    for path in SIGNAL_REGISTRY["transaction"][:5]:
        v = _get(p, path)
        if isinstance(v, dict):
            v = v.get("amount")
        if v is not None:
            return float(v)
    return None


class SegmentsV1Pipeline:
    def __init__(self, cfg: Dict[str, Any], run_id: str):
        self.cfg = cfg
        self.run_id = run_id
        self.rule_version = cfg.get("rule_version", "segments_v1_pilot")

    # ---- PRODUCT_HARD_SCREEN（业务显示 L3）----

    def l3_screen(self, platform: str, packets: List[CandidateDataPacket]):
        vals = sorted(v for v in (_txn_value(p) for p in packets) if v is not None)
        need = self.cfg["l3_hard_screen"].get(platform, {}).get("min_txn_percentile", 0.2)
        out = []
        for p in packets:
            v = _txn_value(p)
            if not p.basic_facts.get("title"):
                out.append({"product_id": p.candidate_id, "status": "L3_REJECTED",
                            "reason": "数据合法性：缺标题/身份不可核"})
                continue
            if v is None or not vals:
                out.append({"product_id": p.candidate_id, "status": "L3_REJECTED",
                            "reason": "无可验证销量（销量为硬性指标，无例外通道）"})
                continue
            pct = sum(1 for x in vals if x <= v) / len(vals)
            ok = pct >= need
            out.append({"product_id": p.candidate_id,
                        "status": "L3_PASS" if ok else "L3_REJECTED",
                        "txn_percentile": round(pct, 3),
                        "reason": None if ok else f"销量分位 {pct:.2f} < {need}",
                        "evidence_refs": [e for s in SIGNAL_REGISTRY["transaction"][:5]
                                          for e in p.evidence_for(s)][:4],
                        "l3_rule_version": self.rule_version})
        return out

    # ---- HUMAN_SEGMENT_ASSIGNMENT（属性解析 + 人工确认唯一细分市场）----

    def parse_attributes(self, p: CandidateDataPacket) -> Dict[str, Any]:
        title = (p.basic_facts.get("title") or "").lower()
        tax = self.cfg["taxonomy"]
        segs = [w for w in tax["garment_segments"] if w in title]
        attrs = [w for w in tax["style_attributes"] if w in title]
        return {"product_id": p.candidate_id,
                "segment_candidates": segs, "style_attributes": attrs,
                "confidence": "high" if len(segs) == 1 else
                              ("low" if segs else "no_match"),
                "evidence_refs": p.evidence_for("basic_facts.title")}

    def assign_segments(self, profiles: List[Dict[str, Any]]):
        pilot = self.cfg.get("human_gate", {}).get("pilot_auto_approve")
        tax_v = self.cfg["taxonomy"]["taxonomy_version"]
        approved, queue = [], []
        for pr in profiles:
            if pilot and pr["confidence"] == "high":
                approved.append({
                    "product_id": pr["product_id"], "taxonomy_version": tax_v,
                    "primary_segment_id": f"seg_{pr['segment_candidates'][0]}",
                    "status": "HUMAN_APPROVED",
                    "approver": "pilot_config_batch(试点批量确认,须人工复核)",
                    "attributes": pr["style_attributes"],
                    "evidence_refs": pr["evidence_refs"]})
            else:
                queue.append({**pr, "status": "HUMAN_REVIEW_PENDING",
                              "taxonomy_version": tax_v,
                              "note": "多候选/无匹配或试点开关关闭，待人工确认"})
        return approved, queue

    # ---- 快照 + SEGMENT_INITIAL_SCREEN + SEGMENT_DEEP_DIVE + FINAL_TIER ----

    def evaluate_platform(self, platform, packets, l3, assignments):
        by_id = {p.candidate_id: p for p in packets}
        passed = {d["product_id"]: d for d in l3 if d["status"] == "L3_PASS"}
        members = defaultdict(list)
        for a in assignments:
            if a["product_id"] in passed:
                members[a["primary_segment_id"]].append(a["product_id"])
        dims = self.cfg["segment_screen"]["dims"]
        snapshots, evals = {}, []
        for seg, ids in members.items():
            pks = [by_id[i] for i in ids]
            txns = [passed[i].get("txn_percentile") or 0 for i in ids]
            ratings = [r for r in (_get(p, "basic_facts.rating") for p in pks)
                       if r is not None]
            prices = [pr.get("amount") for pr in
                      (p.basic_facts.get("price") or {} for p in pks)
                      if isinstance(pr, dict) and pr.get("amount")]
            raw = [v for v in (_txn_value(p) for p in pks) if v is not None]
            head = (max(raw) / sum(raw)) if raw and sum(raw) > 0 else None
            cov = statistics.mean(
                min(1.0, len(p.evidence_pack) / 10) for p in pks)
            snapshots[seg] = {
                "segment_id": seg, "platform": platform, "members": ids,
                "member_count": len(ids),
                "txn_percentile_median": round(statistics.median(txns), 3),
                "rating_median": round(statistics.median(ratings), 2) if ratings else None,
                "price_range": [min(prices), max(prices)] if prices else None,
                "head_share": round(head, 3) if head is not None else None,
                "field_coverage": round(cov, 3),
                "note": "商品数量口径=L3 通过商品数量，非平台全市场规模（§5.2）"}
            score = (dims["demand_strength"] * statistics.median(txns)
                     + dims["demand_stability"] * min(1.0, len(ids) / 8)
                     + dims["competition_opportunity"] * 0.5
                     + dims["price_profit_room"] * (0.7 if prices else 0.0)
                     + dims["evidence_confidence"] * cov)
            evals.append({"platform": platform, "segment_id": seg,
                          "stage": "SEGMENT_INITIAL_SCREEN",
                          "l2_score": round(score, 1),
                          "missing": [] if prices else ["价格带（成员多缺价格）"]})
        evals.sort(key=lambda e: -e["l2_score"])
        dd = self.cfg["segment_deep_dive"]
        budget = self.cfg["segment_screen"]["l1_budget_per_platform"]
        th = self.cfg["final_tier"]["thresholds_draft"]
        for i, e in enumerate(evals):
            s = snapshots[e["segment_id"]]
            if i < budget:
                e["stage"] = "SEGMENT_DEEP_DIVE"
                problems = []
                if s["member_count"] < dd["min_members"]:
                    problems.append(f"样本量 {s['member_count']} < {dd['min_members']}")
                if s["head_share"] and s["head_share"] > dd["max_head_share"]:
                    problems.append(f"头部集中度 {s['head_share']} 超限（单品拉高整体）")
                if s["field_coverage"] < dd["min_field_coverage"]:
                    problems.append("关键字段覆盖率不足")
                e["l1_gate"] = "PENDING_DATA" if problems else "PASS"
                e["l1_problems"] = problems
            else:
                e["l1_gate"] = None
            sc = e["l2_score"]
            if e.get("l1_gate") == "PASS":
                tier = ("S" if sc >= th["S"] else "A" if sc >= th["A"]
                        else "B" if sc >= th["B"] else "C")
            else:            # 未过 L1：上限 B（§10 只有 L1 PASS 可 A/S）
                tier = "B" if sc >= th["B"] else "C"
                if e.get("l1_gate") == "PENDING_DATA":
                    tier = None  # 待补不伪装成低等级（§9.2）
            e["final_tier"] = tier
            e["rule_version"] = self.rule_version
            e["rule_status"] = self.cfg.get("rule_status")
        return snapshots, evals

    # ---- MULTI_PLATFORM_SYNTHESIS（范围齐套 Gate；缺任务不发布优势标签）----

    def synthesis_scope(self, platform_runs):
        status = {}
        for pk in PLATFORM_KEYS:
            r = platform_runs.get(pk)
            if r is None:
                status[pk] = "RUN_PENDING"
            elif r["evals"]:
                status[pk] = "RUN_COMPLETE"
            else:
                status[pk] = "RUN_COMPLETE_EMPTY"
        ready = all(s in ("RUN_COMPLETE", "RUN_COMPLETE_EMPTY")
                    for s in status.values())
        return {"platform_task_status": status,
                "scope_status": "READY" if ready else "INCOMPLETE_SCOPE",
                "advantage_labels": ("允许进入校准 Gate" if ready else
                                     "不发布（任一平台任务未完成，仅并列展示各平台事实）"),
                "calibration_status": "NOT_CALIBRATED",
                "note": "同 segment_id 跨平台默认可对齐；跨平台分数校准通过前"
                        "只并列展示，不输出方向性优势（§13.4）"}

    # ---- PRODUCT_STRATEGY_EVALUATION（机会入选 Gate 后；双轴独立）----

    def product_strategy(self, p: CandidateDataPacket, txn_pct: float):
        scfg = self.cfg["product_strategy"]
        rating = _get(p, "basic_facts.rating")
        rc = _get(p, "basic_facts.review_count") or 0
        if (rating is not None and rating <= scfg["modification_rating_ceiling"]
                and rc >= scfg["modification_min_reviews"]):
            action = "MODIFICATION"
        elif p.freshness.get("freshness_status") in ("confirmed", "inferred"):
            action = "DIRECT_NEW_LAUNCH"
        else:
            action = "PENDING_REVIEW"
        lo, hi = scfg["long_tail_stable_range"]
        if txn_pct >= scfg["hit_min_txn_percentile"]:
            otype = "HIT_POTENTIAL"
        elif lo <= txn_pct < hi:
            otype = "LONG_TAIL"
        elif txn_pct > 0:
            otype = "NORMAL"
        else:
            otype = "UNCERTAIN"
        return {"product_id": p.candidate_id, "development_action": action,
                "operating_type": otype,
                "evidence_refs": (p.evidence_for("basic_facts.rating")
                                  + p.evidence_for("basic_facts.review_count"))[:4],
                "human_confirmation": "pending",
                "rule_version": self.rule_version}

    # ---- 主入口 ----

    def run(self, packets: List[CandidateDataPacket]):
        by_platform = defaultdict(list)
        excluded = 0
        for p in packets:
            pk = platform_of(p, self.cfg)
            if pk:
                by_platform[pk].append(p)
            else:
                excluded += 1        # 独立站等 -> 趋势体系
        platform_runs = {}
        for pk, plist in by_platform.items():
            l3 = self.l3_screen(pk, plist)
            profiles = [self.parse_attributes(p) for p in plist
                        if next(d for d in l3
                                if d["product_id"] == p.candidate_id)["status"] == "L3_PASS"]
            approved, queue = self.assign_segments(profiles)
            snaps, evals = self.evaluate_platform(pk, plist, l3, approved)
            platform_runs[pk] = {"l3": l3, "assignments": approved,
                                 "review_queue": queue,
                                 "snapshots": snaps, "evals": evals}
        scope = self.synthesis_scope(platform_runs)
        # 机会入选 Gate：S/A 细分市场生成待审决策（默认 PENDING_REVIEW，人工批准
        # APPROVED_FOR_PRODUCT_ANALYSIS 后才运行商品策略拆解 §12.4/§14）
        decisions = []
        for pk, r in platform_runs.items():
            for e in r["evals"]:
                if e["final_tier"] in ("S", "A"):
                    decisions.append({"platform": pk, "segment_id": e["segment_id"],
                                      "final_tier": e["final_tier"],
                                      "decision": "PENDING_REVIEW",
                                      "approver": None})
        return {"platform_runs": platform_runs, "scope": scope,
                "opportunity_decisions": decisions,
                "excluded_to_trend_system": excluded,
                "rule_version": self.rule_version,
                "rule_status": self.cfg.get("rule_status")}
