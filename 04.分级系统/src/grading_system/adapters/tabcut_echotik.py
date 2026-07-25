"""Tabcut / EchoTik 插件源适配器。

输入：`02.插件数据源/Tabcut_EchoTik_最终原数据交付表.xlsx`
  - `最终原数据` sheet：每行一个 JSON 记录（Top商品/Top店铺/Top视频/Top达人/
    视频原始行/关系明细等），市场 US。
  - `EchoTik商品库实采` sheet：Computer Use 读取的商品库页面可见表格（10 条）。

口径说明：
  - Tabcut 价格与视频 GMV 为 USD（商品价格×估算销量 == 估算GMV 验证通过）。
  - EchoTik 的 product_id 在源数据中即为截断值（如 ``17295083709696299...``），
    记录 ``id_truncated=True``，由 PacketBuilder 用前缀匹配合并，合并动作
    写入 field_issues 保持可追溯。
  - 播放量/互动率只能作为内容热度信号，销量类字段使用「估算」口径入库。
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List

from .base import BaseAdapter, read_sheet_dicts
from ..parsing import (is_truncated_id, parse_money, parse_number, parse_percent,
                       parse_price_range, strip_truncated_id)

USD = "USD"


class TabcutEchotikAdapter(BaseAdapter):
    """一个工作簿、两个数据源：Tabcut 与 EchoTik 是两个独立插件工具，
    输出两个 SourceEnvelope（source_id 分别为 ``tabcut`` / ``echotik``），
    这样「多源互验」的语义才成立。"""

    source_id = "tabcut"  # collect 过程中按当前处理的数据源切换
    source_type = "plugin"

    def _field(self, record, name, value, sheet, row, confidence="high", note="",
               source_type=None):
        if value is None:
            return
        record["fields"][name] = value
        ev = self.make_evidence(name, sheet, row, confidence=confidence, note=note,
                                source_type=source_type)
        record["evidence"][name] = ev.to_dict()

    def collect(self):
        sheet = "最终原数据"
        raw_rows = read_sheet_dicts(self.workbook_path, sheet)
        by_dataset: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in raw_rows:
            payload = r.get("原始JSON")
            try:
                data = json.loads(payload) if isinstance(payload, str) else None
            except (ValueError, TypeError):
                data = None
            if data is None:
                self.warnings.append(f"{sheet} 行 {r['_row']} 原始JSON 解析失败")
                continue
            by_dataset[str(r.get("数据集"))].append({"row": r["_row"], "data": data})

        # ---- Tabcut envelope ----
        self.source_id = "tabcut"
        tabcut_products: Dict[str, Dict[str, Any]] = {}
        self._tabcut_top_products(by_dataset.get("Top商品", []), tabcut_products, sheet)
        self._tabcut_video_rows(by_dataset.get("视频原始行", []), tabcut_products, sheet)
        tabcut_env = self.make_envelope(list(tabcut_products.values()))

        # ---- EchoTik envelope ----
        self.source_id = "echotik"
        self.warnings, self.errors = [], []
        echotik_products: Dict[str, Dict[str, Any]] = {}
        self._echotik_product_table(echotik_products)
        echotik_env = self.make_envelope(list(echotik_products.values()))
        return [tabcut_env, echotik_env]

    # ---------- Tabcut Top商品 ----------

    def _tabcut_top_products(self, entries, products, sheet):
        for e in entries:
            d, row = e["data"], e["row"]
            pid = str(d.get("商品ID") or "").strip()
            if not pid:
                continue
            rec = products.setdefault(pid, {
                "record_kind": "product", "product_id": pid,
                "id_truncated": False, "source_roles": ["transaction", "content"],
                "fields": {}, "evidence": {},
            })
            self._field(rec, "title", d.get("商品标题"), sheet, row)
            price = parse_number(d.get("价格"))
            cur = d.get("币种") or USD
            if price is not None:
                self._field(rec, "unit_price", {"amount": price, "currency": cur}, sheet, row)
            self._field(rec, "sales_30d_units", parse_number(d.get("销量")), sheet, row,
                        note="Tabcut Top商品榜销量（30 天榜口径，估算值）")
            self._field(rec, "sales_growth_rate", parse_percent(d.get("销量增长率")), sheet, row)
            self._field(rec, "category_name", d.get("类目名称"), sheet, row)
            self._field(rec, "image_url", d.get("图片URL"), sheet, row)

    # ---------- Tabcut 视频原始行（按商品聚合内容热度） ----------

    def _tabcut_video_rows(self, entries, products, sheet):
        agg: Dict[str, Dict[str, Any]] = {}
        for e in entries:
            d, row = e["data"], e["row"]
            pid = str(d.get("商品ID") or "").strip()
            if not pid:
                continue
            a = agg.setdefault(pid, {"rows": [], "views": [], "video_gmv": [],
                                     "engagement": [], "total_sales": None,
                                     "price": None, "title": None,
                                     "creator_followers": []})
            a["rows"].append(row)
            v = parse_number(d.get("播放量"))
            if v is not None:
                a["views"].append(v)
            g = parse_number(d.get("估算视频GMV"))
            if g is not None:
                a["video_gmv"].append(g)
            er = parse_number(d.get("互动率"))
            if er is not None:
                a["engagement"].append(er)
            ts = parse_number(d.get("商品总销量"))
            if ts is not None:
                a["total_sales"] = ts
            p = parse_number(d.get("商品价格"))
            if p is not None:
                a["price"] = p
            a["title"] = a["title"] or d.get("商品标题")
            f = parse_number(d.get("达人粉丝数"))
            if f is not None:
                a["creator_followers"].append(f)

        for pid, a in agg.items():
            rec = products.setdefault(pid, {
                "record_kind": "product", "product_id": pid,
                "id_truncated": False, "fields": {}, "evidence": {},
            })
            rows = f"{min(a['rows'])}..{max(a['rows'])}"
            if "title" not in rec["fields"]:
                self._field(rec, "title", a["title"], sheet, rows)
            if "unit_price" not in rec["fields"] and a["price"] is not None:
                self._field(rec, "unit_price", {"amount": a["price"], "currency": USD},
                            sheet, rows, note="视频原始行中的商品价格（USD）")
            self._field(rec, "product_total_sales", a["total_sales"], sheet, rows,
                        note="Tabcut 商品累计总销量（估算）")
            if a["views"]:
                self._field(rec, "video_views_max", max(a["views"]), sheet, rows,
                            note="关联带货视频最高播放量（内容热度信号，非销量）")
                self._field(rec, "video_count_observed", len(a["views"]), sheet, rows)
            if a["video_gmv"]:
                self._field(rec, "video_gmv_sum",
                            {"amount": round(sum(a["video_gmv"]), 2), "currency": USD},
                            sheet, rows, confidence="medium",
                            note="derived: 观测到的视频估算GMV求和（估算口径）",
                            source_type="derived")
            if a["engagement"]:
                self._field(rec, "video_engagement_max", max(a["engagement"]), sheet, rows)

    # ---------- EchoTik 商品库实采 ----------

    def _echotik_product_table(self, products):
        sheet = "EchoTik商品库实采"
        rows = read_sheet_dicts(self.workbook_path, sheet)
        for r in rows:
            pid_raw = str(r.get("product_id") or "").strip()
            if not pid_raw:
                continue
            truncated = is_truncated_id(pid_raw)
            pid = strip_truncated_id(pid_raw)
            rec = {
                "record_kind": "product", "product_id": pid,
                "id_truncated": truncated, "source_roles": ["transaction"],
                "fields": {}, "evidence": {},
            }
            row = r["_row"]
            self._field(rec, "title", r.get("product_name"), sheet, row)
            lo, hi, cur = parse_price_range(r.get("price"), USD)
            if lo is not None:
                self._field(rec, "unit_price", {"amount": lo, "currency": cur or USD},
                            sheet, row, note="EchoTik 商品库页面价格（区间取下限）")
                if hi is not None and hi != lo:
                    self._field(rec, "price_range",
                                {"low": lo, "high": hi, "currency": cur or USD}, sheet, row)
            self._field(rec, "category_name", r.get("category"), sheet, row)
            self._field(rec, "rating", parse_number(r.get("rating")), sheet, row)
            self._field(rec, "review_count", parse_number(r.get("review_count")), sheet, row)
            self._field(rec, "sales_7d_units", parse_number(r.get("sales_7d")), sheet, row,
                        note="EchoTik 7 天销量（估算）")
            g7, _ = parse_money(r.get("gmv_7d_usd"), USD)
            if g7 is not None:
                self._field(rec, "gmv_7d", {"amount": g7, "currency": USD}, sheet, row)
            self._field(rec, "product_total_sales", parse_number(r.get("total_sales")),
                        sheet, row, note="EchoTik 商品累计销量（估算）")
            self._field(rec, "affiliate_creator_count",
                        parse_number(r.get("affiliate_creator_count")), sheet, row)
            self._field(rec, "shop_name", r.get("shop_name"), sheet, row)
            if truncated:
                rec["fields"]["_id_note"] = "product_id 在源数据中即为截断值，需前缀匹配"
            # 截断 id 与既有完整 id 的合并交给 PacketBuilder（保持单一职责）
            products[f"echotik::{pid}"] = rec
