# 插件 HTTP MCP 数据源组总任务书

---

## 1. 组目标

本组目标是把业务逻辑文档中列出的第三方工具和浏览器插件，封装成可被多人、多个 AI agent、多个分析流程稳定调用的 HTTP MCP 数据源。

重要前提：

1. SellerSprite 卖家精灵已经实现，不再作为本轮待开发任务。
2. SellerSprite 只作为已完成基线，用来说明“浏览器插件登录态如何服务化”“工具如何按业务能力封装”“输出如何脱敏并进入 SourceEnvelope”。
3. 本轮真正要安排开发的是其他插件和工具：Helium10、Jungle Scout、Kalodata、FastMoss、EchoTik，以及必要时的 TikTok Creative Center 官方趋势源。
4. 每个插件都要形成可长期运行的 HTTP MCP 服务，而不是临时脚本。
5. 每个插件服务内部可以包含多个 MCP tool。工具边界按业务能力或 endpoint family 划分，不按单字段机械拆分。

本组最终交付：

```text
其他插件登录态/账号态读取
→ 凭证有效性验证
→ HTTP MCP Server
→ 插件业务工具集
→ raw artifact
→ normalized artifact
→ SourceEnvelope
→ CandidateDataPacket
→ L1/L2/L3 分层分析
```

---

## 2. 数据源优先级

本轮插件开发不再做 SellerSprite。按业务价值和可落地性，推荐优先级如下。

| 优先级 | 插件/工具 | 平台 | 主要价值 | 建议负责人 |
|---|---|---|---|---|
| P0-A | Helium10 | Amazon | Black Box、Magnet、Cerebro、Xray、Review Insights，可校验 SellerSprite 口径 | B1+B2 |
| P0-B | Jungle Scout | Amazon | Product Database、Opportunity Finder、销量估算、季节性趋势 | B1+B2 |
| P0-C | Kalodata | TikTok Shop | 商品、达人、直播分析，竞品直播逐分钟挂车和出单，子类目 AOV | B1+B2 |
| P0-D | FastMoss | TikTok Shop | 趋势品、爆款视频、达人销售效率、趋势识别 | B1+B2 |
| P0-E | EchoTik | TikTok Shop | 早期潜力品识别，插件可叠加视频预估营收/互动 | B1+B2 |
| P1 | TikTok Creative Center | TikTok 内容侧 | 官方热门标签、视频、音乐、趋势词，通常不需要复杂插件登录态 | 可由轻量任务池或 C 组协作 |

推荐开发顺序：

1. 先做一个 Amazon 第二工具源：Helium10 或 Jungle Scout。
2. 再做一个 TikTok Shop 工具源：Kalodata / FastMoss / EchoTik 三选一。
3. 一周 P0 内只要求跑通一个非 SellerSprite 插件样板；第二个工具源进入下周扩展清单。
4. 等第一个非 SellerSprite 插件能输出 SourceEnvelope 后，再考虑更多工具。

为什么这样排：

1. SellerSprite 已能覆盖 Amazon 市场和竞争的主力数据，但单源估算容易产生口径偏差，所以 Amazon 第二工具源的价值是交叉校验。
2. TikTok Shop 是女装趋势强平台，SellerSprite 覆盖不了短视频、直播、达人、挂车转化，所以必须补 TikTok Shop 工具源。
3. Shein 第三方工具弱，主要靠前端采集，不是插件 MCP 组的主战场。

---

## 3. 组内分工

四人总配置下，本组固定由 2 人承担：

| 人 | 角色 | 主要责任 | 不负责 |
|---|---|---|---|
| B1 | 会话授权与 HTTP 服务 | 账号态读取、登录态验证、HTTP MCP Server、安全、脱敏、多客户端 | 字段业务解释、评分 |
| B2 | 工具接口、字段采集、测试回放与接入验收 | endpoint 发现、tool 设计、参数、字段映射、normalizer、SourceEnvelope、单测、回放、脱敏扫描、验收报告 | 服务器安全框架、评分 |

并行方式：

1. B1 先搭通通用 HTTP MCP Server、会话读取接口、脱敏日志、健康检查。
2. B2 同时根据目标插件的真实界面和网络请求建立 tool spec。
3. B2 从第一天开始写验收用例，不等开发完成才测试。
4. 一周 P0 不安排第二插件并行接入，避免四人配置下范围失控。

---

## 4. 统一架构

本组所有插件必须进入同一类结构。

```text
mcp_sources/
  server.py
  registry.py
  http_client.py
  credential_store.py
  redaction.py
  models.py
  adapters/
    sellersprite_baseline.py
    helium10.py
    junglescout.py
    kalodata.py
    fastmoss.py
    echotik.py
  normalizers/
    amazon_market.py
    tiktok_shop_market.py
    keyword.py
    creator.py
    review.py
  tests/
    fixtures/
    test_server_health.py
    test_credential_redaction.py
    test_tool_contract.py
    test_source_envelope.py
```

每个插件 adapter 至少实现：

```python
class PluginAdapter(Protocol):
    source_id: str
    source_name: str
    source_type: str = "plugin_mcp"
    adapter_version: str

    def discover_credentials(self) -> list[CredentialCandidate]:
        ...

    def validate_credential(self, candidate: CredentialCandidate) -> CredentialStatus:
        ...

    def list_tools(self) -> list[ToolSpec]:
        ...

    def call_tool(self, tool_name: str, params: dict) -> ToolResponse:
        ...

    def normalize(self, tool_name: str, raw: dict) -> SourceEnvelope:
        ...
```

HTTP MCP Server 对外提供：

| 路径 | 方法 | 用途 |
|---|---|---|
| `/health` | GET | 服务健康检查 |
| `/sources` | GET | 列出已接入插件 |
| `/sources/{source_id}/session/status` | GET | 会话状态，不返回 token/cookie |
| `/sources/{source_id}/tools` | GET | 列出工具 |
| `/sources/{source_id}/tools/{tool_name}` | POST | 调用工具 |
| `/sources/{source_id}/artifacts/{run_id}` | GET | 查看 artifact 清单 |

所有响应都要使用统一结构：

```json
{
  "ok": true,
  "source_id": "helium10",
  "tool_name": "keyword_research",
  "run_id": "run_20260626_001",
  "data": {},
  "source_envelope": {},
  "artifact_paths": {},
  "errors": [],
  "meta": {
    "credential_source": "browser_profile",
    "credential_redacted": true,
    "adapter_version": "0.1.0",
    "schema_version": "source.envelope.v1"
  }
}
```

错误响应：

```json
{
  "ok": false,
  "source_id": "kalodata",
  "tool_name": "product_trends",
  "run_id": "run_20260626_001",
  "data": null,
  "source_envelope": null,
  "artifact_paths": {},
  "errors": [
    {
      "code": "SESSION_EXPIRED",
      "message": "Login session is expired. Re-login in browser or authorized account environment.",
      "retryable": false,
      "requires_human": true
    }
  ],
  "meta": {
    "credential_redacted": true
  }
}
```

---

## 5. SellerSprite 已完成基线：内置逻辑

本节不是安排员工重做 SellerSprite，而是把已验证工程逻辑写在任务书中，供其他插件照着迁移。

### 5.1 基线流程

```text
人工登录浏览器插件
→ 服务扫描 profile / extension storage / 环境变量候选凭证
→ 候选凭证去重
→ 单 ASIN harmless 查询验证凭证
→ 批量 quick-view / competitor lookup
→ 聚合 summary
→ 输出 quick-view.json、competitor-lookup.json、summary.json、market-data-brief.md、collection-meta.json
→ collection-meta.json 标记 token_redacted=true
```

### 5.2 已验证源码片段：请求签名与 HTTP 请求

以下片段用于说明“插件请求通常需要固定参数、随机 token、鉴权 header、请求签名”。其他插件不复制 SellerSprite 的签名算法，但要在 adapter 中把这类逻辑集中封装。

```python
EXTENSION_ID = "lnbmbgocenenhhhdojdielgnmeflbnfb"
EXTENSION_VERSION = "5.0.3"
TOKEN_SEED = "500003.1364508470"
BASE_URL = "https://e.sellersprite.com"

def seller_sprite_token(text, seed=TOKEN_SEED):
    def shift(value, pattern):
        for index in range(0, len(pattern) - 2, 3):
            offset = pattern[index + 2]
            offset = ord(offset) - 87 if "a" <= offset else int(offset)
            shifted = (value % (1 << 32)) >> offset if pattern[index + 1] == "+" else (value << offset) & 0xFFFFFFFF
            value = (value + shifted) & 0xFFFFFFFF if pattern[index] == "+" else value ^ shifted
        return value

    parts = seed.split(".")
    base_value = int(parts[0]) if parts[0] else 0
    encoded = []
    for char in text:
        code = ord(char)
        if code < 128:
            encoded.append(code)
        elif code < 2048:
            encoded.extend([code >> 6 | 192, code & 63 | 128])
        else:
            encoded.extend([code >> 12 | 224, code >> 6 & 63 | 128, code & 63 | 128])

    value = base_value
    for byte in encoded:
        value = shift((value + byte) & 0xFFFFFFFF, "+-a^+6")
    value = shift(value, "+-3^+b+-f") ^ (int(parts[1]) if len(parts) > 1 and parts[1] else 0)
    if value < 0:
        value = 2147483648 + (2147483647 & value)
    result = value % 1000000
    return f"{result}.{result ^ base_value}"

def request_json(path, token, params=None, timeout=45):
    params = dict(params or {})
    params.setdefault("version", EXTENSION_VERSION)
    params.setdefault("language", "zh_CN")
    params.setdefault("extension", EXTENSION_ID)
    sign_base = params.get("asins") or params.get("asin") or params.get("q")
    if sign_base and "tk" not in params:
        params["tk"] = seller_sprite_token(str(sign_base))
    url = BASE_URL + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "Auth-Token": token,
            "Random-Token": str(uuid.uuid4()),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout, context=ssl._create_unverified_context()) as response:
        return json.loads(response.read().decode("utf-8", "replace"))
```

迁移要求：

1. 其他插件如果存在签名参数，要封装为 `sign_request()` 或 `prepare_params()`，不得散落在各工具里。
2. 其他插件如果依赖 cookie/session header，要封装为 `CredentialCandidate`，不得在工具函数中直接读本地文件。
3. 每次请求必须有 timeout。
4. 每次请求必须有 run_id 或 request_id，方便审计和回放。

### 5.3 已验证源码片段：候选凭证发现与验证

```python
def candidate_tokens(userdata):
    env_token = os.environ.get("SELLERSPRITE_AUTH_TOKEN")
    if env_token:
        yield ("env", env_token)
    for default in sorted(userdata.glob("*/Default")):
        storage = default / "Local Extension Settings" / EXTENSION_ID
        if not storage.exists():
            continue
        files = [p for p in storage.iterdir() if p.suffix in {".log", ".ldb"}]
        for path in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True):
            text = path.read_bytes().decode("utf-8", "ignore")
            for match in re.finditer(r'\{"avatar".*?"token":"([^"]+)"\}', text):
                yield (str(default), match.group(1))

def find_valid_token(userdata, marketplace, test_asin):
    seen = set()
    for source, token in candidate_tokens(userdata):
        if token in seen:
            continue
        seen.add(token)
        try:
            payload = request_json(
                f"/v2/extension/competitor-lookup/quick-view/{marketplace}",
                token,
                {
                    "asins": test_asin,
                    "source": "BEST_SELLER",
                    "miniMode": "false",
                    "withRelation": "false",
                    "withSaleTrend": "false",
                },
                timeout=20,
            )
        except Exception:
            continue
        if payload.get("code") == "OK":
            return token, source
    raise SystemExit("No valid SellerSprite token found.")
```

迁移要求：

1. `candidate_tokens()` 在其他插件中改名为 `discover_credentials()`。
2. 返回值必须包含 `credential_source`，例如 `env`、`browser_profile`、`extension_storage`、`manual_authorized_cookie`。
3. 验证函数必须使用低风险查询，不得调用会写入、购买、关注、发送消息、改配置的接口。
4. 验证成功后只返回凭证对象到内存，不写入 artifact。
5. 日志只允许出现 `credential_source`、`expires_at`、`scopes`、`redacted=true`。

### 5.4 已验证源码片段：批量采集、趋势采集和输出 meta

```python
def collect_quick_view(asins, token, marketplace, batch_size, sleep):
    result = {}
    for batch in chunks(asins, batch_size):
        payload = request_json(
            f"/v2/extension/competitor-lookup/quick-view/{marketplace}",
            token,
            {
                "asins": ",".join(batch),
                "source": "BEST_SELLER",
                "miniMode": "false",
                "withRelation": "true",
                "withSaleTrend": "true",
            },
        )
        if payload.get("code") != "OK":
            raise SystemExit(f"quick-view failed: {payload.get('code')} {payload.get('message')}")
        for item in (payload.get("data") or {}).get("items") or []:
            if item.get("asin"):
                result[item["asin"]] = item
        time.sleep(sleep)
    return result

def collect_key_trends(asins, token, marketplace, sleep):
    endpoints = {
        "sales": "/v2/extension/competitor-lookup/trend-sales",
        "bsr": "/v2/extension/competitor-lookup/trend-bsr",
        "price": "/v2/extension/competitor-lookup/trend-price",
        "review": "/v2/extension/competitor-lookup/trend-review",
        "profile": f"/v2/extension/asin/{marketplace}",
    }
    output = {}
    for asin in asins:
        output[asin] = {}
        for name, path in endpoints.items():
            params = {"station": marketplace, "asin": asin}
            if name == "profile":
                params = {"asins": asin}
            try:
                output[asin][name] = request_json(path, token, params)
            except Exception as error:
                output[asin][name] = {"error": f"{type(error).__name__}: {error}"}
            time.sleep(sleep)
    return output
```

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

迁移要求：

1. 每个工具必须支持 batch size 和 sleep/rate limit。
2. 趋势类接口必须按 key product 触发，不默认对全量候选调用。
3. 单个 item 失败不能污染整个批次，必须进入 `errors` 或 item 级 `field_issues`。
4. meta 必须写 `credential_redacted: true`。

---

## 6. 目标插件工具设计

### 6.1 Helium10

建议工具：

| Tool | 输入 | 输出 | 服务层级 |
|---|---|---|---|
| `helium10_keyword_research` | keyword / category / marketplace | search volume、keyword difficulty、related keywords、Cerebro/Magnet 关键词 | L1 |
| `helium10_product_xray` | ASIN list / category URL | sales estimate、revenue estimate、price、review、rating、BSR | L1 |
| `helium10_black_box` | category / filters | product opportunities、price band、review range、sales range | L1 |
| `helium10_review_insights` | ASIN list | negative review themes、review snippets、rating distribution | L2 |

字段重点：

1. 关键词月搜索量。
2. 关键词竞争度。
3. ASIN 估算销量/销售额。
4. Review 门槛。
5. 差评主题。
6. 价格带。
7. BSR 或类目排名。

### 6.2 Jungle Scout

建议工具：

| Tool | 输入 | 输出 | 服务层级 |
|---|---|---|---|
| `junglescout_product_database` | category / filters / marketplace | product list、sales estimate、price、reviews、brand、seller | L1 |
| `junglescout_opportunity_finder` | keyword / category | opportunity score、demand、competition、seasonality | L1 |
| `junglescout_keyword_scout` | keyword | search volume、related terms、trend | L1 |
| `junglescout_seasonality` | product / keyword | seasonal trend、demand pattern | L2 |

字段重点：

1. Opportunity Score。
2. Demand / Competition 口径。
3. 季节性。
4. 价格带。
5. 估算销量。
6. review_count。

### 6.3 Kalodata

建议工具：

| Tool | 输入 | 输出 | 服务层级 |
|---|---|---|---|
| `kalodata_product_rank` | category / marketplace / date_range | TikTok Shop 商品榜、销量/GMV、价格、类目 | L1 |
| `kalodata_creator_rank` | category / keyword / date_range | 达人榜、带货 GMV、视频数、直播数 | L2 |
| `kalodata_live_analysis` | product_id / creator_id / live_id | 直播逐分钟挂车、出单、AOV、转化 | L2/L3 |
| `kalodata_shop_product_detail` | product_id / shop_id | 商品详情、价格、评价、店铺、视频关联 | L2 |

字段重点：

1. 商品销量/GMV。
2. 达人带货能力。
3. 视频挂车转化。
4. 直播节奏。
5. 子类目 AOV。
6. 趋势上升速度。

### 6.4 FastMoss

建议工具：

| Tool | 输入 | 输出 | 服务层级 |
|---|---|---|---|
| `fastmoss_trending_products` | category / country / date_range | 趋势品、销售效率、价格、销量/GMV | L1 |
| `fastmoss_viral_videos` | keyword / category | 爆款视频、播放、互动、挂车商品 | L2 |
| `fastmoss_creator_efficiency` | creator_id / category | 每千次观看 GMV、达人效率、粉丝画像 | L2 |
| `fastmoss_product_detail` | product_id | 商品详情、素材、销量趋势、关联视频 | L2/L3 |

字段重点：

1. 趋势识别。
2. 爆款视频素材。
3. 达人销售效率。
4. 价格带。
5. 类目增长速度。

### 6.5 EchoTik

建议工具：

| Tool | 输入 | 输出 | 服务层级 |
|---|---|---|---|
| `echotik_potential_products` | category / country / date_range | 早期潜力品、增长、销量/GMV | L1 |
| `echotik_video_overlay_metrics` | video_url / video_id list | 视频预估营收、互动、挂车商品 | L2 |
| `echotik_product_detail` | product_id | 商品、店铺、视频、达人、价格 | L2 |
| `echotik_trend_watch` | keyword / tag | 趋势标签、增长速度、关联商品 | L1/L2 |

字段重点：

1. 早期潜力品。
2. 视频预估营收。
3. 商品增长速度。
4. 互动率。
5. 挂车商品。

---

## 7. SourceEnvelope 输出要求

每个插件工具都必须输出 SourceEnvelope。

```json
{
  "run_id": "run_20260626_001",
  "source_id": "helium10",
  "source_type": "plugin_mcp",
  "tool_name": "helium10_product_xray",
  "retrieved_at": "2026-06-26T10:00:00+08:00",
  "query": {
    "marketplace": "US",
    "keyword": "summer dress",
    "category": "women dresses",
    "asins": [],
    "urls": []
  },
  "records": [
    {
      "record_id": "helium10_B0XXXX",
      "product_id": "B0XXXX",
      "canonical_url": "https://www.amazon.com/dp/B0XXXX",
      "metrics": {
        "estimated_monthly_sales": 1200,
        "estimated_monthly_revenue": 36000,
        "price": "29.99",
        "currency": "USD",
        "rating": "4.4",
        "review_count": 850
      },
      "raw_ref": "raw://mcp_sources/helium10/run_20260626_001/product_xray.json",
      "evidence_refs": []
    }
  ],
  "quality": {
    "status": "VALID",
    "missing_fields": [],
    "warnings": []
  },
  "artifact_paths": {
    "raw": "data/raw/mcp_sources/helium10/run_20260626_001/product_xray.json",
    "normalized": "data/normalized/mcp_sources/helium10/run_20260626_001/product_xray.normalized.json",
    "meta": "data/raw/mcp_sources/helium10/run_20260626_001/collection-meta.json"
  },
  "meta": {
    "adapter_version": "0.1.0",
    "schema_version": "source.envelope.v1",
    "credential_source": "browser_profile",
    "credential_redacted": true
  }
}
```

字段规则：

1. 金额用字符串或 Decimal，不用 float。
2. 没有字段就写 `null` 或进入 `missing_fields`，不能写 0。
3. 估算类字段必须保留 source_id 和 tool_name，不能混成事实销量。
4. 任何推荐理由都不能直接从插件响应文本生成，必须先进入 EvidenceRef。

---

## 8. 安全与合规红线

允许：

1. 读取人工已登录、已授权账号态。
2. 只读请求。
3. 保存脱敏 raw artifact。
4. 记录 endpoint family、参数、字段、状态码。
5. 对失败请求做回放测试。

禁止：

1. 输出 token、cookie、Auth-Token、session、refresh token。
2. 自动绕过验证码、短信、二次验证、风控。
3. 自动购买、关注、私信、修改账号配置。
4. 在日志或 Markdown 中粘贴完整请求 header。
5. 将未授权付费接口提供给无权限人员。

脱敏规则：

```python
SENSITIVE_KEYS = [
    "token", "auth-token", "authorization", "cookie", "session",
    "refresh", "access_token", "x-api-key", "csrf"
]

def redact_value(key: str, value: str) -> str:
    lowered = key.lower()
    if any(pattern in lowered for pattern in SENSITIVE_KEYS):
        return "[REDACTED]"
    if isinstance(value, str) and len(value) > 80:
        return value[:12] + "...[TRUNCATED]"
    return value
```

---

## 9. B1/B2 交付清单

### 9.1 B1 交付

1. `plugin_http_mcp_server.md`
2. `credential_discovery_policy.md`
3. `session_validation_policy.md`
4. `redaction_policy.md`
5. `multi_client_http_policy.md`
6. HTTP MCP server 最小代码。
7. 至少一个非 SellerSprite 插件的 session status 接口。

### 9.2 B2 交付

1. `target_plugin_tool_map.md`
2. `endpoint_discovery_notes.md`
3. `tool_specs.md`
4. `field_mapping.md`
5. `normalization_rules.md`
6. 至少两个非 SellerSprite tools。
7. 每个 tool 的 SourceEnvelope 样例。

### 9.3 B2 兼任验收交付

1. `plugin_mcp_test_plan.md`
2. `redaction_test_report.md`
3. `multi_client_test_report.md`
4. `replay_fixtures.md`
5. `source_envelope_validation_report.md`
6. 每个非 SellerSprite 插件的验收报告。

---

## 10. 验收标准

P0 插件 MCP 组通过标准：

1. SellerSprite 被标记为已完成基线，不作为本轮开发任务。
2. 至少一个非 SellerSprite 插件 HTTP MCP 服务可启动。
3. 至少两个客户端能调用该服务。
4. 客户端不能接触 token/cookie。
5. 至少两个非 SellerSprite tools 能返回真实业务字段。
6. 每个 tool 都输出 SourceEnvelope。
7. 每个 SourceEnvelope 都有 raw artifact、normalized artifact、meta。
8. meta 中 `credential_redacted=true`。
9. 明文 token/cookie/Auth-Token 不出现在日志、报告、artifact、Markdown。
10. B2 能用 fixture 或 mock replay 复现 normalizer。

阻断项：

1. 把 SellerSprite 当作本轮待开发任务。
2. 要求员工手工复制 token。
3. 日志出现明文 cookie/token。
4. 只有临时脚本，没有 HTTP MCP 服务。
5. 只有 raw JSON，没有 SourceEnvelope。
6. 工具按单字段拆分，导致下游调用成本极高。
7. 没有多客户端测试。

---

## 11. 可直接丢给大模型的组级提示词

```markdown
你是插件 HTTP MCP 数据源组负责人。

重要前提：

- SellerSprite 卖家精灵已经实现，本轮不要重做 SellerSprite。
- SellerSprite 只作为已完成基线，用来复用登录态读取、凭证验证、endpoint 调用、脱敏、artifact、SourceEnvelope 的工程模式。
- 当前要开发的是其他插件和工具：Helium10、Jungle Scout、Kalodata、FastMoss、EchoTik，必要时包括 TikTok Creative Center。

请输出：

1. 非 SellerSprite 插件优先级和接入计划。
2. HTTP MCP Server 架构。
3. credential discovery / validation 机制。
4. 每个目标插件的 tool map。
5. SourceEnvelope 输出规范。
6. 脱敏规则。
7. B1/B2 分工，其中 B2 兼任测试回放与接入验收。
8. 测试和验收标准。

硬性要求：

- 不引用任何本机路径。
- 不要求执行者读取外部源文件。
- 所有已实现参考逻辑必须直接写在文档中。
- 不输出 token、cookie、Auth-Token。
- 工具按业务能力划分，不按单字段划分。
- 每个工具必须输出 raw artifact、normalized artifact、SourceEnvelope 和 collection meta。
- meta 必须包含 credential_redacted=true。
- 至少一个非 SellerSprite 插件要能作为 P0 开发目标。
```
