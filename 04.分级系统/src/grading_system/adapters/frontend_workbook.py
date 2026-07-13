"""前端采集工作簿通用适配器（配置驱动）。

输入：`03.前端数据源/` 下由前端采集系统导出的验收工作簿。
四个工作簿列结构不同（Shein 带销量/畅销榜/评价标签；独立站带收藏数；
图案连裤袜带库存状态/图案元素；万圣节带设计细节），列映射全部来自
`configs/sources_p0.yaml`，不在代码里写死。

行级质量状态映射：
  OK -> VALID；ACCESS_CHALLENGE -> ACCESS_BLOCKED；NEEDS_REVIEW -> PARTIAL_SOURCE_MISSING。
ACCESS_BLOCKED 的行仍然入库（字段真实存在），但候选包 quality_status 会降级，
打分时按规则降置信度。
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import BaseAdapter, read_sheet_dicts
from ..parsing import parse_number, parse_percent, parse_sales_floor

QUALITY_MAP = {
    "OK": "VALID",
    "ACCESS_CHALLENGE": "ACCESS_BLOCKED",
    "NEEDS_REVIEW": "PARTIAL_SOURCE_MISSING",
}


class FrontendWorkbookAdapter(BaseAdapter):
    source_type = "frontend"

    def __init__(self, workbook_path, source_id: str, sites: List[str],
                 column_map: Dict[str, str], category_label: str,
                 seasonal: bool = False, market: str = "US",
                 track_hint: str = None, source_roles=None, **kwargs):
        super().__init__(workbook_path, market=market, **kwargs)
        self.source_id = source_id
        self.sites = sites
        self.column_map = column_map
        self.category_label = category_label
        self.seasonal = seasonal
        # 三赛道采集提示：该数据源按哪个赛道立项采集（trend_rising/evergreen/
        # pain_improvement）。仅当候选自身信号不足以判定赛道时作为兜底。
        self.track_hint = track_hint
        # 来源角色（多值）：discovery/content/transaction/voc/supply/owned_feedback
        self.source_roles = source_roles or []

    def _field(self, record, name, value, sheet, row, confidence="high", note=""):
        if value in (None, ""):
            return
        record["fields"][name] = value
        ev = self.make_evidence(name, sheet, row, confidence=confidence, note=note)
        record["evidence"][name] = ev.to_dict()

    def _col(self, r: Dict[str, Any], key: str):
        col = self.column_map.get(key)
        if not col:
            return None
        v = r.get(col)
        if isinstance(v, str):
            v = v.strip()
        return v if v not in ("", None) else None

    def collect(self):
        records: List[Dict[str, Any]] = []
        blocked = 0
        for sheet in self.sites:
            rows = read_sheet_dicts(self.workbook_path, sheet)
            for r in rows:
                rec = self._row_to_record(r, sheet)
                if rec is None:
                    continue
                if rec["quality_status"] == "ACCESS_BLOCKED":
                    blocked += 1
                records.append(rec)
        status = "VALID"
        if blocked:
            self.warnings.append(f"{blocked} 行 ACCESS_CHALLENGE（未绕过，按原样入库并降置信度）")
        return [self.make_envelope(records, quality_status=status)]

    def _row_to_record(self, r: Dict[str, Any], sheet: str):
        pid = self._col(r, "product_id")
        url = self._col(r, "url")
        if pid is None and url is None:
            self.warnings.append(f"{sheet} 行 {r['_row']} 无商品ID与URL，跳过")
            return None
        row = r["_row"]
        raw_quality = str(self._col(r, "quality") or "OK")
        rec = {
            "record_kind": "product",
            "product_id": str(pid) if pid is not None else None,
            "id_truncated": False,
            "site": sheet,
            "category_label": self.category_label,
            "seasonal": self.seasonal,
            "track_hint": self.track_hint,
            "source_roles": self.source_roles,
            "quality_status": QUALITY_MAP.get(raw_quality, "PARTIAL_SOURCE_MISSING"),
            "fields": {},
            "evidence": {},
        }
        self._field(rec, "title", self._col(r, "title"), sheet, row)
        self._field(rec, "title_cn", self._col(r, "title_cn"), sheet, row)
        self._field(rec, "brand", self._col(r, "brand"), sheet, row)
        self._field(rec, "category_name", self._col(r, "category"), sheet, row)
        self._field(rec, "image_url", self._col(r, "image_url"), sheet, row)
        self._field(rec, "canonical_url", url, sheet, row)
        self._field(rec, "raw_evidence_path", self._col(r, "raw_evidence"), sheet, row)

        currency = self._col(r, "currency")
        price = parse_number(self._col(r, "price"))
        if price is not None:
            if currency:
                self._field(rec, "unit_price", {"amount": price, "currency": currency},
                            sheet, row)
            else:
                rec["fields"]["_price_no_currency"] = price
        orig = parse_number(self._col(r, "original_price"))
        if orig is not None and currency:
            self._field(rec, "original_price", {"amount": orig, "currency": currency},
                        sheet, row)
        disc = parse_percent(self._col(r, "discount_pct"))
        if disc is not None:
            self._field(rec, "discount_pct", disc, sheet, row)
        elif price is not None and orig and orig > 0 and price < orig:
            self._field(rec, "discount_pct", round(price / orig - 1.0, 4), sheet, row,
                        confidence="medium", note="derived: 售价/原价-1")

        self._field(rec, "rating", parse_number(self._col(r, "rating")), sheet, row)
        self._field(rec, "review_count", parse_number(self._col(r, "review_count")), sheet, row)
        self._field(rec, "sales_floor_units", parse_sales_floor(self._col(r, "sales_floor")),
                    sheet, row, note="Shein 销量为下限口径（如 100+ 记 100）")
        self._field(rec, "favorites_count", parse_number(self._col(r, "favorites")), sheet, row,
                    note="收藏/心愿单热度，非成交数据")
        self._field(rec, "bestseller_rank_text", self._col(r, "bestseller_rank"), sheet, row)
        self._field(rec, "image_count", parse_number(self._col(r, "image_count")), sheet, row)

        trend = self._col(r, "trend_tags")
        if trend:
            tags = [t.strip().lstrip("#") for t in str(trend).replace(";", ",").split(",") if t.strip()]
            self._field(rec, "trend_tags", tags, sheet, row)
        self._field(rec, "review_tags_raw", self._col(r, "review_tags"), sheet, row)

        design_signals = []
        for key in ("pattern_elements", "design_details", "fabric_features"):
            v = self._col(r, key)
            if v:
                design_signals.append({"kind": key, "text": str(v)[:200]})
        if design_signals:
            self._field(rec, "design_signals", design_signals, sheet, row)

        stock = self._col(r, "stock_status")
        if stock:
            self._field(rec, "in_stock", str(stock), sheet, row)
        return rec
