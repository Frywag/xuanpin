# 员工 B2 任务书：其他插件 MCP 工具接口与字段采集

---

## 1. 你的定位

你负责“非 SellerSprite 插件”的工具接口设计、endpoint 发现、字段采集、字段映射和 normalizer。

重要前提：

1. SellerSprite 已经实现，你不要重做 SellerSprite。
2. 你要开发 Helium10、Jungle Scout、Kalodata、FastMoss、EchoTik 等其他插件或工具的数据能力。
3. B1 负责会话授权和 HTTP 服务，你不要直接读取 token/cookie。
4. 你兼任测试、回放和验收，因此必须为每个 tool 同步提供稳定的 tool spec、fixture 和 SourceEnvelope 样例。
5. 你的输出必须能进入分层分析系统，而不是只保存 raw JSON。

你的核心交付：

```text
目标插件业务能力拆解
→ endpoint family / 页面请求发现
→ MCP tool 设计
→ raw 字段采集
→ normalizer
→ SourceEnvelope
→ EvidenceRef 所需字段
→ CandidateDataPacket 可消费字段
```

---

## 2. 目标插件范围与优先级

本轮插件采集不再做 SellerSprite。目标是业务逻辑文档里列出的其他插件。

| 优先级 | 插件/工具 | 平台 | 你要采什么 | 服务层级 |
|---|---|---|---|---|
| P0-A | Helium10 | Amazon | Black Box、Magnet/Cerebro、Xray、Review Insights | L1/L2 |
| P0-B | Jungle Scout | Amazon | Product Database、Opportunity Finder、Keyword Scout、Seasonality | L1/L2 |
| P0-C | Kalodata | TikTok Shop | 商品榜、达人榜、直播分析、商品详情 | L1/L2/L3 |
| P0-D | FastMoss | TikTok Shop | 趋势品、爆款视频、达人效率、商品详情 | L1/L2 |
| P0-E | EchoTik | TikTok Shop | 潜力品、视频叠加指标、商品详情、趋势标签 | L1/L2 |
| P1 | TikTok Creative Center | TikTok 内容侧 | 热门标签、热门视频、热门音乐、趋势词 | L1 趋势信号 |

主线程未指定插件时，建议先做：

1. Amazon 第二工具源：Helium10 或 Jungle Scout。
2. TikTok Shop 工具源：Kalodata / FastMoss / EchoTik 选一个。

这样能同时解决两个问题：Amazon 单源口径校验、TikTok Shop 趋势数据缺口。

---

## 3. 已完成基线：SellerSprite 工具设计可复用逻辑

SellerSprite 已完成，不是你的目标。但它证明了一个插件工具应该怎样设计。

### 3.1 基线工具边界

SellerSprite 已验证的工具边界是按业务能力划分：

| Tool | endpoint family | 业务用途 |
|---|---|---|
| `asin_quick_view` | ASIN quick view | 30 天销量、销售额、seller、BSR、variation、LQS、trend |
| `asin_competitor_lookup` | competitor lookup | title、image、price、rating、reviews、category path、badges |
| `asin_trends` | sales / BSR / price / review trend | L2/L3 趋势验证 |
| `asin_profile` | ASIN profile | images、bullets、variation、A+/video flags |

你给其他插件设计工具时，也必须按业务能力划分。禁止拆成：

```text
get_price
get_rating
get_reviews
get_image
get_title
```

这种单字段工具会导致调用次数爆炸、上下文碎片化、后续整合困难。

### 3.2 基线源码片段：批量 quick view

```python
def collect_quick_view(asins, token, marketplace, batch_size, sleep):
    result = {}
    for batch in chunks(asins, batch_size):
        payload = request_json(
            f"/v2/extension/competitor-lookup/quick-view/{marketplace}",
            token,
            {
                "asins": ",".join(batch),
                "source": "BEST_SELLER",
                "miniMode": "false",
                "withRelation": "true",
                "withSaleTrend": "true",
            },
        )
        if payload.get("code") != "OK":
            raise SystemExit(f"quick-view failed: {payload.get('code')} {payload.get('message')}")
        for item in (payload.get("data") or {}).get("items") or []:
            if item.get("asin"):
                result[item["asin"]] = item
        time.sleep(sleep)
    return result
```

可迁移思想：

1. 输入是一批业务对象，不是一个字段。
2. batch size 可配置。
3. sleep/rate limit 可配置。
4. 响应必须检查业务 code。
5. 每个 item 用稳定 ID 做 key。
6. 返回 raw dict 给 normalizer。

### 3.3 基线源码片段：聚合 summary

```python
def aggregate(records, key, units_key, amount_key):
    grouped = defaultdict(lambda: {"count": 0, "units": 0, "amount": 0.0})
    for record in records:
        label = record.get(key) or "Unknown"
        grouped[label]["count"] += 1
        grouped[label]["units"] += int(record.get(units_key) or 0)
        grouped[label]["amount"] += float(record.get(amount_key) or 0)
    rows = [{"name": k, **v, "amount": round(v["amount"], 2)} for k, v in grouped.items()]
    return sorted(rows, key=lambda row: row["units"], reverse=True)

def price_bands(records):
    bands = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 25), (25, 30), (30, 40), (40, None)]
    grouped = {f"{lo}-{hi}" if hi else f"{lo}+": {"count": 0, "units": 0, "amount": 0.0} for lo, hi in bands}
    for record in records:
        price = record.get("price")
        if not isinstance(price, (int, float)):
            continue
        for lo, hi in bands:
            if price >= lo and (hi is None or price < hi):
                label = f"{lo}-{hi}" if hi else f"{lo}+"
                grouped[label]["count"] += 1
                grouped[label]["units"] += int(record.get("units") or 0)
                grouped[label]["amount"] += float(record.get("amount") or 0)
                break
    return [{"band": k, **v, "amount": round(v["amount"], 2)} for k, v in grouped.items()]
```

可迁移思想：

1. 插件 raw 字段不等于最终字段，要经过 normalizer 和 summary。
2. brand concentration、seller concentration、price band 是 L1 很有用的聚合字段。
3. 聚合过程必须保留 missing_fields。
4. 估算销量/GMV是工具估算，不是平台官方事实，下游要保留 source_id。

---

## 4. 你要设计的 ToolSpec

每个 MCP tool 必须写成结构化规格。

```json
{
  "tool_name": "helium10_product_xray",
  "source_id": "helium10",
  "business_capability": "Amazon ASIN market snapshot and competition metrics",
  "layer": ["L1"],
  "input_schema": {
    "marketplace": "US",
    "asins": ["B0XXXX"],
    "category_url": null,
    "keyword": null,
    "limit": 100
  },
  "output_schema": {
    "records": [],
    "summary": {},
    "missing_fields": [],
    "artifact_paths": {}
  },
  "rate_limit": {
    "batch_size": 20,
    "sleep_seconds": 1.0,
    "max_retries": 1
  },
  "failure_policy": {
    "401": "SESSION_EXPIRED",
    "403": "ACCESS_BLOCKED",
    "429": "RATE_LIMITED",
    "captcha": "ACCESS_CHALLENGE"
  },
  "downstream_use": ["market_demand", "competition", "price_band", "L1_gate"]
}
```

ToolSpec 必须回答：

1. 这个工具解决什么业务问题。
2. 输入是什么。
3. 输出字段是什么。
4. 哪些字段是已验证可得。
5. 哪些字段是推测可得。
6. 字段服务 L1/L2/L3 哪一层。
7. 调用成本如何控制。
8. 失败怎么处理。
9. 如何生成 SourceEnvelope。

---

## 5. Endpoint 发现流程

你可以通过浏览器开发者工具、已授权页面、插件请求日志、XHR/fetch 观察来发现 endpoint，但必须遵守只读和脱敏原则。

### 5.1 发现步骤

```text
1. 人工登录目标插件或工具。
2. 打开目标业务页面，如 Product Database / Trend Products / Creator Rank。
3. 开启 Network，只观察 XHR/fetch。
4. 进行一次低风险查询。
5. 记录 endpoint family、method、query/body 参数、分页方式、排序方式。
6. 记录响应字段，不保存敏感 headers。
7. 将敏感 headers 替换为 [REDACTED]。
8. 判断该 endpoint 对应哪个业务能力。
9. 写入 ToolSpec。
10. 用 B1 AuthenticatedClient 实现工具调用。
```

### 5.2 Endpoint 记录模板

```markdown
## Endpoint Family: <name>

- source_id:
- tool_name:
- business_capability:
- method:
- url_pattern:
- auth_mode: cookie / header / extension_storage / unknown
- pagination:
- sort:
- filters:
- request_params:
- request_body:
- success_signal:
- expired_signal:
- rate_limit_observed:
- fields_observed:
- downstream_layer:
- notes:
```

### 5.3 禁止记录的内容

1. 完整 cookie。
2. Authorization header。
3. Auth-Token。
4. CSRF token。
5. 浏览器 profile 本机路径。
6. 账号邮箱、手机号等不必要个人信息。

如果必须说明请求头，写成：

```json
{
  "Cookie": "[REDACTED]",
  "Authorization": "[REDACTED]",
  "User-Agent": "Mozilla/5.0",
  "Content-Type": "application/json"
}
```

---

## 6. Helium10 工具设计

### 6.1 `helium10_keyword_research`

用途：提供 Amazon 关键词需求、相关词、竞争难度，用于 L1 市场需求和趋势候选。

输入：

```json
{
  "marketplace": "US",
  "keyword": "summer dress",
  "limit": 100,
  "include_related": true
}
```

输出字段：

| 字段 | 说明 | 下游 |
|---|---|---|
| `keyword` | 关键词 | CandidateDataPacket.market_metrics |
| `search_volume` | 搜索量估算 | L1 市场需求 |
| `trend` | 趋势变化 | L1 趋势 |
| `competition_level` | 竞争难度 | L1 竞争 |
| `related_keywords` | 相关词 | 趋势词扩展 |
| `source_metric_name` | 原始指标名称 | 证据追溯 |

### 6.2 `helium10_product_xray`

用途：对 ASIN 或列表页商品做基础市场快照。

输入：

```json
{
  "marketplace": "US",
  "asins": ["B0XXXX", "B0YYYY"],
  "category_url": null,
  "limit": 100
}
```

输出字段：

| 字段 | 说明 | 下游 |
|---|---|---|
| `asin` | 商品 ID | candidate_id |
| `title` | 标题 | basic_facts |
| `brand` | 品牌 | competition |
| `price` | 价格 | price_band |
| `currency` | 币种 | price_band |
| `estimated_monthly_sales` | 月销量估算 | market_demand |
| `estimated_monthly_revenue` | 月销售额估算 | market_demand |
| `review_count` | 评论数 | competition |
| `rating` | 评分 | competition |
| `bsr` | 排名 | demand |

### 6.3 `helium10_review_insights`

用途：只对 L2 入围 ASIN 做差评主题，不对全量候选调用。

输入：

```json
{
  "marketplace": "US",
  "asins": ["B0XXXX"],
  "rating_filter": [1, 2, 3],
  "limit_reviews_per_asin": 100
}
```

输出字段：

| 字段 | 说明 | 下游 |
|---|---|---|
| `review_id` | 评论 ID | evidence |
| `rating` | 星级 | review clustering |
| `review_text` | 评论正文 | L2 痛点 |
| `topic` | 工具或模型识别主题 | improvement |
| `variant` | 颜色/尺码 | 152 键 |
| `date` | 时间 | 时效 |

---

## 7. Jungle Scout 工具设计

### 7.1 `junglescout_product_database`

用途：按类目和筛选条件获得商品池，作为 SellerSprite 之外的 Amazon 市场容量校验。

输入：

```json
{
  "marketplace": "US",
  "category": "Women Dresses",
  "filters": {
    "price_min": 10,
    "price_max": 60,
    "reviews_max": 2000,
    "monthly_sales_min": 100
  },
  "limit": 200
}
```

输出字段：

1. product_id / ASIN。
2. title。
3. brand。
4. price。
5. monthly_sales_estimate。
6. monthly_revenue_estimate。
7. review_count。
8. rating。
9. seller_count。
10. category。

### 7.2 `junglescout_opportunity_finder`

用途：输出机会评分、需求与竞争概览。

输出字段：

| 字段 | 说明 |
|---|---|
| `opportunity_score` | 工具原生机会评分 |
| `demand_score` | 需求 |
| `competition_score` | 竞争 |
| `seasonality` | 季节性 |
| `top_keywords` | 关键词 |
| `example_products` | 样例商品 |

注意：工具自带 opportunity score 不能直接等于本项目 PScore，只能作为 PScore 的一个证据项。

---

## 8. TikTok Shop 工具设计

### 8.1 Kalodata

`kalodata_product_rank`：

```json
{
  "country": "US",
  "category": "Womenswear",
  "date_range": "last_30_days",
  "sort": "gmv",
  "limit": 100
}
```

输出字段：

| 字段 | 下游用途 |
|---|---|
| `product_id` | candidate_id |
| `product_title` | basic_facts |
| `shop_id` / `shop_name` | competition |
| `price` / `currency` | price_band |
| `units_sold` | market_demand |
| `gmv` | market_demand |
| `aov` | price_band |
| `creator_count` | content distribution |
| `video_count` | trend signal |
| `live_count` | live commerce |
| `growth_rate` | trend |

`kalodata_live_analysis` 只给 L2/L3 使用，不对全量候选调用。

### 8.2 FastMoss

`fastmoss_trending_products` 输出字段：

1. product_id。
2. product_title。
3. category。
4. price。
5. units_sold。
6. gmv。
7. growth_rate。
8. related_video_count。
9. top_creator。
10. sales_efficiency。

`fastmoss_viral_videos` 输出字段：

1. video_id。
2. video_url。
3. creator_id。
4. product_id。
5. views。
6. likes。
7. comments。
8. shares。
9. estimated_gmv_per_1k_views。
10. hook_text / caption / tags。

### 8.3 EchoTik

`echotik_potential_products` 输出字段：

1. product_id。
2. product_title。
3. potential_score。
4. early_growth_rate。
5. estimated_revenue。
6. creator_count。
7. video_count。
8. category。
9. price。
10. country。

`echotik_video_overlay_metrics` 用于 L2 内容验证，不用于 L1 全量。

---

## 9. Normalizer 规则

插件 raw 字段必须标准化为 SourceEnvelope records。不要让分析系统直接读插件 raw JSON。

### 9.1 通用商品记录

```json
{
  "record_type": "product_metric",
  "source_id": "fastmoss",
  "source_product_id": "123456",
  "canonical_url": null,
  "title": "Women's Summer Dress",
  "brand": null,
  "shop_name": "Example Shop",
  "category_path": ["Womenswear", "Dresses"],
  "market": "US",
  "price": "29.99",
  "currency": "USD",
  "estimated_units": 1200,
  "estimated_revenue": "36000.00",
  "rating": null,
  "review_count": null,
  "trend_signals": [
    {
      "type": "growth_rate",
      "value": "0.35",
      "period": "last_30_days"
    }
  ],
  "raw_ref": "raw://mcp_sources/fastmoss/run_001/trending_products.json",
  "evidence_refs": []
}
```

### 9.2 通用关键词记录

```json
{
  "record_type": "keyword_metric",
  "source_id": "helium10",
  "keyword": "summer dress",
  "market": "US",
  "search_volume": 50000,
  "competition_level": "medium",
  "trend": "up",
  "related_keywords": [],
  "raw_ref": "raw://mcp_sources/helium10/run_001/keyword_research.json"
}
```

### 9.3 通用内容/达人记录

```json
{
  "record_type": "content_metric",
  "source_id": "kalodata",
  "platform": "tiktok_shop",
  "video_id": "7330000000",
  "creator_id": "creator_001",
  "product_id": "product_001",
  "views": 1000000,
  "likes": 50000,
  "comments": 1200,
  "shares": 3000,
  "estimated_gmv": "12000.00",
  "currency": "USD",
  "raw_ref": "raw://mcp_sources/kalodata/run_001/video_metrics.json"
}
```

### 9.4 缺字段规则

1. 没有字段写入 `missing_fields`。
2. 数值不确定写入 `field_issues`。
3. 工具估算口径必须写 `metric_source_label`。
4. 价格没有币种则价格字段无效。
5. Google Trends / TikTok Creative Center 指数不能写成销量。

---

## 10. SourceEnvelope 生成

每个工具必须输出：

```json
{
  "run_id": "run_20260626_001",
  "source_id": "kalodata",
  "source_type": "plugin_mcp",
  "tool_name": "kalodata_product_rank",
  "retrieved_at": "2026-06-26T10:00:00+08:00",
  "query": {
    "marketplace": "US",
    "category": "Womenswear",
    "date_range": "last_30_days",
    "limit": 100
  },
  "records": [],
  "summary": {
    "sample_size": 100,
    "top_products": [],
    "price_bands": [],
    "creator_concentration": [],
    "missing_fields": {}
  },
  "quality": {
    "status": "VALID",
    "missing_fields": [],
    "warnings": []
  },
  "artifact_paths": {
    "raw": "data/raw/mcp_sources/kalodata/run_20260626_001/product_rank.json",
    "normalized": "data/normalized/mcp_sources/kalodata/run_20260626_001/product_rank.normalized.json",
    "meta": "data/raw/mcp_sources/kalodata/run_20260626_001/collection-meta.json"
  },
  "meta": {
    "adapter_version": "0.1.0",
    "schema_version": "source.envelope.v1",
    "credential_redacted": true
  }
}
```

质量状态：

| 状态 | 含义 |
|---|---|
| `VALID` | 可进入分析 |
| `PARTIAL_SOURCE_MISSING` | 可进入分析，但需要降低置信度 |
| `PARSE_FAILED` | 不进入分析 |
| `ACCESS_BLOCKED` | 访问受阻，需要 B1 处理 |
| `RATE_LIMITED` | 限流，需要等待 |
| `OUT_OF_SCOPE` | 市场、类目或平台不符合 |

---

## 11. 你与 B1 的接口

你不直接处理 token/cookie。你通过 B1 提供的 AuthenticatedClient 调用。

```python
def call_endpoint(client, source_id, tool_name, method, url, params, body, run_id):
    response = client.request(
        source_id=source_id,
        method=method,
        url=url,
        params=params,
        json_body=body,
        timeout=30,
        run_id=run_id,
    )
    return response
```

你要提供给 B1：

1. source_id。
2. endpoint family。
3. method。
4. query params。
5. request body。
6. success signal。
7. expired signal。
8. access blocked signal。

B1 提供给你：

1. 已注入凭证的 HTTP 请求能力。
2. session status。
3. rate limit 状态。
4. 脱敏错误。

---

## 12. 你的验收接口

你必须为测试回放提供：

1. ToolSpec。
2. raw fixture 样例。
3. normalized fixture 样例。
4. SourceEnvelope 样例。
5. missing_fields 样例。
6. error 样例。
7. 字段映射表。

每个 tool 至少提供 3 类 fixture：

| fixture | 内容 |
|---|---|
| `success` | 正常返回，字段较完整 |
| `partial` | 部分字段缺失 |
| `expired` | 过期/未登录响应 |

---

## 13. 你的交付物

文档：

1. `target_plugin_tool_map.md`
2. `endpoint_discovery_notes.md`
3. `tool_specs.md`
4. `field_mapping.md`
5. `normalization_rules.md`
6. `source_envelope_examples.md`
7. `b2_to_b1_client_requirements.md`
8. `b2_to_b3_fixture_contract.md`

代码或伪代码：

1. 至少一个非 SellerSprite adapter。
2. 至少两个非 SellerSprite tool。
3. normalizer。
4. SourceEnvelope builder。
5. summary builder。
6. missing_fields detector。

样例：

1. raw JSON 样例。
2. normalized JSON 样例。
3. SourceEnvelope 样例。
4. collection meta 样例。

---

## 14. 验收标准

B2 通过标准：

1. 没有把 SellerSprite 当作开发任务。
2. 至少完成一个非 SellerSprite 插件的 tool map。
3. 至少两个非 SellerSprite tools 有 ToolSpec。
4. 至少一个 tool 能通过 B1 AuthenticatedClient 返回真实业务字段。
5. 每个 tool 输出 SourceEnvelope。
6. 每个字段标明下游用途：L1、L2、L3、PScore、XLSX、152 键。
7. 每个字段标明口径：官方、工具估算、页面字段、模型归纳、人工输入。
8. 缺字段进入 missing_fields。
9. 估算值保留 source_id 和 tool_name。
10. 提供 success/partial/expired fixture。

阻断项：

1. 直接读 token/cookie。
2. 在文档中粘贴敏感 header。
3. 只给 raw JSON，没有 normalizer。
4. 只给字段表，没有 ToolSpec。
5. 一个字段一个 tool。
6. 把工具自带 opportunity score 直接当 PScore。
7. 把 TikTok 指数或趋势热度写成销量事实。

---

## 15. 可直接丢给大模型的执行提示词

```markdown
你是员工 B2，负责非 SellerSprite 插件 MCP 工具接口与字段采集。

重要前提：

- SellerSprite 已经实现，不要重做。
- 你的目标是 Helium10、Jungle Scout、Kalodata、FastMoss、EchoTik 等其他插件。
- B1 负责会话授权和 HTTP 服务，你只能通过 AuthenticatedClient 调用，不得直接读 token/cookie。
- 你要按业务能力设计工具，不得一个字段一个工具。

请输出：

1. target_plugin_tool_map.md
2. endpoint_discovery_notes.md
3. tool_specs.md
4. field_mapping.md
5. normalization_rules.md
6. source_envelope_examples.md
7. 至少一个非 SellerSprite adapter 设计
8. 至少两个非 SellerSprite tool 设计

每个 tool 必须包含：

- tool_name
- source_id
- business_capability
- input_schema
- output_schema
- endpoint family
- rate limit
- failure policy
- downstream_layer
- SourceEnvelope 样例
- missing_fields 规则

硬性约束：

- 不引用任何本机路径。
- 不要求读取外部源文件。
- 不输出 token/cookie/Auth-Token。
- 不直接读凭证。
- 工具输出必须能进入 CandidateDataPacket。
- 所有估算字段必须保留 source_id/tool_name。
- 缺字段不能用 0 代替。
```
