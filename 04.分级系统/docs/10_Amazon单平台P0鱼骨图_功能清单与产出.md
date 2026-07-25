# Amazon 单平台试点 P0 鱼骨图（segments.v1 主链）

## 中心问题：Amazon 单平台先跑通「L3→细分市场→L2→L1→C/B/A/S」并产出可信选品结果

### 目标结果
- 以 Amazon (US, women_dresses, 20260710 窗口) 为唯一试点任务
- 每一步有明确产出物、人工 Gate 留痕、规则版本可审计
- 多平台综合暂缓（等 Amazon 单平台验收，tiktok/shein 复制同模式）

## 主骨 1：数据入口 SOURCE_VALIDATION

### 功能介绍
- 卖家精灵插件 + Amazon.xlsx 前端报表 → 统一信封 → 候选包（身份/去重/证据/缺失登记）

### 已完成 ✅
- 两源已接入 sources_p0.yaml（sellersprite_amazon 产品总表 / amazon_frontend 商品详情）
- 字段映射：ASIN/标题/价格/星级/评分数/近30天销量(父体)，625 条入链
- 证据链 EvidenceRef 到工作簿/表/行；缺失字段照记不补 0

### P0 待搭建 🔧
- 卖家精灵富字段补挂：BSR/类目排名、首次上架日期→freshness、变体数、销量增长率
- 同 ASIN 双源合并核验（卖家精灵 × Amazon.xlsx 前端）
- 全字段长表/销量趋势 sheet 的接入决策（多周期序列是 L1 深挖的关键证据）

### 步骤产出
- envelopes/*.json、candidate_packets.jsonl、字段覆盖率统计

## 主骨 2：L3 商品硬筛 PRODUCT_HARD_SCREEN

### 功能介绍
- 销量硬性指标 PASS/REJECTED 二值，无例外通道；淘汰留审计不再消耗成本

### 已完成 ✅
- 组内销量分位硬筛（占位 p20），缺可验证销量即淘汰；本轮 625→161 通过
- 淘汰原因+规则版本落 segments_v1_results.json

### P0 待搭建 🔧
- Amazon 专属 L3 规则版本化：绝对销量线（如月销≥N）× 生命周期分档（上架时间）
- source_lifecycle_status 字段（新品/成熟期用不同销量标准）
- L3 阈值校准报告（拿 161/625 的分布给业务定线）

### 步骤产出
- 平台商品筛选表（L3_PASS/REJECTED+原因）、l3_rule_version

## 主骨 3：细分市场确认 HUMAN_SEGMENT_ASSIGNMENT

### 功能介绍
- 受控属性解析→候选细分市场→人工确认唯一 primary_segment_id（LLM 只提候选）

### 已完成 ✅
- taxonomy v1（品类词试点+风格属性标签）、唯一归属、审批队列
- pilot_auto_approve 批量确认留痕（154 自动、7 进队列，已给人工建议）

### P0 待搭建 🔧
- **taxonomy 细化（最关键）**：dress 下按 领型×袖型×长度×场景×价格带 拆真细分市场
  （当前 161 款挤在一个 seg_dress，分级无区分度）
- LLM 属性提取任务包（复用 llm_tasks 契约：白名单+校验+回灌）
- 人工审批 CLI（segment-approve 批量确认/驳回/发起 taxonomy 变更）
- 消歧规则沉淀（swim/corset/bra 等功能词自动归属性不进队列）

### 步骤产出
- ProductAttributeProfile、ProductSegmentAssignment（含审批人）、taxonomy_version+lineage

## 主骨 4：细分市场快照 + L2 初筛 SEGMENT_INITIAL_SCREEN

### 功能介绍
- 按 segment 聚合成不可变快照；对细分市场（非商品）评需求/竞争/价格/置信度

### 已完成 ✅
- 快照聚合（成员/销量分位/评分/价格带/头部集中度/覆盖率，口径标注 L3 通过数）
- L2 加权评分（草案权重）+ 入围排序

### P0 待搭建 🔧
- Amazon 专属 L2 维度接入：BSR 需求代理、评论增速、价格带竞争密度
- L2 参考总体冻结（reference_population_id，供后续标准化）
- 快照落 SQLite（当前只有 JSON 产物）

### 步骤产出
- PlatformSegmentSnapshot、L2 初评分+入围队列（l1_budget 8）

## 主骨 5：L1 深挖 SEGMENT_DEEP_DIVE

### 功能介绍
- 只对 L2 入围市场投入补采成本；PASS/PENDING_DATA/REJECTED 三态

### 已完成 ✅
- Gate 骨架：样本量≥3、头部占比≤0.7、字段覆盖率≥0.5；PENDING 不伪装低等级

### P0 待搭建 🔧
- 代表商品抽样（头部/中位/低位/新近/问题型五类）
- 定向补采任务生成（评论全文、多周期销量→接三赛道补采数据源信封）
- 需求持续性验证（销量趋势 sheet 多周期序列）
- 风险验证（品牌/IP、单品异常拉高）

### 步骤产出
- L1 Gate 结论+问题清单、补采任务包、代表商品证据包

## 主骨 6：最终分级 FINAL_SEGMENT_TIER

### 功能介绍
- 平台内细分市场 C/B/A/S；仅 L1 PASS 可 A/S；等级绑分数/分位/证据/版本

### 已完成 ✅
- 阈值分级（占位 75/55/35）+ L1 PASS 约束；本轮 seg_dress=A

### P0 待搭建 🔧
- C/B/A/S 发布人工审批 Gate（当前直接落盘，缺审批留痕）
- 等级阈值校准（需 taxonomy 细化后有多市场可比才有意义）

### 步骤产出
- 平台细分市场结果表.xlsx、PlatformSegmentEvaluation

## 主骨 7：机会入选与商品策略（单平台版）

### 功能介绍
- S/A 市场经人工入选 Gate → 拆解成员商品：开发动作×经营类型双轴

### 已完成 ✅
- OpportunitySelectionDecision（PENDING_REVIEW 待审）；策略双轴规则+测试

### P0 待搭建 🔧
- 入选审批通道（人工批准 APPROVED_FOR_PRODUCT_ANALYSIS 的操作入口）
- 策略结果表产出（商品策略候选表.xlsx：直接上新/改款×爆款型/长尾型+证据）
- 与 152 键画像衔接（入选商品补画像）

### 步骤产出
- 机会入选决策记录、商品策略候选表

## 主骨 8：支撑设施（横向）

### 已完成 ✅
- 版本冻结（rule_version/taxonomy_version/run_mode）、校准态语义、稳定阶段键
- 132 项测试、双口径查询、MCP 服务、信封直投契约

### P0 待搭建 🔧
- segments.v1 结果入 SQLite（新表）+ CLI/MCP 查询口径
- 人工 Gate 操作留痕表（改前/改后/操作者/时间）
- Amazon 单平台试点验收报告（对照总方案 §20 验收标准逐条勾验）

## 排序建议（P0 内部优先级）
- ① taxonomy 细化（主骨 3）——没有真细分市场，后面全部环节无区分度
- ② L3 阈值定线（主骨 2）——决定分析池
- ③ 人工审批 CLI + Gate 留痕（主骨 3/6/7/8）——人是最终确认者
- ④ 富字段与多周期接入（主骨 1/5）——L1 深挖的证据基础
- ⑤ 入库与验收报告（主骨 8）
