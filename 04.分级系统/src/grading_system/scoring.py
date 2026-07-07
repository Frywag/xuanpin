"""Priority Score v0 打分引擎（确定性规则，无 LLM 参与数值计算）。

所有阈值来自 configs/scoring_v0.yaml；本模块只做：
  1. 从 CandidateDataPacket 抽取信号（附带 evidence_refs）；
  2. 按配置分档打分，输出 ScoreComponent（每个组件必须带 evidence_refs，
     没有证据的组件只能得 0 分并标记 missing）；
  3. 组内运行时统计（价格带分位数、评论护城河代理值）；
  4. 风险规则扣分（每条扣分绑定证据或标记人工待确认）；
  5. S/A/B/C 分箱 + 硬性条件 + 覆盖率封顶。
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .models import CandidateDataPacket, ScoreComponent
from .packet_builder import KEY_FIELDS

GRADE_ORDER = ["S", "A", "B", "C"]

# 需求信号 -> (packet 路径, 是否成交类证据)
DEMAND_VOLUME_SIGNALS = {
    "sales_30d_units": ("market_metrics.sales_30d_units", True),
    "sales_7d_units": ("market_metrics.sales_7d_units", True),
    "sales_floor_units": ("market_metrics.sales_floor_units", True),
    "favorites_count": ("market_metrics.favorites_count", False),
    "review_count_as_demand": ("basic_facts.review_count", True),
}

UNIQUE_CLAIM_KEYWORDS = ["patented", "patent", "exclusive", "官方专利", "独家"]


def _get(packet: CandidateDataPacket, path: str):
    section, key = path.split(".", 1)
    return getattr(packet, section).get(key)


def eval_bands(value: float, bands: List[Dict[str, Any]]) -> Optional[float]:
    """按配置分档：第一条命中的规则生效。支持 gte/gt/lte/lt。"""
    for band in bands:
        ok = True
        for op in ("gte", "gt", "lte", "lt"):
            if op in band:
                threshold = band[op]
                if op == "gte" and not value >= threshold:
                    ok = False
                elif op == "gt" and not value > threshold:
                    ok = False
                elif op == "lte" and not value <= threshold:
                    ok = False
                elif op == "lt" and not value < threshold:
                    ok = False
        if ok:
            return float(band["points"])
    return None


def percentile(sorted_vals: List[float], q: float) -> Optional[float]:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


class GroupStats:
    """组内运行时统计：价格带分位数 + 评论护城河代理（组内评论数 p90）。

    组 key = (source_group, currency)。统计结果写入运行记录 sheet，保证
    「配置定义分位数、数据给出具体数值」这一链路可复查。
    """

    def __init__(self, packets: List[CandidateDataPacket]):
        prices = defaultdict(list)
        reviews = defaultdict(list)
        for p in packets:
            price = p.basic_facts.get("price")
            group = p.context.get("source_group") or p.platform
            if price and price.get("amount") is not None and price.get("currency"):
                prices[(group, price["currency"])].append(float(price["amount"]))
            rc = p.basic_facts.get("review_count")
            if rc is not None:
                reviews[group].append(float(rc))
        self.price_bands: Dict[Tuple[str, str], Dict[str, float]] = {}
        for key, vals in prices.items():
            vals.sort()
            self.price_bands[key] = {
                "n": len(vals),
                "p10": percentile(vals, 0.10), "p25": percentile(vals, 0.25),
                "p50": percentile(vals, 0.50), "p75": percentile(vals, 0.75),
                "p90": percentile(vals, 0.90),
            }
        self.review_p90: Dict[str, Dict[str, float]] = {}
        for group, vals in reviews.items():
            vals.sort()
            self.review_p90[group] = {"n": len(vals), "p90": percentile(vals, 0.90)}

    # 组内样本量低于该值时不做价格带定位/低价风险判定，避免小样本分位数误导
    MIN_PRICE_SAMPLE = 20

    def price_position(self, packet: CandidateDataPacket) -> Optional[str]:
        price = packet.basic_facts.get("price")
        if not price or price.get("amount") is None or not price.get("currency"):
            return None
        group = packet.context.get("source_group") or packet.platform
        band = self.price_bands.get((group, price["currency"]))
        if not band or band["n"] < self.MIN_PRICE_SAMPLE:
            return None
        amt = float(price["amount"])
        if band["p25"] <= amt <= band["p75"]:
            return "within_p25_p75"
        if band["p10"] <= amt <= band["p90"]:
            return "within_p10_p90"
        return "outside"

    def below_p10(self, packet: CandidateDataPacket) -> bool:
        price = packet.basic_facts.get("price")
        if not price or price.get("amount") is None or not price.get("currency"):
            return False
        group = packet.context.get("source_group") or packet.platform
        band = self.price_bands.get((group, price["currency"]))
        if not band or band["n"] < self.MIN_PRICE_SAMPLE:
            return False
        return float(price["amount"]) < band["p10"]

    def above_median(self, packet: CandidateDataPacket) -> Optional[bool]:
        """售价是否高于组内中位（利润结构代理用）；样本不足返回 None。"""
        price = packet.basic_facts.get("price")
        if not price or price.get("amount") is None or not price.get("currency"):
            return None
        group = packet.context.get("source_group") or packet.platform
        band = self.price_bands.get((group, price["currency"]))
        if not band or band["n"] < self.MIN_PRICE_SAMPLE:
            return None
        return float(price["amount"]) >= band["p50"]

    def competitor_review_moat(self, packet: CandidateDataPacket) -> Optional[float]:
        group = packet.context.get("source_group") or packet.platform
        stats = self.review_p90.get(group)
        if stats and stats["n"] >= 8:
            return stats["p90"]
        return None

    def to_dict(self):
        return {
            "price_bands": {f"{g}|{c}": v for (g, c), v in self.price_bands.items()},
            "review_p90": self.review_p90,
        }


TRACK_LABELS = {
    "trend_rising": "趋势上升款", "evergreen": "基础常青款",
    "pain_improvement": "痛点改良款", "balanced": "均衡款",
}


class PriorityScorer:
    def __init__(self, config: Dict[str, Any], stats: GroupStats,
                 capability_profile: Optional[Dict[str, Any]] = None):
        self.cfg = config
        self.stats = stats
        self.capability = capability_profile or {}

    # ================= 赛道判定（打分前，确定性） =================

    def assign_track(self, p: CandidateDataPacket) -> str:
        cfg = self.cfg.get("track_assignment")
        if not cfg:
            return "balanced"
        growth = _get(p, "market_metrics.sales_growth_rate")
        rating = _get(p, "basic_facts.rating")
        rc = _get(p, "basic_facts.review_count")
        title = (p.basic_facts.get("title") or "").lower()
        for track in cfg["order"]:
            rule = cfg[track]
            if track == "trend_rising":
                if (growth is not None and growth >= rule["growth_gte"]) or (
                        rule.get("or_bestseller_rank")
                        and _get(p, "market_metrics.bestseller_rank_text")):
                    return track
            elif track == "pain_improvement":
                if (rating is not None and rc is not None
                        and rating <= rule["rating_lte"]
                        and rc >= rule["min_review_count"]):
                    return track
            elif track == "evergreen":
                lo, hi = rule["growth_between"]
                has_kw = any(k in title for k in rule["basics_keywords"])
                in_range = growth is None or lo <= growth < hi
                if has_kw and in_range:
                    return track
        hint = p.context.get("track_hint")
        if hint in self.cfg.get("weight_profiles", {}):
            return hint
        return cfg.get("fallback", "balanced")

    # ================= 维度打分 =================

    def score_demand(self, p: CandidateDataPacket) -> ScoreComponent:
        cfg = self.cfg["demand"]
        best_pts, best_sig, best_val, best_ev = 0.0, None, None, []
        for sig, (path, _is_txn) in DEMAND_VOLUME_SIGNALS.items():
            val = _get(p, path)
            if val is None:
                continue
            sig_cfg = cfg["volume_signals"].get(sig)
            if not sig_cfg:
                continue
            pts = eval_bands(float(val), sig_cfg["bands"]) or 0.0
            pts = min(pts, sig_cfg.get("cap", cfg["volume_max"]))
            if pts > best_pts:
                best_pts, best_sig, best_val = pts, sig, val
                best_ev = p.evidence_for(path)
        volume_pts, volume_reason = best_pts, ""
        if best_sig:
            volume_reason = f"量级信号 {best_sig}={best_val:,.0f} -> {best_pts:.0f}分"
        evidence = list(best_ev)

        # 动能
        momentum_pts, momentum_reason = 0.0, ""
        growth = _get(p, "market_metrics.sales_growth_rate")
        if growth is not None:
            g_pts = eval_bands(float(growth), cfg["growth_bands"]) or 0.0
            if g_pts > momentum_pts:
                momentum_pts = g_pts
                momentum_reason = f"销量/销售额增长率 {growth:+.1%} -> {g_pts:.0f}分"
                evidence += p.evidence_for("market_metrics.sales_growth_rate")
        if _get(p, "market_metrics.bestseller_rank_text"):
            b_pts = float(cfg["bestseller_rank_points"])
            if b_pts > momentum_pts:
                momentum_pts = b_pts
                momentum_reason = "带平台畅销榜标记"
                evidence += p.evidence_for("market_metrics.bestseller_rank_text")
        if p.market_metrics.get("trend_signals"):
            t_pts = float(cfg["trend_tag_points"])
            if t_pts > momentum_pts:
                momentum_pts = t_pts
                momentum_reason = "带平台趋势标签（弱动能证据）"
                evidence += p.evidence_for("context.trend_tags")
        momentum_pts = min(momentum_pts, cfg["momentum_max"])

        # 广度：需求字段证据覆盖的独立数据源数
        demand_sources = set()
        for sig, (path, _t) in DEMAND_VOLUME_SIGNALS.items():
            if _get(p, path) is not None:
                for ev in p.evidence_pack:
                    if ev.field_path == path:
                        demand_sources.add(ev.source_id)
        breadth_pts = eval_bands(len(demand_sources), cfg["breadth_bands"]) or 0.0
        breadth_pts = min(breadth_pts, cfg["breadth_max"])

        total = min(volume_pts + momentum_pts + breadth_pts, self.cfg["weights"]["demand"])
        reasons = [r for r in [volume_reason, momentum_reason,
                               f"{len(demand_sources)}个独立需求证据源" if demand_sources else ""] if r]
        missing = volume_pts == 0
        if missing:
            p.mark_missing("market_metrics.<any_demand_signal>")
        return ScoreComponent(
            name="市场需求", score=total, max_score=self.cfg["weights"]["demand"],
            reason="；".join(reasons) if reasons else "无任何需求信号",
            evidence_refs=sorted(set(evidence)), missing=missing)

    def score_competition(self, p: CandidateDataPacket) -> ScoreComponent:
        cfg = self.cfg["competition"]
        pts, reasons, evidence, missing_parts = 0.0, [], [], []

        top10 = _get(p, "competition_metrics.category_top10_shop_ratio")
        if top10 is not None:
            c_pts = eval_bands(float(top10), cfg["top10_ratio_bands"]) or 0.0
            pts += min(c_pts, cfg["concentration_max"])
            reasons.append(f"类目Top10店铺占比 {top10:.1%} -> 集中度{c_pts:.0f}分")
            evidence += p.evidence_for("competition_metrics.category_top10_shop_ratio")
        else:
            missing_parts.append("competition_metrics.category_top10_shop_ratio")

        moat = _get(p, "competition_metrics.competitor_review_max")
        moat_src = "packet"
        if moat is None:
            moat = self.stats.competitor_review_moat(p)
            moat_src = "组内评论数p90代理"
        if moat is not None:
            m_pts = eval_bands(float(moat), cfg["review_moat_bands"]) or 0.0
            pts += min(m_pts, cfg["review_moat_max"])
            reasons.append(f"竞品评论门槛≈{moat:,.0f}（{moat_src}）-> {m_pts:.0f}分")
            rc_ev = p.evidence_for("basic_facts.review_count")
            evidence += rc_ev
        else:
            missing_parts.append("competition_metrics.competitor_review_max")

        pos = self.stats.price_position(p)
        if pos is not None:
            pb_pts = float(cfg["price_band_points"].get(pos, 0))
            pts += min(pb_pts, cfg["price_band_max"])
            reasons.append(f"价格带位置 {pos} -> {pb_pts:.0f}分")
            evidence += p.evidence_for("basic_facts.price")
        else:
            missing_parts.append("basic_facts.price(band)")

        for m in missing_parts:
            p.mark_missing(m)
        total = min(pts, self.cfg["weights"]["competition"])
        return ScoreComponent(
            name="竞争可突破", score=total, max_score=self.cfg["weights"]["competition"],
            reason="；".join(reasons) if reasons else "无竞争信号",
            evidence_refs=sorted(set(evidence)), missing=not reasons)

    def score_product(self, p: CandidateDataPacket) -> ScoreComponent:
        cfg = self.cfg["product"]
        pts, reasons, evidence = 0.0, [], []

        # 痛点：L2 有负面聚类时优先；否则用评分代理
        neg_clusters = p.product_opportunity.get("negative_review_clusters") or []
        rating = _get(p, "basic_facts.rating")
        if neg_clusters:
            n_pts = eval_bands(len(neg_clusters), cfg["l2_negative_cluster_bands"]) or 0.0
            pts += min(n_pts, cfg["pain_max"])
            names = "、".join(c["cluster_name"] for c in neg_clusters[:3])
            reasons.append(f"L2负面评价聚类{len(neg_clusters)}个（{names}）-> {n_pts:.0f}分")
            for c in neg_clusters:
                evidence += c.get("evidence_refs", [])
        elif rating is not None:
            r_pts = None
            for band in cfg["rating_pain_bands"]:
                r_pts = eval_bands(float(rating), [band])
                if r_pts is not None:
                    reasons.append(f"评分{rating}（{band.get('label','')}）-> {r_pts:.0f}分")
                    break
            pts += min(r_pts or 0.0, cfg["pain_max"])
            evidence += p.evidence_for("basic_facts.rating")
        else:
            p.mark_missing("basic_facts.rating")

        # 差异化
        diff_pts = 0.0
        has_design = bool(p.market_metrics.get("trend_signals")) or bool(
            p.context.get("design_signals"))
        if has_design:
            diff_pts += float(cfg["trend_or_design_points"])
            reasons.append("有趋势标签/设计细节记录")
            evidence += p.evidence_for("context.trend_tags") or p.evidence_for("context.design_signals")
        title = (p.basic_facts.get("title") or "").lower()
        if any(k in title for k in UNIQUE_CLAIM_KEYWORDS):
            diff_pts += float(cfg["unique_claim_points"])
            reasons.append("标题含专利/独家类主张")
            evidence += p.evidence_for("basic_facts.title")
        pts += min(diff_pts, cfg["diff_max"])

        # 内容/渠道杠杆
        content_pts = 0.0
        vshare = _get(p, "market_metrics.video_revenue_share")
        if vshare is not None:
            c = eval_bands(float(vshare), cfg["video_share_bands"]) or 0.0
            if c > content_pts:
                content_pts = c
                reasons.append(f"视频销售额占比 {vshare:.0%}")
                evidence += p.evidence_for("market_metrics.video_revenue_share")
        creators = _get(p, "market_metrics.related_creator_count")
        if creators is not None:
            c = eval_bands(float(creators), cfg["creator_count_bands"]) or 0.0
            if c > content_pts:
                content_pts = c
                reasons.append(f"关联达人 {creators:,.0f} 个")
                evidence += p.evidence_for("market_metrics.related_creator_count")
        views = _get(p, "market_metrics.video_views_max")
        if views is not None:
            c = eval_bands(float(views), cfg["video_views_bands"]) or 0.0
            if c > content_pts:
                content_pts = c
                reasons.append(f"关联视频最高播放 {views:,.0f}（热度信号）")
                evidence += p.evidence_for("market_metrics.video_views_max")
        imgs = p.context.get("image_count")
        if imgs is not None and content_pts == 0:
            c = eval_bands(float(imgs), cfg["image_count_bands"]) or 0.0
            content_pts = c
            if c:
                reasons.append(f"素材图片 {imgs:.0f} 张")
                evidence += p.evidence_for("context.image_count")
        pts += min(content_pts, cfg["content_max"])

        total = min(pts, self.cfg["weights"]["product"])
        return ScoreComponent(
            name="产品机会", score=total, max_score=self.cfg["weights"]["product"],
            reason="；".join(reasons) if reasons else "无产品机会信号",
            evidence_refs=sorted(set(evidence)), missing=not reasons)

    def score_supply(self, p: CandidateDataPacket) -> ScoreComponent:
        """供应链两级化：利润结构代理（零人力）+ 能力档案匹配（一次性维护）。

        逐款级 target_cost/moq/lead_time 人工核实不在此处发生——
        只对 L3 终选款触发（layer_gates_v0.yaml）。
        """
        cfg = self.cfg["supply"]
        pts, reasons, evidence = 0.0, [], []
        achievable = 0.0

        # ① 利润结构代理（max 10）——注意：是结构代理，不是毛利事实
        proxy_pts, has_proxy = 0.0, False
        comm = _get(p, "competition_metrics.commission_rate")
        if comm is not None:
            c = eval_bands(float(comm), cfg["commission_bands"]) or 0.0
            proxy_pts += c
            has_proxy = True
            reasons.append(f"佣金率 {comm:.0%}（利润结构代理）-> {c:.0f}分")
            evidence += p.evidence_for("competition_metrics.commission_rate")
        ship = p.context.get("shipping_fee")
        price = p.basic_facts.get("price")
        if (ship and price and price.get("amount")
                and ship.get("currency") == price.get("currency")):
            ratio = float(ship["amount"]) / float(price["amount"])
            c = eval_bands(ratio, cfg["shipping_ratio_bands"]) or 0.0
            proxy_pts += c
            has_proxy = True
            reasons.append(f"运费/售价 {ratio:.0%}（利润结构代理）-> {c:.0f}分")
            evidence += p.evidence_for("context.shipping_fee")
        above = self.stats.above_median(p)
        if above is not None:
            c = float(cfg["price_position_points"]["upper_half" if above else "lower_half"])
            proxy_pts += c
            has_proxy = True
            reasons.append(("售价高于组内中位，有溢价空间（代理）" if above
                            else "售价低于组内中位，薄利结构（代理）") + f"-> {c:.0f}分")
            evidence += p.evidence_for("basic_facts.price")
        if has_proxy:
            pts += min(proxy_pts, cfg["profit_proxy_max"])
            achievable += cfg["profit_proxy_max"]

        # ② 供应链能力档案匹配（max 10）
        if self.capability.get("enabled"):
            achievable += cfg["capability_max"]
            title = (p.basic_facts.get("title") or "").lower()
            import re as _re
            words = set(_re.findall(r"[a-z\-]+", title))
            hit_profile = None
            for prof in self.capability.get("profiles", []):
                if words & set(prof.get("style_tokens", [])):
                    hit_profile = prof
                    break
            mp = self.capability.get("match_points", {"hit": 10, "miss": 2})
            if hit_profile:
                c = float(mp["hit"])
                reasons.append(f"命中供应链能力档案「{hit_profile['name']}」-> {c:.0f}分")
            else:
                c = float(mp["miss"])
                reasons.append(f"未命中能力档案（需外协评估）-> {c:.0f}分")
            pts += min(c, cfg["capability_max"])
            evidence += p.evidence_for("basic_facts.title")
        else:
            p.mark_missing("owned_supply_inputs.capability_profile")

        if achievable == 0:
            return ScoreComponent(
                name="自有供应链", score=None, max_score=self.cfg["weights"]["supply"],
                reason="无利润代理信号且能力档案未启用，本维不计分（required_next_data）",
                evidence_refs=[], missing=True)
        return ScoreComponent(
            name="自有供应链", score=min(pts, achievable), max_score=achievable,
            reason="；".join(reasons) + "。逐款成本/MOQ/交期仅对终选款人工核实",
            evidence_refs=sorted(set(evidence)),
            missing=achievable < self.cfg["weights"]["supply"])

    # ================= 风险 =================

    def score_risk(self, p: CandidateDataPacket) -> Tuple[ScoreComponent, List[Dict]]:
        rules = self.cfg["risk"]["rules"]
        floor = float(self.cfg["risk"]["floor"])
        hits: List[Dict[str, Any]] = []

        def hit(rule: str, evidence_refs: List[str], detail: str = "",
                human_confirm: bool = False):
            r = rules[rule]
            hits.append({
                "rule": rule, "points": float(r["points"]),
                "reason": r["desc"] + (f"：{detail}" if detail else ""),
                "evidence_refs": evidence_refs, "human_confirm": human_confirm,
            })

        title_ev = p.evidence_for("basic_facts.title")

        if p.source_count <= 1:
            hit("single_source", title_ev,
                f"仅 {p.source_refs[0]['source_id'] if p.source_refs else '?'} 单源")
        if p.quality_status != "VALID":
            hit("quality_degraded", title_ev, p.quality_status)

        # 只有热度类需求证据
        txn_paths = ["market_metrics.sales_30d_units", "market_metrics.sales_7d_units",
                     "market_metrics.sales_floor_units", "market_metrics.gmv_7d",
                     "market_metrics.revenue_30d", "basic_facts.review_count"]
        heat_paths = ["market_metrics.favorites_count", "market_metrics.video_views_max"]
        has_txn = any(_get(p, path) is not None for path in txn_paths)
        has_heat = any(_get(p, path) is not None for path in heat_paths)
        if has_heat and not has_txn:
            ev = []
            for path in heat_paths:
                ev += p.evidence_for(path)
            hit("heat_only_demand", ev)

        if p.context.get("seasonal"):
            hit("seasonal_window", title_ev,
                f"品类 {p.context.get('category_label')}", human_confirm=True)

        seller_type = _get(p, "competition_metrics.seller_type")
        brand = (p.basic_facts.get("brand") or "").strip()
        brand_hit = False
        if seller_type == "BRAND":
            hit("brand_ip", p.evidence_for("competition_metrics.seller_type"),
                "卖家类型 BRAND", human_confirm=True)
            brand_hit = True
        elif brand and p.context.get("source_group") == "indie_frontend":
            hit("brand_ip", p.evidence_for("basic_facts.brand"),
                f"独立站品牌自营商品（{brand}），仿款需人工评估侵权风险",
                human_confirm=True)
            brand_hit = True
        if not brand_hit and "brand_shop_heuristic" in rules:
            shop = str(p.context.get("shop_name") or p.context.get("seller_name") or "")
            title_l = (p.basic_facts.get("title") or "").lower()
            token = shop.split()[0].lower() if shop.split() else ""
            if len(token) >= 4 and token.isalpha() and token in title_l:
                hit("brand_shop_heuristic",
                    p.evidence_for("context.shop_name")
                    or p.evidence_for("context.seller_name") or title_ev,
                    f"店铺「{shop}」品牌词出现在标题中", human_confirm=True)

        if self.stats.below_p10(p):
            hit("ultra_low_price", p.evidence_for("basic_facts.price"),
                "售价低于来源类目P10")

        rating = _get(p, "basic_facts.rating")
        rc = _get(p, "basic_facts.review_count")
        if rating is not None and rc is not None and rating < 3.5 and rc >= 30:
            hit("low_rating_high_volume",
                p.evidence_for("basic_facts.rating") + p.evidence_for("basic_facts.review_count"),
                f"评分{rating}、评论{rc:,.0f}")

        conc = _get(p, "competition_metrics.creator_gmv_concentration")
        if conc is not None and conc > 0.4:
            hit("creator_concentration",
                p.evidence_for("competition_metrics.creator_gmv_concentration"),
                f"达人GMV集中度 {conc:.0%}")

        if any(i.issue.startswith("new_launch_spike") for i in p.field_issues):
            hit("new_launch_spike", p.evidence_for("market_metrics.sales_growth_rate")
                or title_ev)

        total = max(sum(h["points"] for h in hits), floor)
        reason = "；".join(h["reason"] for h in hits) if hits else "未命中风险规则"
        evidence = sorted({e for h in hits for e in h["evidence_refs"]})
        comp = ScoreComponent(name="风险", score=total, max_score=0.0,
                              reason=reason, evidence_refs=evidence)
        return comp, hits

    # ================= 汇总 =================

    _DIM_KEYS = {"市场需求": "demand", "竞争可突破": "competition",
                 "产品机会": "product", "自有供应链": "supply"}

    def score(self, p: CandidateDataPacket):
        track = self.assign_track(p)
        base_w = self.cfg["weights"]
        profile = self.cfg.get("weight_profiles", {}).get(track) or {
            **base_w, "risk_floor": base_w["risk_floor"]}

        comps = [self.score_demand(p), self.score_competition(p),
                 self.score_product(p), self.score_supply(p)]
        # 赛道权重缩放：得分与满分同比例缩放（保留“部分可得满分”的语义，
        # 如供应链维只有利润代理时 max=10 -> 10×supply系数）
        for comp in comps:
            key = self._DIM_KEYS.get(comp.name)
            if key is None:
                continue
            scale = float(profile[key]) / float(base_w[key])
            if scale != 1.0:
                if comp.score is not None:
                    comp.score = round(comp.score * scale, 1)
                comp.max_score = round(comp.max_score * scale, 1)

        risk_comp, risk_hits = self.score_risk(p)
        risk_scale = float(profile["risk_floor"]) / float(base_w["risk_floor"])
        if risk_scale != 1.0 and risk_comp.score is not None:
            risk_comp.score = max(round(risk_comp.score * risk_scale, 1),
                                  float(profile["risk_floor"]))
            risk_comp.reason += f"（赛道风险系数×{risk_scale:.2f}）"
        comps.append(risk_comp)

        achievable = sum(c.max_score for c in comps if c.score is not None and c.max_score > 0)
        raw = sum(c.score for c in comps if c.score is not None)
        pct = round(max(0.0, raw) / achievable * 100, 1) if achievable else 0.0

        confidence = self._confidence(p)
        grade = self._grade(p, pct, confidence, risk_hits, comps, track=track)
        tags = self._track_tags(p, comps)
        return {
            "components": comps, "risk_hits": risk_hits, "track": track,
            "track_label": TRACK_LABELS.get(track, track),
            "total": round(raw, 1), "achievable_max": achievable, "pct": pct,
            "grade": grade, "confidence": confidence, "track_tags": tags,
        }

    def _confidence(self, p: CandidateDataPacket) -> str:
        cfg = self.cfg["grading"]["confidence"]
        n_ev, n_src = len(p.evidence_pack), p.source_count
        if (n_ev >= cfg["high"]["min_evidence"] and n_src >= cfg["high"]["min_sources"]
                and p.quality_status == "VALID"):
            return "high"
        if n_ev >= cfg["medium"]["min_evidence"] and n_src >= cfg["medium"]["min_sources"]:
            return "medium"
        return "low"

    def _grade(self, p, pct, confidence, risk_hits, comps,
               track: str = "balanced") -> str:
        cfg = self.cfg["grading"]
        # 赛道内分箱：同一赛道内部比较，低天花板赛道也能产出自己的头部
        th = cfg.get("thresholds_by_track", {}).get(track) or cfg["thresholds"]
        grade = "C"
        for g in ("S", "A", "B"):
            if pct >= th[g]:
                grade = g
                break

        caps: List[str] = []
        hard = cfg["hard_rules"]
        if grade == "S":
            if p.source_count < hard["s_requires_source_count"]:
                caps.append("A")
            if confidence == "low":
                caps.append("A")
            critical = set(hard["s_blocked_by_critical_risk"])
            if any(h["rule"] in critical for h in risk_hits):
                caps.append("A")
        coverage = p.field_coverage(KEY_FIELDS)
        for rule in hard["coverage_caps"]:
            if coverage < rule["below"]:
                caps.append(rule["cap"])
        demand_sources = {e.source_id for e in p.evidence_pack
                          if e.field_path.startswith("market_metrics.sales")
                          or e.field_path in ("basic_facts.review_count",
                                              "market_metrics.favorites_count")}
        if len(demand_sources) <= 1 and grade == "S":
            caps.append(hard["single_demand_signal_cap"])
        for cap in caps:
            if GRADE_ORDER.index(cap) > GRADE_ORDER.index(grade):
                grade = cap
        return grade

    def _track_tags(self, p: CandidateDataPacket, comps) -> List[str]:
        cfg = self.cfg["track_tags"]
        tags: List[str] = []
        growth = _get(p, "market_metrics.sales_growth_rate")
        rating = _get(p, "basic_facts.rating")
        rc = _get(p, "basic_facts.review_count")
        title = (p.basic_facts.get("title") or "").lower()

        tr = cfg["trend_rising"]
        if (growth is not None and growth >= tr["growth_gte"]) or (
                tr.get("or_bestseller_rank") and _get(p, "market_metrics.bestseller_rank_text")):
            tags.append("趋势上升款")
        ev = cfg["evergreen"]
        lo, hi = ev["growth_between"]
        if growth is not None and lo <= growth < hi and any(
                k in title for k in ev["basics_keywords"]):
            tags.append("基础常青款")
        pi = cfg["pain_improvement"]
        if (rating is not None and rating <= pi["rating_lte"]
                and rc is not None and rc >= pi["min_review_count"]):
            tags.append("痛点改良款")
        pb = cfg["price_band_opportunity"]
        top10 = _get(p, "competition_metrics.category_top10_shop_ratio")
        if (self.stats.price_position(p) == pb["price_position"]
                and top10 is not None and top10 < pb["concentration_lt"]):
            tags.append("价格带机会款")
        coverage = p.field_coverage(KEY_FIELDS)
        if coverage < cfg["watchlist"]["max_coverage_below"] or not tags:
            tags.append("观察款")
        return tags
