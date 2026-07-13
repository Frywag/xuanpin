# xuanpin 仓库指南（供 Claude Code / agent CLI 使用）

女装跨境选品项目：`01.分级标准参考/` 是规范，`02.插件数据源/` 与
`03.前端数据源/` 是已采集的真实数据（只读，不要修改），`04.分级系统/`
是可运行的分级系统（Python 3.10+，依赖 openpyxl + pyyaml）。

## 常用命令（在仓库根目录执行）

```bash
# 安装依赖
pip install -r 04.分级系统/requirements.txt

# 端到端分级：数据源 -> 候选包 -> L1/L2/L3 -> XLSX + SQLite 选品库 + LLM 任务包
PYTHONPATH="04.分级系统/src" python3 -m grading_system.cli run \
    --repo-root . --out "04.分级系统/data/analysis_runs/run_$(date +%Y%m%d)_001"

# 测试（62 用例，含消费真实数据的端到端集成测试）
cd 04.分级系统 && python3 -m pytest tests/ -q

# 查询选品库（SQLite：04.分级系统/data/selection.db，表结构见 store.py）
PYTHONPATH="04.分级系统/src" python3 -m grading_system.cli grades
PYTHONPATH="04.分级系统/src" python3 -m grading_system.cli db-query \
    --sql "SELECT candidate_id, grade, pct FROM results WHERE grade IN ('S','A') ORDER BY pct DESC"
PYTHONPATH="04.分级系统/src" python3 -m grading_system.cli show <candidate_id>     # 完整 packet+result JSON
PYTHONPATH="04.分级系统/src" python3 -m grading_system.cli explain <candidate_id>  # 证据链（工作簿/sheet/行）

# 外部采集数据直投（免写 Adapter；输入契约见 docs/05）
PYTHONPATH="04.分级系统/src" python3 -m grading_system.cli validate-envelope <目录或文件>
PYTHONPATH="04.分级系统/src" python3 -m grading_system.cli run --repo-root . \
    --out <run目录> --envelopes <信封目录>

# 选品库 MCP 服务（其他系统/agent 只读调用，接口契约见 docs/04 §5）
PYTHONPATH="04.分级系统/src" python3 -m grading_system.mcp_server
```

## LLM 分析任务（agent 执行分析环节的方式）

`run` 会在 `<out>/llm_tasks/` 生成五类任务包：

| 任务 | 对象 | 性质 |
|---|---|---|
| `deep_review` | S 级全部 + pct≥48 的 A 级 | **正式分析产出**：五维全量评审 + grade_challenge（可不同意引擎等级，变更需人审）+ go/hold/reject |
| `run_report` | 整轮运行（选品表产出后） | **正式分析产出**：整轮分析报告（市场格局/赛道/头部点评/数据缺口/下轮计划） |
| `review_clustering` | L2 入围款 | 差评/评价标签聚类草稿 |
| `cross_platform_compare` | L3 终选款 | 多平台比对解读草稿 |
| `reason_writer` | L3 终选款 | 理由业务化改写草稿 |

执行方式：

1. 读任务包 JSON：`inputs` 是全部可用事实，`allowed_evidence_ids` 是证据白名单，
   `output_schema` 是要求的输出结构，`rules` 必须逐条遵守；
2. 按 schema 生成**纯 JSON**结果写入文件；
3. 回灌校验入库（deep_review / run_report 建议加 --render 生成业务可读 Markdown）：
   ```bash
   PYTHONPATH="04.分级系统/src" python3 -m grading_system.cli llm-ingest \
       --bundle <out>/llm_tasks/<candidate>.deep_review.json \
       --output <你的结果.json> --model <模型标识> \
       --render <out>/deep_reviews/<candidate>.md
   ```
   校验器会拒绝：引用白名单外证据、输出输入中不存在的数值（疑似编造；
   引用 inputs 里已有的数字和候选/证据 id 是合法的）、缺少 missing_fields 声明、
   deep_review 某个 section 缺证据、run_report 提到输入之外的候选。
   被拒绝就修正后重试，不要绕过校验器。
4. 输出 JSON 归档到 `<out>/llm_outputs/`，渲染的 Markdown 放
   `<out>/deep_reviews/` 与 `<out>/选品分析报告.md`。

## 三赛道模型（tracks.v1，校准态）

自 v0.5 起分级采用三赛道独立管道（trend_new/hit_improvement/long_tail_direct）：
每候选每 run 恰好 3 条 TrackEvaluation（ELIGIBLE/PENDING_DATA/REJECTED；
PENDING/REJECTED 无正式等级），趋势新品不设销量等数据门槛，独立站按发现源
参与。所有等级带 rule_status=calibration——业务金标批准前不是正式生产等级，
待确认参数清单见 configs/tracks_v1.yaml。产物：三赛道选品推荐表.xlsx、
track_evaluations.jsonl、opportunity_clusters.json；规则见
01.分级标准参考/三赛道独立SAB分级_目标业务规则.md 与 docs/07。
旧单赛道输出保留为 legacy。本项目不含商品立项/产品定义/组合与上市回流。

## 硬性红线（对所有 agent 生效，来自项目总纲）

1. 不编造销量、搜索量、销售额、毛利、MOQ、成本等任何数值；
2. 所有结论必须绑定 evidence_refs；拿不到数据写 missing_fields，不写 0；
3. 价格必须带币种；不同币种不做换算比较；
4. 趋势/热度信号（收藏、播放、Google Trends）不能当成交事实；
5. 不修改 `02.插件数据源/`、`03.前端数据源/` 下的原始数据文件；
6. 不在任何输出中写入 token、cookie、Auth-Token、敏感 header；
7. 等级与分数由确定性引擎计算；LLM 深度评审可以在 grade_challenge 中
   不同意引擎等级并给出证据充分的理由，但等级的实际变更必须由人审确认
   （llm_analyses.human_review 从 pending 改为 approved 后生效）。

## 关键文件

| 路径 | 内容 |
|---|---|
| `04.分级系统/configs/scoring_v0.yaml` | 分级标准（权重档、分档、赛道内 S/A/B/C 阈值），改标准只改这里 |
| `04.分级系统/configs/sources_p0.yaml` | 数据源注册与列映射（新增前端源只加配置） |
| `04.分级系统/configs/layer_gates_v0.yaml` | L1/L2/L3 Gate 与成本上限 |
| `04.分级系统/configs/supply_capability.yaml` | 供应链能力档案（品类级，一次性维护） |
| `04.分级系统/schemas/*.json` | 四个数据契约的 JSON Schema |
| `04.分级系统/docs/04_数据中台对接标准_产品画像存储与MCP接口.md` | 中台建库 DDL（152 键画像 + 分级/证据表）、交换协议、MCP 接口面 |
| `04.分级系统/docs/05_分级系统输入契约_SourceEnvelope接入指南.md` | 输入格式标准：采集 agent 免代码直投分级系统 |
| `04.分级系统/docs/` | 使用指南、分级标准推导、Gate 规则、演进设计 |
| `04.分级系统/data/selection.db` | 选品库（SQLite，跨 run 累积，gitignore，可复现） |

改动代码后必须跑测试；改动分级标准后必须重跑管道并检查
「运行记录」sheet 里的等级/赛道分布是否仍合理（S 建议 ≤ 赛道前 1%）。
