"""Kalodata 插件源适配器。

输入：`02.插件数据源/Kalodata最终交付原数据.xlsx`
  - 采集对象：TikTok Shop US「女装与女士内衣」类目（category_id=601152），
    周期 2026-05-31 ~ 2026-06-29（30 天）。
  - 金额均为 Kalodata 页面展示的人民币换算值（CNY）。

输出 SourceEnvelope.records：
  - 1 条 ``category_context``：类目大盘（销售额、增速、Top3/Top10 店铺集中度、店铺数）。
  - 10 条 ``product``：类目 Top10 商品（30 天销售额/销量/单价/佣金率/趋势数组）。
  - 深挖商品（product_detail 系列 sheet）在对应 product 记录上富化：
    评分、评论数、价格区间、达人 GMV 集中度、关联达人数、视频销售额占比等。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .base import BaseAdapter, read_sheet_dicts
from ..parsing import parse_money, parse_number, parse_percent, trend_growth

CURRENCY = "CNY"


class KalodataAdapter(BaseAdapter):
    source_id = "kalodata"
    source_type = "plugin"

    def _field(self, record: Dict[str, Any], name: str, value, sheet: str, row,
               confidence: str = "high", note: str = "", source_type: str = None):
        if value is None:
            return
        record["fields"][name] = value
        ev = self.make_evidence(name, sheet, row, confidence=confidence, note=note,
                                source_type=source_type)
        record["evidence"][name] = ev.to_dict()

    def collect(self):
        records: List[Dict[str, Any]] = []
        records.append(self._category_context())
        products = self._top_products()
        self._enrich_product_detail(products)
        records.extend(products.values())
        return [self.make_envelope(records)]

    # ---------- 类目大盘 ----------

    def _category_context(self) -> Dict[str, Any]:
        sheet = "category_summary"
        rows = read_sheet_dicts(self.workbook_path, sheet)
        rec = {"record_kind": "category_context", "fields": {}, "evidence": {}}
        if not rows:
            self.errors.append("category_summary 为空")
            return rec
        r = rows[0]
        row = r["_row"]
        rec["category_id"] = str(r.get("category_id"))
        rec["category_name"] = r.get("category_name")
        self._field(rec, "category_revenue_30d_cny", parse_number(r.get("revenue_origin")), sheet, row)
        self._field(rec, "category_revenue_growth_rate", parse_percent(r.get("revenue_growth_rate")), sheet, row)
        self._field(rec, "category_top3_shop_ratio", parse_percent(r.get("top_3_shop_revenue_ratio")), sheet, row)
        self._field(rec, "category_top10_shop_ratio", parse_percent(r.get("top_10_shop_revenue_ratio")), sheet, row)
        self._field(rec, "category_shop_count", parse_number(r.get("shop_number")), sheet, row)
        self._field(rec, "category_total_sale_30d", parse_number(r.get("total_sale")), sheet, row)
        rev = parse_number(r.get("revenue_origin"))
        sale = parse_number(r.get("total_sale"))
        if rev and sale:
            self._field(rec, "category_avg_unit_price_cny", round(rev / sale, 2), sheet, row,
                        confidence="medium", note="derived: revenue_origin / total_sale",
                        source_type="derived")
        return rec

    # ---------- 类目 Top10 商品 ----------

    def _top_products(self) -> Dict[str, Dict[str, Any]]:
        sheet = "category_top_products"
        rows = read_sheet_dicts(self.workbook_path, sheet)
        products: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            pid = str(r.get("id") or "").strip()
            if not pid:
                self.warnings.append(f"{sheet} 行 {r['_row']} 无商品 id，跳过")
                continue
            row = r["_row"]
            rec = {
                "record_kind": "product",
                "product_id": pid,
                "id_truncated": False,
                "fields": {},
                "evidence": {},
            }
            self._field(rec, "title", r.get("product_title"), sheet, row)
            self._field(rec, "seller_name", r.get("seller_name"), sheet, row)
            amount, cur = parse_money(r.get("revenue"), CURRENCY)
            if amount is not None:
                self._field(rec, "revenue_30d", {"amount": amount, "currency": cur}, sheet, row,
                            note="Kalodata 30d GMV，人民币换算口径")
            self._field(rec, "sales_30d_units", parse_number(r.get("sale")), sheet, row)
            up, upc = parse_money(r.get("unit_price"), CURRENCY)
            if up is not None:
                self._field(rec, "unit_price", {"amount": up, "currency": upc}, sheet, row)
            self._field(rec, "commission_rate", parse_percent(r.get("commission_rate")), sheet, row)
            trend_raw = r.get("revenue_trend")
            arr = None
            if isinstance(trend_raw, str):
                try:
                    arr = json.loads(trend_raw)
                except (ValueError, TypeError):
                    arr = None
            elif isinstance(trend_raw, (list, tuple)):
                arr = list(trend_raw)
            if arr:
                g = trend_growth(arr)
                if g is not None:
                    self._field(rec, "sales_growth_rate", round(g, 4), sheet, row,
                                confidence="medium",
                                note="derived: mean(trend后3点)/mean(前3点)-1",
                                source_type="derived")
                else:
                    rec["field_issue"] = "new_launch_spike_or_short_trend"
            rec["fields"]["category_id"] = "601152"
            rec["fields"]["category_name"] = "Womenswear & Underwear"
            products[pid] = rec
        return products

    # ---------- 深挖商品富化 ----------

    def _enrich_product_detail(self, products: Dict[str, Dict[str, Any]]):
        detail_rows = read_sheet_dicts(self.workbook_path, "product_detail")
        for r in detail_rows:
            pid = str(r.get("id") or "").strip()
            rec = products.get(pid)
            if rec is None:
                continue
            sheet, row = "product_detail", r["_row"]
            self._field(rec, "rating", parse_number(r.get("product_rating")), sheet, row)
            self._field(rec, "review_count", parse_number(r.get("product_review_count")), sheet, row)
            self._field(rec, "shop_rating", parse_number(r.get("shop_rating")), sheet, row)
            self._field(rec, "brand", r.get("brand_name"), sheet, row)
            self._field(rec, "seller_type", r.get("seller_type"), sheet, row)
            self._field(rec, "creator_gmv_concentration",
                        parse_percent(r.get("creator_gmv_concentration")), sheet, row)
            cats = [r.get("pri_cate_id"), r.get("sec_cate_id"), r.get("ter_cate_id")]
            cats = [c for c in cats if c]
            if cats:
                self._field(rec, "category_path", cats, sheet, row)
            lo, _ = parse_money(r.get("min_real_price"), CURRENCY)
            hi, _ = parse_money(r.get("max_real_price"), CURRENCY)
            if lo is not None or hi is not None:
                self._field(rec, "price_range",
                            {"low": lo, "high": hi, "currency": CURRENCY}, sheet, row)
            sf, _ = parse_money(r.get("shipping_fee"), CURRENCY)
            if sf is not None:
                self._field(rec, "shipping_fee", {"amount": sf, "currency": CURRENCY},
                            sheet, row, note="运费，供利润结构代理指标使用")

        total_rows = read_sheet_dicts(self.workbook_path, "product_detail_total")
        for r in total_rows:
            # product_detail_total 无 id 列；本工作簿仅深挖 1 个商品，
            # 通过 unit_price 与 top_products 匹配校验后挂到同一商品。
            sheet, row = "product_detail_total", r["_row"]
            target = self._match_by_unit_price(products, r.get("unit_price"))
            if target is None:
                self.warnings.append("product_detail_total 无法匹配到商品，已跳过")
                continue
            self._field(target, "related_creator_count",
                        parse_number(r.get("related_creator_count")), sheet, row)
            self._field(target, "creator_conversion_ratio",
                        parse_number(r.get("creatorConversionRatio")), sheet, row)
            video_rev = parse_number(r.get("original_video_revenue"))
            total_rev = parse_number(r.get("original_revenue"))
            if video_rev is not None and total_rev:
                self._field(target, "video_revenue_share",
                            round(video_rev / total_rev, 4), sheet, row,
                            confidence="medium",
                            note="derived: original_video_revenue / original_revenue",
                            source_type="derived")

        video_rows = read_sheet_dicts(self.workbook_path, "product_videos")
        views = [parse_number(r.get("views")) for r in video_rows]
        views = [v for v in views if v is not None]
        if views and video_rows:
            target = self._deep_dive_target(products)
            if target is not None:
                self._field(target, "video_views_max", max(views),
                            "product_videos", "1..{}".format(len(video_rows)),
                            note="深挖商品关联视频最高播放量")

    def _match_by_unit_price(self, products, unit_price_raw) -> Optional[Dict[str, Any]]:
        up, _ = parse_money(unit_price_raw, CURRENCY)
        if up is None:
            return self._deep_dive_target(products)
        for rec in products.values():
            rec_up = rec["fields"].get("unit_price")
            if rec_up and abs(rec_up["amount"] - up) < 0.5:
                return rec
        return self._deep_dive_target(products)

    def _deep_dive_target(self, products) -> Optional[Dict[str, Any]]:
        detail_rows = read_sheet_dicts(self.workbook_path, "product_detail")
        for r in detail_rows:
            pid = str(r.get("id") or "").strip()
            if pid in products:
                return products[pid]
        return None
