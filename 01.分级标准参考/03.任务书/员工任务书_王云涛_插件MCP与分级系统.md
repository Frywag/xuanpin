# 员工任务书：王云涛（插件 MCP + 分级系统）

> 项目：女装跨境选品数据源采集与分层分析自动化项目  
> 角色：PM + 插件 MCP + 分级系统负责人  
> P0 截止：2026-07-03  

## 1. 你的 P0 目标

你负责把 `Kalodata + EchoTik` 两个插件源接入 P0 数据链路，并把插件样例和前端样例统一合并到分级系统中，最终输出 P0 XLSX 推荐表。

必须完成：

1. Kalodata 数据源卡。
2. EchoTik 数据源卡。
3. 每个插件至少 1 个真实 `SourceEnvelope` 样例。
4. 至少 3 个 `CandidateDataPacket`。
5. L1/L2/L3 样例分析结果。
6. P0 XLSX 推荐表。
7. P0 验收报告主稿。

## 2. 输入资料

优先阅读：

1. `立项前梳理_两人版.md`
2. `P0执行总控计划_两人版.md`
3. `TK第三方平台网址+账号.xlsx`
4. `选品全流程工作安排/00_共同知识与执行总纲_完整版.md`
5. `选品全流程工作安排/07_分层分析系统组任务书.md`

注意：`TK第三方平台网址+账号.xlsx` 包含敏感字段，只能用于授权环境核对，不得写入日志、报告、artifact。

## 3. 执行步骤

### Step 1：建立插件数据源卡模板

每个插件源一份，字段固定：

```text
source_id：
平台名称：
官网：
账号状态：
可采字段：
字段用途：
采集方式：
登录态/授权方式：
限制：
失败信号：
脱敏规则：
SourceEnvelope 样例路径：
replay fixture 路径：
```

### Step 2：Kalodata 采集验证

目标：

1. 确认能否访问商品、达人、直播、视频或热度字段。
2. 保存 raw artifact。
3. 输出 normalized artifact。
4. 输出 `SourceEnvelope`。
5. 记录失败、缺字段、权限限制。

最低可接受输出：

```json
{
  "source_id": "kalodata",
  "source_type": "plugin",
  "market": "US",
  "collection_mode": "mixed",
  "layer_hint": "L1",
  "quality_status": "VALID",
  "records": [],
  "errors": [],
  "artifact_paths": {}
}
```

### Step 3：EchoTik 采集验证

目标同 Kalodata。EchoTik 更偏早期潜力品、视频和趋势信号，字段用途要写清楚，不要把互动热度当作真实销量。

最低可接受输出：

```json
{
  "source_id": "echotik",
  "source_type": "plugin",
  "market": "US",
  "collection_mode": "mixed",
  "layer_hint": "L1",
  "quality_status": "VALID",
  "records": [],
  "errors": [],
  "artifact_paths": {}
}
```

### Step 4：构建 CandidateDataPacket

至少生成 3 个候选包。每个候选包必须包含：

1. `candidate_id`
2. `market`
3. `source_refs`
4. `basic_facts`
5. `market_metrics`
6. `competition_metrics`
7. `trend_signals`
8. `evidence_refs`
9. `missing_fields`

没有证据的字段不要写进事实，只能写进 `missing_fields`。

### Step 5：运行 L1/L2/L3 样例

P0 不要求完整评分体系成熟，但必须证明链路可用：

1. L1：低成本粗筛。
2. L2：只对入围候选做精调研。
3. L3：只对终选候选做多平台/趋势比对。

每条推荐理由必须绑定证据。

### Step 6：输出 XLSX 推荐表

建议 sheet：

1. 推荐总表
2. 评分明细
3. 数据源覆盖
4. 缺字段
5. 风险清单
6. 证据索引
7. 运行记录

## 4. 验收标准

运营验收前，你需要自查：

1. 两个插件源是否都输出数据源卡。
2. 两个插件源是否都有 `SourceEnvelope`。
3. 是否至少 3 个 `CandidateDataPacket`。
4. XLSX 是否能打开。
5. 推荐理由是否 100% 绑定 EvidenceRef 或 missing_fields。
6. 是否无明文账号密码、token、cookie、Auth-Token、完整敏感 header。

## 5. 日报

```text
日期：
插件源进展：
分级系统进展：
XLSX 进展：
产物路径：
阻塞项：
明日计划：
```

