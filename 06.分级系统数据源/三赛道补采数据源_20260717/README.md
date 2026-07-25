# 三赛道补采数据源包（2026-07-17）

## 直接接入

- 分级系统入口：`三赛道补采_20260717.source_envelope.json`。
- 可将整个目录作为 `--envelopes` 输入目录；根目录仅保留一份 SourceEnvelope JSON。
- 标准化商品事实：`processed/enriched_candidate_facts.jsonl`。
- 字段级覆盖账本：`ledgers/field_coverage_ledger.jsonl`。
- 业务查看表：`workbooks/三赛道补采数据源索引_20260717.xlsx`。
- 补采后的推荐表：`workbooks/三赛道选品推荐表_补采版.xlsx`。

## 本轮覆盖

- 候选商品：1786。
- 有图片 URL：1627。
- 有可核验上架时间：93。
- 有销量证据：262。
- Amazon 连衣裙语料：425 条，趋势词：200 个。

## 缺失值口径

- JSON/JSONL：缺失字段为 `null` 或不写入，并列入 `missing_fields`。
- Excel：缺失值统一写为“暂无”。
- 收藏数、评价数不替代销量；单周期销量不推断多周期稳定性。
- 本包只增强数据事实和证据，不修改既有 SAB 分级、排序或准入判断。
