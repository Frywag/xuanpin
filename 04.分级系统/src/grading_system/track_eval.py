"""三赛道独立评估管道（tracks.v1）。

目标结构（《三赛道独立SAB分级_目标业务规则》§3）：

    同一事实候选
      ├─ 趋势新品准入   → 趋势评分 → 趋势 S/A/B
      ├─ 爆款改款准入   → 改款评分 → 改款 S/A/B
      └─ 长尾直接选品准入 → 长尾评分 → 长尾 S/A/B

关键语义：
  * 每个候选每个 run 恰好产生三条 TrackEvaluation（含 PENDING_DATA/REJECTED）；
  * PENDING_DATA / REJECTED 的正式 grade 为空（导出旧格式才附 legacy_grade=C）；
  * 趋势新品不设任何数据准入门槛（销量/评论/GMV/收藏/增长率/畅销榜）；
  * 独立站按「款型发现源」角色参与趋势判断，不因缺交易字段被结构性淘汰；
  * 三赛道独立 L2/L3 预算，不共享全局队列；
  * 本模块产出的等级带 rule_status=calibration —— 业务金标与参数批准前
    不是正式生产等级（交接清单 §14）。
本项目范围不含商品立项/产品定义/组合与上市回流（业务确认排除）。
"""
from __future__ import annotations

import dataclasses
import hashlib
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

from .models import CandidateDataPacket
from .scoring import GroupStats, eval_bands, percentile

# ================= 统一信号注册表（评分与 Gate 共用同一口径） =================
SIGNAL_REGISTRY = {
    # 交易类证据（可支撑「需求已验证/稳定需求」；评论数=有人买过的痕迹，计入）
    "transaction": [
        "market_metrics.sales_30d_units", "market_metrics.sales_7d_units",
        "market_metrics.sales_floor_units", "market_metrics.gmv_7d",
        "market_metrics.revenue_30d", "basic_facts.review_count",
        "market_metrics.bestseller_rank_text",
    ],
    # 热度类证据（趋势加权用，不得当成交）
    "heat": ["market_metrics.favorites_count", "market_metrics.video_views_max",
             "context.trend_tags"],
    # VOC 类证据
    "voc": ["basic_facts.rating", "basic_facts.review_count",
            "context.review_tags_raw"],
}

TRACK_IDS = ("trend_new", "hit_improvement", "long_tail_direct")


def _get(p: CandidateDataPacket, path: str):
    section, key = path.split(".", 1)
    return getattr(p, section, None).get(key) if hasattr(p, section) else None


def _has(p, path) -> bool:
    return _get(p, path) not in (None, [], {}, "")


@dataclasses.dataclass
class TrackEvaluation:
    run_id: str
    subject_type: str          # candidate / cluster
    subject_id: str
    track_id: str
    admission_status: str      # ELIGIBLE / PENDING_DATA / REJECTED
    admission_reasons: List[str]
    grade: Optional[str]       # 仅 ELIGIBLE 有值；PENDING/REJECTED 为空
    score: Optional[float]
    components: List[Dict[str, Any]]
    evidence_refs: List[str]
    missing_fields: List[str]
    refetch_tasks: List[str]
    risks: List[str]
    recommended_development_mode: str
    confidence: str
    cluster_id: Optional[str] = None
    rule_status: str = "calibration_pending_business_approval"
    rule_version: str = ""
    legacy_grade: Optional[str] = None   # 仅旧表兼容导出用

    def to_dict(self):
        return dataclasses.asdict(self)


# ================= 新鲜度评估（红线：观察≠上架；页面新≠款型新） =================

def assess_freshness(p: CandidateDataPacket):
    """填充 packet.freshness。只登记有证据的事实，推断必须标注。"""
    f = p.freshness
    # 页面新品集合位置（规则 7.3.A：新品集合中的明确位置是合法近期推出证据）
    rank_text = str(_get(p, "market_metrics.bestseller_rank_text") or "")
    if "new arrival" in rank_text.lower() or "new in" in rank_text.lower():
        f["new_arrival_flag"] = "bestseller_in_new_arrival_collection"
        f["freshness_status"] = "inferred"
        f["freshness_evidence_refs"] = p.evidence_for(
            "market_metrics.bestseller_rank_text")
    # first_seen_at 仅为系统首次观察：本轮无更早快照，不得作为近期推出证据
    if f["freshness_status"] == "unknown":
        p.mark_missing("freshness.listed_at")
    # 款型新颖度：需要历史款型库/多站点时序/业务确认，本轮均缺失
    if f["novelty_status"] == "unknown":
        p.mark_missing("freshness.style_novelty_evidence")


# ================= 机会簇骨架（召回+证据+版本+人工复核；阈值待标注） =================

_STYLE_TOKENS = ["bra", "bras", "shapewear", "bodysuit", "corset", "dress",
                 "dresses", "skirt", "skort", "tights", "pantyhose", "legging",
                 "tank", "tee", "romper", "jumpsuit", "swim", "bikini", "lace",
                 "floral", "plaid", "ruched", "wrap", "halter", "maxi", "mini"]


def _style_tokens(title: str) -> frozenset:
    words = set(re.findall(r"[a-z\-]+", (title or "").lower()))
    return frozenset(t for t in _STYLE_TOKENS if t in words)


def build_opportunity_clusters(packets: List[CandidateDataPacket],
                               cfg: Dict[str, Any], run_id: str) -> List[Dict[str, Any]]:
    """标题风格词召回的同趋势候选簇。召回≠确认：全部 confidence=low、
    human_review=pending；版本化，成员与关系证据保留。"""
    ccfg = cfg.get("clusters", {})
    buckets: Dict[frozenset, List[CandidateDataPacket]] = defaultdict(list)
    for p in packets:
        toks = _style_tokens(p.basic_facts.get("title") or "")
        if len(toks) >= 2:
            buckets[toks].append(p)
    clusters = []
    for toks, members in buckets.items():
        groups = {m.context.get("source_group") for m in members}
        if len(members) < ccfg.get("min_members", 2):
            continue
        if ccfg.get("cross_source_required", True) and len(groups) < 2:
            continue
        key = "+".join(sorted(toks))
        cid = "clu_" + hashlib.sha1(key.encode()).hexdigest()[:12]
        clusters.append({
            "cluster_id": cid, "cluster_version": 1,
            "representative_style": key,
            "relation_type": "same_trend",            # 召回级判定
            "confidence": ccfg.get("confidence_default", "low"),
            "human_review": ccfg.get("human_review_default", "pending"),
            "run_id": run_id,
            "members": [{
                "candidate_id": m.candidate_id,
                "source_group": m.context.get("source_group"),
                "relation": "same_trend_recall",
                "relation_evidence_refs": m.evidence_for("basic_facts.title"),
            } for m in members],
            "lineage": {"previous_version": None, "change_reason": "initial"},
        })
    return clusters


# ================= 三赛道准入与评分 =================

class TrackEvaluator:
    def __init__(self, cfg: Dict[str, Any], stats: GroupStats, run_id: str):
        self.cfg = cfg
        self.stats = stats
        self.run_id = run_id
        self.rule_version = cfg.get("rule_version", "tracks_v1_draft")
        self._peers: Dict[str, list] = defaultdict(list)

    # ---------- 公共 ----------

    def _mk(self, p, track_id, status, reasons, **kw) -> TrackEvaluation:
        tcfg = self.cfg["tracks"][track_id]
        ev = TrackEvaluation(
            run_id=self.run_id, subject_type="candidate",
            subject_id=p.candidate_id, track_id=track_id,
            admission_status=status, admission_reasons=reasons,
            grade=kw.get("grade"), score=kw.get("score"),
            components=kw.get("components", []),
            evidence_refs=kw.get("evidence_refs", []),
            missing_fields=kw.get("missing_fields", []),
            refetch_tasks=kw.get("refetch_tasks", []),
            risks=kw.get("risks", []),
            recommended_development_mode=tcfg["recommended_development_mode"],
            confidence=kw.get("confidence", "low"),
            cluster_id=kw.get("cluster_id"),
            rule_version=self.rule_version,
            legacy_grade=None if kw.get("grade") else "C")
        return ev

    def _grade_from(self, track_id: str, score: float) -> str:
        th = self.cfg["tracks"][track_id]["grade_thresholds_draft"]
        if score >= th["S"]:
            return "S"
        if score >= th["A"]:
            return "A"
        return "B"   # ELIGIBLE 的下限是 B（观察），不是 C

    def _txn_percentile(self, p) -> Optional[float]:
        """交易信号在同组内的相对位置（0-1）；用于「适量销量」相对口径。"""
        group = p.context.get("source_group") or p.platform
        my = None
        for path in SIGNAL_REGISTRY["transaction"][:5]:
            v = _get(p, path)
            if isinstance(v, dict):
                v = v.get("amount")
            if v is not None:
                my = float(v)
                my_path = path
                break
        if my is None:
            return None
        peers = []
        for q in self._peers.get(group, []):
            v = _get(q, my_path)
            if isinstance(v, dict):
                v = v.get("amount")
            if v is not None:
                peers.append(float(v))
        if len(peers) < 8:
            return None
        peers.sort()
        below = sum(1 for x in peers if x <= my)
        return below / len(peers)

    # ---------- 赛道一：趋势新品 ----------

    def eval_trend_new(self, p: CandidateDataPacket,
                       cluster_id: Optional[str]) -> TrackEvaluation:
        f = p.freshness
        reasons, missing = [], []
        # 近期推出证据（不设任何数据门槛；无销量绝不淘汰）
        has_recent = f.get("freshness_status") in ("confirmed", "inferred")
        if has_recent:
            reasons.append(f"近期推出证据：{f.get('new_arrival_flag') or f.get('listed_at')}"
                           f"（{f['freshness_status']}）")
        else:
            missing.append("近期推出证据（上架时间/新品标签/更早快照对照/业务确认）")
        # 款型新颖度证据
        has_novel = f.get("novelty_status") in ("confirmed", "inferred")
        if has_novel:
            reasons.append("款型新颖度证据成立")
        else:
            missing.append("款型新颖度证据（历史款型库对照/多站点时序/业务确认）")
        # 确证旧款 -> REJECTED
        if f.get("freshness_status") == "old" or f.get("novelty_status") == "old":
            return self._mk(p, "trend_new", "REJECTED",
                            ["已确认旧商品/旧款型（销量增长不能覆盖该结论）"],
                            evidence_refs=f.get("freshness_evidence_refs", []),
                            cluster_id=cluster_id)
        if not (has_recent and has_novel):
            return self._mk(p, "trend_new", "PENDING_DATA",
                            reasons + [f"缺：{m}" for m in missing],
                            missing_fields=missing,
                            refetch_tasks=self.cfg["tracks"]["trend_new"]["refetch_focus"],
                            cluster_id=cluster_id)
        # ELIGIBLE：草案评分（多平台相似/内容只加权）
        dims = self.cfg["tracks"]["trend_new"]["scoring_dims_draft"]
        comps, score, ev = [], 0.0, list(f.get("freshness_evidence_refs", []))
        ev += f.get("novelty_evidence_refs", [])
        fresh_pts = dims["freshness_credibility"] * (
            1.0 if f.get("freshness_status") == "confirmed" else 0.7)
        comps.append({"name": "新鲜度可信度", "score": round(fresh_pts, 1),
                      "max": dims["freshness_credibility"]})
        score += fresh_pts
        if cluster_id:
            pts = dims["cross_platform_resonance"] * 0.5   # 召回级共振（低置信）
            comps.append({"name": "跨平台款型共振(召回级)", "score": pts,
                          "max": dims["cross_platform_resonance"]})
            score += pts
        if p.market_metrics.get("trend_signals") or _has(p, "market_metrics.video_views_max"):
            pts = dims["content_diffusion"] * 0.5
            comps.append({"name": "内容扩散", "score": pts, "max": dims["content_diffusion"]})
            score += pts
            ev += p.evidence_for("context.trend_tags") + p.evidence_for(
                "market_metrics.video_views_max")
        if p.context.get("design_signals") or p.style_attributes:
            pts = dims["style_definability"] * 0.7
            comps.append({"name": "款型可定义性", "score": pts,
                          "max": dims["style_definability"]})
            score += pts
            ev += p.evidence_for("context.design_signals")
        pct = round(score / sum(v for k, v in dims.items() if k != "risk_floor") * 100, 1)
        return self._mk(p, "trend_new", "ELIGIBLE", reasons, grade=self._grade_from(
            "trend_new", pct), score=pct, components=comps,
            evidence_refs=sorted(set(ev)), confidence="medium",
            cluster_id=cluster_id,
            refetch_tasks=self.cfg["tracks"]["trend_new"]["refetch_focus"])

    # ---------- 赛道二：爆款改款 ----------

    def eval_hit_improvement(self, p: CandidateDataPacket) -> TrackEvaluation:
        tcfg = self.cfg["tracks"]["hit_improvement"]["admission"]
        reasons, missing = [], []
        # ① 适量销量/需求（相对口径）
        txn_pct = self._txn_percentile(p)
        has_txn = any(_has(p, s) for s in SIGNAL_REGISTRY["transaction"][:5])
        adequate = (txn_pct is not None
                    and txn_pct >= tcfg["adequate_demand_draft"]["min_transaction_percentile"]) \
                   or (txn_pct is None and has_txn)
        if adequate:
            reasons.append(f"需求基础成立（组内交易信号分位 {txn_pct}）")
        elif has_txn:
            reasons.append("有交易信号但低于「适量」草案分位")
        else:
            missing.append("交易类需求证据（销量/GMV/评论积累）")
        # ② 评分偏低（相对+样本量）
        rating = _get(p, "basic_facts.rating")
        rc = _get(p, "basic_facts.review_count") or 0
        low_cfg = tcfg["low_rating_draft"]
        low_rating = None
        if rating is None:
            missing.append("评分")
        elif rc < low_cfg["min_review_sample"]:
            missing.append(f"评分样本不足（评论 {rc:.0f} < {low_cfg['min_review_sample']}，"
                           "低分可能是小样本噪音）")
        else:
            low_rating = rating <= low_cfg["absolute_ceiling"]
            reasons.append(f"评分 {rating}（样本 {rc:.0f}）"
                           + ("显著偏低" if low_rating else "未显著偏低"))
        # ③ 负面聚类较多
        clusters = p.product_opportunity.get("negative_review_clusters") or []
        n_clusters = len(clusters)
        enough_clusters = n_clusters >= tcfg["negative_clusters_draft"]["min_clusters"]
        if not clusters and not p.context.get("review_tags_raw"):
            missing.append("评论全文/负面聚类（缺 VOC 数据）")
        # 判定
        if low_rating is False or (has_txn and rating is not None
                                   and rc >= low_cfg["min_review_sample"]
                                   and not low_rating):
            return self._mk(p, "hit_improvement", "REJECTED",
                            reasons + ["评分正常，不构成改款机会"],
                            evidence_refs=p.evidence_for("basic_facts.rating"))
        if missing or not enough_clusters:
            if not enough_clusters and n_clusters:
                missing.append(f"负面聚类不足（{n_clusters} < "
                               f"{tcfg['negative_clusters_draft']['min_clusters']}）")
            return self._mk(p, "hit_improvement", "PENDING_DATA",
                            reasons + [f"缺：{m}" for m in missing],
                            missing_fields=missing,
                            refetch_tasks=self.cfg["tracks"]["hit_improvement"]["refetch_focus"])
        # 三项联合成立 -> ELIGIBLE
        dims = self.cfg["tracks"]["hit_improvement"]["scoring_dims_draft"]
        base = sum(v for k, v in dims.items() if k != "risk_floor")
        score = (dims["demand_validated"] * min(1.0, (txn_pct or 0.5) + 0.3)
                 + dims["pain_intensity"] * min(1.0, n_clusters / 4)
                 + dims["fixability"] * 0.6)
        pct = round(score / base * 100, 1)
        ev = (p.evidence_for("basic_facts.rating")
              + p.evidence_for("context.review_tags_raw")
              + [e for c in clusters for e in c.get("evidence_refs", [])])
        return self._mk(p, "hit_improvement", "ELIGIBLE", reasons,
                        grade=self._grade_from("hit_improvement", pct), score=pct,
                        components=[{"name": "三项联合准入", "score": pct, "max": 100}],
                        evidence_refs=sorted(set(ev)), confidence="medium",
                        refetch_tasks=self.cfg["tracks"]["hit_improvement"]["refetch_focus"])

    # ---------- 赛道三：长尾直接选品 ----------

    def eval_long_tail(self, p: CandidateDataPacket) -> TrackEvaluation:
        tcfg = self.cfg["tracks"]["long_tail_direct"]["admission"]
        reasons, missing = [], []
        has_txn = any(_has(p, s) for s in SIGNAL_REGISTRY["transaction"][:6])
        growth = _get(p, "market_metrics.sales_growth_rate")
        spike = growth is not None and growth >= tcfg["stable_demand_draft"][
            "exclude_single_spike_growth"]
        if has_txn and not spike:
            reasons.append("存在交易类需求证据且非单次异常暴涨")
        elif spike:
            reasons.append(f"增长率 {growth:+.0%} 属爆发形态，稳定性待多周期验证")
            missing.append("多周期需求稳定性证据")
        else:
            missing.append("交易类稳定需求证据（热度信号不计入）")
        rating = _get(p, "basic_facts.rating")
        if rating is not None and rating < tcfg["no_blocking_quality_draft"]["rating_floor"]:
            return self._mk(p, "long_tail_direct", "REJECTED",
                            reasons + [f"评分 {rating} 低于直接承接下限，应走改款赛道"],
                            evidence_refs=p.evidence_for("basic_facts.rating"))
        risks = []
        if _get(p, "competition_metrics.seller_type") == "BRAND":
            risks.append("品牌自营，直接跟卖有 IP 风险（人工确认前不阻断）")
        if not _has(p, "basic_facts.price"):
            missing.append("价格与币种（价格带匹配无法判断）")
        if missing:
            return self._mk(p, "long_tail_direct", "PENDING_DATA",
                            reasons + [f"缺：{m}" for m in missing],
                            missing_fields=missing, risks=risks,
                            refetch_tasks=self.cfg["tracks"]["long_tail_direct"]["refetch_focus"])
        dims = self.cfg["tracks"]["long_tail_direct"]["scoring_dims_draft"]
        base = sum(v for k, v in dims.items() if k != "risk_floor")
        txn_pct = self._txn_percentile(p) or 0.5
        pos = self.stats.price_position(p)
        score = (dims["demand_stability"] * min(1.0, txn_pct + 0.3)
                 + dims["competition_breakthrough"] * 0.5
                 + dims["price_profit_structure"] * (
                     1.0 if pos == "within_p25_p75" else 0.5)
                 + dims["fact_completeness"] * min(1.0, len(p.evidence_pack) / 10))
        pct = round(score / base * 100, 1)
        ev = p.evidence_for("basic_facts.price") + p.evidence_for(
            "basic_facts.review_count")
        for s in SIGNAL_REGISTRY["transaction"][:5]:
            ev += p.evidence_for(s)
        return self._mk(p, "long_tail_direct", "ELIGIBLE", reasons,
                        grade=self._grade_from("long_tail_direct", pct), score=pct,
                        components=[{"name": "稳定需求×竞争×价格结构", "score": pct,
                                     "max": 100}],
                        evidence_refs=sorted(set(ev)), confidence="medium",
                        risks=risks,
                        refetch_tasks=self.cfg["tracks"]["long_tail_direct"]["refetch_focus"])

    # ---------- 运行 ----------

    def run(self, packets: List[CandidateDataPacket]):
        # 组内 peers 索引（适量销量相对口径用）
        self._peers = defaultdict(list)
        for p in packets:
            self._peers[p.context.get("source_group") or p.platform].append(p)
        for p in packets:
            assess_freshness(p)
        clusters = build_opportunity_clusters(packets, self.cfg, self.run_id)
        cluster_of = {}
        for c in clusters:
            for m in c["members"]:
                cluster_of.setdefault(m["candidate_id"], c["cluster_id"])
        evaluations: List[TrackEvaluation] = []
        for p in packets:
            evaluations.append(self.eval_trend_new(p, cluster_of.get(p.candidate_id)))
            evaluations.append(self.eval_hit_improvement(p))
            evaluations.append(self.eval_long_tail(p))
        # 赛道独立 L2/L3 预算（互不挤占）
        budgets = {}
        for tid in TRACK_IDS:
            tcfg = self.cfg["tracks"][tid]
            eligible = sorted([e for e in evaluations
                               if e.track_id == tid and e.admission_status == "ELIGIBLE"],
                              key=lambda e: -(e.score or 0))
            budgets[tid] = {
                "l2_queue": [e.subject_id for e in eligible[: tcfg["l2_budget_draft"]]],
                "l3_queue": [e.subject_id for e in eligible[: tcfg["l3_budget_draft"]]],
            }
        return evaluations, clusters, budgets
