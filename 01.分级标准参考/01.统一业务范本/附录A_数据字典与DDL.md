# 附录 A · 数据字典与 DDL

> 主纲 §3/§7/§10 的落库规格。所有主数据 / 决策数据物理表归**数据中台**；运行态表归智能平台；单据表归 ERP（此处只列与中台同步的镜像约定）。
> DDL 用平台无关写法（类型示意），落地时按中台技术栈（如 Doris/StarRocks/MySQL）适配。
> 命名规范：主数据表 `t_*`；枚举 ENUM 用业务值；金额一律带币种列；所有表含 `created_at/updated_at/source_system`。

---

## 0. 表清单与优先级

| 表 | 用途 | 权威源 | 优先级 | 缺口 |
|---|---|---|---|---|
| t_legal_entity | 公司主体 | 中台 | P0 | GAP-01 新建 |
| t_brand | 品牌备案 | 中台 | P0 | GAP-02 新建 |
| t_platform | 销售平台 | 中台 | P0 | 新建 |
| t_shop | 店铺（三维分级枢纽） | 中台 | P0 | 改造（加分级列） |
| t_category | 平台类目 | 中台 | P0 | 新建/对齐 |
| t_spu | 标准产品单元 | 中台 | **P0** | GAP-09 新建 |
| t_sku | 库存单元 | 中台（实物数 ERP） | **P0** | 新建 |
| t_canonical_product | 平台无关主数据（不变核心） | 中台 | **P0** | 新建 |
| t_canonical_attribute | Canonical 属性键值 | 中台 | **P0** | 新建 |
| t_site_offer | 变域实例（价格/库存/站点） | 中台 | P0 | 新建 |
| t_category_mapping | 类目字段映射配置 | 中台 | **P0** | 新建（规格见附录E） |
| t_value_dict | 枚举值字典（平台↔canonical） | 中台 | **P0** | 新建 |
| t_listing | 在线商品镜像 | 平台/中台镜像 | P0 | 既有，对齐 |
| t_content_asset | 文案/图片资产多版本 | 中台（运行态智能平台） | P1 | 新建 |
| t_priority_score | 选品打分结果 | 中台 | P1 | 新建 |
| t_trend_signal | 情报信号（情报源≠销售平台） | 中台 | P1 | 新建 |
| t_rule | 统一规则承载 | 中台 | P0 | 新建/对齐 |
| t_kpi_redline | 店铺安全红线配置 | 中台 | P1 | 新建 |
| t_project | 决策容器 | 中台 | P1 | 新建/对齐 |
| t_cost_item / t_profit | 成本血缘与双利润 | 中台 | P2 | 既有评分表对齐 |

---

## 1. 主体 / 平台 / 店铺

```sql
t_legal_entity (
  entity_id      PK,
  name           VARCHAR,
  country        VARCHAR,          -- 注册国
  vat_no         VARCHAR,          -- VAT 税号
  epr_no         VARCHAR,          -- EPR 注册号
  tax_agent      VARCHAR,
  entry_cost     DECIMAL, currency CHAR(3),  -- 进入成本（成本血缘挂载点）
  status         ENUM(active,suspended,closed),
  created_at, updated_at, source_system
)

t_brand (                          -- GAP-02 预留
  brand_id       PK,
  name           VARCHAR,
  trademark_no   VARCHAR,          -- 文字商标号（R3 侵权判据之一）
  registration   VARCHAR,          -- 备案信息
  entity_id      FK->t_legal_entity,
  status         ENUM(draft,registered,active),
  created_at, updated_at, source_system
)

t_platform (
  platform_id    PK,
  code           ENUM(amazon,walmart,temu,shein,tiktok,aliexpress,etsy,ebay,independent,...),
  role           SET(sales,intelligence),  -- ★A8: 销售平台/情报源, 同站点可兼具, 数据模型分标识
  api_type       VARCHAR,          -- SP-API/Open API/批量表/RPA
  status         ENUM(onboarding,active,disabled)
)

t_shop (                           -- ★店铺三维分级枢纽 (A2), out-degree 最高
  shop_id          PK,
  platform_id      FK->t_platform,
  entity_id        FK->t_legal_entity,
  -- 三维正交分级（禁合并为单一"等级"）
  safety_level     ENUM(S,A,B,C,D),         -- ① 安全等级, KPI红线驱动, 可自动降级
  publish_priority ENUM(S,A,B),             -- ② 刊登优先级
  inventory_tag    SET(通铺,分类,品牌,热销,人群,风格,场景),  -- ③a 货盘定位"卖什么" (A12)
  content_tone     ENUM(品牌店,清货店,高毛利店,风格店),       -- ③b 内容调性"怎么说" (A12)
  stock_status     ENUM(现货,海外仓,无现货),
  quality_grade    ENUM(S,A,B),
  status           ENUM(active,downgraded,suspended,exited),
  created_at, updated_at, source_system
)
```

---

## 2. 商品主数据（★ Canonical 核心，P0）

```sql
t_spu (                            -- GAP-09: SPU 物理表
  spu_id         PK,
  canonical_id   FK->t_canonical_product,
  category_id    FK->t_category,
  title_base     VARCHAR,
  grade          ENUM(S,A,B,C),    -- 商品分级, 来源=Priority Score 分箱 (A1/A9)
  status         ENUM(created,listed,maintaining,delisted),
  created_at, updated_at, source_system
)

t_sku (
  sku_id         PK,
  spu_id         FK->t_spu,
  spec_color     VARCHAR,          -- 规格轴: 颜色
  spec_size      VARCHAR,          -- 规格轴: 尺码
  sku_seller     VARCHAR,          -- 卖家SKU/SKC
  -- 库存"实物数"权威在 ERP, 此处镜像
  created_at, updated_at, source_system
)

t_canonical_product (              -- 跨平台复用基石(不变核心) — 152键中的"不变89"
  canonical_id   PK,
  spu_id         FK->t_spu,
  title_base     JSON,             -- 多语言 Map {en:..,es:..}
  description    JSON,             -- 多语言 Map
  brand_id       FK->t_brand,
  category_canonical VARCHAR,
  attrs_json     JSON,             -- 不变属性集合 (D5材质/D6款式/D7人群/D9尺寸/D13包装...)
  variant_axes   JSON,             -- 结构化规格轴(颜色/尺码), 发布时按平台展开 (M6/E·R-变体展开)
  version        INT,              -- 主数据版本
  created_at, updated_at, source_system
)

t_canonical_attribute (            -- 标准属性键值(规范化检索用; attrs_json的展开镜像)
  attr_id        PK,
  canonical_id   FK->t_canonical_product,
  attr_key       VARCHAR,          -- cp.* 152键之一
  attr_value     VARCHAR,
  unit           VARCHAR,          -- SI 归一: 克重g/m² 重量g 长度cm
  value_dict_id  FK->t_value_dict NULL,
  type           ENUM(text,enum,number,bool,media,composition)
)

t_site_offer (                     -- 变域实例: 价格/库存/站点级, 按 SiteScope 实例化, 不进canonical核心
  offer_id       PK,
  canonical_id   FK->t_canonical_product,
  sku_id         FK->t_sku,
  platform_id    FK->t_platform,
  shop_id        FK->t_shop,
  site           VARCHAR,          -- 站点(US/EU...)
  price          DECIMAL, currency CHAR(3),
  sale_price     DECIMAL, sale_start DATE, sale_end DATE,
  quantity       INT,              -- 可售库存(实物数源自ERP)
  fulfillment    VARCHAR,          -- FBA/自发货
  handling_time  INT,
  -- 价格/库存=确定性操作, 走规则引擎, 硬隔离LLM (R5)
  created_at, updated_at, source_system
)
```

---

## 3. 类目映射与枚举字典（★配置化内核，P0）

```sql
t_category (
  category_id    PK,
  platform_id    FK->t_platform,
  name           VARCHAR,          -- 平台类目(女装上衣/女士一件式...)
  attr_template  JSON,             -- 绑定的属性模板
  status         ENUM(active,deprecated)
)

t_category_mapping (               -- 映射引擎唯一事实来源, 按"平台×类目×canonical键"一行
  mapping_id     PK,
  platform       ENUM(amazon,temu,shein,...),
  category_id    FK->t_category,
  canonical_key  VARCHAR,          -- cp.* 152键之一
  platform_field VARCHAR,          -- 平台模版列内部名/列号(item_name / attr_62 / 模版!G)
  required       ENUM(必填,条件必填,选填),   -- 平台×类目动态必填 (M4)
  value_dict_id  FK->t_value_dict NULL,    -- 枚举字典 (M3), 非枚举为空
  transform_rule VARCHAR,          -- R-多语言/R-成分结构化/R-单位归一/... (附录E·§6)
  cardinality    VARCHAR,          -- 1 / N (多列槽位: 图片×10, 成分×5)
  version        INT,              -- 模版升级走版本化
  status         ENUM(draft,active) DEFAULT draft   -- draft 不参与自动发布
)

t_value_dict (                     -- 枚举值字典: 平台枚举↔canonical枚举 双向 (M3)
  dict_id        PK,
  canonical_key  VARCHAR,          -- 归属的canonical键(如 cp.style)
  canonical_value VARCHAR,         -- 标准值
  platform       ENUM(amazon,temu,shein,...),
  platform_value VARCHAR,          -- 平台值(Shein attr_<id> 有效值 / Temu下拉 / Amazon Valid Values)
  source         ENUM(amazon_valid_values,temu_dropdown,shein_attr),
  status         ENUM(draft,active)
)
```

---

## 4. 在线商品 / 内容资产

```sql
t_listing (                        -- 平台为真, 中台镜像
  listing_id     PK,
  shop_id        FK->t_shop,
  platform_id    FK->t_platform,
  canonical_id   FK->t_canonical_product,
  offer_id       FK->t_site_offer,
  platform_item_id VARCHAR,        -- 平台侧ID(ASIN等)
  listing_level  ENUM(L1,L2,L3,L4),       -- 刊登级别=内容投入档(与发布风险正交, A3)
  lifecycle      ENUM(draft,mapped,risk_graded,published,online,maintaining,delisted,archived),
  created_at, updated_at, source_system
)

t_content_asset (                  -- 文案/图片多版本 (A11: 主档源=中台, 非ERP)
  asset_id       PK,
  canonical_id   FK->t_canonical_product,
  shop_id        FK->t_shop NULL,  -- 可店铺级(随内容调性)
  type           ENUM(title,description,bullet,keyword,image,video),
  tone           ENUM(品牌店,清货店,高毛利店,风格店),   -- 内容调性 (A12)
  sellpoint_view ENUM(功能型,身材型,场景型,风格型),       -- 卖点四视角
  version_stage  ENUM(ai_raw,human_edit,final),         -- 原始/人工/最终版本
  quality_level  ENUM(P0,P1,P2,pass) NULL,              -- 内容质量分级 (A13→发布风险)
  content        TEXT/URL,
  perf_data      JSON,             -- 点击/转化/退货 表现数据(回流迭代)
  created_at, updated_at, source_system
)
```

---

## 5. 选品 / 情报（落中台, 非独立库, A7）

```sql
t_trend_signal (                   -- 情报信号; platform.role=intelligence (情报源≠销售平台, A8)
  signal_id      PK,
  spu_id         FK->t_spu NULL,   -- 候选可未建SPU
  track          ENUM(趋势款,爆款款,长尾款),   -- 三赛道
  source         VARCHAR,          -- GoogleTrends/Pinterest/TikTokCC/Lyst/Amazon BSR/...
  metric         VARCHAR,          -- 搜索斜率/声量/BSR/销量/评论/长尾量...
  value          DECIMAL,
  captured_at    DATETIME
)

t_priority_score (                 -- 打分结果 → 派生商品 S/A/B/C (A9)
  score_id       PK,
  spu_id         FK->t_spu,
  track          ENUM(趋势款,爆款款,长尾款),
  trend_score    DECIMAL,          -- 趋势分
  hit_score      DECIMAL,          -- 爆款验证分
  longtail_score DECIMAL,          -- 长尾机会分
  profit_score   DECIMAL,          -- 利润分
  supply_score   DECIMAL,          -- 供应链可做分
  risk_score     DECIMAL,          -- 风险分(减项)
  total          DECIMAL,          -- 加权合计(公式见附录C·§1)
  derived_grade  ENUM(S,A,B,C),    -- 分箱→商品等级
  weight_profile VARCHAR,          -- 所用权重档(按赛道/店铺配置)
  version        INT, scored_at DATETIME
)
```

---

## 6. 规则 / 风控 / 决策

```sql
t_rule (                           -- 统一承载: 发布判级/维护阈值/风控降级/复制退出/字段必填
  rule_id        PK,
  domain         ENUM(priority_score,publish_risk,maintain,shop_safety,replicate_exit,content_quality,field_required),
  scope          JSON,             -- 适用范围(平台/类目/店铺/商品等级)
  condition      VARCHAR,          -- 触发条件(指标+比较)
  threshold      VARCHAR,          -- 阈值(★多为 GAP-03/04 待业务赋值)
  action         VARCHAR,          -- 动作(改价/降级/下架/转人工/加码...)
  owner_agent    VARCHAR,          -- 责任Agent/规则引擎
  status         ENUM(draft,active) DEFAULT draft   -- draft 不参与自动决策
)

t_kpi_redline (                    -- 店铺安全红线, 按平台配置 (A5)
  redline_id     PK,
  platform_id    FK->t_platform,
  kpi            VARCHAR,          -- ODR/LSR/VTR/Scorecard/DSR...
  threshold      DECIMAL,
  downgrade_to   ENUM(S,A,B,C,D),  -- 命中后降到的安全等级
  status         ENUM(draft,active)
)

t_project (                        -- L1 决策容器, 一级主对象
  project_id     PK,
  market         VARCHAR,          -- 市场×平台×品类
  platform_id    FK->t_platform,
  category_id    FK->t_category,
  budget         DECIMAL, currency CHAR(3),
  stage          ENUM(立项,预算,执行,评估,复制,退出),
  margin_profit  DECIMAL,          -- 边际贡献利润(口径见附录C·§5)
  full_profit    DECIMAL,          -- 全成本利润
  created_at, updated_at, source_system
)

t_cost_item (                      -- 成本血缘六层
  cost_id        PK, project_id FK->t_project,
  layer          ENUM(战略进入,平台店铺,品牌知识产权,产品准入认证,商品经营,售后风险),
  amount         DECIMAL, currency CHAR(3)
)
```

---

## 7. 建表治理铁律

1. **主数据写入只走中台权威接口**（见附录 B·B1）；其他系统只读镜像。
2. **所有自动决策依赖的数据须可血缘追溯**（R1）：t_priority_score/t_rule/t_category_mapping 均带 version + status。
3. **draft 不参与自动**：t_rule / t_category_mapping / t_value_dict 的 `status=draft` 行不进自动决策与自动发布，赋值/审核后置 active。
4. **金额必带币种**；单位走 SI 归一（克重 g/m²、重量 g、长度 cm），平台单位换算在出参层做、保留原值审计。
5. **个人/敏感数据**不入 URL 参数、不跨源拼装。
