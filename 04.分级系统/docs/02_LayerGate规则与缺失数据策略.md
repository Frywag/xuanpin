# Layer Gate 规则与缺失数据策略

对应 `configs/layer_gates_v0.yaml` 与《07_分层分析系统组任务书》§7。

## 1. 数据流

```text
02.插件数据源/*.xlsx + 03.前端数据源/*.xlsx
  → Adapter（kalodata / tabcut / echotik / frontend_workbook）
  → SourceEnvelope[]（7 个：3 插件 + 4 前端）
  → PacketBuilder（product_id 精确/前缀合并；证据逐字段绑定）
  → CandidateDataPacket[]（2,234 个）
  → L1 全量粗筛（低成本字段，禁差评/多平台）
  → Gate 1：S/A/B 入围，无需求信号强制 C
  → L2 精调研（仅入围前 40：评价标签聚类 → 负面聚类 → 改良点 → 重打分）
  → Gate 2：S/A 终选
  → L3 多平台比对（仅前 10：多源互验 / 价格带比较 / 同风格红海 / 平台优先级）
  → AnalysisResult[] → XLSX 推荐表（8 sheet）
```

## 2. L1 Gate

| 条件 | 动作 | 实测效果 |
|---|---|---|
| 无产品 ID 且无 URL | 阻断（blocked_reasons） | 0 款（数据源均有 ID/URL） |
| 非 US 市场 | 阻断 | 0 款（P0 冻结 US） |
| 无任何需求信号 | 强制 C + 补采清单 | 图案连裤袜 534 款全部命中 |
| 价格/币种缺失 | 可继续，标记 L2 补采 | required_next_data 记录 |
| 关键字段覆盖率 <35% / <20% | 等级封顶 B / C | 防止薄数据高分 |

需求信号定义（命中任一即算有）：30 天/7 天销量、销量下限、GMV、收藏数、畅销榜标记。

## 3. L2 Gate（成本控制：上限 40 款）

- 只处理 L1 入围 S/A/B，按 pct 降序截断。
- 有 Shein 评价标签 → 确定性负面聚类（词表见 `review_tags.py`，全部来自实采词汇），
  产出 negative_review_clusters / improvement_points，并**重打分**（产品机会维刷新）。
- 无评论数据 → 不阻断，写 `required_next_data: reviews_or_review_tags` +
  固定说明「本轮前端采集未包含评论全文…」+ 降置信度。**绝不把「没采到」写成「没问题」**。

## 4. L3 Gate（成本控制：上限 10 款）

只做三类有真实证据的比较，禁止凭空找同款：

1. **多源商品 ID 互验**：PacketBuilder 已按完整 product_id 精确合并
   （OEAK：Kalodata×Tabcut），EchoTik 截断 id（源数据即截断）按 ≥12 位前缀
   唯一匹配合并（medicube），合并动作写入 field_issues 可追溯。
2. **价格带比较**：仅同品类组（dress/hosiery/intimates 确定性词表）之间比较
   组内分位数，各报各币种，不换算。
3. **同风格信号**：语料内标题风格词命中统计 → 红海程度（高≥50 / 中≥10 / 低），
   明确标注「不构成同款结论」。

平台优先级规则：视频销售额占比 ≥50% → TikTok Shop 优先；带 Shein 畅销榜 →
原平台巩固；否则维持来源平台（每条判定绑定证据）。

## 5. 缺失数据策略（missing_data_policy）

| 情形 | 处理 |
|---|---|
| 字段缺失 | 写入 `missing_fields`，绝不写 0、绝不补行业均值 |
| 解析失败 | 返回 None + envelope warnings，不猜测 |
| 价格无币种 | 视为无效字段 → field_issues + missing_fields |
| 需求信号全缺 | L1 强制 C + 补采清单 |
| 供应链维全缺 | 整维 score=None，achievable_max 扣除 20，required_next_data |
| 评论不可得 | L2 记录原因 + 降置信度，不影响入围资格 |
| 采集质量 ACCESS_CHALLENGE | 行数据保留、quality_status 降级、风险扣分 -3、S 封顶 |
| 趋势从 0 起量 | 增长率记 None + field_issue，风险 -1，防止天文数字增长率 |
| EchoTik 截断 id | 前缀唯一匹配则合并（field_issues 留痕），否则独立成包 |

## 6. 证据模型（evidence_model）

- 证据绑定到**字段**（`field_path`），不是绑定到数据源：
  `EvidenceRef{evidence_id, source_id, field_path, artifact_path, record_locator(sheet/行号)}`。
- 派生字段（如趋势增长率、视频销售额占比）标 `source_type=derived` +
  note 写明公式，confidence 降为 medium。
- `AnalysisResult.claims` 在模型层强制非空 `evidence_refs`（构造即抛错），
  推荐理由/风险理由只能由 claims 拼装 → **无证据的结论在结构上进不了 XLSX**。
- 运行时统计（价格带分位数、评论门槛代理）写入 run_meta 与运行记录 sheet，
  保证「配置定分位、数据出数值」可复查。

## 7. 安全红线（继承总纲 §10）

- 输入工作簿即为脱敏交付物（Tabcut 素材 URL 中 auth_key 已 [REDACTED]）；
- 系统不发起任何网络请求、不触发采集、不绕过任何访问挑战；
- 输出（JSON/XLSX/日志）不包含 token、cookie、Auth-Token、敏感 header；
- 端到端测试 `test_no_credentials_in_outputs` 持续校验。
