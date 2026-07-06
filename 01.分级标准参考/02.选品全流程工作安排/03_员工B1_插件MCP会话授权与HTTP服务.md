# 员工 B1 任务书：其他插件 MCP 会话授权与 HTTP 服务

---

## 1. 你的定位

你负责“非 SellerSprite 插件”的会话授权、安全脱敏和 HTTP MCP 服务。

重要前提：

1. SellerSprite 卖家精灵已经实现，不是你的开发目标。
2. 你的目标是把 Helium10、Jungle Scout、Kalodata、FastMoss、EchoTik 等其他工具的登录态或账号态，做成可被多人请求访问的 HTTP MCP 服务。
3. 你不负责最终评分、不负责 XLSX 报告、不负责业务推荐理由。
4. 你只负责让服务稳定、安全、可多人调用、可被 B2 的工具层复用并测试验收。

你的核心交付可以用一句话概括：

```text
把“某个人浏览器里已经登录的插件/工具账号态”
封装成
“团队和 AI agent 可通过 HTTP 调用、但永远看不到 token/cookie 的 MCP 服务”
```

---

## 2. 目标插件范围

本轮不要重做 SellerSprite。你的目标插件从以下列表中选择，按项目主线程分配优先级执行。

| 优先级 | 插件/工具 | 平台 | 会话形态可能性 | 你的重点 |
|---|---|---|---|---|
| P0-A | Helium10 | Amazon | Web app cookie、localStorage、Chrome extension storage | 浏览器登录态读取、低风险验证 |
| P0-B | Jungle Scout | Amazon | Web app cookie、Chrome extension、API-like XHR | cookie/session 验证、HTTP 服务 |
| P0-C | Kalodata | TikTok Shop | Web app cookie、账号态接口 | session cookie、quota、region |
| P0-D | FastMoss | TikTok Shop | Web app cookie、账号态接口 | session 验证、限流 |
| P0-E | EchoTik | TikTok Shop | Chrome extension + Web app | extension storage、页面叠加接口 |

你需要为目标插件产出一个通用框架，不要每个插件写一套互不兼容的服务。

---

## 3. 已完成基线：SellerSprite 的可复用会话逻辑

以下逻辑来自已实现的 SellerSprite 基线，执行时不需要读取任何源文件。你要复用的是工程思想，而不是 SellerSprite 的常量。

### 3.1 基线流程

```text
人工登录浏览器插件
→ 服务扫描候选凭证
→ 对候选凭证去重
→ 用低风险请求验证凭证是否有效
→ 有效凭证只留在服务内存或安全存储中
→ 对外只暴露 session status，不暴露 token/cookie
→ B2 工具层通过内部 credential provider 发请求
→ 输出 collection meta，标记 credential_redacted=true
```

### 3.2 可复用源码片段：候选凭证扫描

这个片段说明了已验证的模式：优先读环境变量作为显式配置，其次扫描浏览器 profile 的插件本地存储，再从 `.log` / `.ldb` 中提取 token。

```python
EXTENSION_ID = "lnbmbgocenenhhhdojdielgnmeflbnfb"

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
```

你迁移到其他插件时，应改成通用结构：

```python
@dataclass
class CredentialCandidate:
    source_id: str
    credential_type: str
    credential_source: str
    value: str
    profile_hint: str | None = None
    expires_at: str | None = None
    scopes: list[str] = field(default_factory=list)

class CredentialDiscoverer:
    def discover(self, source_id: str) -> list[CredentialCandidate]:
        candidates = []
        candidates.extend(self.from_environment(source_id))
        candidates.extend(self.from_browser_cookies(source_id))
        candidates.extend(self.from_local_storage(source_id))
        candidates.extend(self.from_extension_storage(source_id))
        return self.deduplicate(candidates)
```

### 3.3 可复用源码片段：低风险验证

```python
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

迁移成其他插件时，你要把它变成：

```python
class CredentialValidator:
    def validate(self, candidate: CredentialCandidate, source_id: str) -> CredentialStatus:
        request = self.validation_request_for(source_id)
        try:
            response = self.http_client.send(request, candidate)
        except TimeoutError:
            return CredentialStatus(valid=False, code="TIMEOUT", retryable=True)
        except AccessBlocked:
            return CredentialStatus(valid=False, code="ACCESS_BLOCKED", retryable=False, requires_human=True)
        if response.ok and self.looks_authenticated(response):
            return CredentialStatus(valid=True, code="OK", credential_source=candidate.credential_source)
        if response.status_code in (401, 403):
            return CredentialStatus(valid=False, code="SESSION_EXPIRED", retryable=False, requires_human=True)
        return CredentialStatus(valid=False, code="UNKNOWN_VALIDATION_FAILED", retryable=False)
```

验证请求必须满足：

1. 只读。
2. 低成本。
3. 不触发购买、关注、评论、发信、改设置。
4. 返回结果足以判断是否已登录。
5. 不需要抓全量业务数据。

---

## 4. 你要实现的服务结构

### 4.1 HTTP MCP Server

你需要建立一个 HTTP 服务，对外隐藏具体插件凭证，对内为 B2 的 tool 层提供鉴权能力。

建议路径：

| 路径 | 方法 | 说明 |
|---|---|---|
| `/health` | GET | 服务健康检查 |
| `/sources` | GET | 返回已配置的插件列表 |
| `/sources/{source_id}/session/status` | GET | 返回该插件会话状态，不返回凭证 |
| `/sources/{source_id}/session/refresh` | POST | 重新扫描候选凭证并验证 |
| `/sources/{source_id}/tools` | GET | 返回 B2 注册的工具 |
| `/sources/{source_id}/tools/{tool_name}` | POST | 调用工具 |
| `/audit/runs/{run_id}` | GET | 返回某次调用的脱敏审计记录 |

`/sources/{source_id}/session/status` 响应样例：

```json
{
  "ok": true,
  "source_id": "helium10",
  "session": {
    "status": "VALID",
    "credential_source": "browser_profile",
    "credential_type": "cookie",
    "credential_redacted": true,
    "validated_at": "2026-06-26T10:00:00+08:00",
    "expires_at": null,
    "requires_human": false,
    "scopes": ["read_product", "read_keyword"]
  },
  "errors": []
}
```

过期响应：

```json
{
  "ok": false,
  "source_id": "kalodata",
  "session": {
    "status": "SESSION_EXPIRED",
    "credential_source": "browser_profile",
    "credential_redacted": true,
    "validated_at": "2026-06-26T10:00:00+08:00",
    "requires_human": true
  },
  "errors": [
    {
      "code": "SESSION_EXPIRED",
      "message": "Login session is expired. Re-login in the authorized browser profile.",
      "retryable": false,
      "requires_human": true
    }
  ]
}
```

### 4.2 服务内部模块

```text
mcp_sources/
  server.py
  credential_discovery.py
  credential_validation.py
  credential_store.py
  redaction.py
  audit_log.py
  http_client.py
  registry.py
  adapters/
    helium10.py
    junglescout.py
    kalodata.py
    fastmoss.py
    echotik.py
```

职责划分：

| 模块 | 职责 |
|---|---|
| `credential_discovery.py` | 环境变量、浏览器 cookie、localStorage、extension storage 候选凭证扫描 |
| `credential_validation.py` | 每个 source_id 的低风险验证请求 |
| `credential_store.py` | 内存或安全文件中的凭证对象，禁止明文落日志 |
| `redaction.py` | 对日志、artifact、错误、响应做脱敏 |
| `audit_log.py` | 记录 run_id、source_id、tool_name、credential_source、状态 |
| `http_client.py` | timeout、retry、限流、header 注入 |
| `registry.py` | B2 注册工具 |

---

## 5. 凭证来源设计

不同插件可能用不同登录态。你不能假设所有插件都有 token，也不能要求员工手动复制 token。

### 5.1 凭证类型

| 类型 | 说明 | 适用 |
|---|---|---|
| `cookie` | 浏览器登录 cookie | Helium10、Jungle Scout、Kalodata、FastMoss |
| `local_storage_token` | localStorage 中的 token | Web app 工具 |
| `extension_storage_token` | Chrome extension 本地存储 | EchoTik、部分浏览器插件 |
| `session_header` | 从已登录请求中提取的 header | XHR-heavy 工具 |
| `manual_authorized_export` | 人工从后台导出的只读文件 | 没有稳定接口时的过渡方案 |

### 5.2 凭证发现策略

每个 source_id 要有自己的 profile 配置：

```json
{
  "source_id": "fastmoss",
  "credential_strategies": [
    "browser_cookie",
    "local_storage",
    "manual_authorized_export"
  ],
  "validation": {
    "method": "GET",
    "url_template": "https://example.fastmoss.domain/account/profile",
    "success_signals": ["user", "plan", "quota"],
    "expired_signals": ["login", "unauthorized", "expired"]
  },
  "redaction": {
    "sensitive_keys": ["cookie", "authorization", "token", "session", "csrf"]
  }
}
```

注意：这里的 URL 只是配置格式示意。实际 endpoint 由 B2 通过授权环境抓包或页面分析后提供。

### 5.3 不同插件的验证建议

| 插件 | 低风险验证请求建议 | 成功信号 | 失败信号 |
|---|---|---|---|
| Helium10 | account/profile、quota、单关键词 preview | account id、plan、quota、keyword result | 401、login page、subscription required |
| Jungle Scout | user/profile、product database preview | user、plan、product rows | login required、403 |
| Kalodata | account info、category list、single product preview | region、quota、category data | auth expired、403、captcha |
| FastMoss | user info、trend list preview | plan、region、trend rows | login、unauthorized |
| EchoTik | extension overlay preview、video metric preview | metric rows、account info | extension inactive、login required |

---

## 6. 多客户端访问策略

HTTP MCP 的目的就是让多人和多个 AI agent 可以同时请求同一个服务。你要处理并发和隔离。

### 6.1 客户端模型

```json
{
  "client_id": "agent_d1_analysis",
  "role": "analysis",
  "allowed_sources": ["helium10", "kalodata"],
  "allowed_tools": ["product_xray", "keyword_research", "product_rank"],
  "rate_limit": {
    "requests_per_minute": 20,
    "burst": 5
  }
}
```

### 6.2 并发规则

1. 同一个 source_id 的会话验证应加锁，避免多个客户端同时刷新凭证。
2. 工具调用按 source_id + tool_name 限流。
3. 若插件平台出现 429，B1 服务必须暂停该 source_id 的调用窗口。
4. 若出现 401/403，立即标记 `SESSION_EXPIRED`，要求人工重新登录。
5. 若出现验证码或二次验证，标记 `ACCESS_CHALLENGE`，不得自动绕过。

### 6.3 返回给客户端的信息

客户端可以看到：

1. source_id。
2. tool_name。
3. run_id。
4. 调用状态。
5. 脱敏错误。
6. artifact 路径。
7. SourceEnvelope。

客户端不能看到：

1. token。
2. cookie。
3. Auth-Token。
4. Authorization header。
5. refresh token。
6. 完整浏览器 profile 路径。
7. 任何可复用的私密 header。

---

## 7. 脱敏规则

B1 必须把脱敏做成基础设施，不要依赖员工自觉。

### 7.1 敏感字段

```python
SENSITIVE_KEY_PATTERNS = [
    "token",
    "auth-token",
    "authorization",
    "cookie",
    "session",
    "refresh",
    "access_token",
    "x-api-key",
    "csrf",
    "jwt",
    "bearer"
]
```

### 7.2 脱敏函数

```python
def redact_obj(value):
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(pattern in lowered for pattern in SENSITIVE_KEY_PATTERNS):
                output[key] = "[REDACTED]"
            else:
                output[key] = redact_obj(item)
        return output
    if isinstance(value, list):
        return [redact_obj(item) for item in value]
    if isinstance(value, str) and len(value) > 120:
        return value[:12] + "...[TRUNCATED]"
    return value
```

### 7.3 日志规则

允许日志：

```json
{
  "run_id": "run_20260626_001",
  "source_id": "helium10",
  "tool_name": "keyword_research",
  "credential_source": "browser_profile",
  "credential_redacted": true,
  "status": "OK",
  "duration_ms": 942
}
```

禁止日志：

```json
{
  "cookie": "完整 cookie",
  "authorization": "Bearer 完整 token",
  "auth-token": "完整 token"
}
```

---

## 8. 你与 B2 的接口

B2 需要你提供一个 `AuthenticatedClient`，用于调用插件 endpoint。

接口建议：

```python
class AuthenticatedClient:
    def request(
        self,
        source_id: str,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        headers: dict | None = None,
        timeout: int = 30,
        run_id: str,
    ) -> dict:
        credential = self.credential_store.get_valid(source_id)
        prepared = self.inject_credential(source_id, credential, headers or {})
        response = self.raw_http(method, url, params=params, json_body=json_body, headers=prepared, timeout=timeout)
        return self.handle_response(source_id, response, run_id)
```

B2 不应该直接读取凭证。B2 只给你：

1. source_id。
2. endpoint。
3. method。
4. params。
5. body。
6. tool_name。
7. run_id。

B1 返回给 B2：

1. raw JSON。
2. status code。
3. 脱敏 headers。
4. error code。
5. rate limit 状态。

---

## 9. 你与 B2 验收职责的接口

B2 需要你提供可测试对象。

必须支持：

1. mock credential。
2. fake browser profile。
3. fake expired credential。
4. fake 401/403。
5. fake captcha/access challenge。
6. redaction scan。
7. multi-client concurrent requests。

测试辅助接口：

```text
GET /debug/redaction/self-test
POST /debug/session/mock
POST /debug/session/expire
GET /audit/runs/{run_id}
```

这些 debug 接口只允许在测试环境开启，生产环境必须关闭。

---

## 10. 你的交付物

文档交付：

1. `credential_discovery_policy.md`
2. `session_validation_policy.md`
3. `http_mcp_server_design.md`
4. `multi_client_policy.md`
5. `redaction_policy.md`
6. `b1_to_b2_authenticated_client_contract.md`
7. `b1_to_b3_test_contract.md`

代码交付：

1. HTTP MCP Server。
2. source registry。
3. credential discoverer。
4. credential validator。
5. credential store。
6. authenticated client。
7. redaction middleware。
8. audit log。
9. health/session endpoints。

样例交付：

1. 一个非 SellerSprite source_id 的 session 配置。
2. 一个有效 session status 样例。
3. 一个 expired session status 样例。
4. 一个 access challenge 样例。
5. 一个多客户端调用审计样例。

---

## 11. 验收标准

B1 通过标准：

1. 不重做 SellerSprite。
2. 至少一个非 SellerSprite 插件能完成 session discovery。
3. 至少一个非 SellerSprite 插件能完成 low-risk validation。
4. HTTP 服务可启动。
5. `/health` 可用。
6. `/sources` 可用。
7. `/sources/{source_id}/session/status` 可用。
8. 两个客户端可同时调用，不互相看到凭证。
9. B2 可通过 AuthenticatedClient 调用工具。
10. B2 可注入 mock credential 做测试。
11. 日志和响应中无明文 token/cookie/Auth-Token。
12. session 过期时能返回 `SESSION_EXPIRED`。
13. 访问挑战时能返回 `ACCESS_CHALLENGE`。

阻断项：

1. 要求人手工复制 token 到任务书或聊天窗口。
2. 在 Markdown 中粘贴完整 cookie。
3. 在日志中出现 Auth-Token。
4. B2 工具层绕过 B1 直接读凭证。
5. 出现验证码时自动绕过。
6. 服务只能本机单客户端使用，不能多人 HTTP 调用。

---

## 12. 可直接丢给大模型的执行提示词

```markdown
你是员工 B1，负责非 SellerSprite 插件 MCP 会话授权与 HTTP 服务。

重要前提：

- SellerSprite 已经实现，不要重做。
- 你的目标是 Helium10、Jungle Scout、Kalodata、FastMoss、EchoTik 等其他插件。
- 你要复用 SellerSprite 基线的工程模式：候选凭证发现、低风险验证、HTTP 服务、脱敏、多客户端。
- 不得引用本机路径，不得要求读取外部源文件。

请产出：

1. credential_discovery_policy.md
2. session_validation_policy.md
3. http_mcp_server_design.md
4. multi_client_policy.md
5. redaction_policy.md
6. b1_to_b2_authenticated_client_contract.md
7. b1_to_b3_test_contract.md
8. HTTP MCP server 最小代码或伪代码

硬性约束：

- 不输出 token/cookie/Auth-Token。
- 不要求员工手工复制 token。
- 只做只读请求。
- 验证凭证必须使用低风险 endpoint。
- 401/403 标记 SESSION_EXPIRED。
- 验证码/二次验证/风控标记 ACCESS_CHALLENGE，等待人工。
- 两个客户端必须能并发调用服务。
- B2 只能通过 AuthenticatedClient 调用，不得直接读凭证。
```
