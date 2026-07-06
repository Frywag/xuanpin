# 附录 B · 系统接口与契约

> 主纲 §3 三系统边界的落地。所有跨系统交互只能走本附录定义的契约，禁绕过。
> 每个接口给：方向 / 触发 / 入参 / 出参 / 幂等键 / 失败处理。命名 `<系统>.<动作>`。
> 三系统：**中台**（主数据/决策权威）、**ERP**（单据）、**智能平台**（Agent运行时+编排器）+ 外部**平台 API**。

---

## 0. 接口全景

```
                    ┌──────────── 数据中台 (权威) ───────────┐
                    │  B1 主数据发布   B2 主数据订阅(变更通知)  │
                    │  B3 决策数据读   B7 回写(Listing/表现)    │
                    └───▲────────▲──────────▲────────▲────────┘
          B4 单据同步 │        │ B1/B3      │ B7      │ B2
        ┌─────────────┴──┐   ┌─┴───────────┴─────────┴──┐
        │   ERP (单据)    │   │   智能平台 (运行时+编排器)   │
        └────────────────┘   │ B5 编排调度  B6 Skill执行   │
                             └──────────┬─────────────────┘
                                        │ B8 执行通道
                              ┌─────────▼──────────┐
                              │  平台API/批量表/RPA  │
                              └────────────────────┘
```

---

## B1 · 中台主数据发布（write）

| 项 | 内容 |
|---|---|
| 方向 | 任意写入方 → **中台**（唯一写入口，R1/治理铁律1） |
| 触发 | 新建/更新 CanonicalProduct、SPU、Shop、Brand、LegalEntity、映射配置 |
| 入参 | `{entity_type, payload, version, source_system, idempotency_key}` |
| 出参 | `{canonical_id/spu_id/..., version, status}` |
| 幂等 | `idempotency_key`（业务键+版本哈希）；同键重放返回首次结果 |
| 失败 | 校验失败→返回字段级错误，不落库；版本冲突→返回最新版本要求 rebase |
| 约束 | 主数据**只能**经此接口写；ERP/智能平台不得直写主数据表 |

---

## B2 · 中台主数据订阅 / 变更通知（read-stream）

| 项 | 内容 |
|---|---|
| 方向 | **中台** → ERP / 智能平台（推或拉） |
| 触发 | 主数据变更（CanonicalProduct/Shop 分级/映射配置 active 化） |
| 出参 | `{entity_type, id, version, change_type(create/update/deactivate), payload}` |
| 幂等 | 消费端按 `id+version` 去重；**单向收敛**：ERP 仅订阅、不回写主数据（R2） |
| 失败 | 至少一次投递 + 消费端幂等；积压告警 |

---

## B3 · 中台决策数据读（read）

| 项 | 内容 |
|---|---|
| 方向 | 智能平台 / 各 Agent → **中台** |
| 触发 | 选品打分、店货匹配、风险判级、维护阈值、复制决策需读数据 |
| 入参 | `{query_type, filters}`（如商品等级、店铺三维、Priority Score、规则、成本/利润） |
| 出参 | 对应决策数据 + `version`（须可血缘追溯，R1） |
| 约束 | 自动决策只可依赖 `status=active` 的规则/映射 |

---

## B4 · ERP 单据同步（read，单向收敛）

| 项 | 内容 |
|---|---|
| 方向 | **ERP** → 中台（库存实物数/采购/订单/履约状态） |
| 触发 | 单据变更（入库/出库/订单/履约） |
| 出参 | `{doc_type, sku_id, qty/status, ts, source=erp}` |
| 幂等 | `doc_id+ts` |
| 失败 | 重试 + 对账兜底 |
| 约束 | **迁移期单向收敛**：库存"实物数"权威在 ERP→同步中台；商品主数据权威在中台（D5/R2），禁双向打架 |

---

## B5 · 编排器调度（智能平台内）

| 项 | 内容 |
|---|---|
| 方向 | 编排器（M9，★待建）→ 主干/专职/评审 Agent |
| 触发 | 收到业务任务（铺货/复用/维护/选品/内容） |
| 入参 | `{task_type, context, sub_tasks}` |
| 出参 | `{task_id, status, results[]}` |
| 能力 | 任务分解 / 子 Agent 路由 / 并发控制 / 失败回滚 / 跨 Agent 记忆共享 |
| 选型 | 外挂 LangGraph 式状态机，与 app-platform 经 MCP/API 集成，不改其内核 |
| 失败 | 步骤级回滚 + 重试 + 转人工评审 |

---

## B6 · Skill 执行（6 通用操作）

| 项 | 内容 |
|---|---|
| 方向 | Agent → Skill（SubmitListing/UpdatePrice/UpdateInventory/Delist-Relist/SyncStatus/UpdateDetail） |
| 入参 | `Operation{op_type(6种), canonical_id, shop_id, payload, channel, risk_level}` |
| 出参 | `Result{op_id, status, platform_resp, listing_id}` |
| 铁律 | **确定性操作**（UpdatePrice/UpdateInventory/Delist-Relist）payload 数值来自规则引擎/映射引擎，**禁经 LLM 决定**（A4/R5） |
| 幂等 | `op_id`；同 op 重放不重复下发 |
| 失败 | 通道失败按 B8 降级；回写失败进对账队列 |

---

## B7 · 回写中台（write-back）

| 项 | 内容 |
|---|---|
| 方向 | 智能平台/执行通道 → **中台** |
| 触发 | 刊登/维护结果、Listing 镜像、内容表现数据(perf_data)、选品销售反馈 |
| 入参 | `{listing_id, op_id, status, platform_resp}` / `{asset_id, perf_data}` |
| 幂等 | `op_id` / `listing_id+ts` |
| 用途 | Listing 镜像更新；表现数据回流迭代选品权重(M1)与内容卖点(M5) |

---

## B8 · 执行通道（中台决策 → 平台落地）

| 项 | 内容 |
|---|---|
| 方向 | Skill → 平台 API / 批量上传表 / RPA(紫鸟) |
| 优先级 | **API > 批量表 > RPA**；按 `t_platform.api_type` 选通道 |
| 入参 | 经类目映射引擎转换后的平台字段（附录 E） |
| 出参 | 平台响应 → 经 B7 回写 |
| 失败 | API 失败→降级批量表；批量表失败→RPA；全失败→人工 + 告警 |
| 权限边界 | 涉及发布/提交/删除等不可逆动作，遵循发布风险等级（附录 C·§2）：人工档须人工确认 |

---

## B9 · 平台 API 接入（外部）

| 项 | 内容 |
|---|---|
| 方向 | 执行通道 ↔ 各平台（Amazon SP-API / Temu / Shein / TikTok / ...） |
| 入参/出参 | 平台原生模版字段（真值见附录 E 三平台快照） |
| 约束 | 模版升级走版本化（t_category_mapping.version）；新平台只加配置不改代码（A5） |
| 安全 | 凭证不入代码/URL；OAuth/授权类动作由人确认 |

---

## 契约通则

1. **唯一写入口**：主数据写只走 B1；决策数据写只走中台权威接口。
2. **幂等优先**：所有写接口带幂等键，支持安全重放。
3. **版本可追溯**：主数据/规则/映射变更带 version，供血缘校验（R1）。
4. **单向收敛**：ERP 与中台迁移期单向（B2 中台→ERP 主数据；B4 ERP→中台单据），禁双向（R2）。
5. **失败分级**：可逆操作自动重试/降级；不可逆操作（发布/提交/删除）按发布风险等级，必要时转人工。
