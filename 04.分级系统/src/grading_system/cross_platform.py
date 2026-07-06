"""L3 多平台比对（确定性，不凭空找同款）。

只做三类有真实证据支撑的比较：
  1. matched_products：PacketBuilder 已按 product_id 精确/前缀合并的多源记录
     （如 OEAK 文胸同时出现在 Kalodata 与 Tabcut，双源互验）；
  2. platform_price_comparison：同品类组在各来源的价格带（组内分位数），
     不做跨币种换算（换算率不是采集事实），各平台各报各币种；
  3. same_style_signals：语料内确定性风格词命中统计（如类目 Top10 中
     N 款同为文胸/塑身类 -> 红海程度），只输出「同风格」信号，
     禁止写成「同款」结论。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from .models import CandidateDataPacket
from .scoring import GroupStats

# 风格词表：从实采标题词频人工筛选的可判定品类词，不做模糊语义匹配
STYLE_TOKENS = [
    "bra", "bras", "shapewear", "bodysuit", "corset", "dress", "dresses",
    "skirt", "tights", "pantyhose", "stocking", "legging", "tank", "tee",
    "t-shirt", "romper", "jumpsuit", "swim", "bikini", "costume", "prop",
    "skeleton", "animatronic",
]


def style_tokens_of(title: str) -> set:
    words = set(re.findall(r"[a-z\-]+", (title or "").lower()))
    return {t for t in STYLE_TOKENS if t in words}


class CrossPlatformComparer:
    def __init__(self, packets: List[CandidateDataPacket], stats: GroupStats):
        self.packets = packets
        self.stats = stats
        self._style_index: List[Dict[str, Any]] = []
        for p in packets:
            toks = style_tokens_of(p.basic_facts.get("title") or "")
            if toks:
                self._style_index.append({
                    "candidate_id": p.candidate_id, "platform": p.platform,
                    "source_group": p.context.get("source_group"),
                    "tokens": toks,
                })

    def analyze(self, p: CandidateDataPacket) -> Dict[str, Any]:
        matched = self._matched_products(p)
        price_cmp = self._price_comparison(p)
        same_style = self._same_style_signals(p)
        platform_priority, priority_reason, priority_ev = self._platform_priority(p)
        p.cross_platform["matched_products"] = matched
        p.cross_platform["platform_price_comparison"] = price_cmp
        p.cross_platform["same_style_signals"] = same_style
        return {
            "matched_products": matched,
            "platform_price_comparison": price_cmp,
            "same_style_signals": same_style,
            "platform_priority": platform_priority,
            "priority_reason": priority_reason,
            "priority_evidence": priority_ev,
        }

    def _matched_products(self, p: CandidateDataPacket) -> List[Dict[str, Any]]:
        if p.source_count <= 1:
            return []
        out = []
        for ref in p.source_refs:
            out.append({
                "source_id": ref["source_id"],
                "envelope_id": ref["envelope_id"],
                "product_id": p.primary_product_id,
                "match_type": "exact_or_prefix_product_id",
            })
        return out

    def _price_comparison(self, p: CandidateDataPacket) -> List[Dict[str, Any]]:
        """同品类组的各来源价格带；带币种，不换算。"""
        own_price = p.basic_facts.get("price")
        own_tokens = style_tokens_of(p.basic_facts.get("title") or "")
        rows: List[Dict[str, Any]] = []
        DRESS = {"dress", "dresses", "skirt"}
        HOSIERY = {"tights", "pantyhose", "stocking", "legging"}
        INTIMATE = {"bra", "bras", "shapewear", "bodysuit", "corset"}
        group_of_tokens = None
        for name, toks in (("dress", DRESS), ("hosiery", HOSIERY), ("intimates", INTIMATE)):
            if own_tokens & toks:
                group_of_tokens = (name, toks)
                break
        if group_of_tokens is None:
            return rows
        name, toks = group_of_tokens
        # 只统计「同来源组 × 同风格词 × 同币种」子集的价格带，避免混入无关品类
        from .scoring import percentile
        style_prices: Dict[tuple, List[float]] = {}
        style_ids = {e["candidate_id"] for e in self._style_index
                     if e["tokens"] & toks}
        for cand in self.packets:
            if cand.candidate_id not in style_ids:
                continue
            price = cand.basic_facts.get("price")
            if not price or price.get("amount") is None or not price.get("currency"):
                continue
            grp = cand.context.get("source_group") or cand.platform
            style_prices.setdefault((grp, price["currency"]), []).append(
                float(price["amount"]))
        MIN_N = 5
        for (grp, cur), vals in sorted(style_prices.items()):
            if len(vals) < MIN_N:
                continue
            vals.sort()
            rows.append({
                "style_group": name,
                "source_group": grp,
                "currency": cur,
                "n": len(vals),
                "p25": round(percentile(vals, 0.25), 2),
                "p50": round(percentile(vals, 0.50), 2),
                "p75": round(percentile(vals, 0.75), 2),
                "note": "同风格子集价格分位数（运行时统计），不跨币种换算",
            })
        if own_price and own_price.get("amount") is not None:
            rows.insert(0, {
                "style_group": name,
                "source_group": f"候选自身（{p.platform}）",
                "currency": own_price.get("currency"),
                "n": 1,
                "p25": own_price["amount"], "p50": own_price["amount"],
                "p75": own_price["amount"],
                "note": "候选商品价格",
            })
        return rows

    def _same_style_signals(self, p: CandidateDataPacket) -> List[Dict[str, Any]]:
        own = style_tokens_of(p.basic_facts.get("title") or "")
        if not own:
            return []
        counter: Counter = Counter()
        for entry in self._style_index:
            if entry["candidate_id"] == p.candidate_id:
                continue
            shared = own & entry["tokens"]
            if shared:
                counter[(entry["source_group"] or entry["platform"])] += 1
        out = []
        for group, n in counter.most_common():
            out.append({
                "signal": f"{group} 语料中有 {n} 款标题命中相同风格词 {sorted(own)}",
                "kind": "style_token_overlap",
                "count": n,
                "note": "同风格信号（确定性词表匹配），不构成同款结论",
            })
        return out

    def _platform_priority(self, p: CandidateDataPacket):
        """平台优先级：只用已有证据字段判定。"""
        ev: List[str] = []
        vshare = p.market_metrics.get("video_revenue_share")
        if vshare is not None and vshare >= 0.5:
            ev = p.evidence_for("market_metrics.video_revenue_share")
            return ("tiktok_shop",
                    f"视频销售额占比 {vshare:.0%}，内容驱动型，优先 TikTok Shop", ev)
        if p.market_metrics.get("bestseller_rank_text"):
            ev = p.evidence_for("market_metrics.bestseller_rank_text")
            return (p.platform,
                    "带站内畅销榜标记，优先在原平台（Shein）巩固，再评估多平台", ev)
        if p.platform == "tiktok_shop":
            ev = p.evidence_for("basic_facts.title")
            return ("tiktok_shop", "候选来自 TikTok Shop 数据源，维持原平台优先", ev)
        ev = p.evidence_for("basic_facts.title")
        return (p.platform, "无跨平台优先级证据，维持来源平台", ev)
