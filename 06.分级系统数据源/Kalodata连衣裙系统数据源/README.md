# Kalodata 连衣裙系统直投包

## 直接接入

- 将整个文件夹交给系统保存或上传。
- 分级系统入口文件：`Kalodata连衣裙_20260710.source_envelope.json`。
- 分级系统可直接把本文件夹作为 `--envelopes` 目录；根目录只有这一份 JSON，其他 JSON/JSONL 位于子目录中，不会被误识别为 SourceEnvelope。
- 无损机器主源：`raw/`、`processed/`、`ledgers/`。
- XLSX 仅作标准索引：`workbooks/Kalodata连衣裙全量采集_20260710.xlsx`。

## 目录职责

- `raw/`：脱敏后的原始接口响应，保留实际返回结构与重复记录。
- `processed/`：标准化 JSONL，适合数据库、大模型和批处理读取。
- `ledgers/request_response_ledger.jsonl`：逐请求响应账本。
- `ledgers/raw_dataset_records.jsonl`：逐数据集原始记录账本。
- `ledgers/raw_field_ledger.jsonl`：逐叶子字段账本。
- `ledgers/raw_field_dictionary.jsonl`：标准化字段路径字典。
- `metadata/lossless_manifest.json`：采集边界、记录数和源文件哈希。
- `metadata/delivery_manifest.json`：本直投包全部文件的大小和 SHA-256。

## 覆盖边界

本包完整保留本轮成功只读请求实际返回的数据，不代表 Kalodata 平台数据库全部数据。当前套餐限制列表每页 10 条且只开放第一页；搜索与详情额度均已用完，导出额度为 0。
