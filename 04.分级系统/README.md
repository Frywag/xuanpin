# 04.分级系统 —— 女装跨境选品分层分级系统（P0）

把仓库中两类真实数据源（`02.插件数据源/` Kalodata + Tabcut/EchoTik、
`03.前端数据源/` Shein/独立站/图案连裤袜/万圣节）统一为
`SourceEnvelope → CandidateDataPacket → L1/L2/L3 → Priority Score → S/A/B/C → XLSX 推荐表`
的可运行、可追溯选品分级链路。

- **全确定性规则引擎**：无 LLM 参与数值计算，不编造任何事实。
- **证据强绑定**：每条推荐/风险理由必须携带 EvidenceRef（模型层强制，无证据构造即抛错）；
  证据定位到 `工作簿 → sheet → 行号 → 字段`。
- **缺失即缺失**：缺字段进 `missing_fields`，不写 0、不补均值；价格必须带币种。
- **成本分层**：L1 全量 2,234 款低成本粗筛 → L2 仅入围前 40 款做差评/标签聚类 →
  L3 仅前 10 款做多平台比对。

## 快速开始

```bash
pip install -r requirements.txt   # openpyxl + pyyaml（pytest 仅测试用）

# 在仓库根目录运行端到端管道
PYTHONPATH="04.分级系统/src" python3 -m grading_system.cli run \
    --repo-root . \
    --out "04.分级系统/data/analysis_runs/run_$(date +%Y%m%d)_001"

# 运行测试（49 用例，含消费真实数据的端到端集成测试）
cd 04.分级系统 && python3 -m pytest tests/ -q
```

产物（run 目录下）：

| 产物 | 说明 |
|---|---|
| `选品推荐表.xlsx` | 8 sheet：推荐总表 / 评分明细 / 数据源覆盖 / 差评改良 / 多平台比对 / 风险清单 / 证据索引 / 运行记录 |
| `envelopes/*.json` | 7 个 SourceEnvelope（3 插件 + 4 前端） |
| `candidate_packets/*.json` | 头部候选的完整 CandidateDataPacket + AnalysisResult 样例 |
| `candidate_packets.jsonl` / `analysis_results.jsonl` | 全量 2,234 候选（体积大，不入库，可复现） |
| `run_meta.json` | 运行记录：等级分布、分位数、组内价格带统计、警告 |

## 目录结构

```text
04.分级系统/
  configs/
    sources_p0.yaml        # 数据源注册（工作簿路径、列映射、类目/季节/赛道标记）
    scoring_v0.yaml        # ★ 分级标准：赛道权重档、分档、赛道内 S/A/B/C 阈值（带数据依据注释）
    layer_gates_v0.yaml    # L1/L2/L3 Gate 与成本上限
    supply_capability.yaml # 供应链能力档案（品类级，一次性维护，enabled 后生效）
  schemas/                 # SourceEnvelope / EvidenceRef / CandidateDataPacket / AnalysisResult 的 JSON Schema
  src/grading_system/
    parsing.py             # ¥938.59万 / $565.19万 / 100+ / 11% 等真实格式解析（失败返回 None）
    models.py              # 数据契约；Claim 无证据构造即抛错
    adapters/              # kalodata / tabcut_echotik / frontend_workbook（配置驱动）
    packet_builder.py      # 多源合并（精确/前缀 product_id）、逐字段证据绑定、缺字段登记
    scoring.py             # PScore v0 引擎 + 组内运行时统计 + 风险规则 + 分箱
    review_tags.py         # L2 确定性负面标签聚类（词表来自 Shein 实采）
    cross_platform.py      # L3 多源互验 / 价格带 / 同风格红海（不凭空找同款）
    gates.py               # L1/L2/L3 编排与成本控制
    xlsx_report.py         # 8-sheet 推荐工作簿
    store.py               # SQLite 选品库（跨 run 累积，agent 可 SQL 直查）
    llm_tasks.py           # LLM 分析任务契约（任务包生成 + 回灌校验）
    pipeline.py / cli.py   # 端到端管道与命令行（run/grades/db-query/show/explain/llm-ingest）
  tests/                   # 49 用例：解析、契约、聚类、打分、Gate、端到端
  docs/
    01_分级标准_PScoreV0与SABC阈值.md   # ★ 每个阈值的数据推导
    02_LayerGate规则与缺失数据策略.md
  data/analysis_runs/      # 运行产物（XLSX、样例包、插件信封入库；全量 JSONL 不入库）
```

## 分级标准一览（详见 docs/01 与 docs/03）

`PScore = 市场需求 + 竞争可突破 + 产品机会 + 自有供应链 − 风险`，
**打分前先判定赛道**（趋势上升/痛点改良/基础常青/均衡），各赛道用不同权重档
（附录C「赛道只调权重」），S/A/B/C 在**赛道内部**按该赛道 pct 分布分箱：

| 赛道 | 权重档（需求/竞争/产品/供应链/风险下限） | 阈值 S/A/B | 实测 n | 实测 S/A |
|---|---|---|---:|---|
| 趋势上升款 | 30/15/15/20/-20 | 52/45/32 | 88 | S=3，A=9 |
| 痛点改良款 | 20/15/30/20/-15 | 48/44/35 | 51 | A=12 |
| 基础常青款 | 22/28/15/20/-10 | 45/34/20 | 394 | A=4 |
| 均衡（兜底） | 25/20/20/20/-15 | 50/45/30 | 1,701 | A=17 |

硬性条件不随赛道放松：S 必须 ≥2 源互验 + 置信度≥medium + 无未确认重大
IP/质量风险；单源候选封顶 A；覆盖率 <35%/<20% 封顶 B/C；无需求信号强制 C。

供应链维两级化（docs/03 §1）：利润结构代理（佣金/运费/价格位置，零人力，
实测 2,169/2,234 款可得）+ 品类能力档案匹配（`configs/supply_capability.yaml`
一次性维护）；逐款成本/MOQ/交期人工核实只对 L3 终选款触发。

实测 S 级全部为双插件源（Tabcut×EchoTik）按商品 ID 互验的头部商品；
OEAK 文胸（54.7%、high 置信度）因 seller_type=BRAND 侵权风险被硬性条件封顶 A——
这是分级系统在如实执行风险规则，不是漏检。

## 选品库与 Agent 集成（详见 docs/03 §3 与仓库根 CLAUDE.md）

- 每次运行写入 SQLite 选品库 `data/selection.db`（runs/envelopes/candidates/
  results/evidence/llm_analyses 六表，关键字段拉平 + 完整 JSON，跨 run 可对比）；
- CLI 子命令：`grades` / `db-query --sql`（只读）/ `show` / `explain` / `llm-ingest`；
- 运行时自动生成 LLM 分析任务包（差评聚类、多平台比对、理由改写），由
  Claude Code 等 agent 执行后经 `llm-ingest` 校验回灌（证据白名单 +
  数值封闭性校验，防编造）；LLM 产出仅为分析草稿，等级由确定性引擎决定。

## 如何扩展

1. **新增数据源**：写一个 Adapter（或复用 `frontend_workbook` 只加列映射），
   在 `configs/sources_p0.yaml` 注册即可进入管道。
2. **调整标准**：只改 `configs/scoring_v0.yaml`（分档/权重/阈值），代码零改动；
   重跑后用运行记录 sheet 的分布重新校准分箱。
3. **接入供应链输入**：向 `owned_supply_inputs` 提供人工输入后，
   supply 维启用（`supply.enabled: true`），achievable_max 恢复 85，需重校准阈值。
4. **接入评论全文**：L2 聚类会优先消费真实差评文本（当前用 Shein 评价标签聚合）。
