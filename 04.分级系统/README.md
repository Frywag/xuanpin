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
    sources_p0.yaml        # 数据源注册（工作簿路径、列映射、类目/季节标记）
    scoring_v0.yaml        # ★ 分级标准：权重、分档、S/A/B/C 阈值（全部带数据依据注释）
    layer_gates_v0.yaml    # L1/L2/L3 Gate 与成本上限
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
    pipeline.py / cli.py   # 端到端管道与命令行
  tests/                   # 49 用例：解析、契约、聚类、打分、Gate、端到端
  docs/
    01_分级标准_PScoreV0与SABC阈值.md   # ★ 每个阈值的数据推导
    02_LayerGate规则与缺失数据策略.md
  data/analysis_runs/      # 运行产物（XLSX、样例包、插件信封入库；全量 JSONL 不入库）
```

## 分级标准一览（详见 docs/01）

`PScore = 市场需求25 + 竞争可突破20 + 产品机会20 + 自有供应链20 − 风险15`；
P0 供应链维缺失 → achievable_max=65，`pct = 总分/65×100`。

在 2,234 个真实候选的 pct 分布（p50=20.0 / p90=36.9 / p99.5=50.8 / max=60.0）上校准：

| 等级 | 阈值 | 硬性条件 | 实测数量 |
|---|---|---|---:|
| S | pct≥50 | ≥2 源互验 + 置信度≥medium + 无未确认重大 IP/质量风险 | 7（0.3%） |
| A | pct≥45 | 单源候选封顶 A | 48（2.1%） |
| B | pct≥30 | 覆盖率<35% 封顶 B | 387（17.3%） |
| C | 其余 | 无需求信号强制 C；覆盖率<20% 封顶 C | 1,792（80.2%） |

实测 S 级全部为双插件源（Tabcut×EchoTik / Kalodata×Tabcut）按商品 ID 互验的头部商品；
OEAK 文胸（52.3%、high 置信度）因 seller_type=BRAND 侵权风险被硬性条件封顶 A——
这是分级系统在如实执行风险规则，不是漏检。

## 如何扩展

1. **新增数据源**：写一个 Adapter（或复用 `frontend_workbook` 只加列映射），
   在 `configs/sources_p0.yaml` 注册即可进入管道。
2. **调整标准**：只改 `configs/scoring_v0.yaml`（分档/权重/阈值），代码零改动；
   重跑后用运行记录 sheet 的分布重新校准分箱。
3. **接入供应链输入**：向 `owned_supply_inputs` 提供人工输入后，
   supply 维启用（`supply.enabled: true`），achievable_max 恢复 85，需重校准阈值。
4. **接入评论全文**：L2 聚类会优先消费真实差评文本（当前用 Shein 评价标签聚合）。
