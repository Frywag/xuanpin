"""XLSX 推荐表导出（只读 AnalysisResult / CandidateDataPacket，不触发新采集）。

Sheet 结构对应《07_分层分析系统组任务书》§12：
  推荐总表 / 评分明细 / 数据源覆盖 / 差评改良 / 多平台比对 / 风险清单 /
  证据索引 / 运行记录
"""
from __future__ import annotations

from typing import Any, Dict, List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import AnalysisResult, CandidateDataPacket
from .packet_builder import KEY_FIELDS

GRADE_COLORS = {"S": "FFB45309", "A": "FF2563EB", "B": "FF6B7280", "C": "FFD1D5DB"}
HEADER_FILL = PatternFill("solid", fgColor="FF1F2937")
HEADER_FONT = Font(color="FFFFFFFF", bold=True)


def _money(v) -> str:
    if not v or not isinstance(v, dict) or v.get("amount") is None:
        return ""
    cur = v.get("currency") or "?"
    return f"{v['amount']:,.2f} {cur}"


def _sheet(wb: Workbook, title: str, headers: List[str], widths: List[int]):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"
    return ws


def export_report(path, packets: List[CandidateDataPacket],
                  results: Dict[str, AnalysisResult],
                  run_meta: Dict[str, Any],
                  evidence_sheet_top_n: int = 300):
    wb = Workbook()
    wb.remove(wb.active)
    by_id = {p.candidate_id: p for p in packets}
    ordered = sorted(results.values(),
                     key=lambda r: -(r.priority_score["pct"] or 0))

    # ---------- 1. 推荐总表 ----------
    ws = _sheet(wb, "推荐总表",
                ["candidate_id", "product_link", "image", "title", "category",
                 "source_platform", "track", "grade", "priority_score", "pct",
                 "track_tags", "recommendation_reason",
                 "risk_summary", "missing_key_fields", "next_action", "evidence_count",
                 "confidence", "L1", "L2", "L3"],
                [30, 40, 40, 50, 20, 16, 12, 7, 12, 8, 22, 60, 50, 40, 40, 12, 10, 6, 6, 6])
    for r in ordered:
        p = by_id[r.candidate_id]
        category = (p.context.get("category_name")
                    or "/".join(p.basic_facts.get("category_path") or [])
                    or p.context.get("category_label") or "")
        ws.append([
            r.candidate_id, p.canonical_url or "", p.basic_facts.get("image_url") or "",
            p.basic_facts.get("title") or "", category, p.platform,
            r.priority_score.get("track_label") or "",
            r.priority_score["grade"] or "", r.priority_score["total"],
            r.priority_score["pct"], "、".join(r.track_tags),
            r.recommendation["reason_summary"], r.recommendation["risk_summary"],
            "、".join(r.missing_fields[:6]), "；".join(r.recommendation["next_actions"][:3]),
            len(p.evidence_pack), r.confidence,
            r.layer_results["L1"].grade or "", r.layer_results["L2"].grade or "",
            r.layer_results["L3"].grade or ""])
        g = r.priority_score["grade"]
        if g in GRADE_COLORS:
            ws.cell(row=ws.max_row, column=8).font = Font(
                color=GRADE_COLORS[g], bold=g in ("S", "A"))
    ws.auto_filter.ref = ws.dimensions

    # ---------- 2. 评分明细 ----------
    ws = _sheet(wb, "评分明细",
                ["candidate_id", "grade", "维度", "得分", "满分", "理由",
                 "evidence_refs", "missing"],
                [30, 7, 12, 8, 8, 70, 45, 8])
    for r in ordered:
        for c in r.priority_score["components"]:
            ws.append([r.candidate_id, r.priority_score["grade"], c.name,
                       c.score, c.max_score, c.reason,
                       ",".join(c.evidence_refs[:8]), "Y" if c.missing else ""])
    ws.auto_filter.ref = ws.dimensions

    # ---------- 3. 数据源覆盖 ----------
    ws = _sheet(wb, "数据源覆盖",
                ["candidate_id", "sources", "source_count", "关键字段覆盖率",
                 "quality_status", "missing_fields", "field_issues"],
                [30, 35, 12, 15, 18, 60, 60])
    for r in ordered:
        p = by_id[r.candidate_id]
        ws.append([p.candidate_id,
                   "、".join(sorted({s['source_id'] for s in p.source_refs})),
                   p.source_count, round(p.field_coverage(KEY_FIELDS), 2),
                   p.quality_status, "、".join(p.missing_fields),
                   "；".join(f"{i.field_path}:{i.issue}" for i in p.field_issues[:5])])
    ws.auto_filter.ref = ws.dimensions

    # ---------- 4. 差评改良 ----------
    ws = _sheet(wb, "差评改良",
                ["candidate_id", "聚类", "提及次数", "频率", "代表标签",
                 "改良方向", "不改的风险", "evidence_refs"],
                [30, 16, 10, 8, 40, 30, 30, 40])
    for r in ordered:
        p = by_id[r.candidate_id]
        for c in p.product_opportunity.get("negative_review_clusters", []):
            ws.append([p.candidate_id, c["cluster_name"], c["mention_count"],
                       c["frequency_hint"], "、".join(c["representative_reviews"]),
                       c["improvement_opportunity"], c["risk_if_unfixed"],
                       ",".join(c["evidence_refs"][:5])])

    # ---------- 4b. 商品画像（152 键模板；直取+manual，语义键经 LLM 回灌入库） ----------
    ws = _sheet(wb, "商品画像",
                ["candidate_id", "分组", "键", "值", "来源", "evidence_refs"],
                [30, 14, 18, 60, 12, 40])
    for r in ordered:
        p = by_id[r.candidate_id]
        for name, item in (p.context.get("profile") or {}).items():
            ws.append([p.candidate_id, item.get("group", ""), name,
                       str(item.get("value")) if item.get("value") is not None
                       else "（待人工补录）",
                       item.get("source", ""),
                       ",".join(item.get("evidence_refs", [])[:4])])
    ws.auto_filter.ref = ws.dimensions

    # ---------- 5. 多平台比对 ----------
    ws = _sheet(wb, "多平台比对",
                ["candidate_id", "类型", "内容", "币种", "备注"],
                [30, 22, 70, 10, 50])
    for r in ordered:
        p = by_id[r.candidate_id]
        for m in p.cross_platform.get("matched_products", []):
            ws.append([p.candidate_id, "多源商品ID互验",
                       f"{m['source_id']} / product_id={m['product_id']}", "",
                       m["match_type"]])
        for row in p.cross_platform.get("platform_price_comparison", []):
            ws.append([p.candidate_id, f"价格带（{row['style_group']}）",
                       f"{row['source_group']}: p25={row['p25']} p50={row['p50']} "
                       f"p75={row['p75']} (n={row['n']})",
                       row["currency"] or "", row["note"]])
        for s in p.cross_platform.get("same_style_signals", []):
            ws.append([p.candidate_id, "同风格信号", s["signal"], "", s["note"]])

    # ---------- 6. 风险清单 ----------
    ws = _sheet(wb, "风险清单",
                ["candidate_id", "grade", "风险描述", "confidence", "evidence_refs"],
                [30, 7, 80, 12, 45])
    for r in ordered:
        for c in r.claims:
            if c.claim_type == "risk":
                ws.append([r.candidate_id, r.priority_score["grade"], c.claim,
                           c.confidence, ",".join(c.evidence_refs[:6])])
    ws.auto_filter.ref = ws.dimensions

    # ---------- 7. 证据索引（前 N 个候选，全量证据见 JSON 产物） ----------
    ws = _sheet(wb, "证据索引",
                ["evidence_id", "candidate_id", "source_id", "field_path",
                 "artifact_path", "record_locator", "confidence", "note"],
                [26, 30, 16, 40, 55, 28, 10, 40])
    for r in ordered[:evidence_sheet_top_n]:
        p = by_id[r.candidate_id]
        for e in p.evidence_pack:
            ws.append([e.evidence_id, p.candidate_id, e.source_id, e.field_path,
                       e.artifact_path, e.record_locator, e.confidence, e.note])
    ws.auto_filter.ref = ws.dimensions

    # ---------- 8. 运行记录 ----------
    ws = _sheet(wb, "运行记录", ["key", "value"], [40, 110])
    for k, v in run_meta.items():
        ws.append([k, str(v)])

    wb.save(path)
    return path
