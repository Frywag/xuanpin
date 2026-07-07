# 分级系统输入契约：SourceEnvelope 接入指南

> 读者：采集侧同事 / 采集 agent 的编写者。
> 回答的问题：**分级系统的输入格式是什么？** 以及未来「抓取大量数据源
> 回来直接投入 agent 驱动的分级系统」怎么落地。
> 结论先行：输入格式已定型为 `source.envelope.v1`（JSON Schema：
> `schemas/source_envelope.schema.json`），并且已支持**免代码直投**——
> 按本契约产出 JSON 文件，一条命令校验、一条命令进管道出分级结果。

---

## 1. 两种接入方式

| 方式 | 适用 | 成本 |
|---|---|---|
| ① 标准信封直投（推荐） | 任何新数据源；采集 agent 批量产出 | **零代码**：产出符合本契约的 JSON → `validate-envelope` 自检 → `run --envelopes` |
| ② 写 Adapter | 数据保持原始形态（如工具导出的 XLSX），由分级系统负责解析 | 一个 Python 类（参考 `adapters/kalodata.py`，约 150 行）+ 注册 `sources_p0.yaml` |

方式①是面向未来的主路径：采集与分级解耦，采集 agent 只需要遵守契约。

## 2. 信封结构（source.envelope.v1）

一个信封 = 一个数据源一次采集的全部记录。单文件 `.json` 一个信封，
或 `.jsonl` 每行一个信封（大批量推荐）。

```json
{
  "schema_version": "source.envelope.v1",
  "envelope_id": "envelope_myspider_shein_20260707",
  "source_id": "myspider_shein",
  "source_type": "frontend",            // plugin | frontend | trend | owned
  "market": "US",                       // 必须与本轮范围一致，混市场整封拒收
  "collection_mode": "full_then_analyze",
  "layer_hint": "L1",
  "collected_at": "2026-07-07T08:00:00+00:00",
  "collector_version": "myspider/1.2.0",
  "quality_status": "VALID",            // VALID | PARTIAL_SOURCE_MISSING | PARSE_FAILED | ACCESS_BLOCKED
  "artifact_paths": {"raw": "oss://bucket/raw/myspider/20260707/"},
  "errors": [], "warnings": [],
  "records": [ ...见 §3... ]
}
```

## 3. 记录结构（record）

每条商品记录三要素：**标识**、**fields（事实字段）**、**evidence（逐字段证据）**。

```json
{
  "record_kind": "product",
  "product_id": "1731406217255686451",   // 平台稳定 ID；没有则必须给 fields.canonical_url
  "platform": "tiktok_shop",             // plugin 记录可选；同 platform+product_id 自动跨源合并
  "site": "shein",                        // frontend 记录：站点名（每行独立成候选）
  "category_label": "women_dresses",      // 可选：品类组标签（价格带分组用）
  "seasonal": false,                      // 可选：强季节品类标记（触发风险规则）
  "track_hint": "evergreen",              // 可选：三赛道采集立项提示
  "quality_status": "VALID",              // 行级质量，可缺省
  "fields": {
    "title": "OEAK Women Jelly Bras ...",
    "canonical_url": "https://...",
    "unit_price": {"amount": 108.51, "currency": "CNY"},
    "rating": 4.4,
    "review_count": 68609,
    "sales_30d_units": 86499,
    "sales_growth_rate": 0.5547
  },
  "evidence": {
    "title":        {"evidence_id": "ev_myspider_000001", "source_id": "myspider_shein",
                      "source_type": "frontend", "tool_or_adapter": "myspider",
                      "field_path": "title",
                      "artifact_path": "oss://bucket/raw/.../page_123.json",
                      "record_locator": "page=123;item=4", "confidence": "high", "note": ""},
    "unit_price":   {"evidence_id": "ev_myspider_000002", "...": "同上结构"}
  }
}
```

### 3.1 fields 标准字段词表

分级引擎按下表识别字段并路由到候选包；词表外的字段会被忽略
（不报错，但也不参与打分）。缺失的字段**直接不写**，禁止填 0/空串。

**基础事实**

| 字段 | 类型 | 说明 |
|---|---|---|
| `title` / `brand` / `category_path` / `image_url` / `canonical_url` | str / str / [str] / url / url | 标题、品牌、类目路径、主图、商品链接 |
| `unit_price` / `original_price` | Money `{amount, currency}` | **无币种拒收** |
| `discount_pct` | float 小数 | -0.27 表示 -27% |
| `rating` / `review_count` | float / int | 评分、评论数 |
| `in_stock` | str | in_stock / sold_out / unknown |

**需求信号**（至少给一个，否则 L1 直接 C + 补采清单）

| 字段 | 类型 | 口径要求 |
|---|---|---|
| `sales_30d_units` / `sales_7d_units` | int | 30/7 天销量（估算需在 evidence.note 注明） |
| `sales_floor_units` | int | 下限口径（如页面 "800+" 记 800） |
| `revenue_30d` / `gmv_7d` | Money | 销售额/GMV |
| `sales_growth_rate` | float 小数 | 环比增长率 |
| `favorites_count` | int | 收藏/心愿单（热度信号，封顶计分） |
| `bestseller_rank_text` | str | 畅销榜原文 |
| `trend_tags` | [str] | 平台趋势标签 |

**竞争/供应链信号（可选）**：`commission_rate`、`seller_type`、
`creator_gmv_concentration`、`category_top3_shop_ratio`、
`category_top10_shop_ratio`、`category_shop_count`、`shipping_fee`(Money)、
`video_views_max`、`video_revenue_share`、`related_creator_count`、
`review_tags_raw`（`"Tag (n)Tag2 (m)"` 格式，L2 聚类用）、
`image_count`、`design_signals`。

类目级数据用 `record_kind: "category_context"` 单独一条记录承载
（字段名同上竞争信号，参考 `adapters/kalodata.py` 输出）。

### 3.2 证据规则（硬性）

1. 每个事实字段都应有同名 evidence 条目；无证据的字段进不了推荐理由
   （校验器给 W 级警告，分数照算但 claim 生成会跳过）；
2. `artifact_path` 必须指向可回看的原始产物（对象存储 URI / 仓库路径），
   `record_locator` 精确到行/条目——这是运营验收抽查的入口；
3. 估算类数值（销量估算、趋势推断）在 `note` 注明口径，`confidence`
   用 medium/low；
4. 载荷中不得出现 token/cookie/Auth-Token/敏感 header。

## 4. 接入流程（采集 agent 三步走）

```bash
# ① 采集 agent 产出信封文件到任意目录
drops/myspider_20260707/envelope_shein.json

# ② 交付前自检（E 级错误必须清零；W 级警告尽量处理）
PYTHONPATH="04.分级系统/src" python3 -m grading_system.cli \
    validate-envelope drops/myspider_20260707/

# ③ 直投分级管道（可与仓库既有数据源共同参与打分/合并）
PYTHONPATH="04.分级系统/src" python3 -m grading_system.cli run \
    --repo-root . --out "04.分级系统/data/analysis_runs/run_$(date +%Y%m%d)_001" \
    --envelopes drops/myspider_20260707/
```

长期接入可写进配置（`configs/sources_p0.yaml`）：

```yaml
envelope_inputs:
  - path: drops/myspider_20260707/     # 相对仓库根目录
```

产出：XLSX 推荐表 + SQLite 选品库 + LLM 任务包（deep_review/run_report），
下游用 MCP 或 SQL 消费（见 docs/04）。

## 5. 校验行为一览

| 级别 | 情形 | 处理 |
|---|---|---|
| E（整封拒收） | 缺头字段 / market 不符 / 金额无币种 / record 无 product_id 且无 canonical_url / evidence 缺 artifact_path 等键 | 写入 problems，该信封不进管道 |
| W（收但降权） | 字段无 evidence / quality_status 非标准枚举 | 记警告；无证据字段不进推荐理由 |
| 行级 | quality_status=ACCESS_BLOCKED 的行 | 照常入库，候选降置信度 + 风险扣分 |

## 6. 版本与演进

- 当前版本 `source.envelope.v1`；新增字段向后兼容（引擎忽略未知字段），
  改字段语义/类型必须升 v2 并保留 v1 解析一个过渡期；
- 字段词表扩充流程：先在 `packet_builder._FIELD_ROUTES` 注册路由 +
  `scoring_v0.yaml` 定分档（带数据依据注释）→ 跑测试 → 更新本契约表格。
