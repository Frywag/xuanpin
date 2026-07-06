# 女装跨境选品全流程 SOP 鱼骨图

## 中心问题：把多源数据稳定转成可追溯选品推荐

### 目标结果
- 输出一套可持续运行的选品 SOP，而不是一次性报告
- 数据源覆盖插件数据、网页前端数据、趋势内容、自有供应链输入
- 所有数据先进入 SourceEnvelope 或 FrontendSourceEnvelope
- 多源数据合并为 CandidateDataPacket
- 分层执行 L1 粗筛、L2 精调研、L3 多平台比对
- 最终输出 Priority Score、S/A/B/C 分级、推荐理由、风险、证据索引、XLSX 推荐表

### 当前原则
- SellerSprite / 卖家精灵作为已完成基线，不重复开发
- 爬虫 MCP 不建议做成公网 MCP，优先改为本地 Skill + 本地服务 + 浏览器插件方案
- 所有登录态和账号态只在授权本机或授权浏览器中使用
- 不输出 token、cookie、Auth-Token、session、完整请求 header
- 遇验证码、登录墙、Cloudflare、设备校验异常时暂停并记录，不绕过
- 没有 raw evidence 或 EvidenceRef 的数据不得进入推荐结论

## 主骨 1：已完成能力沉淀

### 网站爬虫通用 Skill
- 状态：已完成第一版
- 定位：替代不适合公网部署的爬虫 MCP
- 已具备能力
  - 浏览器或网页采集流程封装
  - 站点适配思路沉淀
  - 采集任务 SOP 化
  - 可作为后续 TikTok Shop、Shein、趋势内容采集的基础
- 后续需调整
  - 稳定性
  - 失败重试
  - 断点续采
  - 采集日志
  - 输出字段标准化
  - 验收样例沉淀

### 插件 MCP 封装 Skill
- 状态：已完成第一版
- 定位：把插件账号态数据采集封装为可复用工作流
- 已具备能力
  - 复用 SellerSprite 基线经验
  - 支持插件登录态读取和验证思路
  - 支持工具接口、字段映射、脱敏、SourceEnvelope 输出的 SOP
- 后续需调整
  - 非 SellerSprite 插件适配稳定性
  - 多客户端调用边界
  - 回放测试
  - 脱敏扫描
  - 插件工具清单和字段覆盖表

### 知衣抓取
- 状态：已完成可用版本
- 当前架构
  - Chrome 插件监听页面 fetch 和 XHR 请求
  - 页面内采集面板控制采集、暂停、重置、导出
  - 本地 Node 接收服务保存接口记录
  - 本地 Excel 导出多 sheet 结果
- 已完成模块
  - 商品中心列表接口捕获
  - 商品详情销售、趋势、SKU、评论、相似款、图搜接口采集
  - 社区流行 INS 列表采集
  - Excel 导出安全处理
- 当前限制
  - INS 接口有设备校验，必须先由页面成功触发一次请求
  - 批量页数增大后仍需节流、重试、断点续采
  - Pinterest 仅识别到历史接口，尚未正式模块化
- 后续动作
  - 稳定 INS 分页批量采集
  - 增加 Pinterest 模块
  - 增加采集页数、模块、延迟等任务配置
  - 增加断点续采
  - 优化社区 INS 和 Pinterest 中文字段说明

## 主骨 2：未完成任务池

### 爬虫 MCP
- 当前判断：不建议继续做公网 MCP
- 原因
  - 浏览器登录态、设备上下文、验证码和风控不适合公网服务化
  - 部分站点依赖真实浏览器环境
  - 公开 MCP 服务会放大账号态和凭证风险
- 替代方案
  - 本地 Skill
  - 本地接收服务
  - 浏览器插件
  - 本地 raw evidence
  - 离线 replay
- 验收标准
  - 同一 SOP 可被不同站点复用
  - 能保存原始证据包
  - 能输出 FrontendSourceEnvelope
  - 能在访问挑战时暂停并保存 checkpoint

### TK 插件内容爬取
- 目标范围
  - TikTok Shop 商品榜
  - 商品详情
  - 视频和达人信号
  - 直播或挂车数据
  - 评论或互动数据
- 推荐数据源
  - Kalodata
  - FastMoss
  - EchoTik
  - TikTok Creative Center
- 输出要求
  - 商品、达人、视频、直播维度字段清单
  - 至少一个 TikTok 数据源真实样例
  - SourceEnvelope
  - 数据源卡
  - 字段覆盖率和缺字段说明

### 非浏览器插件内容爬取
- 当前限制：只有网页端，无插件登录态可复用
- 目标范围
  - Shein
  - Zara
  - H&M
  - Amazon 前台未完成页面
  - 独立站或快时尚标杆站点
- 采集方式
  - CDP 浏览器采集
  - HTTP + DOM / JSON-LD fallback
  - 本地 raw evidence 保存
  - SnapshotStore 快照
  - replay 测试
- 输出要求
  - 至少 10 个商品详情 raw evidence
  - 至少 3 个评论、差评或互动样例，拿不到则记录原因
  - FrontendSourceEnvelope
  - 验收工作簿

### 趋势内容爬取
- 目标范围
  - Google Trends
  - AnySearch
  - Pinterest
  - Instagram
  - TikTok Creative Center
  - 小红书或抖音趋势内容，按合规可得性决定
- 使用边界
  - 趋势信号不能当销量
  - 搜索热度不能当销售额
  - 内容互动只能作为 trend_signals
- 输出要求
  - 趋势词计划
  - 趋势信号字段标准
  - trend SourceEnvelope
  - 进入 CandidateDataPacket.market_metrics.trend_signals

### 分级系统制作
- 目标输出
  - CandidateDataPacket
  - L1/L2/L3 Gate
  - Priority Score v0
  - S/A/B/C 分级
  - EvidenceRef
  - AnalysisResult
  - XLSX 推荐表
- 核心规则
  - L1 全量低成本粗筛
  - L2 只对入围款做差评和精调研
  - L3 只对终选款做多平台比对
  - 缺字段必须进入 missing_fields
  - 推荐理由必须绑定 evidence_refs

## 主骨 3：三人任务分配

### 人员 A：数据源与插件 / Skill 负责人
- 负责范围
  - 插件 MCP 封装 Skill 继续稳定
  - 爬虫 MCP 替代方案落地为本地 Skill SOP
  - TK 插件内容爬取的数据源接入
  - 非 SellerSprite 插件或网页工具的数据源卡
- 重点任务
  - 梳理 Kalodata / FastMoss / EchoTik / TikTok Creative Center 优先级
  - 选择 1 个 TikTok 数据源作为 P0 样板
  - 设计 tool map 和字段映射
  - 输出 SourceEnvelope 样例
  - 做凭证和日志脱敏检查
  - 建立 replay fixture
- 交付物
  - `data_source_priority.md`
  - `plugin_skill_stability_plan.md`
  - `tiktok_tool_map.md`
  - `source_envelope_examples.md`
  - `redaction_check_report.md`
  - `replay_fixture_report.md`
- 验收标准
  - 至少 1 个 TikTok 或非 SellerSprite 数据源能返回真实业务字段
  - 至少 2 个工具或页面输出 SourceEnvelope
  - artifact、日志、报告中无明文 token/cookie/Auth-Token
  - 有失败样例和回放样例

### 人员 B：网页前端采集与内容趋势负责人
- 负责范围
  - 网站爬虫通用 Skill 稳定性调整
  - 非浏览器插件网页端采集
  - 趋势内容采集
  - 知衣 INS / Pinterest 后续稳定化
- 重点任务
  - 选择 Shein 或 TikTok Shop 前端站点作为 P0 样板
  - 建立 discover / fetch_product / normalize / normalize_offer / fetch_reviews 流程
  - 保存 raw evidence 和商品级 manifest
  - 建立 checkpoint、challenge、rate limit 规则
  - 输出 FrontendSourceEnvelope
  - 将趋势内容按 trend_signals 输出
- 交付物
  - `frontend_collection_sop.md`
  - `target_site_profile.md`
  - `raw_evidence_manifest_spec.md`
  - `checkpoint_challenge_policy.md`
  - `trend_collection_plan.md`
  - `frontend_acceptance_workbook.xlsx`
- 验收标准
  - 至少 10 个商品详情原始证据包
  - 至少 3 个评论、差评、互动或明确失败原因
  - 每个关键字段有 EvidenceRef 或 missing_fields
  - 遇验证码、登录墙、设备校验异常时能暂停并记录
  - 工作簿可人工核查

### 人员 C：分级分析与交付验收负责人
- 负责范围
  - CandidateDataPacket
  - L1/L2/L3 Gate
  - Priority Score
  - S/A/B/C 分级
  - XLSX 推荐表
  - 三条主线验收汇总
- 重点任务
  - 定义 CandidateDataPacket v0 schema
  - 定义 SourceEnvelope 到 CandidateDataPacket 的合并规则
  - 建立 L1、L2、L3 Gate
  - 建立 missing data policy
  - 建立 evidence model
  - 设计 xlsx_report_spec
- 交付物
  - `candidate_data_packet_schema.md`
  - `layer_gate_rules.md`
  - `priority_score_v0.md`
  - `evidence_model.md`
  - `missing_data_policy.md`
  - `xlsx_report_spec.md`
  - `p0_acceptance_report.md`
- 验收标准
  - 能消费 A、B 两组的真实样例
  - 能生成至少 3 个 CandidateDataPacket
  - 能输出 L1/L2/L3 样例 AnalysisResult
  - 推荐理由全部有 evidence_refs
  - XLSX 包含推荐总表、评分明细、数据源覆盖、差评改良、多平台比对、风险清单、证据索引、运行记录

## 主骨 4：标准 SOP 流程

### Step 1：范围冻结
- 输入
  - 当前工作安排文档
  - 已完成 Skill 和知衣进度
  - 本周人员数量：3 人
- 动作
  - 确认 P0 数据源
  - 确认一个 TikTok 数据源
  - 确认一个前端网页站点
  - 确认 CandidateDataPacket v0
  - 确认采用 layered / full_then_analyze / mixed 采集模式
- 输出
  - P0 范围表
  - 数据源优先级
  - 三人任务看板
- 验收
  - 每个任务有负责人、输入、输出、验收标准

### Step 2：数据源采集
- 输入
  - 插件或网页授权环境
  - 目标页面或工具页面
  - 字段清单
- 动作
  - 捕获或请求目标数据
  - 保存 raw artifact
  - 生成 normalized artifact
  - 生成 SourceEnvelope 或 FrontendSourceEnvelope
  - 记录 missing_fields 和 errors
- 输出
  - raw evidence
  - normalized JSON
  - collection meta
  - 数据源卡
- 验收
  - 至少一个真实数据源跑通
  - 有失败样例和限制说明
  - 无敏感凭证泄漏

### Step 3：字段标准化
- 输入
  - SourceEnvelope
  - FrontendSourceEnvelope
  - trend SourceEnvelope
  - 自有供应链输入
- 动作
  - 统一 product_id / canonical_url / title / price / currency / rating / review_count
  - 统一 market_metrics
  - 统一 competition_metrics
  - 统一 trend_signals
  - 统一 reviews 和 improvement_points
  - 统一 evidence_pack
- 输出
  - CandidateDataPacket
  - field coverage report
  - missing data report
- 验收
  - 缺字段不隐藏
  - null 不被写成 0
  - 价格必须带币种
  - 每个关键事实有证据

### Step 4：L1 粗筛
- 输入
  - CandidateDataPacket
  - 低成本需求信号
  - 低成本竞争信号
  - 趋势信号
- 动作
  - 检查产品链接和市场
  - 检查需求信号
  - 检查价格带、评分、评论数、榜单或搜索信号
  - 输出初始 grade 和 L2 补采字段
- 输出
  - L1 AnalysisResult
  - S/A/B/C 初排
  - required_next_data
- 验收
  - L1 不调用差评全文
  - L1 不做多平台深比
  - 无需求信号的候选被降级或补采

### Step 5：L2 精调研
- 输入
  - L1 入围 S/A/B 候选
  - 评论、差评、Q&A、详情补充
- 动作
  - 差评聚类
  - 痛点识别
  - 改良点输出
  - 风险理由整理
  - 判断是否进入 L3
- 输出
  - L2 AnalysisResult
  - negative_review_clusters
  - improvement_points
  - risk_summary
- 验收
  - 只对入围款做高成本分析
  - 差评结论绑定原评论 evidence_refs
  - 拿不到评论时写明原因并降低 confidence

### Step 6：L3 多平台比对
- 输入
  - L2 终选 S/A 候选
  - Amazon / TikTok Shop / Shein / 快时尚 / 独立站同款或同趋势证据
- 动作
  - 对比平台价格带
  - 对比同款红海程度
  - 对比趋势和痛点
  - 输出平台优先级
  - 输出终选建议
- 输出
  - L3 AnalysisResult
  - cross_platform comparison
  - final recommendation
- 验收
  - 不凭空找同款
  - 只比较已有 matched_products
  - 平台优先级有证据支撑

### Step 7：报告与复盘
- 输入
  - AnalysisResult
  - EvidenceRef
  - run log
  - missing fields
- 动作
  - 生成 XLSX 推荐表
  - 生成 P0 验收报告
  - 汇总阻塞项
  - 更新下周扩展清单
- 输出
  - 推荐总表
  - 评分明细
  - 数据源覆盖
  - 差评改良
  - 多平台比对
  - 风险清单
  - 证据索引
  - 运行记录
- 验收
  - 业务负责人先看推荐总表即可判断下一步
  - 技术人员可通过证据索引追溯每条结论
  - 阻塞项、缺字段、风险不被隐藏

## 主骨 5：一周 P0 里程碑

### 第 1 天：范围和接口冻结
- 人员 A
  - 确认 TikTok 数据源优先级
  - 确认插件 / Skill 方式
- 人员 B
  - 确认目标前端站点
  - 确认采集模式和页数上限
- 人员 C
  - 确认 CandidateDataPacket v0
  - 确认 L1/L2/L3 Gate v0
- 统一验收
  - 三人任务表冻结
  - P0 数据源和输出格式冻结

### 第 2-3 天：真实样例跑通
- 人员 A
  - 跑通至少一个 TikTok 或非 SellerSprite 数据源样例
  - 输出 SourceEnvelope
- 人员 B
  - 跑通至少 10 个商品详情 raw evidence
  - 输出 FrontendSourceEnvelope
- 人员 C
  - 用 A、B 样例生成 CandidateDataPacket
  - 初步跑通 L1 Gate
- 统一验收
  - 至少一个插件或网页数据源真实返回字段
  - 至少一个前端站点有证据包

### 第 4-5 天：进入分层分析
- 人员 A
  - 完成字段映射和脱敏检查
  - 补充回放样例
- 人员 B
  - 补充评论、差评、互动或失败原因
  - 完成趋势信号样例
- 人员 C
  - 完成 L1/L2 样例
  - 定义 PScore v0
- 统一验收
  - CandidateDataPacket 能合并多源数据
  - 推荐理由开始绑定 evidence_refs

### 第 6 天：端到端样例
- 人员 A
  - 数据源样例稳定复跑
- 人员 B
  - 前端采集工作簿可打开核查
- 人员 C
  - 输出端到端 XLSX 推荐表样例
- 统一验收
  - 数据源到 CandidateDataPacket 到 AnalysisResult 到 XLSX 跑通

### 第 7 天：验收和下周计划
- 人员 A
  - 提交数据源验收报告
- 人员 B
  - 提交前端采集验收报告
- 人员 C
  - 提交 P0 总验收报告和下周扩展清单
- 统一验收
  - 明确已完成、未完成、阻塞项、风险、下周优先级

## 主骨 6：验收红线与风险控制

### 数据质量红线
- 无 raw evidence 的前端商品不能进入分析
- 无 EvidenceRef 的推荐理由不能进入 XLSX
- 缺字段不能被隐藏
- null 不能写成 0
- 价格没有币种视为无效字段
- Google Trends / AnySearch 不能被当作销量或销售额

### 安全红线
- 不输出 token
- 不输出 cookie
- 不输出 Auth-Token
- 不输出 session
- 不输出完整敏感 header
- 不绕过验证码、登录墙、Cloudflare、设备校验
- 不操作购物车、订单、广告、库存、店铺后台

### 稳定性风险
- 浏览器页面结构变化
- 接口设备校验
- 频率限制和 429
- 401 / 403 账号态失效
- 评论和趋势内容翻页成本过高
- Excel 超长文本和特殊字符

### 应对策略
- 每个数据源保留 replay fixture
- 每次采集写 collection meta
- 每个长任务保存 checkpoint
- 每个站点设置页数和商品数上限
- 单商品失败不影响整批
- 重复错误两次后先研究 3-5 个修复方案再选最省方案执行

## 主骨 7：最终交付清单

### SOP 文档
- 数据源优先级 SOP
- 插件 / Skill 采集 SOP
- 前端网页采集 SOP
- 趋势内容采集 SOP
- 分层分析 SOP
- 验收 SOP

### 数据与证据
- SourceEnvelope 样例
- FrontendSourceEnvelope 样例
- trend SourceEnvelope 样例
- CandidateDataPacket 样例
- EvidenceRef 样例
- raw evidence 包
- replay fixture

### 分析与报告
- L1/L2/L3 AnalysisResult 样例
- Priority Score v0
- S/A/B/C 分级规则
- XLSX 推荐表
- P0 验收报告
- 下周扩展清单

### 下周扩展优先级
- 知衣 INS 分页、节流、断点续采
- 知衣 Pinterest 模块
- TikTok Shop 数据源第二工具
- Shein 或 TikTok Shop 前端站点深化
- 趋势内容 Pinterest / TikTok Creative Center
- 分级系统接入更多真实样例
