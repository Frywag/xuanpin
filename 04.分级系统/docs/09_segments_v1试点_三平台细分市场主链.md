# segments.v1 试点：三平台独立细分市场主链（2026-07-24 总方案落地）

> 依据：《最新选品总方案：三平台独立分级、细分市场聚类与产品策略拆解》。
> 状态：试点（P0~P3 骨架 + P4/P5 结构位），rule_status=calibration。
> 配置：configs/segments_v1.yaml（阈值全部占位草案，人工改即生效）。

## 1. 可行性结论

方案整体可行，与现有底座（信封/证据链/组内分位/校准语义）兼容。已落地：
三平台隔离运行（§4）、L3 商品销量硬筛无例外通道（§5）、受控属性解析+唯一
primary_segment_id+人工确认流（§6，含 pilot_auto_approve 试点批量确认开关，
默认开、留痕 approver=pilot_config_batch、正式生产须关闭走审批队列）、
细分市场快照与 L2/L1/C/B/A/S（评价对象=细分市场，主键无 track_id，仅
L1 PASS 可 A/S、PENDING 不伪装低等级）、跨平台范围齐套 Gate（§12.1）、
机会入选 Gate（S/A 生成 PENDING_REVIEW 决策，人工批准后才进商品策略）、
商品策略双轴（开发动作×经营类型，§14.2）、独立站全部剔出主链进趋势体系。

## 2. 现实约束（诚实登记，不绕过）

- **Amazon 平台数据缺失**：任务=RUN_PENDING，比较范围=INCOMPLETE_SCOPE，
  按 §12.1 不发布正式聚类优先级与平台优势标签，只并列展示 tiktok/shein 事实；
- **跨平台校准未建锚点**：calibration_status=NOT_CALIBRATED（§13.4）；
- **人工 Gate 待配岗**：细分市场审批队列已产出（review_queue），机会入选
  决策全部 PENDING_REVIEW 待人审。

## 3. 首轮试点（run_20260724_001，2,234 候选）

| 平台 | L3 通过 | 细分市场 | 等级 | 待人工确认 |
|---|---:|---:|---|---:|
| amazon | — | — | RUN_PENDING（数据源待接入） | — |
| tiktok | 28 | 3 | B×1（其余 L1 PENDING_DATA） | 21 |
| shein | 112 | 1 | A×1（seg_dress，待机会入选审批） | 10 |

独立站等 1,988 条剔出主链进趋势体系（趋势词表 ABCDE 照常产出）。
机会入选待审 1 项。产物：segments_v1_results.json、平台细分市场结果表.xlsx
（各平台细分市场/L3与队列/跨平台范围状态）。

## 4. 与旧链关系及下一步

tracks.v2 与 legacy 本轮起为过渡对照（新主链验收后按 P6 切换/下线，业务
拍板）。下一步按方案分期：接入 Amazon 源→补 P4 标准化聚类与校准锚点→
机会审批后启用 P5 商品策略拆解→taxonomy 细化（人群/场景/价格带）。
待确认清单见 segments_v1.yaml pending_business_confirmation。
