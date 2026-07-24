from __future__ import annotations

import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN_ROOT = Path(__file__).resolve().parent
BASE_RUN = ROOT / "06.分级系统数据源/全量产品推荐运行_20260710"


def load_base_module():
    spec = importlib.util.spec_from_file_location(
        "base_augment_delivery", BASE_RUN / "augment_delivery.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.RUN_ROOT = RUN_ROOT
    module.OUTPUT = RUN_ROOT / "output"
    module.DELIVERY = RUN_ROOT / "delivery"
    module.BASE_WORKBOOK = module.OUTPUT / "选品推荐表.xlsx"
    module.DELIVERY_WORKBOOK = (
        module.DELIVERY / "产品推荐表_全量数据_补充SHEIN_含入库152键_20260711.xlsx")
    module.DELIVERY_JSONL = (
        module.DELIVERY / "产品推荐_补充SHEIN_入库152键_20260711.jsonl")
    module.DELIVERY_REPORT = (
        module.DELIVERY / "数据源充分性与SHEIN补充运行结论_20260711.md")
    return module


def enhance_gallery_mapping(module):
    base_builder = module.build_canonical_152

    def build_canonical_152(packet, result, records, canonical_spec):
        output = base_builder(packet, result, records, canonical_spec)
        gallery, evidence, _ = module.record_field(records, ["gallery_images"])
        if module.present(gallery):
            main_image = output["values"].get("cp.main_image")
            gallery = [url for url in gallery if url != main_image]
            if gallery:
                output["values"]["cp.gallery_images"] = gallery
                output["statuses"]["cp.gallery_images"] = "FILLED_DIRECT_SOURCE"
                output["evidence"]["cp.gallery_images"] = sorted(set(evidence))
                output["modes"]["cp.gallery_images"] = "direct"
                output["reasons"]["cp.gallery_images"] = "由来源媒体表按商品ID关联"
        filled = sum(module.present(value) for value in output["values"].values())
        output["summary"]["filled_count"] = filled
        output["summary"]["filled_pct"] = round(filled / len(output["values"]) * 100, 1)
        output["summary"]["status_counts"] = dict(Counter(output["statuses"].values()))
        return output

    module.build_canonical_152 = build_canonical_152


def write_report(module, deliveries, audit, run_meta):
    grade_dist = run_meta["grade_distribution"]
    selected_dist = Counter(item["grade"] for item in deliveries)
    filled_counts = [item["canonical_summary"]["filled_count"] for item in deliveries]
    status_totals = Counter()
    for item in deliveries:
        status_totals.update(item["canonical_status"].values())
    incremental = audit.get("incremental_source") or {}
    lines = [
        "# 补充 SHEIN 后的全量选品运行结论",
        "",
        "## 结论",
        "",
        "**SHEIN 已作为独立前端数据源补入，并与原全量数据一起重新完成分级及 Canonical 152 键交付。**",
        "",
        "本次没有覆盖 2026-07-10 原交付；新增运行复用了原 25 个数据源，并补入 SHEIN 美国站女装连衣裙商品与媒体数据。",
        "",
        "## SHEIN 接入情况",
        "",
        f"- SHEIN 商品记录：{incremental.get('products', 0):,} 条，全部进入 L1 评分。",
        f"- SHEIN 媒体记录：{incremental.get('media_records', 0):,} 条，按商品ID关联并去重保留。",
        "- 使用字段：价格、原价、折扣、销量下限、评分、评论数、畅销榜、趋势标签、评价标签、描述、颜色、尺码、主图和附图。",
        "- 销量保持来源下限口径，例如 `100+` 记为 `100`，不推断真实上限。",
        "",
        "## 重跑结果",
        "",
        f"- 规范化前有效候选记录：{audit['candidate_records_before_cross_source_merge']:,}。",
        f"- 跨来源合并后唯一候选：{run_meta['candidates_total']:,}。",
        f"- 等级分布：S={grade_dist.get('S', 0)}、A={grade_dist.get('A', 0)}、B={grade_dist.get('B', 0)}、C={grade_dist.get('C', 0)}。",
        f"- S/A/B 共 {len(deliveries):,} 款，其中 S/A 共 {selected_dist['S'] + selected_dist['A']:,} 款。",
        f"- L2/L3：{run_meta['l2_processed']} / {run_meta['l3_processed']} 款。",
        f"- 152 键：每个 S/A/B 产品固定输出 152 个唯一键；已填范围 {min(filled_counts)}–{max(filled_counts)}。",
        "",
        "## 使用边界",
        "",
        f"- 本轮数据源总数：{audit['source_count']}，其中前端工作簿 {audit['frontend_workbooks']} 个、插件源 {audit['plugin_sources']} 个。",
        "- SHEIN 与其他平台商品ID不跨平台强行合并，避免把不同平台的相似款误认成同一商品。",
        "- 供应链、合规和 SiteScope 缺失仍按状态保留，不用零值、均值或猜测补齐。",
        "",
        "## 交付文件",
        "",
        f"- `{module.DELIVERY_WORKBOOK.name}`：补充 SHEIN 后的推荐表、全量分级、152 键宽/长表和数据源审计。",
        f"- `{module.DELIVERY_JSONL.name}`：机器入库 JSONL。",
        "- `delivery_manifest.json`：交付文件 SHA-256。",
        "",
        "## 152 键状态统计",
        "",
    ]
    lines += [f"- `{status}`：{count:,}" for status, count in status_totals.most_common()]
    module.DELIVERY_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def refresh_explanation_sheet(module, audit, run_meta):
    workbook = module.load_workbook(module.DELIVERY_WORKBOOK)
    worksheet = workbook["交付说明"]
    rows = {
        worksheet.cell(row, 1).value: row
        for row in range(2, worksheet.max_row + 1)
    }
    worksheet.cell(rows["数据源"], 2).value = (
        f"{audit['source_count']} 个来源：SellerSprite、Kalodata、"
        f"{audit['frontend_workbooks']} 个前端站点工作簿（含本次补充 SHEIN）")
    worksheet.cell(rows["全量底稿"], 2).value = (
        f"工作簿中的‘全量分级底稿’保留全部 {run_meta['candidates_total']:,} 个候选，"
        "含 C 级淘汰依据。")
    workbook.save(module.DELIVERY_WORKBOOK)


def main():
    module = load_base_module()
    enhance_gallery_mapping(module)
    module.DELIVERY.mkdir(parents=True, exist_ok=True)
    canonical_spec, packets, results, deliveries = module.delivery_records()
    with module.DELIVERY_JSONL.open("w", encoding="utf-8") as handle:
        for item in deliveries:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    audit = json.loads((RUN_ROOT / "source_usage_audit.json").read_text(encoding="utf-8"))
    run_meta = json.loads((module.OUTPUT / "run_meta.json").read_text(encoding="utf-8"))
    module.build_workbook(canonical_spec, packets, results, deliveries, audit, run_meta)
    refresh_explanation_sheet(module, audit, run_meta)
    write_report(module, deliveries, audit, run_meta)
    spec_path = module.DELIVERY / "canonical_152_spec.json"
    spec_path.write_text(
        json.dumps(canonical_spec, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "generated_from_run": run_meta["run_id"],
        "canonical_key_count": len(canonical_spec),
        "delivery_products": len(deliveries),
        "shein_products_added": audit.get("incremental_source", {}).get("products"),
        "files": [],
    }
    for file_path in (
        module.DELIVERY_WORKBOOK,
        module.DELIVERY_JSONL,
        module.DELIVERY_REPORT,
        spec_path,
    ):
        manifest["files"].append({
            "path": file_path.name,
            "bytes": file_path.stat().st_size,
            "sha256": hashlib.sha256(file_path.read_bytes()).hexdigest(),
        })
    (module.DELIVERY / "delivery_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "delivery_products": len(deliveries),
        "canonical_keys": len(canonical_spec),
        "workbook": str(module.DELIVERY_WORKBOOK),
        "jsonl": str(module.DELIVERY_JSONL),
        "report": str(module.DELIVERY_REPORT),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
