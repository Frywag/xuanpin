"""L2 评价标签聚类（确定性，不使用 LLM，不编造评论）。

P0 前端采集未包含评论全文；Shein 商品页带「评价标签 + 出现次数」聚合
（如 ``No Smell (3)Runs Large (24)``），这是页面真实展示的用户评价聚合，
可作为 L2 痛点聚类的证据。其他来源没有评论数据时，只记录不可得原因。

负面词表全部来自本轮 Shein 实采 200 款的标签词汇表（95 个去重标签），
不外推、不自造标签。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

TAG_RE = re.compile(r"([A-Za-z&'’\-\. ]+?)\s*\((\d+)\)")

# 负面标签 -> (聚类名, 改良方向, 不改的风险)
NEGATIVE_TAG_MAP = {
    "never received this item": ("物流履约投诉", "核查物流与履约链路、发货时效", "差评率与退款率上升"),
    "runs large": ("尺码偏大", "版型收正与尺码表校准", "退货率偏高"),
    "runs small": ("尺码偏小", "版型放量与尺码表校准", "退货率偏高"),
    "wrong size": ("尺码发错/不符", "尺码标签与发货质检", "退货率偏高"),
    "wrong style": ("款式与图片不符", "图片与实物一致性管控", "信任流失、差评"),
    "see-through": ("面料偏透", "提升克重或增加里衬", "穿着场景受限、差评"),
    "missing accessories": ("配件缺失", "包装配件质检", "差评与客诉"),
    "no stretch": ("弹力不足", "面料弹性改良", "舒适度差评"),
    "too short": ("衣长偏短", "版型长度调整", "退货"),
    "too long": ("衣长偏长", "版型长度调整", "退货"),
    "too tight": ("版型偏紧", "版型放量", "退货"),
    "too loose": ("版型偏松", "版型收正", "退货"),
    "dislike": ("综合不满", "需人工阅读差评原文定位原因", "原因未知，风险不可控"),
    "color difference": ("色差", "影像色彩管理与面料对色", "差评与退货"),
    "poor quality": ("质量差评", "供应商与质检标准提升", "退货与店铺评分下滑"),
}


def parse_review_tags(raw: str) -> List[Dict[str, Any]]:
    """把 ``Tag (n)Tag2 (m)`` 解析成 [{tag, count}]。"""
    out = []
    for m in TAG_RE.finditer(str(raw or "")):
        out.append({"tag": m.group(1).strip(), "count": int(m.group(2))})
    return out


def _freq_hint(count: int) -> str:
    if count >= 10:
        return "high"
    if count >= 3:
        return "medium"
    return "low"


def cluster_review_tags(raw: str, evidence_refs: List[str]) -> Dict[str, Any]:
    """返回 {clusters, positive_tags, total_mentions}。

    仅消费页面真实标签；``No Smell``/``No Color Difference`` 等
    「No + 负面名词」组合是正面标签，不会误判为负面。
    """
    tags = parse_review_tags(raw)
    clusters: Dict[str, Dict[str, Any]] = {}
    positive: List[Dict[str, Any]] = []
    for t in tags:
        key = t["tag"].lower()
        mapped = NEGATIVE_TAG_MAP.get(key)
        if mapped is None:
            positive.append(t)
            continue
        name, improvement, risk = mapped
        c = clusters.setdefault(name, {
            "cluster_name": name,
            "mention_count": 0,
            "representative_reviews": [],
            "improvement_opportunity": improvement,
            "risk_if_unfixed": risk,
            "evidence_refs": list(evidence_refs),
        })
        c["mention_count"] += t["count"]
        c["representative_reviews"].append(f"{t['tag']} ({t['count']})")
    result = sorted(clusters.values(), key=lambda c: -c["mention_count"])
    for c in result:
        c["frequency_hint"] = _freq_hint(c["mention_count"])
    return {
        "clusters": result,
        "positive_tags": positive,
        "total_mentions": sum(t["count"] for t in tags),
    }
