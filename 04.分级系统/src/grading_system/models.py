"""统一数据契约：SourceEnvelope / EvidenceRef / CandidateDataPacket / AnalysisResult。

对应《07_分层分析系统组任务书》§5/§6 与《00_共同知识与执行总纲》§6。
硬性规则（在模型层强制）：
  1. 事实字段必须通过 ``set_fact`` 写入并同时登记 EvidenceRef；
  2. 缺字段必须进 ``missing_fields``，绝不写 0 或猜测值；
  3. 价格必须带币种，缺币种视为无效并进 ``field_issues``；
  4. AnalysisResult 的每条 claim 必须携带非空 evidence_refs。
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SCHEMA_VERSION_ENVELOPE = "source.envelope.v1"
SCHEMA_VERSION_PACKET = "candidate.data.packet.v1"
SCHEMA_VERSION_RESULT = "analysis.result.v1"


def _asdict(obj):
    if dataclasses.is_dataclass(obj):
        return {k: _asdict(v) for k, v in dataclasses.asdict(obj).items()}
    return obj


@dataclass
class EvidenceRef:
    """字段级证据引用：绑定到具体字段，而不是只绑定到数据源。"""

    evidence_id: str
    source_id: str
    source_type: str  # plugin / frontend / trend / owned / derived
    tool_or_adapter: str
    field_path: str
    artifact_path: str  # 指向仓库内真实数据文件
    record_locator: str  # sheet/行号 或 dataset/行号，用于人工回溯
    observed_at: Optional[str] = None
    confidence: str = "high"  # high / medium / low
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class SourceEnvelope:
    envelope_id: str
    source_id: str
    source_type: str  # plugin / frontend
    market: str
    collection_mode: str  # layered / full_then_analyze / mixed
    layer_hint: str
    collected_at: Optional[str]
    collector_version: str
    quality_status: str  # VALID / PARTIAL_SOURCE_MISSING / PARSE_FAILED / ACCESS_BLOCKED
    records: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    artifact_paths: Dict[str, str] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION_ENVELOPE

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class FieldIssue:
    field_path: str
    issue: str
    detail: str = ""
    evidence_refs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class CandidateDataPacket:
    """分析系统唯一输入。所有事实字段带证据；缺字段显式登记。"""

    def __init__(self, candidate_id: str, market: str, platform: str,
                 primary_product_id: Optional[str] = None,
                 canonical_url: Optional[str] = None):
        self.candidate_id = candidate_id
        self.market = market
        self.platform = platform  # tiktok_shop / shein / freepeople / ...
        self.primary_product_id = primary_product_id
        self.canonical_url = canonical_url
        self.source_refs: List[Dict[str, str]] = []
        self.basic_facts: Dict[str, Any] = {
            "title": None, "brand": None, "category_path": [], "image_url": None,
            "price": None, "original_price": None, "discount_pct": None,
            "rating": None, "review_count": None, "in_stock": None,
        }
        self.market_metrics: Dict[str, Any] = {
            "sales_30d_units": None, "sales_7d_units": None, "sales_floor_units": None,
            "revenue_30d": None, "gmv_7d": None, "sales_growth_rate": None,
            "favorites_count": None, "bestseller_rank_text": None,
            "video_views_max": None, "video_revenue_share": None,
            "related_creator_count": None, "trend_signals": [],
        }
        self.competition_metrics: Dict[str, Any] = {
            "category_top3_shop_ratio": None, "category_top10_shop_ratio": None,
            "category_shop_count": None, "competitor_review_max": None,
            "commission_rate": None, "seller_type": None,
            "creator_gmv_concentration": None,
        }
        self.product_opportunity: Dict[str, Any] = {
            "review_tag_clusters": [], "negative_review_clusters": [],
            "improvement_points": [], "differentiation_signals": [],
        }
        self.cross_platform: Dict[str, Any] = {
            "matched_products": [], "platform_price_comparison": [],
            "same_style_signals": [],
        }
        # 新鲜度与款型（三赛道改造新增；红线：first_seen_at 只代表系统首次观察，
        # 不得冒充上架时间；页面新品标签≠市场款型新颖度，两者分字段保存）
        self.freshness: Dict[str, Any] = {
            "listed_at": None,            # 来源明确提供的上架/首次可售时间
            "first_seen_at": None,        # 系统首次观察时间（仅观察事实）
            "last_seen_at": None,
            "new_arrival_flag": None,     # 页面明确新品标记/新品集合位置
            "collection_name": None,
            "earliest_review_at": None,
            "earliest_content_at": None,
            "freshness_status": "unknown",   # confirmed/inferred/unknown/old
            "novelty_status": "unknown",     # 市场款型新颖度：confirmed/inferred/unknown/old
            "freshness_evidence_refs": [],
            "novelty_evidence_refs": [],
        }
        self.style_attributes: Dict[str, Any] = {}   # 廓形/材质/颜色/图案/长度/场景
        self.owned_supply_inputs: Dict[str, Any] = {
            "target_cost": None, "moq": None, "lead_time_days": None,
            "fabric_capability": [], "compliance_notes": [],
        }
        self.missing_fields: List[str] = []
        self.field_issues: List[FieldIssue] = []
        self.evidence_pack: List[EvidenceRef] = []
        self.quality_status: str = "VALID"
        # 运行时上下文（打分用）：来源分组、类目标签、季节性
        self.context: Dict[str, Any] = {
            "source_group": None, "category_label": None, "seasonal": False,
        }
        self._field_evidence: Dict[str, List[str]] = {}

    # ---------- 证据绑定 ----------

    def set_fact(self, section: str, key: str, value, evidence: Optional[EvidenceRef]):
        """写入事实字段并绑定证据。value 为 None 时不写（用 mark_missing）。"""
        if value is None:
            return
        target = getattr(self, section)
        target[key] = value
        path = f"{section}.{key}"
        if evidence is not None:
            self.add_evidence(evidence, path)

    def add_evidence(self, evidence: EvidenceRef, field_path: Optional[str] = None):
        if field_path:
            evidence.field_path = field_path
        self.evidence_pack.append(evidence)
        self._field_evidence.setdefault(evidence.field_path, []).append(evidence.evidence_id)

    def evidence_for(self, field_path: str) -> List[str]:
        """字段的 evidence_id 列表（打分组件引用用）。"""
        return list(self._field_evidence.get(field_path, []))

    def mark_missing(self, field_path: str):
        if field_path not in self.missing_fields:
            self.missing_fields.append(field_path)

    def add_issue(self, field_path: str, issue: str, detail: str = "",
                  evidence_refs: Optional[List[str]] = None):
        self.field_issues.append(FieldIssue(field_path, issue, detail, evidence_refs or []))

    @property
    def source_count(self) -> int:
        return len({r["source_id"] for r in self.source_refs})

    def field_coverage(self, key_fields: List[str]) -> float:
        """关键字段覆盖率 = 有证据的关键字段数 / 关键字段总数。"""
        filled = 0
        for path in key_fields:
            section, key = path.split(".", 1)
            val = getattr(self, section).get(key)
            if val not in (None, [], {}):
                filled += 1
        return filled / len(key_fields) if key_fields else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION_PACKET,
            "candidate_id": self.candidate_id,
            "market": self.market,
            "platform": self.platform,
            "primary_product_id": self.primary_product_id,
            "canonical_url": self.canonical_url,
            "source_refs": self.source_refs,
            "basic_facts": self.basic_facts,
            "market_metrics": self.market_metrics,
            "competition_metrics": self.competition_metrics,
            "product_opportunity": self.product_opportunity,
            "cross_platform": self.cross_platform,
            "freshness": self.freshness,
            "style_attributes": self.style_attributes,
            "owned_supply_inputs": self.owned_supply_inputs,
            "missing_fields": self.missing_fields,
            "field_issues": [i.to_dict() for i in self.field_issues],
            "evidence_pack": [e.to_dict() for e in self.evidence_pack],
            "quality_status": self.quality_status,
            "context": self.context,
        }


@dataclass
class ScoreComponent:
    name: str
    score: Optional[float]
    max_score: float
    reason: str
    evidence_refs: List[str] = field(default_factory=list)
    missing: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class Claim:
    claim: str
    claim_type: str  # demand / competition / product / risk / cross_platform
    evidence_refs: List[str] = field(default_factory=list)
    confidence: str = "medium"

    def __post_init__(self):
        if not self.evidence_refs:
            raise ValueError(
                f"Claim 缺少 evidence_refs，禁止进入结果：{self.claim!r}")

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class LayerResult:
    passed: Optional[bool] = None
    score: Optional[float] = None
    grade: Optional[str] = None
    gate_reason: Optional[str] = None
    required_next_data: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class AnalysisResult:
    def __init__(self, candidate_id: str, market: str):
        self.analysis_id = f"analysis_{candidate_id}"
        self.candidate_id = candidate_id
        self.market = market
        self.layer_results: Dict[str, LayerResult] = {
            "L1": LayerResult(), "L2": LayerResult(), "L3": LayerResult()}
        self.priority_score: Dict[str, Any] = {
            "total": None, "achievable_max": None, "pct": None,
            "grade": None, "components": []}
        self.track_tags: List[str] = []
        self.recommendation: Dict[str, Any] = {
            "decision": None, "reason_summary": "", "risk_summary": "",
            "next_actions": []}
        self.claims: List[Claim] = []
        self.missing_fields: List[str] = []
        self.blocked_reasons: List[str] = []
        self.confidence: str = "low"
        self.created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION_RESULT,
            "analysis_id": self.analysis_id,
            "candidate_id": self.candidate_id,
            "market": self.market,
            "layer_results": {k: v.to_dict() for k, v in self.layer_results.items()},
            "priority_score": {
                **self.priority_score,
                "components": [c.to_dict() for c in self.priority_score["components"]],
            },
            "track_tags": self.track_tags,
            "recommendation": self.recommendation,
            "claims": [c.to_dict() for c in self.claims],
            "missing_fields": self.missing_fields,
            "blocked_reasons": self.blocked_reasons,
            "confidence": self.confidence,
            "created_at": self.created_at,
        }


def dump_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
