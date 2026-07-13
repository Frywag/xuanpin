"""SourceEnvelope[] -> CandidateDataPacket[]。

合并规则：
  1. 插件源（TikTok Shop）按完整 product_id 精确合并；EchoTik 的截断 id
     用前缀匹配挂到已有完整 id 上，合并动作写入 field_issues 可追溯。
  2. 前端源按 (site, product_id) 独立成包，不跨站臆测同款。
  3. 字段冲突时保留先写入的值（插件详情 > 榜单 > 页面表格，由采集顺序保证），
     冲突双方的证据都保留，并记 field_issue: source_conflict。
  4. 缺字段写入 missing_fields；价格无币种写入 field_issues 并视为无效。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import CandidateDataPacket, EvidenceRef, SourceEnvelope

# L1 关键字段（覆盖率统计用，见 layer_gates_v0.yaml 注释）
KEY_FIELDS = [
    "basic_facts.title", "basic_facts.price", "basic_facts.rating",
    "basic_facts.review_count",
    "market_metrics.sales_30d_units", "market_metrics.sales_7d_units",
    "market_metrics.sales_floor_units", "market_metrics.favorites_count",
    "market_metrics.sales_growth_rate",
    "competition_metrics.category_top10_shop_ratio",
    "competition_metrics.commission_rate",
]

# [LEGACY] 旧单赛道管道的需求信号口径；三赛道管道使用 track_eval.SIGNAL_REGISTRY
# （评分器与 Gate 共享同一注册表），本列表仅为旧输出兼容保留
DEMAND_SIGNAL_FIELDS = [
    "market_metrics.sales_30d_units", "market_metrics.sales_7d_units",
    "market_metrics.sales_floor_units", "market_metrics.gmv_7d",
    "market_metrics.revenue_30d", "market_metrics.favorites_count",
    "market_metrics.bestseller_rank_text",
]

_FIELD_ROUTES = {
    # record field -> (packet section, packet key)
    "title": ("basic_facts", "title"),
    "brand": ("basic_facts", "brand"),
    "category_path": ("basic_facts", "category_path"),
    "image_url": ("basic_facts", "image_url"),
    "unit_price": ("basic_facts", "price"),
    "original_price": ("basic_facts", "original_price"),
    "discount_pct": ("basic_facts", "discount_pct"),
    "rating": ("basic_facts", "rating"),
    "review_count": ("basic_facts", "review_count"),
    "in_stock": ("basic_facts", "in_stock"),
    "sales_30d_units": ("market_metrics", "sales_30d_units"),
    "sales_7d_units": ("market_metrics", "sales_7d_units"),
    "sales_floor_units": ("market_metrics", "sales_floor_units"),
    "revenue_30d": ("market_metrics", "revenue_30d"),
    "gmv_7d": ("market_metrics", "gmv_7d"),
    "sales_growth_rate": ("market_metrics", "sales_growth_rate"),
    "favorites_count": ("market_metrics", "favorites_count"),
    "bestseller_rank_text": ("market_metrics", "bestseller_rank_text"),
    "video_views_max": ("market_metrics", "video_views_max"),
    "video_revenue_share": ("market_metrics", "video_revenue_share"),
    "related_creator_count": ("market_metrics", "related_creator_count"),
    "commission_rate": ("competition_metrics", "commission_rate"),
    "seller_type": ("competition_metrics", "seller_type"),
    "creator_gmv_concentration": ("competition_metrics", "creator_gmv_concentration"),
}

# 打进 context 的补充字段
_CONTEXT_FIELDS = {
    "category_name", "shop_name", "seller_name", "product_total_sales",
    "video_gmv_sum", "video_engagement_max", "video_count_observed",
    "affiliate_creator_count", "price_range", "shop_rating",
    "creator_conversion_ratio", "trend_tags", "review_tags_raw",
    "design_signals", "raw_evidence_path", "title_cn", "image_count",
    "shipping_fee",
}


class PacketBuilder:
    def __init__(self, market: str = "US"):
        self.market = market
        self.category_context: Dict[str, Any] = {}
        self.category_context_evidence: Dict[str, Dict] = {}

    def build(self, envelopes: List[SourceEnvelope]) -> List[CandidateDataPacket]:
        packets: Dict[str, CandidateDataPacket] = {}
        # 先吃插件源（保证详情优先于页面表格），再吃前端源
        plugin_envs = [e for e in envelopes if e.source_type == "plugin"]
        frontend_envs = [e for e in envelopes if e.source_type == "frontend"]

        for env in plugin_envs:
            for rec in env.records:
                if rec.get("record_kind") == "category_context":
                    self._absorb_category_context(rec)
            for rec in env.records:
                if rec.get("record_kind") != "product":
                    continue
                self._merge_plugin_record(packets, env, rec)

        self._apply_category_context(packets)

        for env in frontend_envs:
            for rec in env.records:
                if rec.get("record_kind") != "product":
                    continue
                self._build_frontend_packet(packets, env, rec)

        for p in packets.values():
            self._finalize(p)
        return list(packets.values())

    # ---------- 插件源合并 ----------

    def _absorb_category_context(self, rec):
        self.category_context.update(rec.get("fields", {}))
        self.category_context_evidence.update(rec.get("evidence", {}))

    def _find_by_prefix(self, packets: Dict[str, CandidateDataPacket],
                        prefix: str) -> Optional[str]:
        if len(prefix) < 12:
            return None
        hits = [cid for cid, p in packets.items()
                if p.platform == "tiktok_shop" and p.primary_product_id
                and p.primary_product_id.startswith(prefix)]
        return hits[0] if len(hits) == 1 else None

    def _merge_plugin_record(self, packets, env: SourceEnvelope, rec):
        pid = rec.get("product_id")
        if not pid:
            return
        # 外部标准信封可在 record 上声明 platform；本仓插件源默认 tiktok_shop
        platform = rec.get("platform") or "tiktok_shop"
        candidate_id = f"{platform}_{pid}"
        merged_via_prefix = False
        if rec.get("id_truncated"):
            hit = self._find_by_prefix(packets, pid)
            if hit:
                candidate_id = hit
                merged_via_prefix = True
            # 未命中则以截断 id 独立成包，问题记录在 _finalize 前补充

        packet = packets.get(candidate_id)
        if packet is None:
            url = rec.get("fields", {}).get("canonical_url")
            if url is None and platform == "tiktok_shop" and not rec.get("id_truncated"):
                url = f"https://shop.tiktok.com/view/product/{pid}"
            packet = CandidateDataPacket(
                candidate_id=candidate_id, market=self.market,
                platform=platform, primary_product_id=pid, canonical_url=url)
            packet.context["source_group"] = f"{platform}_plugin"
            packet.context["source_roles"] = rec.get("source_roles") or []
            packets[candidate_id] = packet
        if rec.get("id_truncated"):
            packet.add_issue(
                "primary_product_id",
                "id_truncated_in_source",
                f"EchoTik 页面导出的 product_id 为截断值 {pid}...，"
                + ("已按前缀合并到完整 id" if merged_via_prefix else "未找到可合并的完整 id，独立成包"))
        self._merge_fields(packet, env, rec)

    def _merge_fields(self, packet: CandidateDataPacket, env: SourceEnvelope, rec):
        ref = {"source_id": env.source_id, "envelope_id": env.envelope_id}
        if ref not in packet.source_refs:
            packet.source_refs.append(ref)
        for name, value in rec.get("fields", {}).items():
            if name.startswith("_"):
                if name == "_price_no_currency":
                    packet.add_issue("basic_facts.price", "price_without_currency",
                                     f"价格 {value} 无币种，按无效处理")
                continue
            ev_dict = rec.get("evidence", {}).get(name)
            evidence = EvidenceRef(**ev_dict) if ev_dict else None
            route = _FIELD_ROUTES.get(name)
            if route:
                section, key = route
                existing = getattr(packet, section).get(key)
                if existing not in (None, [], {}) and existing != value:
                    # 冲突：保留先值，登记双方证据
                    if evidence is not None:
                        packet.add_evidence(evidence, f"{section}.{key}")
                    packet.add_issue(
                        f"{section}.{key}", "source_conflict",
                        f"{env.source_id} 给出 {value!r}，与已有 {existing!r} 不一致，保留先值")
                    continue
                packet.set_fact(section, key, value, evidence)
            elif name in _CONTEXT_FIELDS:
                if name not in packet.context:
                    packet.context[name] = value
                    if evidence is not None:
                        packet.add_evidence(evidence, f"context.{name}")
            # 其余字段忽略但不丢证据意义（envelope 里仍有原值）
        if rec.get("quality_status") in ("ACCESS_BLOCKED", "PARTIAL_SOURCE_MISSING"):
            packet.quality_status = rec["quality_status"]

    def _apply_category_context(self, packets):
        """把 Kalodata 类目大盘写到所有 TikTok 女装候选的竞争指标上。"""
        mapping = {
            "category_top3_shop_ratio": "category_top3_shop_ratio",
            "category_top10_shop_ratio": "category_top10_shop_ratio",
            "category_shop_count": "category_shop_count",
        }
        for packet in packets.values():
            if packet.platform != "tiktok_shop":
                continue
            cat = packet.context.get("category_name") or ""
            is_womenswear = ("女装" in str(cat)) or ("Womenswear" in str(cat)) or (
                packet.basic_facts.get("category_path") and
                any("女装" in str(c) for c in packet.basic_facts["category_path"]))
            if not is_womenswear:
                continue
            for ctx_key, pkt_key in mapping.items():
                val = self.category_context.get(ctx_key)
                ev_dict = self.category_context_evidence.get(ctx_key)
                if val is not None:
                    ev = EvidenceRef(**ev_dict) if ev_dict else None
                    packet.set_fact("competition_metrics", pkt_key, val, ev)
            packet.context["category_avg_unit_price_cny"] = \
                self.category_context.get("category_avg_unit_price_cny")
            packet.context["category_revenue_growth_rate"] = \
                self.category_context.get("category_revenue_growth_rate")

    # ---------- 前端源 ----------

    def _build_frontend_packet(self, packets, env: SourceEnvelope, rec):
        site = rec.get("site", env.source_id)
        pid = rec.get("product_id") or f"row{rec.get('fields', {}).get('canonical_url', '')[-24:]}"
        candidate_id = f"{site}_{pid}"
        if candidate_id in packets:
            candidate_id = f"{site}_{pid}_{len(packets)}"
        packet = CandidateDataPacket(
            candidate_id=candidate_id, market=self.market, platform=site,
            primary_product_id=str(pid) if pid else None,
            canonical_url=rec.get("fields", {}).get("canonical_url"))
        packet.context["source_group"] = env.source_id
        packet.context["category_label"] = rec.get("category_label")
        packet.context["seasonal"] = bool(rec.get("seasonal"))
        packet.context["track_hint"] = rec.get("track_hint")
        packet.context["source_roles"] = rec.get("source_roles") or []
        packet.context["site"] = site
        if rec.get("quality_status") and rec["quality_status"] != "VALID":
            packet.quality_status = rec["quality_status"]
        packets[candidate_id] = packet
        self._merge_fields(packet, env, rec)
        # canonical_url 从字段同步到顶层
        if packet.canonical_url is None:
            packet.canonical_url = rec.get("fields", {}).get("canonical_url")
        # 前端趋势标签 -> trend_signals
        tags = packet.context.get("trend_tags")
        if tags:
            ev_ids = packet.evidence_for("context.trend_tags")
            packet.market_metrics["trend_signals"] = [
                {"signal": t, "kind": "platform_trend_tag", "evidence_refs": ev_ids}
                for t in tags]

    # ---------- 收尾 ----------

    def _finalize(self, packet: CandidateDataPacket):
        checks = [
            ("basic_facts", "title"), ("basic_facts", "price"),
            ("basic_facts", "rating"), ("basic_facts", "review_count"),
            ("market_metrics", "sales_growth_rate"),
        ]
        for section, key in checks:
            if getattr(packet, section).get(key) in (None, [], {}):
                packet.mark_missing(f"{section}.{key}")
        has_demand = any(
            packet_get(packet, path) not in (None, [], {})
            for path in DEMAND_SIGNAL_FIELDS)
        if not has_demand:
            packet.mark_missing("market_metrics.<any_demand_signal>")
        for path in ("owned_supply_inputs.target_cost", "owned_supply_inputs.moq",
                     "owned_supply_inputs.lead_time_days"):
            packet.mark_missing(path)
        price = packet.basic_facts.get("price")
        if price is not None and not price.get("currency"):
            packet.add_issue("basic_facts.price", "price_without_currency",
                             "价格缺币种，视为无效字段")
            packet.basic_facts["price"] = None
            packet.mark_missing("basic_facts.price")


def packet_get(packet: CandidateDataPacket, path: str):
    section, key = path.split(".", 1)
    return getattr(packet, section, {}).get(key)
