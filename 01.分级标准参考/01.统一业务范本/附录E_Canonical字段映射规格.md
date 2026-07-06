# 附录 E · Canonical 字段映射规格（CanonicalProduct → 多平台）

> 主纲 §5 铺货流、模块 M2 的字段级落地规格，是**类目属性映射引擎的唯一事实来源**。
> 真值来源：三份女装类目刊登模版快照（Amazon SP-API Template 295列 / Temu 模版 120列 / Shein 女士一件式 93列），共 **508 个平台字段**，全量映射至 **152 个平台无关 Canonical 键**。
> 落库见附录 A `t_category_mapping` + `t_value_dict`；规则承载见附录 C·§7。品类范围：女装（上衣/连衣裙类）。

---

## 1. 模型定位与映射方向

| 维度 | 说明 |
|---|---|
| 它是什么 | CanonicalProduct 标准属性键 ↔ 各平台模版列的对照表 + 转换规则 |
| 边界 | 只管**字段结构与转换**；枚举取值、必填校验按平台×类目动态读配置，不固化在 canonical 层 |
| 被谁用 | M2 映射引擎、M3 SubmitListing/UpdateDetail、M5 内容复用换图换文 |

```
CanonicalProduct(不变核心) + SiteScope(平台/站点/店铺) ──映射引擎──> 各平台刊登模版字段
                                                          ↑ 规则存 t_category_mapping（配置化, 禁硬编码）
```

---

## 2. 锁定决策 M1–M6

| 编号 | 决策 |
|---|---|
| M1 | 命名空间统一 `cp.*` 前缀、平台无关；变/不变分离落到 CanonicalProduct(不变) 与 SiteScope/Offer(变) |
| M2 | 三平台模版为映射真值(2026-06 快照)，配置化存 t_category_mapping，**禁硬编码**，模版升级走版本化 |
| M3 | 枚举走**值字典映射**(t_value_dict)：平台枚举↔canonical枚举双向；Shein `attr_<id>`、Temu 下拉、Amazon Valid Values 各挂源 |
| M4 | 必填性按**目标平台×类目**动态判定，不在 canonical 层固化 |
| M5 | 合规安全域(电池/GHS/Prop65/GPSR/FCC…)几乎全 Amazon 独有，按「平台扩展属性包」挂载，不污染核心 |
| M6 | 颜色/尺码在 canonical 为结构化规格轴，发布按平台展开：Amazon parent/child、Shein 主/副规格、Temu SKC+尺码组 |

---

## 3. CanonicalProduct Schema 总览（152 键 / 14 域；不变 89 · 变 63）

| 域 | 键数 | 内容 | 变/不变 |
|---|---|---|---|
| D1 标识层级 | 8 | SPU/SKU/类目/层级/刊登动作/父子/变体维度 | 混合 |
| D2 标题文案 | 5 | 多语言标题、描述、卖点、关键词 | 不变为主 |
| D3 品牌编码 | 7 | 品牌/型号/GTIN/类型词/UNSPSC/IP/系列 | 不变 |
| D4 媒体 | 8 | 主图/附图集/色块/方块/SKU图/视频 | 不变为主 |
| D5 材质成分 | 16 | 成分·基布·里衬·克重·纹理·涂层·透明·加绒 | 不变 |
| D6 款式属性 | 25 | 风格/图案/季节/领袖门襟/版型/廓形/护理… | 不变 |
| D7 人群适用 | 4 | 性别/部门/年龄/人群标签 | 不变 |
| D8 规格变体 | 10 | 颜色/尺码/主副规格/尺码表/特殊尺寸 | 变为主 |
| D9 尺寸测量 | 8 | 胸围/衣长/腰围/脚口/内衣/泳装/体型 | 不变 |
| D10 模特 | 3 | 试穿模特/尺码/感受 | 不变 |
| D11 价格 | 9 | 标价/售价/促销/MSRP/限价/B2B/税码 | 变 |
| D12 库存履约 | 11 | 库存/仓/渠道/时效/上架/运费/SKU分类 | 变 |
| D13 物流包装 | 6 | 包装尺寸/重量/净重/类型/件数/独立包装 | 不变为主 |
| D14 合规安全 | 32 | 产地/敏感品/电池/GHS/Prop65/认证/证书… | 变为主 |

> 不变项进 `t_canonical_product`；变项进 `t_site_offer`（按平台×店铺×站点实例化）。

---

## 4. 全量字段映射表（508 列 → 152 键）

> 单元格：`平台字段显示名 [等N列] 列号`；`—`=该平台无对应列。列号为模版真实列字母，可直接定位回模版核对。

### D1 标识层级
| Canonical Key | 变/不变 | 业务含义 | Amazon | Temu | Shein |
|---|---|---|---|---|---|
| `cp.canonical_id` | 不变 | 中台主键(系统生成) | — | — | — |
| `cp.spu_id` | 不变 | SPU 货号 | — | SPU货号 `B` | 货号 `A` |
| `cp.sku_seller` | 变 | 卖家SKU/SKC | SKU `A` | SKC货号 等2列 `BQ–BR` | 卖家SKU `B` |
| `cp.product_type` | 不变 | 平台类目 | Product Type `B` | — | 分类 `C` |
| `cp.product_level` | 不变 | SPU/SKC/SKU 层级 | — | 商品层级 `A` | — |
| `cp.listing_action` | 变 | 创建/更新(Operation.op_type) | Listing Action `C` | — | — |
| `cp.parentage` | 变 | 父子关系 | Parentage Level 等2列 `D–E` | — | — |
| `cp.variation_theme` | 变 | 变体维度 | Variation Theme Name `F` | — | — |

### D2 标题文案
| Canonical Key | 变/不变 | 业务含义 | Amazon | Temu | Shein |
|---|---|---|---|---|---|
| `cp.title_base` | 不变 | 标题(多语言) | Item Name `G` | 商品名称 等2列 `C–D` | 默认商品名称[en] 等2列 `D–E` |
| `cp.title_highlight` | 变 | 标题差异点(防重复) | Item Highlight `H` | — | — |
| `cp.description` | 不变 | 描述(多语言) | Product Description `AA` | 详情图文-英语 `DN` | 默认商品描述[en] 等2列 `F–G` |
| `cp.bullet_points` | 不变 | 卖点要点 | Bullet Point 等5列 `AB–AF` | — | — |
| `cp.keywords` | 变 | 搜索关键词 | Generic Keywords `AG` | — | — |

### D3 品牌编码
| Canonical Key | 变/不变 | 业务含义 | Amazon | Temu | Shein |
|---|---|---|---|---|---|
| `cp.brand` | 不变 | 品牌 | Brand Name `I` | 品牌名 `BK` | 品牌 `H` |
| `cp.model` | 不变 | 型号 | Model Number 等3列 `M,N,BQ` | — | — |
| `cp.gtin` | 不变 | UPC/EAN/GTIN+类型 | Product Id Type 等2列 `J–K` | — | — |
| `cp.item_type_keyword` | 不变 | 商品类型词 | Item Type Keyword `L` | — | — |
| `cp.unspsc` | 不变 | UNSPSC/NSN | UNSPSC Code 等2列 `O–P` | — | — |
| `cp.ip_character` | 不变 | IP形象/主题 | Subject Character 等3列 `BL,CC,CD` | — | 商品IP `J` |
| `cp.product_series` | 不变 | 系列线 | — | 系列线 `BM` | — |

### D4 媒体
| Canonical Key | 变/不变 | 业务含义 | Amazon | Temu | Shein |
|---|---|---|---|---|---|
| `cp.main_image` | 不变 | 主图 | Main Image URL `Q` | — | 首图 `K` |
| `cp.gallery_images` | 不变 | 附图集 | Other Image URL 等8列 `R–Y` | 商品轮播图1 等10列 `DD–DM` | 细节图1 等10列 `N–W` |
| `cp.swatch_image` | 变 | 色块图 | Swatch Image URL `Z` | — | 色块图 `M` |
| `cp.square_image` | 不变 | 方块图 | — | — | 方块图 `L` |
| `cp.sku_image` | 变 | SKU图 | — | — | SKU图 `AG` |
| `cp.offer_images` | 变 | Offer图 | Main Image Location 等6列 `EA–EF` | — | — |
| `cp.main_video` | 不变 | 主图视频 | — | 主图视频 `DO` | 视频[shein-us] `CO` |
| `cp.detail_video` | 不变 | 详情视频 | — | 详情视频 `DP` | — |

### D5 材质成分
| Canonical Key | 变/不变 | 业务含义 | Amazon | Temu | Shein |
|---|---|---|---|---|---|
| `cp.composition` | 不变 | 成分(材质+比例) | — | 成分1 等10列 `G–P` | 成分(62) `AU` |
| `cp.material_main` | 不变 | 主材质 | Material 等3列 `BB–BD` | 材质 `Q` | 材质(160) 等2列 `BO,CH` |
| `cp.fabric_type` | 不变 | 面料 | Fabric Type `BE` | 面料 `BG` | — |
| `cp.fabric_weight` | 不变 | 面料克重(g/m²) | Apparel Fabric Weight Class `DF` | 面料克重1（g/m²) 等4列 `AM,AN,BO,BP` | 主料克重2（g/m²）(1002188) 等2列 `CK–CL` |
| `cp.fabric_texture` | 不变 | 面料纹理 | — | 面料纹理1 等2列 `AL,BN` | — |
| `cp.fabric_stretch` | 不变 | 面料弹性 | Apparel Fabric Stretch `DJ` | 面料弹性 `AH` | 面料弹性(39) `BA` |
| `cp.weave_type` | 不变 | 织造方式 | Compliance Weave Type `JR` | 织造方式 `AK` | — |
| `cp.base_fabric` | 不变 | 基布成分 | — | 基布成分1 等10列 `R–AA` | — |
| `cp.lining_flag` | 不变 | 是否带里衬 | — | — | 是否带里衬(58) `BD` |
| `cp.lining_desc` | 不变 | 里衬描述 | Lining Description `BF` | — | 里衬类型(1002186) `CI` |
| `cp.lining_composition` | 不变 | 里衬成分 | — | 里衬成分1 等10列 `AR–BA` | 里衬成分(1000078) `AV` |
| `cp.lining_weight` | 不变 | 里料纹理克重 | — | 里料纹理 等3列 `AO–AQ` | 里衬克重（g/m²）(1002190) `CM` |
| `cp.coating` | 不变 | 涂层 | — | — | 涂层(1000105) `AW` |
| `cp.secondary_material` | 不变 | 次要材质 | — | — | 次要材质(1000547) 等2列 `CA,CJ` |
| `cp.transparency` | 不变 | 是否透明 | — | 是否透明 `AD` | 是否透明(207) `BT` |
| `cp.fleece_flag` | 不变 | 是否加绒 | — | — | 是否加绒(1000437) `BY` |

### D6 款式属性
| Canonical Key | 变/不变 | 业务含义 | Amazon | Temu | Shein |
|---|---|---|---|---|---|
| `cp.style` | 不变 | 风格 | Style `AN` | 风格 `AG` | 风格(101) `BH` |
| `cp.pattern` | 不变 | 图案 | Pattern `CA` | 图案 `AC` | — |
| `cp.print_type` | 不变 | 印花类型 | — | 印花类型 `AI` | — |
| `cp.season` | 不变 | 季节 | — | 季节 `AE` | 季节(77) `BF` |
| `cp.neckline` | 不变 | 领型 | Neck Style 等5列 `CQ–CU` | 领型 `BL` | 领型(66) `BE` |
| `cp.sleeve_length` | 不变 | 袖长 | Sleeve Length Description `CY` | 袖长 `BD` | 袖长(90) `BG` |
| `cp.sleeve_type` | 不变 | 袖型 | Sleeve Type `CZ` | 袖型 `BE` | — |
| `cp.closure` | 不变 | 门襟/闭合 | Closure Type 等2列 `DA–DB` | 门襟类型 `BH` | 门襟类型(150) `BM` |
| `cp.fit_type` | 不变 | 合身/版型 | Fit Type `BS` | 版型 `BJ` | 合身类型(40) `BB` |
| `cp.silhouette` | 不变 | 廓形 | — | 廓形 `BI` | — |
| `cp.length_style` | 不变 | 长度 | — | 长度 `BB` | 长度(54) `BC` |
| `cp.rise_style` | 不变 | 腰位 | Rise Style `BU` | — | — |
| `cp.waistband` | 不变 | 腰带 | — | 腰带 `BC` | 腰带(9) `AX` |
| `cp.padding` | 不变 | 胸垫/杯 | — | 胸垫 `BF` | 胸垫(23) `AY` |
| `cp.detail_feature` | 不变 | 细节/特殊特征 | Special Features 等6列 `AH,AI,AJ,AK,AL,DE` | 细节 `AB` | 细节(31) 等2列 `AZ,BU` |
| `cp.care_instructions` | 不变 | 护理说明 | Care Instructions `BT` | 护理说明 `AF` | 护理说明(1000069) `BV` |
| `cp.style_source` | 不变 | 款式来源 | — | 款式来源 `AJ` | — |
| `cp.occasion` | 不变 | 场合 | Lifestyle `AM` | — | 场合(128) `BI` |
| `cp.theme_festival` | 不变 | 主题/节日 | Theme `BR` | — | 节日(1001137) `CD` |
| `cp.hem_shape` | 不变 | 下摆形状 | — | — | 下摆形状(129) `BJ` |
| `cp.pocket` | 不变 | 口袋 | — | — | 是否有口袋(1000438) `BZ` |
| `cp.back_style` | 不变 | 背部款式 | Back Style 等5列 `CE–CI` | — | — |
| `cp.embellishment` | 不变 | 装饰工艺 | Embellishment Feature 等5列 `CL–CP` | — | — |
| `cp.function_type` | 不变 | 功能类型 | Product Benefits 等5列 `BV–BZ` | — | 功能类型(1000104) `BX` |
| `cp.set_count` | 不变 | 套装件数 | Number of Pieces `BP` | — | 套装数(1000103) `BW` |

### D7 人群适用
| Canonical Key | 变/不变 | 业务含义 | Amazon | Temu | Shein |
|---|---|---|---|---|---|
| `cp.target_gender` | 不变 | 目标性别 | Target Gender `AP` | — | — |
| `cp.department` | 不变 | 部门 | Department Name `AO` | — | — |
| `cp.age_range` | 不变 | 年龄段 | Age Range Description 等2列 `AQ,JQ` | — | 年龄(154) `BN` |
| `cp.user_group` | 不变 | 用户人群 | — | — | 用户(176) 等2列 `BR–BS` |

### D8 规格变体
| Canonical Key | 变/不变 | 业务含义 | Amazon | Temu | Shein |
|---|---|---|---|---|---|
| `cp.color` | 变 | 颜色值+ColorMap | Color Map 等2列 `BN–BO` | 色值(主规格) `BS` | — |
| `cp.size_value` | 变 | 尺码值 | Size Value `AT` | 尺码 `BW` | — |
| `cp.size_system` | 变 | 尺码体系 | Size System `AR` | 尺码组别 等2列 `BU–BV` | — |
| `cp.size_class` | 变 | 尺码类别 | Size Class `AS` | — | — |
| `cp.main_attribute` | 变 | 主规格 | — | 规格类型2 `BT` | 是否有主规格 等3列 `AN–AP` |
| `cp.secondary_attribute` | 变 | 副规格 | — | — | 规格2 等4列 `AQ–AT` |
| `cp.size_chart_template` | 不变 | 尺寸表模板 | — | — | 尺寸表模板名称 `AM` |
| `cp.special_size` | 不变 | 特殊尺寸 | Shapewear Size To Range 等2列 `AU,BM` | — | — |
| `cp.size_country` | 变 | 尺码国家 | Garment Size Country `DI` | — | — |
| `cp.size_dimension` | 不变 | 尺寸大小 | — | — | 尺寸大小(146) `BL` |

### D9 尺寸测量
| Canonical Key | 变/不变 | 业务含义 | Amazon | Temu | Shein |
|---|---|---|---|---|---|
| `cp.measure_bust` | 不变 | 胸围 | Chest Size 等2列 `CJ–CK` | 胸围全围（cm) 等2列 `BX,CA` | — |
| `cp.measure_length` | 不变 | 衣长 | — | 衣长（cm) `BY` | — |
| `cp.measure_waist` | 不变 | 腰围 | Waist Size 等2列 `CV–CW` | 腰围（cm) `BZ` | — |
| `cp.leg_opening` | 不变 | 脚口 | Leg Hem Opening Width 等2列 `DG–DH` | — | 脚口位置(1001915) `CG` |
| `cp.bottom_type` | 不变 | 下装类型 | — | — | 下装类型(161) `BP` |
| `cp.bra_spec` | 不变 | 内衣规格 | Cup Size Value 等8列 `AV,AW,AX,AY,BI,BJ,BK,JO` | — | 内衣版型(134) 等3列 `BK,CC,CE` |
| `cp.swimwear_form` | 不变 | 泳装形态 | Swimwear Form Type 等3列 `CX,DC,DD` | — | — |
| `cp.body_type` | 不变 | 体型/身高 | Body Type 等2列 `AZ–BA` | — | — |

### D10 模特
| Canonical Key | 变/不变 | 业务含义 | Amazon | Temu | Shein |
|---|---|---|---|---|---|
| `cp.fit_model` | 不变 | 试穿模特 | — | 试穿模特 `CB` | — |
| `cp.fit_model_size` | 不变 | 试穿尺码 | — | 试穿尺码 `CC` | — |
| `cp.fit_sentiment` | 不变 | 试穿感受/偏码 | Fit to Size Sentiment `DK` | 试穿感受 `CD` | — |

### D11 价格
| Canonical Key | 变/不变 | 业务含义 | Amazon | Temu | Shein |
|---|---|---|---|---|---|
| `cp.list_price` | 变 | 标价 | List Price `DU` | — | — |
| `cp.your_price` | 变 | 售价/申报价 | Your Price USD `EX` | 申报价格-美国站 `CF` | 价格 `AH` |
| `cp.currency` | 变 | 币种 | — | 币种 `CG` | — |
| `cp.sale_price` | 变 | 促销价 | Sale Price USD 等5列 `FB–FF` | — | — |
| `cp.msrp` | 变 | 建议零售价 | — | 制造商建议零售价(USD) `CN` | — |
| `cp.price_minmax` | 变 | 限价 | Minimum Seller Allowed Price 等2列 `EZ–FA` | — | — |
| `cp.b2b_pricing` | 变 | B2B阶梯价 | Pricing Rule 等17列 `EY,FG–FV` | — | — |
| `cp.tax_code` | 变 | 税码 | Product Tax Code `DV` | — | — |
| `cp.reference_link` | 变 | 参考链接 | — | 参考链接 `CE` | 参考产品链接 等2列 `AK–AL` |

### D12 库存履约
| Canonical Key | 变/不变 | 业务含义 | Amazon | Temu | Shein |
|---|---|---|---|---|---|
| `cp.quantity` | 变 | 库存数量 | Quantity (US) `ET` | 发货仓1库存 `CI` | 库存 等2列 `X–Y` |
| `cp.warehouse` | 变 | 发货仓 | — | 发货仓1 `CH` | — |
| `cp.fulfillment_channel` | 变 | 履约渠道 | Fulfillment Channel Code (US) `ES` | — | — |
| `cp.handling_time` | 变 | 处理时效 | Handling Time (US) `EU` | — | — |
| `cp.restock_date` | 变 | 补货日期 | Restock Date (US) `EV` | — | — |
| `cp.inventory_always` | 变 | 无限库存 | Inventory Always Available (US) `EW` | — | — |
| `cp.max_order_qty` | 变 | 限购 | Maximum Order Quantity `DX` | — | — |
| `cp.shelf_way` | 变 | 上架方式 | — | — | 上架方式 `AI` |
| `cp.launch_date` | 变 | 上架/发布日期 | Product Site Launch Date 等2列 `CB,DW` | — | 首次期望上架日期 `AJ` |
| `cp.shipping_template` | 变 | 运费模板 | Shipping Template (US) `FW` | — | — |
| `cp.sku_classification` | 变 | SKU分类数量 | — | SKU分类 等3列 `CJ–CL` | — |

### D13 物流包装
| Canonical Key | 变/不变 | 业务含义 | Amazon | Temu | Shein |
|---|---|---|---|---|---|
| `cp.package_dims` | 不变 | 包装尺寸 | Item Package Length 等6列 `FX–GC` | 最长边（cm) 等3列 `CZ–DB` | 长 等4列 `AB–AE` |
| `cp.package_weight` | 不变 | 包装重量 | Package Weight 等2列 `GD–GE` | 重量（g) `DC` | 重量 等2列 `Z–AA` |
| `cp.item_weight` | 不变 | 净重 | Item Weight 等2列 `DP–DQ` | — | — |
| `cp.package_type` | 不变 | 包装类型 | — | — | 包装类型 `AF` |
| `cp.number_of_items` | 不变 | 件数 | Number of Items 等2列 `BG–BH` | — | — |
| `cp.independent_packaging` | 变 | 独立包装 | — | 是否独立包装 `CM` | — |

### D14 合规安全
| Canonical Key | 变/不变 | 业务含义 | Amazon | Temu | Shein |
|---|---|---|---|---|---|
| `cp.country_of_origin` | 不变 | 产地 | Country of Origin `GF` | 商品产地 等2列 `E–F` | 产地 `I` |
| `cp.sensitive_attrs` | 变 | 敏感品属性 | — | 敏感词属性1 等7列 `CO–CU` | — |
| `cp.liquid_capacity` | 不变 | 液体容量 | — | 液体容量（ml) `CV` | — |
| `cp.blade_specs` | 不变 | 刀具规格 | — | 刀具长度(cm) 等2列 `CW–CX` | — |
| `cp.battery_capacity` | 不变 | 储电容量 | — | 储电容量（wh) `CY` | — |
| `cp.battery_compliance` | 变 | 电池合规 | Are batteries required? 等26列 `GG–GV,JM,JP,JS–JZ` | — | — |
| `cp.dangerous_goods` | 变 | 危险品 | Dangerous Goods Regulations 等5列 `GW–HA` | — | — |
| `cp.ghs` | 变 | GHS | GHS Class 等10列 `HB–HF,KB–KF` | — | — |
| `cp.hazmat` | 变 | Hazmat | Hazmat Aspect 等2列 `HG–HH` | — | — |
| `cp.sds` | 变 | SDS | Safety Data Sheet URL `HI` | — | — |
| `cp.age_restriction` | 变 | 年龄限制 | Buyer Age Restrictions `HJ` | — | — |
| `cp.ca_prop65` | 变 | 加州65 | California Proposition 65 等6列 `HK–HP` | — | — |
| `cp.pesticide` | 变 | 农药标识 | Pesticide Marking 等9列 `HQ–HY` | — | — |
| `cp.fcc` | 变 | FCC | Radio Frequency Emission 等6列 `HZ–IE` | — | — |
| `cp.regulatory_cert` | 变 | 法规认证 | Compliance Regulation Type 等11列 `IF–IP` | — | — |
| `cp.compliance_media` | 变 | 合规文档 | Compliance Media Source 等19列 `IQ–JI` | — | — |
| `cp.gpsr` | 变 | GPSR | Safety Attestation 等2列 `JJ–JK` | — | — |
| `cp.pfas` | 变 | PFAS | Contains PFAS `JL` | — | — |
| `cp.baa_taa` | 变 | BAA/TAA | BAA/TAA Compliance 等3列 `KG–KI` | — | — |
| `cp.ships_globally` | 变 | 全球发货 | Ships Globally `JN` | — | — |
| `cp.certificate` | 变 | 证书 | — | — | 证书编号/检测机构(1002226) `CN` |
| `cp.disclaimer` | 变 | 免责声明 | — | — | 免责声明(1001698) `CF` |
| `cp.plated_metal` | 不变 | 镀贵金属 | — | — | 镀贵金属类型(1000561) `CB` |
| `cp.location` | 变 | 所在地 | — | — | 所在地(169) `BQ` |
| `cp.import_designation` | 变 | 进口标识 | Import Designation `EG` | — | — |
| `cp.green_purchasing` | 变 | 绿色采购 | Is Green Purchasing Law Compliant `DO` | — | — |
| `cp.oem_sourced` | 不变 | OEM来源 | Is OEM Sourced Product `KA` | — | — |
| `cp.condition` | 变 | 商品状况 | Item Condition 等13列 `DS,DT,EH–ER` | — | — |
| `cp.gift_options` | 变 | 礼品选项 | Offering Can Be Gift Messaged 等2列 `DY–DZ` | — | — |
| `cp.skip_offer` | 变 | 跳过Offer | Skip Offer `DR` | — | — |
| `cp.government_contract` | 变 | 政府合同 | Government Contract Name 等2列 `DL–DM` | — | — |
| `cp.collection_item` | 变 | 收藏品 | Collection Item `DN` | — | — |

---

## 5. 覆盖核对（完整性证明）

| 平台 | 模版列数 | 映射 Canonical 键数 | 说明 |
|---|---|---|---|
| Amazon（女装 Template） | 295 | 100 | SP-API 全字段，含合规/B2B/Offer |
| Temu（女装上衣） | 120 | 62 | SPU 属性+SKU 规格+敏感品+尺码表+模特 |
| Shein（女士一件式） | 93 | 64 | `attr_<id>` 编码属性+双语文案 |
| **合计（去重）** | **508** | **152 键** | 三平台共有键 20 个 |

校验口径：508 列 100% 落键、无遗漏、无未定义键。`cp.canonical_id` 为系统生成主键，不对应平台列。

---

## 6. 转换规则（映射引擎运行时；承载于 t_category_mapping.transform_rule）

| 规则 | Canonical 侧 | 平台侧落地 | 转换要点 |
|---|---|---|---|
| R-多语言 | title_base/description 存语言 Map | Amazon=en；Temu=中英双列；Shein=默认[en]+多语言 | 按目标站点语言取值，缺译机翻+人工兜底 |
| R-成分结构化 | composition[]=[{material,pct}] | Amazon 拍平 Material×3；Temu 成分1-5+比例；Shein attr_62 多选 | 结构化↔拍平双向；比例带「%」「合计=100%」 |
| R-单位归一 | 克重g/m²、重量g、长度cm(SI) | 各平台带单位列 | 出参按平台单位换算，保留原值审计 |
| R-图片槽位 | gallery_images[] 有序数组 | Amazon 主图+8附图+swatch；Temu 轮播×10；Shein 首图+方块+色块+细节×10 | 按槽位数截断/补位，主图必为第1张 |
| R-枚举字典 | canonical 枚举值 | t_value_dict 双向映射(M3) | 未命中→兜底/人工；Shein 走 attr_<id> 有效值 |
| R-必填合并 | 同键多平台必填性不同 | 取目标平台×类目 required | 缺必填字段**阻断发布**并回写缺口 |
| R-变体展开 | color/size_* 结构化规格轴 | Amazon parent/child；Shein 主/副规格；Temu SKC+尺码组 | 笛卡尔展开为 SKU 行，继承父级不变属性(M6) |
| R-价格库存实例化 | 价格/库存为变域 | 按 SiteScope 实例化，不进 canonical 核心 | 确定性操作，走规则引擎、硬隔离 LLM(R5) |

---

## 7. 平台差异与缺口

**三平台共有 20 键（跨平台复用"安全交集"，铺货样板最小可发布集）**：标题、描述、品牌、卖家SKU、产地、附图集、库存、售价、材质、面料克重、面料弹性、风格、领型、袖长、门襟、版型、护理、细节、包装尺寸、包装重量。
→ 铺货样板(主纲§5·#3)优先用这 20 键打通发布，再按目标平台补独有项。

- **Amazon 独有 57 键**：合规大包(电池/GHS/Prop65/农药/FCC/法规/合规文档/GPSR/BAA-TAA/PFAS/SDS/危险品/Hazmat)、B2B阶梯价、促销/限价/标价/税码、Offer二手状况、履约/时效/补货/限购、变体父子、背部/装饰/泳装/体型 → 以「平台扩展属性包」挂载(M5)，不污染核心。
- **Temu 独有 20 键**：商品层级、基布成分、面料纹理、款式来源、系列线、模特信息、敏感品、液体/刀具/储电、SKU分类、独立包装、币种、详情视频 → 模特信息与基布/纹理建议提升为 canonical 不变属性。
- **Shein 独有 19 键**：方块图、SKU图、attr 体系(涂层/加绒/口袋/镀贵金属/下装类型/所在地/用户人群/免责/证书/尺寸大小/下摆/次要材质)、主副规格、尺寸表模板、上架方式、包装类型 → attr_<id> 必须建独立枚举源(t_value_dict)。

**缺口**：依赖 t_spu/t_canonical_product/t_category_mapping/t_value_dict 先行建表(附录A·P0)；多语言文案入 t_content_asset。

---

## 8. t_category_mapping 配置行样例

| platform | category | canonical_key | platform_field | required | transform_rule | cardinality |
|---|---|---|---|---|---|---|
| amazon | 女装 | `cp.title_base` | item_name (G) | 必填 | R-多语言(en) | 1 |
| temu | 女装上衣 | `cp.composition` | 成分1..5+比例 (G–P) | 必填 | R-成分结构化 | N=5 |
| shein | 女士一件式 | `cp.style` | attr_101 (BH) | 选填 | R-枚举字典 | 1 |
| amazon | 女装 | `cp.gallery_images` | other_product_image_locator_1..8 (R–Y) | 选填 | R-图片槽位 | N=8 |

治理：模版升级→新增 mapping 版本、旧版归档；自动发布只依赖 `status=active` 行，可血缘追溯(R1)。

**【TODO赋值】**：① t_value_dict 三套源逐值对齐；② 各平台×类目 required 清单；③ 模特/基布纹理是否升 canonical 不变属性；④ 扩品类时复用本 schema、仅扩 mapping 行。
