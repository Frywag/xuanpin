"""标准 SourceEnvelope 文件接入：校验 + 载入。

这是「采集 agent 抓回大量数据 -> 直接投入分级系统」的正式入口：
采集方不再需要写 Adapter，只要按 docs/05_分级系统输入契约 产出
`source.envelope.v1` JSON（单文件或 .jsonl 每行一个信封），
放进目录后 `run --envelopes <目录>` 即可进入管道。

校验规则（validate-envelope 命令同用）：
  E级（拒绝）：缺必填头字段 / market 不符 / records 非列表 /
              record 无 product_id 且无 canonical_url / 价格无币种；
  W级（警告）：字段缺少对应 evidence / quality_status 非标准枚举。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .models import SCHEMA_VERSION_ENVELOPE, SourceEnvelope

REQUIRED_HEAD = ["envelope_id", "source_id", "source_type", "market", "records"]
VALID_SOURCE_TYPES = {"plugin", "frontend", "trend", "owned"}
VALID_QUALITY = {"VALID", "PARTIAL_SOURCE_MISSING", "PARSE_FAILED", "ACCESS_BLOCKED"}
REQUIRED_EVIDENCE_KEYS = {"evidence_id", "source_id", "artifact_path", "record_locator"}


def validate_envelope_dict(d: Dict[str, Any], market: str = "US"
                           ) -> Tuple[List[str], List[str]]:
    """返回 (errors, warnings)。errors 非空则该信封拒收。"""
    errors, warnings = [], []
    if not isinstance(d, dict):
        return (["信封不是 JSON 对象"], [])
    for k in REQUIRED_HEAD:
        if not d.get(k) and d.get(k) != []:
            errors.append(f"缺必填字段 {k}")
    if d.get("schema_version") not in (None, SCHEMA_VERSION_ENVELOPE):
        errors.append(f"schema_version 应为 {SCHEMA_VERSION_ENVELOPE}")
    if d.get("source_type") and d["source_type"] not in VALID_SOURCE_TYPES:
        errors.append(f"source_type 非法：{d['source_type']}")
    if d.get("market") and d["market"] != market:
        errors.append(f"market={d['market']} 与本轮范围 {market} 不符")
    if d.get("quality_status") and d["quality_status"] not in VALID_QUALITY:
        warnings.append(f"quality_status 非标准枚举：{d['quality_status']}")

    for i, rec in enumerate(d.get("records") or []):
        loc = f"records[{i}]"
        if not isinstance(rec, dict):
            errors.append(f"{loc} 不是对象")
            continue
        if rec.get("record_kind") not in ("product", "category_context"):
            warnings.append(f"{loc} record_kind={rec.get('record_kind')} 未识别，将被忽略")
            continue
        fields = rec.get("fields")
        if not isinstance(fields, dict):
            errors.append(f"{loc} 缺 fields 对象")
            continue
        if rec.get("record_kind") == "product":
            if not rec.get("product_id") and not fields.get("canonical_url"):
                errors.append(f"{loc} 无 product_id 且无 canonical_url（L1 阻断条件）")
        evidence = rec.get("evidence") or {}
        for name, value in fields.items():
            if name.startswith("_"):
                continue
            # 价格类字段必须带币种
            if name in ("unit_price", "original_price", "revenue_30d", "gmv_7d",
                        "shipping_fee") and isinstance(value, dict):
                if value.get("amount") is not None and not value.get("currency"):
                    errors.append(f"{loc}.fields.{name} 金额缺币种")
            ev = evidence.get(name)
            if ev is None:
                warnings.append(f"{loc}.fields.{name} 无对应 evidence（将无法进入推荐理由）")
            elif not REQUIRED_EVIDENCE_KEYS <= set(ev.keys()):
                errors.append(f"{loc}.evidence.{name} 缺 "
                              f"{sorted(REQUIRED_EVIDENCE_KEYS - set(ev.keys()))}")
    return errors, warnings


def _iter_envelope_dicts(path: Path):
    if path.suffix == ".jsonl":
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                yield f"{path}:{line_no}", json.loads(line)
    else:
        yield str(path), json.loads(path.read_text(encoding="utf-8"))


def load_envelope_files(path: Path, market: str = "US"
                        ) -> Tuple[List[SourceEnvelope], List[str]]:
    """载入目录/文件中的标准信封。返回 (envelopes, problems)。

    校验出 E 级错误的信封整封拒收（宁缺毋滥），错误写入 problems。
    """
    path = Path(path)
    files = ([path] if path.is_file()
             else sorted(list(path.glob("*.json")) + list(path.glob("*.jsonl"))))
    envelopes, problems = [], []
    for f in files:
        try:
            items = list(_iter_envelope_dicts(f))
        except (ValueError, OSError) as exc:
            problems.append(f"{f}: JSON 解析失败 {exc}")
            continue
        for locator, d in items:
            errors, warnings = validate_envelope_dict(d, market)
            if errors:
                problems.append(f"{locator}: 拒收 {errors}")
                continue
            problems.extend(f"{locator}: 警告 {w}" for w in warnings[:5])
            envelopes.append(SourceEnvelope(
                envelope_id=d["envelope_id"], source_id=d["source_id"],
                source_type=d["source_type"], market=d["market"],
                collection_mode=d.get("collection_mode", "full_then_analyze"),
                layer_hint=d.get("layer_hint", "L1"),
                collected_at=d.get("collected_at"),
                collector_version=d.get("collector_version", "external"),
                quality_status=d.get("quality_status", "VALID"),
                records=d.get("records", []),
                errors=d.get("errors", []), warnings=d.get("warnings", []),
                artifact_paths=d.get("artifact_paths", {})))
    return envelopes, problems
