# Agent 执行提示语

> 用途：驱动 Claude Code 等 agent CLI 执行本项目。agent 打开仓库会自动读
> 根目录 `CLAUDE.md`（命令、契约、红线），以下提示语复制即用。
> 运行模式说明：确定性部分（采集→打分→分级→XLSX→选品库→任务包）一条
> 命令跑完，**不需要 agent 参与**；agent 只负责 LLM 分析环节与汇报。

---

## 1. 日常整轮执行（主提示语）

```text
执行一轮完整选品分析：
1. 读仓库根 CLAUDE.md，按其中命令运行分级管道（--out 用今天日期命名 run 目录）；
2. 管道结束后，读 <run>/llm_tasks/ 下全部任务包，按每个包的 rules 和
   output_schema 逐个执行：先做全部 deep_review，再做 profile_extraction、
   review_clustering、cross_platform_compare、reason_writer、
   style_match_report，最后做 __run__.run_report；
3. 每个结果用 llm-ingest 回灌（--model 写你的模型标识；deep_review 渲染到
   <run>/deep_reviews/，run_report 渲染到 <run>/选品分析报告.md，
   style_match_report 渲染到 <run>/多平台同款比对报告.md），
   被校验器拒绝就按错误信息修正重试，禁止绕过校验器；
4. 全部完成后用 grades 和 db-query 汇总：等级×赛道分布、S/A 清单、
   评审中不同意引擎等级的候选（grade_challenge）清单，向我汇报，
   并提醒我人审 llm_analyses 中 human_review=pending 的条目。
红线：不编造数值、结论必须绑定证据、缺数据写 missing_fields。
```

要点：步骤 1→2→3 顺序是硬的（管道先生成任务包和选品库，LLM 环节在后；
`run_report` 放最后，因为它消费整轮结果）。

## 2. 有新采集数据时（前置一步）

```text
drops/<目录> 下是新采集的数据信封。先 validate-envelope 校验，E 级错误
反馈给我；通过后在 run 命令上加 --envelopes drops/<目录> 一起分级。
（信封格式见 04.分级系统/docs/05，让采集 agent 按它产出即可免写代码接入）
```

## 3. 只查结果 / 给下游（不重跑）

```text
用 show/explain/db-query 查最新 run：给我 S 和 A 级清单及理由，
<candidate_id> 的证据链；下游对接直接给 <run>/sab_products_full.jsonl
（S/A/B 全量信息+画像+证据）或启动 mcp_server 供系统调用。
```

## 4. 调整分级标准

```text
把 configs/scoring_v0.yaml 里 <某阈值> 改成 <新值>，重跑管道到新的 run 目录，
对比前后「运行记录」sheet 的等级/赛道分布并汇报差异（S 建议 ≤ 赛道前 1%）。
```

## 5. 人工介入点（仅两处，agent 应主动提醒）

1. **人审队列**：`llm_analyses` 表中 `human_review=pending` 的条目——
   深度评审的 grade_challenge（改级建议）须人审 approved 后才生效；
2. **供应链调查**：S/A/B 终选后按 152 键画像的 manual 键（起订量/交期/
   供货稳定性/采购成本/质检通过率）人工调查存库，不参与打分。

## 6. 输入的组织方式（常见问题）

- **多表是常态**：一个数据源 = 一个工作簿（可多 sheet）或一个信封 JSON；
  多个数据源就是多个交付单元，系统自动合并；
- **单张混源大表是禁忌**：source_id 是多源互验与单源封顶规则的依据，
  混源会让这些语义失真；
- XLSX 路线列名任意（`sources_p0.yaml` 的 column_map 映射一次即可），
  必须有商品 ID 或 URL 列、价格必须能定币种、缺失留空不填 0；
- JSON 信封路线按 `docs/05` 契约，交付前用 `validate-envelope` 自检。
