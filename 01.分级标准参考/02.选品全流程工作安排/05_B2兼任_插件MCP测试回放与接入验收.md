# B2 兼任任务书：其他插件 MCP 测试回放与接入验收

---

## 1. 你的定位

你是 B2，除工具接口、字段采集和 normalizer 外，还兼任“非 SellerSprite 插件 MCP”的测试、回放、脱敏检查、多客户端验收和接入报告。

重要前提：

1. SellerSprite 已经实现，不是你的测试主目标。
2. 你要验收的是 Helium10、Jungle Scout、Kalodata、FastMoss、EchoTik 等其他插件。
3. 你负责写业务工具，也必须同步定义什么叫“可接入分析系统”。
4. 你不负责会话授权，但你必须验证 B1 没有泄漏凭证。
5. 你不负责字段 normalizer，但你必须验证 B2 的 SourceEnvelope 能被分层分析系统消费。

你的核心目标：

```text
防止“某个员工本机跑通一次”
伪装成
“团队可复用、AI 可调用、可回放、可验收的数据源”
```

---

## 2. 验收对象

本轮验收对象是非 SellerSprite 插件。

| 插件/工具 | 验收重点 |
|---|---|
| Helium10 | 关键词、Xray、Review Insights 是否稳定返回，是否能校验 SellerSprite 口径 |
| Jungle Scout | Product Database、Opportunity Finder、季节性字段是否可用 |
| Kalodata | 商品榜、达人榜、直播/视频数据是否能进入 TikTok Shop 趋势分析 |
| FastMoss | 趋势品、爆款视频、达人效率是否能结构化 |
| EchoTik | 潜力品、视频叠加指标、趋势标签是否可采 |

SellerSprite 的作用：

1. 提供已验证的测试思想。
2. 提供 meta 脱敏样例。
3. 提供 raw/normalized/summary 输出结构参考。
4. 不作为本轮主要验收对象。

---

## 3. 已完成基线的测试思想

SellerSprite 已完成基线中，测试重点不是“拿到 token”，而是：

1. 先用低风险单对象请求验证会话。
2. 再跑批量请求。
3. 输出 raw artifact。
4. 输出 summary。
5. 输出 collection meta。
6. meta 中明确 `token_redacted: true`。
7. 不把 token 写进日志和报告。

### 3.1 可复用 meta 输出片段

```python
(output_dir / "collection-meta.json").write_text(
    json.dumps(
        {
            "marketplace": args.marketplace,
            "sample_size": len(asins),
            "token_source": source,
            "token_redacted": True,
            "endpoint_families": ["quick-view", "competitor-lookup"],
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
```

迁移到其他插件时，meta 应变成：

```json
{
  "source_id": "kalodata",
  "sample_size": 100,
  "credential_source": "browser_profile",
  "credential_redacted": true,
  "endpoint_families": ["product_rank", "creator_rank"],
  "adapter_version": "0.1.0",
  "schema_version": "source.envelope.v1"
}
```

### 3.2 可复用错误处理思想

已完成基线里，一旦业务 code 不是 OK，就停止并报告清楚：

```python
if payload.get("code") != "OK":
    raise SystemExit(f"quick-view failed: {payload.get('code')} {payload.get('message')}")
```

你验收其他插件时，要要求 B2 做更细的错误结构：

```json
{
  "code": "SOURCE_API_ERROR",
  "source_status": "LIMIT_EXCEEDED",
  "message": "Quota exceeded or source returned non-success business code.",
  "retryable": false,
  "requires_human": false
}
```

---

## 4. 测试矩阵

每个非 SellerSprite 插件至少跑以下测试。

| 测试类型 | 目的 | 是否阻断 |
|---|---|---|
| 服务启动测试 | HTTP MCP Server 能启动 | 是 |
| health 测试 | `/health` 可返回 OK | 是 |
| sources 测试 | `/sources` 包含目标插件 | 是 |
| session status 测试 | 不暴露凭证，只返回状态 | 是 |
| session expired 测试 | 过期时返回 SESSION_EXPIRED | 是 |
| access challenge 测试 | 验证码/风控时等待人工 | 是 |
| tool list 测试 | 工具清单和 ToolSpec 一致 | 是 |
| tool success 测试 | 至少两个工具真实返回字段 | 是 |
| partial data 测试 | 缺字段进入 missing_fields | 是 |
| SourceEnvelope schema 测试 | 输出能被分析系统读 | 是 |
| artifact 测试 | raw/normalized/meta 都存在 | 是 |
| redaction 测试 | 无 token/cookie/Auth-Token | 是 |
| multi-client 测试 | 两个客户端可并发调用 | 是 |
| replay 测试 | 用 fixture 复现 normalizer | 是 |
| rate limit 测试 | 429 不疯狂重试 | 是 |

---

## 5. HTTP 服务测试

### 5.1 健康检查

请求：

```bash
curl -s http://localhost:PORT/health
```

期望：

```json
{
  "ok": true,
  "service": "plugin-http-mcp",
  "version": "0.1.0"
}
```

失败判定：

1. 端口无法访问。
2. 返回非 JSON。
3. 服务启动但没有版本。
4. 启动必须依赖某个人本机路径。

### 5.2 数据源列表

请求：

```bash
curl -s http://localhost:PORT/sources
```

期望：

```json
{
  "ok": true,
  "sources": [
    {
      "source_id": "helium10",
      "source_type": "plugin_mcp",
      "status": "CONFIGURED",
      "seller_sprite_baseline": false
    }
  ]
}
```

阻断项：

1. 只有 SellerSprite，没有非 SellerSprite source。
2. source_id 不稳定。
3. source_type 缺失。

### 5.3 会话状态

请求：

```bash
curl -s http://localhost:PORT/sources/helium10/session/status
```

期望：

```json
{
  "ok": true,
  "source_id": "helium10",
  "session": {
    "status": "VALID",
    "credential_source": "browser_profile",
    "credential_redacted": true,
    "requires_human": false
  },
  "errors": []
}
```

你要扫描响应里是否包含：

1. `token`
2. `cookie`
3. `authorization`
4. `auth-token`
5. `sessionid`
6. `csrf`
7. `bearer`

如果出现明文，直接阻断。

---

## 6. Tool 测试

### 6.1 工具清单

请求：

```bash
curl -s http://localhost:PORT/sources/kalodata/tools
```

期望：

```json
{
  "ok": true,
  "source_id": "kalodata",
  "tools": [
    {
      "tool_name": "kalodata_product_rank",
      "business_capability": "TikTok Shop product rank and market trend",
      "layer": ["L1"],
      "input_schema": {},
      "output_schema": {}
    },
    {
      "tool_name": "kalodata_creator_rank",
      "business_capability": "Creator commerce ranking",
      "layer": ["L2"]
    }
  ]
}
```

阻断项：

1. 没有 business_capability。
2. 没有 layer。
3. 工具名是单字段式，如 `get_price`。
4. 没有 input_schema/output_schema。

### 6.2 工具调用

请求样例：

```bash
curl -s \
  -X POST http://localhost:PORT/sources/kalodata/tools/kalodata_product_rank \
  -H 'Content-Type: application/json' \
  -d '{
    "country": "US",
    "category": "Womenswear",
    "date_range": "last_30_days",
    "limit": 20
  }'
```

期望响应：

```json
{
  "ok": true,
  "source_id": "kalodata",
  "tool_name": "kalodata_product_rank",
  "run_id": "run_20260626_001",
  "data": {},
  "source_envelope": {},
  "artifact_paths": {
    "raw": "data/raw/mcp_sources/kalodata/run_20260626_001/product_rank.json",
    "normalized": "data/normalized/mcp_sources/kalodata/run_20260626_001/product_rank.normalized.json",
    "meta": "data/raw/mcp_sources/kalodata/run_20260626_001/collection-meta.json"
  },
  "errors": [],
  "meta": {
    "credential_redacted": true
  }
}
```

你要检查：

1. `ok=true`。
2. source_id 正确。
3. tool_name 正确。
4. run_id 存在。
5. source_envelope 存在。
6. artifact_paths 存在。
7. meta.credential_redacted 为 true。
8. 无明文凭证。

---

## 7. SourceEnvelope 验收

每个工具必须输出 SourceEnvelope。

最低结构：

```json
{
  "run_id": "run_20260626_001",
  "source_id": "fastmoss",
  "source_type": "plugin_mcp",
  "tool_name": "fastmoss_trending_products",
  "retrieved_at": "2026-06-26T10:00:00+08:00",
  "query": {},
  "records": [],
  "summary": {},
  "quality": {
    "status": "VALID",
    "missing_fields": [],
    "warnings": []
  },
  "artifact_paths": {},
  "meta": {
    "adapter_version": "0.1.0",
    "schema_version": "source.envelope.v1",
    "credential_redacted": true
  }
}
```

字段检查：

| 字段 | 检查 |
|---|---|
| `run_id` | 必须稳定、可追溯 |
| `source_id` | 必须是非 SellerSprite |
| `source_type` | 必须是 `plugin_mcp` |
| `tool_name` | 必须与 ToolSpec 一致 |
| `records` | 可以为空但必须存在；为空时说明原因 |
| `quality.status` | 必须是枚举 |
| `artifact_paths.raw` | 必须存在 |
| `artifact_paths.normalized` | 必须存在 |
| `artifact_paths.meta` | 必须存在 |
| `meta.credential_redacted` | 必须是 true |

阻断项：

1. 只有 raw JSON，没有 SourceEnvelope。
2. SourceEnvelope 中 source_id 写成 sellersprite。
3. quality 缺失。
4. 缺字段不进入 missing_fields。
5. artifact_paths 缺失。

---

## 8. 脱敏扫描

你必须对以下对象做脱敏扫描：

1. HTTP 响应。
2. 服务日志。
3. raw artifact。
4. normalized artifact。
5. collection meta。
6. SourceEnvelope。
7. 验收报告。
8. 错误堆栈。

扫描关键词：

```text
token
auth-token
authorization
cookie
session
refresh
access_token
x-api-key
csrf
bearer
set-cookie
```

示例扫描脚本：

```python
SENSITIVE_PATTERNS = [
    "token", "auth-token", "authorization", "cookie", "session",
    "refresh", "access_token", "x-api-key", "csrf", "bearer", "set-cookie"
]

def scan_text(name: str, text: str) -> list[str]:
    lowered = text.lower()
    hits = []
    for pattern in SENSITIVE_PATTERNS:
        if pattern in lowered:
            hits.append(pattern)
    return hits

def assert_no_secret(name: str, text: str):
    hits = scan_text(name, text)
    allowed = {"credential_redacted", "token_redacted", "[redacted]"}
    suspicious = [hit for hit in hits if hit not in allowed]
    if suspicious:
        raise AssertionError(f"{name} may contain secrets: {suspicious}")
```

注意：不能因为文本里出现 `credential_redacted` 就判定失败，但如果出现 `Cookie: abc=...` 或 `Authorization: Bearer ...` 必须失败。

---

## 9. Fixture 与回放

B2 必须给每个 tool 提供 fixture，并用这些 fixture 做回放测试。

目录建议：

```text
tests/fixtures/mcp_sources/
  helium10/
    product_xray.success.raw.json
    product_xray.partial.raw.json
    product_xray.expired.raw.json
    product_xray.normalized.json
    product_xray.source_envelope.json
  kalodata/
    product_rank.success.raw.json
    product_rank.partial.raw.json
    product_rank.expired.raw.json
```

每个 fixture 要回答：

1. raw 响应长什么样。
2. normalizer 输出什么。
3. SourceEnvelope 输出什么。
4. 缺字段怎么表现。
5. session 过期怎么表现。

回放测试流程：

```text
读取 raw fixture
→ 调用 B2 normalizer
→ 生成 normalized record
→ 生成 SourceEnvelope
→ 校验 schema
→ 校验 missing_fields
→ 校验 no secrets
```

回放测试不应该访问真实网站。它的作用是保证 parser/normalizer 变动不会破坏已知样例。

---

## 10. 多客户端测试

HTTP MCP 服务必须支持多人调用。你要模拟两个客户端。

客户端 A：

```json
{
  "client_id": "agent_b2_tool_dev",
  "role": "tool_development"
}
```

客户端 B：

```json
{
  "client_id": "agent_d1_analysis",
  "role": "analysis"
}
```

测试步骤：

1. A 调用 tool list。
2. B 调用 session status。
3. A 调用 `product_rank`。
4. B 调用 `keyword_research` 或另一个只读工具。
5. 同时检查日志。
6. 确认两个客户端都没有看到 token/cookie。
7. 确认 rate limit 不会互相污染。

通过标准：

1. 两个客户端都成功得到业务响应或明确错误。
2. 任何客户端都不能获得凭证。
3. 同一个 run_id 不能被两个客户端混用。
4. 审计日志能区分 client_id。

---

## 11. Rate Limit 与失败测试

你要模拟：

| 情况 | 期望 |
|---|---|
| 401 | `SESSION_EXPIRED`，requires_human=true |
| 403 | `ACCESS_BLOCKED`，requires_human=true 或 false，按响应判断 |
| 429 | `RATE_LIMITED`，retryable=true，进入等待 |
| 5xx | `SOURCE_UNAVAILABLE`，retryable=true |
| captcha | `ACCESS_CHALLENGE`，requires_human=true |
| schema mismatch | `PARSE_FAILED` |
| 部分字段缺失 | `PARTIAL_SOURCE_MISSING` |

错误结构：

```json
{
  "code": "RATE_LIMITED",
  "message": "Source returned rate limit response.",
  "retryable": true,
  "requires_human": false,
  "source_status": 429
}
```

阻断项：

1. 429 时无限重试。
2. 401/403 继续用旧凭证请求。
3. captcha 时自动绕过。
4. schema mismatch 却输出 VALID。

---

## 12. 接入分析系统验收

插件 MCP 通过测试后，必须能进入分析系统。

最小接入测试：

```text
非 SellerSprite SourceEnvelope
→ CandidateDataPacket builder
→ L1 Gate
→ AnalysisResult
```

你不需要负责分析逻辑，但要验证：

1. SourceEnvelope records 至少有一个可映射 candidate_id。
2. market 为 US 或明确目标市场。
3. source_id 是非 SellerSprite。
4. metric 字段有口径。
5. evidence_refs 可以生成。
6. missing_fields 被保留。

如果 SourceEnvelope 无法进入 CandidateDataPacket，插件 MCP 不算通过。

---

## 13. 验收报告模板

```markdown
# 插件 MCP 接入验收报告

## 1. 基本信息

- source_id:
- 插件/工具名称:
- 平台:
- 负责人:
- 验收日期:
- 是否非 SellerSprite:

## 2. 服务状态

- /health:
- /sources:
- /session/status:
- 是否多客户端可用:

## 3. 工具清单

| tool_name | business_capability | layer | 是否通过 |
|---|---|---|---|

## 4. 字段返回

| tool_name | 已验证字段 | 缺字段 | 下游用途 |
|---|---|---|---|

## 5. Artifact

- raw:
- normalized:
- meta:
- SourceEnvelope:

## 6. 脱敏检查

- 响应:
- 日志:
- raw artifact:
- normalized artifact:
- meta:
- 报告:

## 7. 回放测试

- success fixture:
- partial fixture:
- expired fixture:
- replay result:

## 8. 多客户端测试

- client A:
- client B:
- 审计日志:

## 9. 分析系统接入

- CandidateDataPacket builder:
- L1 Gate:
- evidence_refs:
- missing_fields:

## 10. 阻塞项

## 11. 结论

- 通过 / 条件通过 / 不通过
```

---

## 14. 你的交付物

文档：

1. `plugin_mcp_test_plan.md`
2. `redaction_test_report.md`
3. `multi_client_test_report.md`
4. `replay_fixtures.md`
5. `source_envelope_validation_report.md`
6. `analysis_ingestion_test_report.md`
7. 每个非 SellerSprite 插件的验收报告。

测试资产：

1. success fixture。
2. partial fixture。
3. expired fixture。
4. schema mismatch fixture。
5. no secrets scan 结果。
6. multi-client 调用记录。

---

## 15. 验收标准

B2 兼任验收通过标准：

1. 没有把 SellerSprite 当作本轮主测试对象。
2. 至少一个非 SellerSprite 插件完成验收。
3. 至少两个非 SellerSprite tools 完成工具测试。
4. HTTP 服务健康检查通过。
5. session status 不泄漏凭证。
6. SourceEnvelope schema 通过。
7. raw/normalized/meta artifact 都存在。
8. collection meta 中 `credential_redacted=true`。
9. success/partial/expired fixture 都存在。
10. replay 测试通过。
11. 多客户端测试通过。
12. 分析系统最小接入测试通过。

阻断项：

1. 响应或日志出现明文 token/cookie/Auth-Token。
2. 只有本机跑通，无法 HTTP 调用。
3. 无 SourceEnvelope。
4. 无 replay fixture。
5. 无多客户端测试。
6. B2 工具直接读凭证，绕开 B1。
7. 401/403/captcha 被当成成功。
8. 缺字段被隐藏。

---

## 16. 可直接丢给大模型的执行提示词

```markdown
你是员工 B2，除插件工具接口和字段采集外，还负责非 SellerSprite 插件 MCP 测试、回放和接入验收。

重要前提：

- SellerSprite 已经实现，不要把 SellerSprite 当作本轮主测试对象。
- 本轮测试对象是 Helium10、Jungle Scout、Kalodata、FastMoss、EchoTik 等其他插件。
- 你要验证服务是否真的可多人 HTTP 调用、是否脱敏、是否有 SourceEnvelope、是否可回放、是否能进入分析系统。

请输出：

1. plugin_mcp_test_plan.md
2. redaction_test_report.md
3. multi_client_test_report.md
4. replay_fixtures.md
5. source_envelope_validation_report.md
6. analysis_ingestion_test_report.md
7. 插件 MCP 接入验收报告

必须测试：

- /health
- /sources
- /session/status
- /tools
- tool success
- tool partial
- session expired
- access challenge
- SourceEnvelope schema
- raw/normalized/meta artifacts
- no token/cookie/Auth-Token
- two clients concurrently calling
- replay from fixture
- CandidateDataPacket ingestion

硬性约束：

- 明文 token/cookie/Auth-Token 一律阻断。
- 无 SourceEnvelope 一律阻断。
- 无回放 fixture 一律阻断。
- 无多客户端测试一律阻断。
- 只有本机跑一次不算通过。
```
