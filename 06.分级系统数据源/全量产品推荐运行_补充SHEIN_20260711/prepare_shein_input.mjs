import fs from "node:fs/promises";
import crypto from "node:crypto";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const repoRoot = "/Users/frywag/Desktop/选品";
const runRoot = path.join(repoRoot, "06.分级系统数据源/全量产品推荐运行_补充SHEIN_20260711");
const sourceWorkbook = path.join(repoRoot, "03.前端数据源/shein数据源.xlsx");
const originalAuditPath = path.join(
  repoRoot,
  "06.分级系统数据源/全量产品推荐运行_20260710/source_usage_audit.json",
);
const normalizedDir = path.join(runRoot, "normalized_inputs");
const normalizedPath = path.join(normalizedDir, "frontend_shein.json");
const collectedAt = new Date().toISOString();

function present(value) {
  return value !== null && value !== undefined && value !== "";
}

function parseNumber(value) {
  if (!present(value) || typeof value === "boolean") return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const match = String(value).replaceAll(",", "").match(/-?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : null;
}

function parsePercent(value) {
  const parsed = parseNumber(value);
  if (parsed === null) return null;
  return String(value).includes("%") ? parsed / 100 : parsed;
}

function parseSalesFloor(value) {
  return parseNumber(value);
}

function parseTrendTags(value) {
  if (!present(value)) return null;
  const tags = String(value)
    .split(/[;,]/)
    .map((item) => item.trim().replace(/^#/, ""))
    .filter(Boolean);
  return tags.length ? tags : null;
}

function relativeToRepo(filePath) {
  return path.relative(repoRoot, filePath);
}

function evidenceId(sourceId, sheet, row, field, column) {
  const digest = crypto
    .createHash("sha1")
    .update(`${sourceId}|${sheet}|${row}|${field}|${column}`)
    .digest("hex")
    .slice(0, 16);
  return `ev_${digest}`;
}

function makeEvidence(sourceId, sheet, row, field, column, note = "") {
  return {
    evidence_id: evidenceId(sourceId, sheet, row, field, column),
    source_id: sourceId,
    source_type: "frontend",
    tool_or_adapter: "shein_workbook_normalizer/1.0",
    field_path: field,
    artifact_path: relativeToRepo(sourceWorkbook),
    record_locator: `sheet=${sheet};row=${row};column=${column}`,
    observed_at: collectedAt,
    confidence: "high",
    note,
  };
}

function addField(record, sheet, row, field, value, column, note = "") {
  if (!present(value) || (Array.isArray(value) && value.length === 0)) return;
  record.fields[field] = value;
  record.evidence[field] = makeEvidence("frontend_shein", sheet, row, field, column, note);
}

function rowsFromValues(values) {
  const headers = values[0].map((value) => String(value ?? "").trim());
  return values.slice(1).map((valuesRow, index) => ({
    rowNumber: index + 2,
    values: Object.fromEntries(headers.map((header, column) => [header, valuesRow[column] ?? null])),
  }));
}

await fs.mkdir(normalizedDir, { recursive: true });
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(sourceWorkbook));
const productSheet = workbook.worksheets.getItem("shein");
const mediaSheet = workbook.worksheets.getItem("media");
const summarySheet = workbook.worksheets.getItem("run_summary");
const productValues = productSheet.getUsedRange(true).values;
const mediaValues = mediaSheet.getUsedRange(true).values;
const summaryValues = summarySheet.getUsedRange(true).values;

const mediaByProduct = new Map();
for (const { rowNumber, values } of rowsFromValues(mediaValues)) {
  const productId = String(values["商品ID"] ?? "").trim();
  const imageUrl = String(values["图片URL"] ?? "").trim();
  if (!productId || !imageUrl) continue;
  const current = mediaByProduct.get(productId) ?? [];
  current.push({
    order: parseNumber(values["顺序"]),
    url: imageUrl,
    alt: present(values["Alt文本"]) ? String(values["Alt文本"]) : null,
    row: rowNumber,
  });
  mediaByProduct.set(productId, current);
}

const records = [];
let skipped = 0;
for (const { rowNumber, values } of rowsFromValues(productValues)) {
  const productId = String(values["商品ID"] ?? "").trim();
  const canonicalUrl = String(values["来源URL"] ?? "").trim();
  if (!productId && !canonicalUrl) {
    skipped += 1;
    continue;
  }
  const record = {
    record_kind: "product",
    platform: "shein",
    site: "shein",
    product_id: productId || `url_${crypto.createHash("sha1").update(canonicalUrl).digest("hex").slice(0, 16)}`,
    id_truncated: false,
    category_label: "women_dresses_shein",
    seasonal: false,
    quality_status: String(values["质量状态"] ?? "OK").toUpperCase() === "OK" ? "VALID" : "PARTIAL_SOURCE_MISSING",
    fields: {},
    evidence: {},
  };

  const price = parseNumber(values["销售价"]);
  const originalPrice = parseNumber(values["原价"]);
  const currency = String(values["币种"] ?? "").trim().toUpperCase();
  const media = (mediaByProduct.get(productId) ?? []).sort(
    (left, right) => (left.order ?? 0) - (right.order ?? 0),
  );
  const galleryImages = [...new Set(media.map((item) => item.url))];
  const description = values["商品描述（原文）"];
  const details = values["详情字段（原文）"];
  const color = values["颜色"];
  const size = values["尺码表/尺码信息"];

  addField(record, "shein", rowNumber, "title", values["商品名称（原文）"], "商品名称（原文）");
  addField(record, "shein", rowNumber, "title_cn", values["商品名称（中文）"], "商品名称（中文）");
  addField(record, "shein", rowNumber, "category_name", "Women Dresses", "站点", "由 SHEIN 美国站女装连衣裙类目确定");
  addField(record, "shein", rowNumber, "canonical_url", canonicalUrl, "来源URL");
  addField(record, "shein", rowNumber, "image_url", values["高清主图链接"], "高清主图链接");
  if (price !== null && currency) {
    addField(record, "shein", rowNumber, "unit_price", { amount: price, currency }, "销售价/币种");
  }
  if (originalPrice !== null && currency) {
    addField(record, "shein", rowNumber, "original_price", { amount: originalPrice, currency }, "原价/币种");
  }
  addField(record, "shein", rowNumber, "discount_pct", parsePercent(values["折扣"]), "折扣");
  addField(record, "shein", rowNumber, "rating", parseNumber(values["评分"]), "评分");
  addField(record, "shein", rowNumber, "review_count", parseNumber(values["评价数"]), "评价数");
  addField(
    record,
    "shein",
    rowNumber,
    "sales_floor_units",
    parseSalesFloor(values["销量"]),
    "销量",
    "SHEIN 销量为下限口径，例如 100+ 记为 100",
  );
  addField(record, "shein", rowNumber, "bestseller_rank_text", values["畅销榜排名"], "畅销榜排名");
  addField(record, "shein", rowNumber, "trend_tags", parseTrendTags(values["趋势标签（原文）"]), "趋势标签（原文）");
  addField(record, "shein", rowNumber, "review_tags_raw", values["评价标签（原文）"], "评价标签（原文）");
  addField(record, "shein", rowNumber, "description_text", description, "商品描述（原文）");
  addField(record, "shein", rowNumber, "description_text_cn", values["商品描述（中文）"], "商品描述（中文）");
  addField(record, "shein", rowNumber, "product_details_text", details, "详情字段（原文）");
  addField(record, "shein", rowNumber, "product_details_text_cn", values["详情字段（中文）"], "详情字段（中文）");
  addField(record, "shein", rowNumber, "color_text", color, "颜色");
  addField(record, "shein", rowNumber, "size_text", size, "尺码表/尺码信息");
  addField(record, "shein", rowNumber, "seller_store", values["店铺（原文）"], "店铺（原文）");
  addField(record, "shein", rowNumber, "gallery_images", galleryImages, "商品ID", "由 media 表按商品ID关联");
  addField(record, "shein", rowNumber, "image_count", galleryImages.length || null, "商品ID", "由 media 表按商品ID去重计数");
  addField(
    record,
    "shein",
    rowNumber,
    "raw_evidence_path",
    relativeToRepo(sourceWorkbook),
    "原始证据路径",
    "工作簿内原始证据路径另保留在 source_raw_evidence_path",
  );
  addField(record, "shein", rowNumber, "source_raw_evidence_path", values["原始证据路径"], "原始证据路径");
  const designSignals = [
    present(description) ? { kind: "description", text: String(description) } : null,
    present(details) ? { kind: "details", text: String(details) } : null,
    present(color) ? { kind: "color", text: String(color) } : null,
    present(size) ? { kind: "size", text: String(size) } : null,
  ].filter(Boolean);
  addField(record, "shein", rowNumber, "design_signals", designSignals, "商品描述/详情字段/颜色/尺码");
  records.push(record);
}

const envelope = {
  schema_version: "source.envelope.v1",
  envelope_id: "envelope_frontend_shein_20260711",
  source_id: "frontend_shein",
  source_type: "frontend",
  market: "US",
  collection_mode: "full_then_analyze",
  layer_hint: "L1",
  collected_at: collectedAt,
  collector_version: "shein_workbook_normalizer/1.0",
  quality_status: records.some((record) => record.quality_status !== "VALID")
    ? "PARTIAL_SOURCE_MISSING"
    : "VALID",
  records,
  errors: [],
  warnings: ["销量采用来源下限口径；媒体表按商品ID关联并去重。"],
  artifact_paths: { workbook: relativeToRepo(sourceWorkbook) },
};
await fs.writeFile(normalizedPath, `${JSON.stringify(envelope, null, 2)}\n`, "utf8");

const originalAudit = JSON.parse(await fs.readFile(originalAuditPath, "utf8"));
const sourceAudit = {
  source_id: "frontend_shein",
  source_type: "frontend",
  artifact_path: relativeToRepo(sourceWorkbook),
  candidate_records: records.length,
  partial_records: records.filter((record) => record.quality_status !== "VALID").length,
  skipped_records: skipped,
  sheet_rows_scanned: {
    run_summary: Math.max(summaryValues.length - 1, 0),
    shein: Math.max(productValues.length - 1, 0),
    media: Math.max(mediaValues.length - 1, 0),
  },
  media_records: Math.max(mediaValues.length - 1, 0),
  usage: "SHEIN 商品行全部进入评分；价格、销量下限、评分、评论、榜单、趋势、描述、颜色、尺码和媒体均保留字段级证据。",
  warnings: envelope.warnings,
};
const audit = {
  ...originalAudit,
  generated_at: collectedAt,
  sources: [...originalAudit.sources, sourceAudit],
  source_count: originalAudit.source_count + 1,
  candidate_records_before_cross_source_merge:
    originalAudit.candidate_records_before_cross_source_merge + records.length,
  frontend_workbooks: originalAudit.frontend_workbooks + 1,
  incremental_source: {
    source_id: "frontend_shein",
    products: records.length,
    media_records: sourceAudit.media_records,
  },
};
await fs.writeFile(
  path.join(runRoot, "source_usage_audit.json"),
  `${JSON.stringify(audit, null, 2)}\n`,
  "utf8",
);

process.stdout.write(
  `${JSON.stringify({
    normalized: normalizedPath,
    shein_products: records.length,
    media_records: sourceAudit.media_records,
    skipped,
    candidate_records_before_merge: audit.candidate_records_before_cross_source_merge,
    source_count: audit.source_count,
  }, null, 2)}\n`,
);
