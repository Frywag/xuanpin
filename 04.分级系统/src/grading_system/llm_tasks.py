"""LLM 分析任务契约：把「必要的分析环节」安全地交给大模型执行。

设计原则（继承总纲 §10 与任务书 §9 的 LLM 边界）：
  1. 系统不直接调用任何 LLM API——它生成**任务包**（task bundle），由
     Claude Code / 其他 agent CLI 读取任务包、执行分析、把 JSON 结果回灌；
  2. 任务包里只放该候选已有的事实和证据 id 白名单，LLM 只能引用白名单内证据；
  3. 回灌结果必须通过 `validate_llm_output` 校验才能入库：
     - 结构符合 output_schema；
     - 每条 claim 的 evidence_refs ⊆ 白名单；
     - 不得出现任务包输入之外的新数值事实（销量/价格/评分等）；
     - 缺数据必须写 missing_fields / assumptions，不许编造。
  4. 校验通过的结果进 llm_analyses 表，标记 human_review=pending——
     LLM 产出是「分析草稿」，等级/分数仍由确定性引擎负责。

三类任务（对应任务书 §11 要求的三个 prompt）：
  review_clustering        差评/评价标签聚类（L2）
  cross_platform_compare   多平台比对解读（L3）
  reason_writer            把 AnalysisResult 改写成业务可读语言（不新增事实）
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

TASK_TYPES = ("review_clustering", "cross_platform_compare", "reason_writer")

COMMON_RULES = [
    "只允许引用 allowed_evidence_ids 里的证据 id，禁止编造证据",
    "禁止编造销量、搜索量、销售额、毛利、MOQ、成本等任何数值",
    "输入中没有的信息必须写入 missing_fields，不得脑补为事实",
    "不确定的判断写入 assumptions 并降低 confidence",
    "输出必须是单个 JSON 对象，不要输出任何 JSON 之外的文字",
]


def _base_bundle(task_type: str, candidate_id: str, run_id: str,
                 inputs: Dict[str, Any], allowed_evidence: List[str],
                 output_schema: Dict[str, Any], extra_rules: List[str]) -> Dict[str, Any]:
    return {
        "task_type": task_type,
        "candidate_id": candidate_id,
        "run_id": run_id,
        "rules": COMMON_RULES + extra_rules,
        "inputs": inputs,
        "allowed_evidence_ids": sorted(allowed_evidence),
        "output_schema": output_schema,
    }


def build_review_clustering_task(packet: Dict[str, Any], run_id: str) -> Optional[Dict[str, Any]]:
    """L2 差评聚类任务包。只消费 L2 评论证据（当前为评价标签聚合）。"""
    raw = packet.get("context", {}).get("review_tags_raw")
    reviews = packet.get("product_opportunity", {}).get("review_tag_clusters")
    if not raw and not reviews:
        return None
    ev_ids = [e["evidence_id"] for e in packet.get("evidence_pack", [])
              if e["field_path"] in ("context.review_tags_raw", "basic_facts.rating",
                                     "basic_facts.review_count")]
    inputs = {
        "title": packet.get("basic_facts", {}).get("title"),
        "rating": packet.get("basic_facts", {}).get("rating"),
        "review_count": packet.get("basic_facts", {}).get("review_count"),
        "review_tags_raw": raw,
        "note": "本轮无评论全文，只有页面评价标签+次数聚合；结论只能基于这些标签",
    }
    schema = {
        "clusters": [{
            "cluster_name": "str", "frequency_hint": "high|medium|low",
            "representative_reviews": ["str（只能引用输入中的标签原文）"],
            "improvement_opportunity": "str", "risk_if_unfixed": "str",
            "evidence_refs": ["必须 ⊆ allowed_evidence_ids"],
        }],
        "missing_fields": ["str"], "assumptions": ["str"],
        "confidence": "high|medium|low", "blocked_reasons": ["str"],
    }
    return _base_bundle(
        "review_clustering", packet["candidate_id"], run_id, inputs, ev_ids, schema,
        ["representative_reviews 只能摘抄输入里的标签原文，不得虚构评论内容"])


def build_cross_platform_task(packet: Dict[str, Any], result: Dict[str, Any],
                              run_id: str) -> Optional[Dict[str, Any]]:
    """L3 多平台比对解读任务包。只消费已存在的 matched_products / 价格带 / 同风格信号。"""
    cp = packet.get("cross_platform", {})
    if not any(cp.get(k) for k in ("matched_products", "platform_price_comparison",
                                   "same_style_signals")):
        return None
    ev_ids = [e["evidence_id"] for e in packet.get("evidence_pack", [])]
    inputs = {
        "title": packet.get("basic_facts", {}).get("title"),
        "platform": packet.get("platform"),
        "matched_products": cp.get("matched_products"),
        "platform_price_comparison": cp.get("platform_price_comparison"),
        "same_style_signals": cp.get("same_style_signals"),
        "note": "不同来源币种不同且未换算；同风格信号不构成同款结论",
    }
    schema = {
        "platform_priority": "str（平台 id）",
        "red_ocean_level": "high|medium|low",
        "differentiation_space": ["str"],
        "final_suggestion": "str",
        "claims": [{"claim": "str", "evidence_refs": ["⊆ allowed_evidence_ids"],
                    "confidence": "high|medium|low"}],
        "missing_fields": ["str"], "assumptions": ["str"],
    }
    return _base_bundle(
        "cross_platform_compare", packet["candidate_id"], run_id, inputs, ev_ids, schema,
        ["只比较 inputs 里已有的 matched_products 与价格带，不得凭空找同款",
         "不得把不同币种的价格直接换算比较"])


def build_reason_writer_task(packet: Dict[str, Any], result: Dict[str, Any],
                             run_id: str) -> Dict[str, Any]:
    """理由改写任务包：把已有 AnalysisResult 改写成业务可读语言，不新增事实。"""
    ev_ids = [e["evidence_id"] for e in packet.get("evidence_pack", [])]
    inputs = {
        "title": packet.get("basic_facts", {}).get("title"),
        "grade": result["priority_score"].get("grade"),
        "track": result["priority_score"].get("track_label"),
        "components": [
            {"name": c["name"], "score": c["score"], "max": c["max_score"],
             "reason": c["reason"], "evidence_refs": c["evidence_refs"]}
            for c in result["priority_score"].get("components", [])],
        "claims": result.get("claims", []),
        "missing_fields": result.get("missing_fields", []),
    }
    schema = {
        "reason_summary_cn": "str（≤120字，业务口语，只重述输入事实）",
        "risk_summary_cn": "str（≤80字）",
        "next_action_cn": "str（≤60字）",
        "evidence_refs": ["⊆ allowed_evidence_ids（引用支撑主要结论的证据）"],
        "missing_fields": ["str"],
    }
    return _base_bundle(
        "reason_writer", packet["candidate_id"], run_id, inputs, ev_ids, schema,
        ["只改写 components/claims 里已有的结论，禁止引入任何新事实或新数值"])


# ================= 回灌校验 =================

_NUM_RE = re.compile(r"\d[\d,\.]*")

# 数值封闭性扫描要跳过的键：证据/标识类字段本身含数字（如
# ev_shein_frontend_001353、candidate_id），不是事实数值
_ID_KEYS = {"evidence_refs", "candidate_id", "run_id", "evidence_id",
            "allowed_evidence_ids"}


def _numbers_in(obj) -> set:
    out = set()
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in _ID_KEYS:
                    continue
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
        elif isinstance(x, (int, float)) and not isinstance(x, bool):
            out.add(round(float(x), 4))
        elif isinstance(x, str):
            for m in _NUM_RE.finditer(x):
                try:
                    out.add(round(float(m.group(0).replace(",", "")), 4))
                except ValueError:
                    pass
    walk(obj)
    return out


def validate_llm_output(bundle: Dict[str, Any], output: Dict[str, Any]) -> List[str]:
    """返回错误列表；空列表 = 校验通过。"""
    errors: List[str] = []
    if not isinstance(output, dict):
        return ["输出不是 JSON 对象"]

    allowed = set(bundle.get("allowed_evidence_ids", []))

    def check_evidence(node, path=""):
        if isinstance(node, dict):
            refs = node.get("evidence_refs")
            if isinstance(refs, list):
                bad = [r for r in refs if r not in allowed]
                if bad:
                    errors.append(f"{path}evidence_refs 引用了白名单外的证据: {bad[:3]}")
            for k, v in node.items():
                check_evidence(v, f"{path}{k}.")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                check_evidence(v, f"{path}[{i}].")

    check_evidence(output)

    # claims 类输出必须带证据
    for key in ("claims", "clusters"):
        for i, item in enumerate(output.get(key) or []):
            if isinstance(item, dict) and not item.get("evidence_refs"):
                errors.append(f"{key}[{i}] 缺少 evidence_refs")

    # 数值封闭性：输出中的数字必须都在输入中出现过（允许 0/1/2/…10 的小计数）
    in_nums = _numbers_in(bundle.get("inputs"))
    trivial = {float(i) for i in range(0, 101)}
    new_nums = _numbers_in(output) - in_nums - trivial
    if new_nums:
        errors.append(f"输出包含输入中不存在的数值（疑似编造）: {sorted(new_nums)[:5]}")

    if "missing_fields" not in output:
        errors.append("输出缺少 missing_fields 字段（缺数据必须显式声明）")
    return errors
