"""商品画像标注（152 键模板落地）。

执行方式（混合，成本最优）：
  1. 直取键：代码从候选包直取（configs/profile_keys_v1.yaml direct_keys），
     零成本、天然带证据；
  2. 语义键：生成 profile_extraction LLM 任务包，从标题/设计细节等真实文本
     提取，枚举键取值必须在业务模板的受控词表内，逐键绑定证据，回灌经校验；
  3. 供应链键：manual，终选款人工补录。
画像挂在 packet.context["profile"]，随 packet_json 入选品库与 XLSX 商品画像 sheet。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .llm_tasks import _base_bundle
from .models import CandidateDataPacket


def _get_path(packet: CandidateDataPacket, spec: Dict[str, Any]):
    if spec.get("top_level"):
        return getattr(packet, spec["path"], None)
    section, key = spec["path"].split(".", 1)
    return getattr(packet, section).get(key)


def build_direct_profile(packet: CandidateDataPacket, registry: Dict[str, Any]
                         ) -> Dict[str, Any]:
    """代码直取键：值 + 证据 + source=direct；缺失键不出现。"""
    profile: Dict[str, Any] = {}
    for name, spec in (registry.get("direct_keys") or {}).items():
        value = _get_path(packet, spec)
        if value in (None, [], {}, ""):
            continue
        if spec.get("money") and isinstance(value, dict):
            if value.get("amount") is None or not value.get("currency"):
                continue
            value = f"{value['amount']} {value['currency']}"
        ev = ([] if spec.get("top_level")
              else packet.evidence_for(spec["path"]))
        profile[name] = {"value": value, "group": spec.get("group", ""),
                         "source": "direct", "evidence_refs": ev}
    for name in registry.get("manual_keys") or []:
        profile[name] = {"value": None, "group": "供应链", "source": "manual",
                         "evidence_refs": [],
                         "note": "终选款人工补录（见 supply_capability 与 L3 流程）"}
    return profile


def _semantic_set(packet: CandidateDataPacket, registry) -> str:
    title = (packet.basic_facts.get("title") or "").lower()
    words = set(re.findall(r"[a-z\-]+", title))
    if words & set(registry.get("womenswear_tokens") or []):
        return "womenswear"
    return "general_goods"


def build_profile_extraction_task(packet: CandidateDataPacket, run_id: str,
                                  registry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """语义键 LLM 任务包。inputs 只放真实文本；枚举键必须取受控词表值。"""
    key_set = _semantic_set(packet, registry)
    keys = registry.get("semantic_keys", {}).get(key_set) or {}
    if not keys:
        return None
    text_fields = {
        "title": packet.basic_facts.get("title"),
        "title_cn": packet.context.get("title_cn"),
        "category_name": packet.context.get("category_name"),
        "category_path": packet.basic_facts.get("category_path"),
        "design_signals": packet.context.get("design_signals"),
        "trend_tags": packet.context.get("trend_tags"),
        "review_tags_raw": packet.context.get("review_tags_raw"),
    }
    ev_ids = []
    for path in ("basic_facts.title", "context.title_cn", "context.category_name",
                 "basic_facts.category_path", "context.design_signals",
                 "context.trend_tags"):
        ev_ids += packet.evidence_for(path)
    schema = {
        "profile": {k: {"value": ("词表内取值" if "vocab" in v else "短语，来自输入文本"),
                        "evidence_refs": ["⊆ allowed_evidence_ids"],
                        "confidence": "high|medium|low"}
                    for k, v in keys.items()},
        "missing_fields": ["输入文本不足以判断的键，逐个列出"],
        "assumptions": ["str"],
    }
    bundle = _base_bundle(
        "profile_extraction", packet.candidate_id, run_id,
        {"key_set": key_set, "keys": keys, "text": text_fields},
        sorted(set(ev_ids)), schema,
        ["只依据 inputs.text 中的真实文本判断，不得使用外部知识补全事实",
         "带 vocab 的键取值必须严格从词表中选择；判断不了就进 missing_fields",
         "multi 键可给列表；每个键必须带 evidence_refs 与 confidence"])
    return bundle


def validate_profile_output(bundle: Dict[str, Any], output: Dict[str, Any]) -> List[str]:
    """profile_extraction 的任务级校验（通用校验之外）。"""
    errors: List[str] = []
    keys_cfg = bundle["inputs"]["keys"]
    for name, item in (output.get("profile") or {}).items():
        if name not in keys_cfg:
            errors.append(f"profile.{name} 不在本任务键集合中")
            continue
        if not isinstance(item, dict) or not item.get("evidence_refs"):
            errors.append(f"profile.{name} 缺少 evidence_refs")
            continue
        vocab = keys_cfg[name].get("vocab")
        if vocab:
            values = item.get("value")
            values = values if isinstance(values, list) else [values]
            bad = [v for v in values if v not in vocab]
            if bad:
                errors.append(f"profile.{name} 取值 {bad} 不在受控词表内")
    return errors


def merge_llm_profile(packet_profile: Dict[str, Any], output: Dict[str, Any],
                      group: str = "语义提取") -> Dict[str, Any]:
    for name, item in (output.get("profile") or {}).items():
        packet_profile[name] = {"value": item.get("value"), "group": group,
                                "source": "llm_semantic",
                                "confidence": item.get("confidence"),
                                "evidence_refs": item.get("evidence_refs", [])}
    return packet_profile
