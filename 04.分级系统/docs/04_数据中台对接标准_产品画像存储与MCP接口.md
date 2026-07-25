# 数据中台对接标准：产品画像存储结构与 MCP 接口

> 读者：数据中台/产品库同事。目的：定义选品系统侧「画像与分级结果」的
> 存储形态、建库 DDL、交换协议与 MCP 调用面，让产品库直接建表接入，
> **全链路杜绝 Excel 文件转发**。
> 本标准与《附录A_数据字典与DDL》《附录E_Canonical字段映射规格》对齐，
> 不另起炉灶；附录未覆盖的分级/证据部分在此补全。

---

## 1. 总览：三层数据模型

```text
┌─ 画像层（中台已有规划，附录A/E）────────────────────────────┐
│ CanonicalProduct 152 键（cp.* 命名空间，14 域，不变89/变63）  │
│ t_canonical_product + t_canonical_attribute + t_site_offer  │
├─ 选品分析层（本系统产出，本标准 §3 定义）───────────────────┤
│ 候选包/分级结果/证据链/LLM评审                                │
│ t_sel_candidate + t_sel_result + t_sel_evidence + t_sel_llm │
├─ 交换层（本标准 §4/§5）────────────────────────────────────┤
│ JSON 契约（schemas/*.json）+ JSONL 批量交换 + MCP 只读服务   │
└─────────────────────────────────────────────────────────────┘
```

关键立场：**选品系统不重复定义 152 键**。画像键注册表的唯一事实来源是
《附录E》（cp.* 命名、D1-D14 域、M1-M6 决策）；选品系统按 §2 的映射把
自己的字段写入画像层，把分级/证据写入 §3 的选品分析层。

## 2. 画像层：152 键的存储形态（对齐附录A）

### 2.1 双形态存储（附录A 已定，此处明确类型与序列化规则）

152 键采用「**JSON 主档 + EAV 检索镜像**」双形态，两者由中台侧同步任务
保持一致（主档为写入口，镜像由触发器/ETL 派生）：

**形态一：JSON 主档**（读整档快、版本化容易）

```sql
-- MySQL 8.0+ / PostgreSQL 14+（JSON 列在 PG 用 JSONB）
CREATE TABLE t_canonical_product (
  canonical_id     VARCHAR(64)  PRIMARY KEY,   -- 生成规则见 §2.3
  spu_id           VARCHAR(64)  NULL,          -- 建 SPU 后回填
  title_base       JSON         NOT NULL,      -- 多语言 Map {"en":..,"zh":..}
  description      JSON         NULL,
  brand_id         VARCHAR(64)  NULL,
  category_canonical VARCHAR(128) NULL,        -- 平台无关类目
  attrs_json       JSON         NOT NULL,      -- 不变属性 89 键，按域分组，见 §2.2
  variant_axes     JSON         NULL,          -- 规格轴 {"color":[..],"size":[..]}
  version          INT          NOT NULL DEFAULT 1,
  source_system    VARCHAR(32)  NOT NULL,      -- 'selection_system' / 'erp' / ...
  created_at       DATETIME     NOT NULL,
  updated_at       DATETIME     NOT NULL
);

CREATE TABLE t_site_offer (                    -- 变域 63 键按 SiteScope 实例化
  offer_id         VARCHAR(64)  PRIMARY KEY,
  canonical_id     VARCHAR(64)  NOT NULL REFERENCES t_canonical_product,
  platform         VARCHAR(32)  NOT NULL,      -- amazon/temu/shein/tiktok_shop/...
  shop_id          VARCHAR(64)  NULL,
  site             VARCHAR(16)  NOT NULL,      -- US/EU/...
  price            DECIMAL(12,2) NULL,
  currency         CHAR(3)      NULL,          -- 价格存在则币种必填（CHECK 约束）
  sale_price       DECIMAL(12,2) NULL,
  quantity         INT          NULL,          -- null=未知，绝不写 0 充数
  offer_attrs      JSON         NULL,          -- 其余变域键（D11/D12/D14 的变项）
  source_system    VARCHAR(32)  NOT NULL,
  created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
  CONSTRAINT chk_price_currency CHECK (price IS NULL OR currency IS NOT NULL)
);
```

**形态二：EAV 检索镜像**（单键筛选/聚合快，属性运营友好）

```sql
CREATE TABLE t_canonical_attribute (
  attr_id        BIGINT       PRIMARY KEY AUTO_INCREMENT,
  canonical_id   VARCHAR(64)  NOT NULL REFERENCES t_canonical_product,
  attr_key       VARCHAR(64)  NOT NULL,   -- cp.* 152 键之一（附录E 注册表）
  attr_value     TEXT         NULL,       -- 统一转字符串；结构值存 JSON 文本
  value_num      DECIMAL(18,4) NULL,      -- number 型冗余一列，便于范围查询
  unit           VARCHAR(16)  NULL,       -- SI 归一：g/m²、g、cm
  value_dict_id  VARCHAR(64)  NULL,       -- 枚举走 t_value_dict（附录A §3）
  type           ENUM('text','enum','number','bool','media','composition','money'),
  evidence_id    VARCHAR(64)  NULL,       -- ★ 选品系统补充：溯源到 t_sel_evidence
  UNIQUE KEY uk_attr (canonical_id, attr_key),
  KEY idx_key_value (attr_key, value_num)
);
```

### 2.2 attrs_json 的域分组序列化规则

`attrs_json` 顶层键 = 附录E 的 14 个域（D1..D14），域内键用 cp.* 全名，
未采集到的键**不出现**（缺失 ≠ 空串 ≠ 0）：

```json
{
  "D5_material": {"cp.fabric_composition": [{"material": "nylon", "pct": 82}],
                   "cp.fabric_weight_gsm": 180},
  "D6_style":    {"cp.style": "casual", "cp.pattern": "floral",
                   "cp.season": ["spring", "summer"]},
  "D8_variant":  {"cp.color_family": "green", "cp.size_system": "US"}
}
```

类型规则：金额一律 `{"amount": 11.79, "currency": "USD"}`；比例存小数
（0.403 而非 40.3%）；多语言文本存 Map；日期 ISO-8601；布尔不缺省。

### 2.3 主键与幂等

| 键 | 生成规则 | 说明 |
|---|---|---|
| `canonical_id` | `cp_{sha1(platform + primary_product_id)[:16]}`；无稳定平台 ID 时用 `cp_{sha1(canonical_url)[:16]}` | 同一平台商品重复接入必须落到同一行（幂等 upsert 依据） |
| `offer_id` | `of_{sha1(canonical_id + platform + shop_id + site)[:16]}` | SiteScope 唯一 |
| 与选品侧关联 | `t_sel_candidate.canonical_id` 外键 | 见 §3 |

## 3. 选品分析层：分级结果与证据链（本系统新增，附录A 缺失部分）

> 附录A 的 `t_spu.grade` 与 `t_priority_score` 只存最终等级/分数；
> 选品系统还产出**证据链与 LLM 评审**——这是「结论可追溯」红线的载体，
> 必须随画像一起入库。表结构与 selection.db（SQLite 参考实现，
> `src/grading_system/store.py`）逐列对应，中台按下述 DDL 建正式库：

```sql
CREATE TABLE t_sel_run (                 -- 一次分级运行
  run_id        VARCHAR(64) PRIMARY KEY,
  started_at DATETIME, finished_at DATETIME,
  market        VARCHAR(8),
  candidates_total INT, l2_processed INT, l3_processed INT,
  grade_distribution JSON, track_distribution JSON,
  config_json   JSON                     -- 分级标准快照（scoring_v0.yaml 内容）
);

CREATE TABLE t_sel_candidate (           -- CandidateDataPacket
  candidate_id  VARCHAR(128), run_id VARCHAR(64),
  canonical_id  VARCHAR(64) NULL,        -- 对齐画像层（§2.3 规则生成）
  platform      VARCHAR(32), source_group VARCHAR(64),
  title TEXT, category VARCHAR(128),
  price_amount DECIMAL(12,2), price_currency CHAR(3),
  rating DECIMAL(3,2), review_count INT,
  sales_30d INT, sales_floor INT, favorites INT, growth_rate DECIMAL(8,4),
  source_count INT, quality_status VARCHAR(32), missing_count INT,
  packet_json   JSON,                    -- 完整 CandidateDataPacket（契约见 §4）
  PRIMARY KEY (candidate_id, run_id)
);

CREATE TABLE t_sel_result (              -- AnalysisResult
  candidate_id  VARCHAR(128), run_id VARCHAR(64),
  track VARCHAR(32), grade ENUM('S','A','B','C'),
  pct DECIMAL(5,1), total DECIMAL(6,1), confidence VARCHAR(8),
  decision VARCHAR(32),                  -- recommend_research/research_more/watchlist/drop/blocked
  l1_grade CHAR(1), l2_grade CHAR(1), l3_grade CHAR(1),
  result_json   JSON,
  PRIMARY KEY (candidate_id, run_id),
  KEY idx_grade (run_id, grade, pct)
);

CREATE TABLE t_sel_evidence (            -- EvidenceRef：字段级证据链
  evidence_id   VARCHAR(64), run_id VARCHAR(64),
  candidate_id  VARCHAR(128),
  source_id     VARCHAR(64),             -- kalodata/tabcut/echotik/shein_frontend/...
  field_path    VARCHAR(128),            -- 如 market_metrics.sales_30d_units
  artifact_path VARCHAR(512),            -- 原始数据文件（对象存储 URI 或仓库路径）
  record_locator VARCHAR(128),           -- sheet=xxx;row=N
  confidence    VARCHAR(8),
  PRIMARY KEY (evidence_id, run_id),
  KEY idx_cand (run_id, candidate_id)
);

CREATE TABLE t_sel_llm_analysis (        -- LLM 深度评审 / 整轮报告
  candidate_id VARCHAR(128), run_id VARCHAR(64),
  task_type    VARCHAR(32),              -- deep_review/run_report/review_clustering/...
  model        VARCHAR(64), ingested_at DATETIME,
  human_review ENUM('pending','approved','rejected') DEFAULT 'pending',
  analysis_json JSON,                    -- 含 grade_challenge / go_recommendation
  PRIMARY KEY (candidate_id, run_id, task_type)
);
```

**写入语义**：同一 `(candidate_id, run_id)` 重推为整行 REPLACE（幂等）；
跨 run 追加不覆盖——历史运行永远可查，等级变化 = 两个 run 的 diff。
**等级变更**：`t_sel_result.grade` 只由分级引擎写入；LLM 的改级建议存在
`t_sel_llm_analysis.analysis_json.grade_challenge`，人审通过
（human_review='approved'）后由**人工流程**更新 `t_spu.grade`，写操作留审计。

## 4. 交换协议（选品系统 -> 中台）

| 项 | 约定 |
|---|---|
| 载体 | JSONL 批量文件或直连写库；**禁止 Excel 作为系统间载体**（XLSX 只是给人看的报表） |
| 契约 | `04.分级系统/schemas/` 四个 JSON Schema：source_envelope / candidate_data_packet / analysis_result / evidence_ref，版本号在文件内（`*.v1`），破坏性变更升 v2 并双写过渡 |
| 频率 | 每次 run 结束推送一批：candidates + results + evidence + llm_analyses 四个 JSONL |
| 幂等键 | `(candidate_id, run_id)`；`evidence_id` 全局唯一（`ev_{source}_{seq}`） |
| 缺失语义 | 字段缺失 = JSON 中不出现该键或为 null，**下游禁止把 null 当 0**；缺失清单在 `packet_json.missing_fields` |
| 金额 | 一律 `{amount, currency}`，无币种的金额是脏数据，拒收 |
| 安全 | 载荷中不得出现 token/cookie/Auth-Token（上游已脱敏，中台入库前可再扫一遍） |
| 画像回写 | 选品候选建 SPU 后，中台把 `spu_id/canonical_id` 回写 `t_sel_candidate.canonical_id`，打通「情报 -> 主数据」链路 |

## 5. MCP 调用面（已实现，可直接联调）

选品库自带零依赖 MCP 服务（stdio 传输，JSON-RPC 2.0）：

```bash
PYTHONPATH="04.分级系统/src" python3 -m grading_system.mcp_server \
    --db 04.分级系统/data/selection.db
```

| 工具 | 参数 | 返回 |
|---|---|---|
| `list_runs` | — | 全部运行及其等级/赛道分布 |
| `query_grades` | run_id? | 等级×赛道分布（缺省最新 run） |
| `get_candidate` | candidate_id, run_id? | 完整 packet + result JSON |
| `get_evidence_chain` | candidate_id, run_id? | 字段级证据链（工作簿/sheet/行） |
| `get_llm_analysis` | candidate_id, task_type?, run_id? | 深度评审/整轮报告（`__run__` 取报告） |
| `db_query` | sql, limit? | 只读 SQL（写语句被拒） |

Claude Code / 其他 MCP 客户端挂载配置见 `mcp_server.py` 文件头注释。
中台若用自有服务框架重新实现，保持**工具名与出入参不变**即可对下游透明。

## 6. 中台接入清单（可直接排期）

1. 按 §2 建画像层三表（若附录A 已建，仅需在 `t_canonical_attribute`
   加 `evidence_id` 列）；
2. 按 §3 建选品分析层五表；
3. 建 JSONL 接收任务（幂等 REPLACE，按 §4 键）；
4. 联调：用仓库现成的 `run_20260707_001` 产物 + `selection.db` 做一次
   全量导入演练（`db_query` 抽查 evidence 与 result 的关联完整性）；
5. 把 MCP 服务注册进内部网关（或按 §5 契约自行实现）。
```
