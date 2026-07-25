# xuanpin —— 女装跨境选品数据源与分级系统

| 目录 | 内容 |
|---|---|
| `01.分级标准参考/` | 项目规范：总控计划、统一业务范本、选品全流程任务书、员工任务书 |
| `02.插件数据源/` | 插件源实采交付：Kalodata、Tabcut/EchoTik（原数据 XLSX + 字段交付表） |
| `03.前端数据源/` | 前端采集交付：Shein 连衣裙 200 款、独立站、图案连裤袜、万圣节 |
| `04.分级系统/` | **分级系统实现**：SourceEnvelope → CandidateDataPacket → L1/L2/L3 → PScore → S/A/B/C → XLSX 推荐表（用法与分级标准见其 README 与 docs/） |

一键运行分级管道（仓库根目录）：

```bash
pip install -r 04.分级系统/requirements.txt
PYTHONPATH="04.分级系统/src" python3 -m grading_system.cli run \
    --repo-root . --out "04.分级系统/data/analysis_runs/run_$(date +%Y%m%d)_001"
```
