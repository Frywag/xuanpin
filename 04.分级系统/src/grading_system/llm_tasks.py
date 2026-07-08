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

TASK_TYPES = ("review_clustering", "cross_platform_compare", "reason_writer",
              "deep_review", "run_report", "profile_extraction")

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


def build_deep_review_task(packet: Dict[str, Any], result: Dict[str, Any],
                           run_id: str) -> Dict[str, Any]:
    """S / 高分 A 的逐款深度评审（全量分析）。

    与前三类任务不同：这是**正式分析产出**而非草稿——评审五个维度、
    可以挑战引擎等级（grade_challenge）、给出 go/hold/reject 建议。
    边界不变：只消费任务包内事实、结论必须绑定证据、等级的最终变更由人审确认。
    """
    ev_ids = [e["evidence_id"] for e in packet.get("evidence_pack", [])]
    ctx = packet.get("context", {})
    inputs = {
        "candidate": {
            "candidate_id": packet["candidate_id"],
            "platform": packet.get("platform"),
            "canonical_url": packet.get("canonical_url"),
            "basic_facts": packet.get("basic_facts"),
            "market_metrics": packet.get("market_metrics"),
            "competition_metrics": packet.get("competition_metrics"),
            "product_opportunity": packet.get("product_opportunity"),
            "cross_platform": packet.get("cross_platform"),
            "owned_supply_inputs": packet.get("owned_supply_inputs"),
            "quality_status": packet.get("quality_status"),
            "missing_fields": packet.get("missing_fields"),
            "field_issues": packet.get("field_issues"),
            "context": {k: ctx.get(k) for k in (
                "category_name", "category_label", "source_group", "site",
                "shop_name", "seller_name", "seasonal", "shipping_fee",
                "product_total_sales", "video_engagement_max",
                "affiliate_creator_count", "review_tags_raw")},
        },
        "engine_analysis": {
            "track": result["priority_score"].get("track"),
            "track_label": result["priority_score"].get("track_label"),
            "grade": result["priority_score"].get("grade"),
            "pct": result["priority_score"].get("pct"),
            "total": result["priority_score"].get("total"),
            "achievable_max": result["priority_score"].get("achievable_max"),
            "components": result["priority_score"].get("components"),
            "claims": result.get("claims"),
            "layer_results": result.get("layer_results"),
            "confidence": result.get("confidence"),
        },
        "evidence_pack": [
            {"evidence_id": e["evidence_id"], "source_id": e["source_id"],
             "field_path": e["field_path"], "record_locator": e["record_locator"],
             "note": e.get("note", "")}
            for e in packet.get("evidence_pack", [])],
    }
    schema = {
        "executive_summary": "str（≤200字，给运营/立项委员会看）",
        "sections": {
            "demand": {"assessment": "str", "strengths": ["str"], "concerns": ["str"],
                       "evidence_refs": ["⊆ allowed_evidence_ids，必填非空"],
                       "confidence": "high|medium|low"},
            "competition": "同上结构", "product": "同上结构",
            "supply_chain": "同上结构", "risk": "同上结构",
        },
        "grade_challenge": {
            "agrees_with_engine": "bool",
            "suggested_grade": "S|A|B|C|null（同意引擎等级时为 null）",
            "rationale": "str（不同意时必填，说明依据哪些证据）",
        },
        "go_recommendation": {"decision": "go_research|hold|reject",
                              "conditions": ["str（放行/持有的前提条件）"]},
        "open_questions": ["str（需要人工或补采回答的问题）"],
        "next_actions": ["str"],
        "missing_fields": ["str"], "assumptions": ["str"],
        "confidence": "high|medium|low",
    }
    return _base_bundle(
        "deep_review", packet["candidate_id"], run_id, inputs, ev_ids, schema,
        ["五个 sections 每个都必须给非空 evidence_refs",
         "grade_challenge 可以不同意引擎等级，但 rationale 必须引用证据支持，"
         "且等级的最终变更由人审确认",
         "supply_chain 一节只能基于利润代理/能力档案/缺失声明，不得推测成本数值"])


def build_run_report_task(run_summary: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """整轮运行分析报告：选品表产出后生成，一次运行一份。

    inputs.derived 里预先算好所有比例/分位数——报告只允许引用这些现成数值，
    不允许自行计算新数字（数值封闭性校验会拒绝）。
    """
    top_ids = [c["candidate_id"] for c in run_summary.get("top_candidates", [])]
    schema = {
        "title": "str",
        "executive_summary": "str（≤300字：这轮分析发现了什么、建议做什么）",
        "market_landscape": "str（数据源覆盖了什么市场、看到什么格局）",
        "track_analysis": [{
            "track": "trend_rising|pain_improvement|evergreen|balanced",
            "narrative": "str（该赛道的发现与建议）",
            "top_candidate_ids": ["⊆ inputs.top_candidates 中的 candidate_id"],
        }],
        "top_candidates_review": [{
            "candidate_id": "⊆ inputs.top_candidates",
            "one_liner": "str（一句话点评）",
        }],
        "data_quality_and_gaps": "str（缺什么数据、对结论的影响）",
        "risk_overview": "str",
        "next_collection_plan": ["str（下一轮补采/扩源的优先级）"],
        "missing_fields": ["str"], "assumptions": ["str"],
    }
    return _base_bundle(
        "run_report", "__run__", run_id, run_summary, [], schema,
        ["报告中的所有数值必须直接取自 inputs（含 inputs.derived 的比例），不要自行计算新数值",
         "提到具体候选时 candidate_id 必须在 inputs.top_candidates 里",
         "对没有需求信号的品类（如本轮图案连裤袜）要说明是数据缺失而非品类否定"])


# ================= 渲染（回灌通过后生成业务可读 Markdown） =================

def render_markdown(bundle: Dict[str, Any], output: Dict[str, Any]) -> str:
    if bundle["task_type"] == "run_report":
        return _render_run_report(bundle, output)
    if bundle["task_type"] == "deep_review":
        return _render_deep_review(bundle, output)
    return "```json\n" + json.dumps(output, ensure_ascii=False, indent=2) + "\n```\n"


def _render_deep_review(bundle, out) -> str:
    cand = bundle["inputs"]["candidate"]
    eng = bundle["inputs"]["engine_analysis"]
    gc = out.get("grade_challenge", {})
    lines = [
        f"# 深度评审：{cand['basic_facts'].get('title', cand['candidate_id'])}",
        "",
        f"- 候选：`{cand['candidate_id']}`（{cand.get('platform')}）",
        f"- 引擎结论：**{eng.get('grade')}**（{eng.get('track_label')}，"
        f"pct={eng.get('pct')}，置信度 {eng.get('confidence')}）",
        f"- 评审结论：**{out.get('go_recommendation', {}).get('decision')}**"
        f"（评审置信度 {out.get('confidence')}）",
        f"- 等级评审：{'同意引擎等级' if gc.get('agrees_with_engine') else '不同意，建议 ' + str(gc.get('suggested_grade'))}"
        + (f"——{gc.get('rationale')}" if gc.get("rationale") else ""),
        "",
        "## 摘要", "", out.get("executive_summary", ""), "",
    ]
    names = {"demand": "市场需求", "competition": "竞争格局", "product": "产品机会",
             "supply_chain": "供应链可行性", "risk": "风险"}
    for key, cn in names.items():
        sec = (out.get("sections") or {}).get(key) or {}
        lines += [f"## {cn}（置信度 {sec.get('confidence', '?')}）", "",
                  sec.get("assessment", ""), ""]
        if sec.get("strengths"):
            lines += ["**优势**：" + "；".join(sec["strengths"]), ""]
        if sec.get("concerns"):
            lines += ["**顾虑**：" + "；".join(sec["concerns"]), ""]
        if sec.get("evidence_refs"):
            lines += ["证据：`" + "`、`".join(sec["evidence_refs"][:8]) + "`", ""]
    if out.get("go_recommendation", {}).get("conditions"):
        lines += ["## 放行条件", ""] + [f"- {c}" for c in out["go_recommendation"]["conditions"]] + [""]
    if out.get("open_questions"):
        lines += ["## 待人工回答", ""] + [f"- {q}" for q in out["open_questions"]] + [""]
    if out.get("next_actions"):
        lines += ["## 下一步", ""] + [f"- {a}" for a in out["next_actions"]] + [""]
    if out.get("missing_fields"):
        lines += ["## 缺失数据", ""] + [f"- {m}" for m in out["missing_fields"]] + [""]
    return "\n".join(lines)


def _render_run_report(bundle, out) -> str:
    lines = [f"# {out.get('title', '选品分析报告')}", "",
             f"> run：`{bundle['run_id']}`（LLM 分析产出，经校验回灌；"
             "等级与分数由确定性引擎产生）", "",
             "## 摘要", "", out.get("executive_summary", ""), "",
             "## 市场格局", "", out.get("market_landscape", ""), ""]
    for t in out.get("track_analysis", []):
        lines += [f"## 赛道：{t.get('track')}", "", t.get("narrative", ""), ""]
        if t.get("top_candidate_ids"):
            lines += ["头部候选：`" + "`、`".join(t["top_candidate_ids"]) + "`", ""]
    if out.get("top_candidates_review"):
        lines += ["## 头部候选点评", ""]
        for c in out["top_candidates_review"]:
            lines.append(f"- `{c.get('candidate_id')}`：{c.get('one_liner')}")
        lines.append("")
    lines += ["## 数据质量与缺口", "", out.get("data_quality_and_gaps", ""), "",
              "## 风险总览", "", out.get("risk_overview", ""), ""]
    if out.get("next_collection_plan"):
        lines += ["## 下一轮采集计划", ""] + [f"- {p}" for p in out["next_collection_plan"]] + [""]
    if out.get("assumptions"):
        lines += ["## 假设声明", ""] + [f"- {a}" for a in out["assumptions"]] + [""]
    return "\n".join(lines)


# ================= 回灌校验 =================

_NUM_RE = re.compile(r"\d[\d,\.]*")

# 数值封闭性扫描要跳过的键：证据/标识类字段本身含数字（如
# ev_shein_frontend_001353、candidate_id），不是事实数值
_ID_KEYS = {"evidence_refs", "candidate_id", "run_id", "evidence_id",
            "allowed_evidence_ids", "top_candidate_ids", "envelope_id"}


def _known_identifiers(bundle: Dict[str, Any]) -> set:
    """任务包中声明过的标识符（候选 id / run id / 证据 id），
    正文内联引用它们是合法的，扫描数字前先剥离。"""
    ids = {str(bundle.get("candidate_id") or ""), str(bundle.get("run_id") or "")}
    ids |= {str(e) for e in bundle.get("allowed_evidence_ids", [])}
    inputs = bundle.get("inputs", {}) or {}
    for c in inputs.get("top_candidates", []) or []:
        if isinstance(c, dict) and c.get("candidate_id"):
            ids.add(str(c["candidate_id"]))
    for key in ("candidate", "run_meta"):
        node = inputs.get(key)
        if isinstance(node, dict):
            for k in ("candidate_id", "run_id"):
                if node.get(k):
                    ids.add(str(node[k]))
    ids.discard("")
    return ids


def _numbers_in(obj, known_ids: Optional[set] = None) -> set:
    out = set()
    ids = sorted(known_ids or (), key=len, reverse=True)

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
            for kid in ids:
                x = x.replace(kid, " ")
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

    task_type = bundle.get("task_type")
    if task_type == "deep_review":
        # 五个评审 section 必须逐个绑定证据
        sections = output.get("sections") or {}
        for key in ("demand", "competition", "product", "supply_chain", "risk"):
            sec = sections.get(key)
            if not isinstance(sec, dict):
                errors.append(f"sections.{key} 缺失")
            elif not sec.get("evidence_refs"):
                errors.append(f"sections.{key} 缺少 evidence_refs")
        gc = output.get("grade_challenge")
        if not isinstance(gc, dict) or "agrees_with_engine" not in gc:
            errors.append("缺少 grade_challenge.agrees_with_engine")
        elif not gc.get("agrees_with_engine") and not gc.get("rationale"):
            errors.append("不同意引擎等级时 grade_challenge.rationale 必填")
        if (output.get("go_recommendation") or {}).get("decision") not in (
                "go_research", "hold", "reject"):
            errors.append("go_recommendation.decision 必须是 go_research/hold/reject")

    if task_type == "profile_extraction":
        from .profile import validate_profile_output  # 局部导入避免循环依赖
        errors.extend(validate_profile_output(bundle, output))

    if task_type == "run_report":
        # 提到的候选必须在输入的头部候选列表里，防止编造候选
        known = {c.get("candidate_id")
                 for c in bundle.get("inputs", {}).get("top_candidates", [])}
        mentioned = [c.get("candidate_id")
                     for c in output.get("top_candidates_review") or []]
        for t in output.get("track_analysis") or []:
            mentioned += list(t.get("top_candidate_ids") or [])
        bad = sorted({m for m in mentioned if m and m not in known})
        if bad:
            errors.append(f"引用了输入之外的候选: {bad[:5]}")

    # 数值封闭性：输出中的数字必须都在输入中出现过；
    # 已声明的标识符（候选/run/证据 id）先剥离，避免误伤合法引用
    known_ids = _known_identifiers(bundle)
    in_nums = _numbers_in(bundle.get("inputs"), known_ids)
    trivial = {float(i) for i in range(0, 101)}
    new_nums = _numbers_in(output, known_ids) - in_nums - trivial
    if new_nums:
        errors.append(f"输出包含输入中不存在的数值（疑似编造）: {sorted(new_nums)[:5]}")

    if "missing_fields" not in output:
        errors.append("输出缺少 missing_fields 字段（缺数据必须显式声明）")
    return errors
