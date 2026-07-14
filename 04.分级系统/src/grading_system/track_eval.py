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

from .models import CandidateDataPacket, EvidenceRef
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
    # 准入依据（业务指示 2026-07-14 临时可跑规则）：confirmed=证据确证准入；
    # provisional_rule=占位规则准入（数据源结构性缺失，最终门限待人工确认）
    admission_basis: str = "confirmed"
    provisional_notes: List[str] = dataclasses.field(default_factory=list)

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
            legacy_grade=None if kw.get("grade") else "C",
            admission_basis=kw.get("admission_basis", "confirmed"),
            provisional_notes=kw.get("provisional_notes", []))
        return ev

    _GRADE_ORDER = {"S": 0, "A": 1, "B": 2}

    def _grade_from(self, track_id: str, score: float,
                    cap: Optional[str] = None) -> str:
        th = self.cfg["tracks"][track_id]["grade_thresholds_draft"]
        if score >= th["S"]:
            g = "S"
        elif score >= th["A"]:
            g = "A"
        else:
            g = "B"   # ELIGIBLE 的下限是 B（观察），不是 C
        # 占位等级上限（临时规则准入时生效；人工在 tracks_v1.yaml 调整）
        if cap and self._GRADE_ORDER[g] < self._GRADE_ORDER[cap]:
            return cap
        return g

    def _risk_penalty(self, dims: Dict[str, Any], risks: List[str]) -> float:
        """风险扣减（草案）：每条已登记风险按 risk_floor 的 1/3 扣，扣满为止。"""
        if not risks:
            return 0.0
        return dims.get("risk_floor", 0) * min(1.0, len(risks) / 3)

    def _apply_business_confirmations(self, packets: List[CandidateDataPacket]):
        """业务确认证据通道（规则 §7.3 A.6/B.3）：configs/business_confirmations.yaml
        中人工登记的确认作为合法证据写入 packet.freshness（confirmed/old），
        confirmed 走确证准入，old 触发 REJECTED；每条确认生成可追溯 EvidenceRef。"""
        entries = (self.cfg.get("business_confirmations") or {}).get("confirmations") or []
        if not entries:
            return
        by_cid = {p.candidate_id: p for p in packets}
        for i, ent in enumerate(entries):
            p = by_cid.get(ent.get("candidate_id"))
            field = ent.get("field")            # freshness | novelty
            status = ent.get("status")          # confirmed | old
            if p is None or field not in ("freshness", "novelty") \
                    or status not in ("confirmed", "old"):
                continue
            eid = f"ev_bizconfirm_{p.candidate_id}_{field}"
            ref = EvidenceRef(eid, "business_confirmation", "manual",
                              "business_confirmations.yaml", f"freshness.{field}",
                              "04.分级系统/configs/business_confirmations.yaml",
                              f"confirmations[{i}]", confidence="high",
                              note=ent.get("note", "业务确认"))
            p.add_evidence(ref, f"freshness.{field}")
            if field == "freshness":
                p.freshness["freshness_status"] = status
                p.freshness.setdefault("freshness_evidence_refs", []).append(eid)
            else:
                p.freshness["novelty_status"] = status
                p.freshness.setdefault("novelty_evidence_refs", []).append(eid)

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
    # 硬性保证（业务确认 2026-07-13）：本方法不得读取 rating/review_count/
    # 销量/GMV/收藏/增长率/畅销榜等数据点位——独立站等发现源与交易平台
    # 公平竞争趋势 S/A/B；评论全量采集与分析后置为 L3 终选验证事项。
    TREND_FORBIDDEN_PATHS = (
        "basic_facts.rating", "basic_facts.review_count",
        "market_metrics.sales_30d_units", "market_metrics.sales_7d_units",
        "market_metrics.sales_floor_units", "market_metrics.gmv_7d",
        "market_metrics.revenue_30d", "market_metrics.favorites_count",
        "market_metrics.sales_growth_rate")

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
        prov = self.cfg["tracks"]["trend_new"].get("provisional_admission", {})
        prov_notes = []
        if not (has_recent and has_novel):
            if not prov.get("enabled"):
                return self._mk(p, "trend_new", "PENDING_DATA",
                                reasons + [f"缺：{m}" for m in missing],
                                missing_fields=missing,
                                refetch_tasks=self.cfg["tracks"]["trend_new"].get(
                                    "l2_focus", self.cfg["tracks"]["trend_new"]["refetch_focus"]),
                                cluster_id=cluster_id)
            # 临时可跑规则（业务指示 2026-07-14）：缺失多为数据源结构性没有，
            # 无「旧款确证」即临时准入，照常按证据强度评分；缺失照记、补证照排
            if not has_recent:
                prov_notes.append("近期推出证据缺失（源无该点位）→ 占位视为待验证新品")
            if not has_novel:
                prov_notes.append("款型新颖度证据缺失（历史款型库未建）→ 无旧款确证即占位视为新款")
            reasons.append("临时规则准入（占位，最终门限与字段待人工确认）")
        # ELIGIBLE：草案评分（多平台相似/内容只加权）
        dims = self.cfg["tracks"]["trend_new"]["scoring_dims_draft"]
        comps, score, ev = [], 0.0, list(f.get("freshness_evidence_refs", []))
        ev += f.get("novelty_evidence_refs", [])
        fresh_factor = (1.0 if f.get("freshness_status") == "confirmed"
                        else 0.7 if f.get("freshness_status") == "inferred"
                        else prov.get("missing_freshness_factor", 0.3))
        fresh_pts = dims["freshness_credibility"] * fresh_factor
        comps.append({"name": "新鲜度可信度", "score": round(fresh_pts, 1),
                      "max": dims["freshness_credibility"]})
        score += fresh_pts
        if cluster_id:
            pts = dims["cross_platform_resonance"] * 0.5   # 召回级共振（低置信）
            comps.append({"name": "跨平台款型共振(召回级)", "score": pts,
                          "max": dims["cross_platform_resonance"]})
            score += pts
        else:
            comps.append({"name": "跨平台款型共振(召回级)", "score": 0,
                          "max": dims["cross_platform_resonance"],
                          "note": "留槽：未命中机会簇（加权项非门槛）"})
        if p.market_metrics.get("trend_signals") or _has(p, "market_metrics.video_views_max"):
            pts = dims["content_diffusion"] * 0.5
            comps.append({"name": "内容扩散", "score": pts, "max": dims["content_diffusion"]})
            score += pts
            ev += p.evidence_for("context.trend_tags") + p.evidence_for(
                "market_metrics.video_views_max")
        else:
            comps.append({"name": "内容扩散", "score": 0,
                          "max": dims["content_diffusion"],
                          "note": "留槽：本轮无内容信号，补证后计分"})
        if p.context.get("design_signals") or p.style_attributes:
            pts = dims["style_definability"] * 0.7
            comps.append({"name": "款型可定义性", "score": pts,
                          "max": dims["style_definability"]})
            score += pts
            ev += p.evidence_for("context.design_signals")
        else:
            comps.append({"name": "款型可定义性", "score": 0,
                          "max": dims["style_definability"],
                          "note": "留槽：缺设计元素/款型属性证据"})
        # 目标市场适配（价格带代理，草案）：价格不是趋势赛道禁读信号；
        # 缺价格按留槽 0 分处理（不淘汰）
        price = _get(p, "basic_facts.price")
        if isinstance(price, dict) and price.get("amount") is not None:
            pos = self.stats.price_position(p)
            pts = dims["market_fit"] * (0.7 if pos == "within_p25_p75" else 0.3)
            comps.append({"name": "目标市场适配(价格带代理)", "score": round(pts, 1),
                          "max": dims["market_fit"]})
            score += pts
            ev += p.evidence_for("basic_facts.price")
        else:
            comps.append({"name": "目标市场适配(价格带代理)", "score": 0,
                          "max": dims["market_fit"], "note": "留槽：缺价格"})
        pct = round(max(0.0, score)
                    / sum(v for k, v in dims.items() if k != "risk_floor") * 100, 1)
        provisional = bool(prov_notes)
        return self._mk(p, "trend_new", "ELIGIBLE", reasons, grade=self._grade_from(
            "trend_new", pct, cap=prov.get("grade_cap") if provisional else None),
            score=pct, components=comps,
            evidence_refs=sorted(set(ev)),
            confidence="low" if provisional else "medium",
            cluster_id=cluster_id,
            missing_fields=missing,
            admission_basis="provisional_rule" if provisional else "confirmed",
            provisional_notes=prov_notes,
            refetch_tasks=self.cfg["tracks"]["trend_new"].get(
                "l2_focus" if provisional else "l3_focus",
                self.cfg["tracks"]["trend_new"]["refetch_focus"]))

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
        # ② 评分偏低（相对口径优先 + 绝对警戒线兜底 + 样本量，均为草案）
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
            # 相对口径：低于同组（平台/来源组）评分分位（规则 §8.4，样本≥8 才启用）
            group = p.context.get("source_group") or p.platform
            peer_ratings = sorted(
                r for q in self._peers.get(group, [])
                if (r := _get(q, "basic_facts.rating")) is not None)
            rel_low, rel_note = None, ""
            if len(peer_ratings) >= 8:
                idx = max(0, int(len(peer_ratings)
                                 * low_cfg.get("relative_percentile", 0.25)) - 1)
                p_low = peer_ratings[idx]
                rel_low = rating <= p_low
                rel_note = f"，组内p{int(low_cfg.get('relative_percentile', 0.25)*100)}={p_low}"
            low_rating = bool(rel_low) or rating <= low_cfg["absolute_ceiling"]
            reasons.append(f"评分 {rating}（样本 {rc:.0f}{rel_note}）"
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
        prov = self.cfg["tracks"]["hit_improvement"].get("provisional_admission", {})
        prov_notes = []
        if missing or not enough_clusters:
            if not enough_clusters and n_clusters:
                missing.append(f"负面聚类不足（{n_clusters} < "
                               f"{tcfg['negative_clusters_draft']['min_clusters']}）")
            # 临时可跑规则（业务指示 2026-07-14）：评论全文属源结构性缺失时，
            # 有交易信号 + 确证低评分（样本达标）即可占位豁免负面聚类准入；
            # 低分前提本身无法确证（缺评分/样本不足/缺交易信号）仍 PENDING
            if (prov.get("enabled")
                    and prov.get("waive_negative_clusters_if_low_rating")
                    and low_rating is True and has_txn):
                prov_notes.append("负面聚类证据缺失（源无评论全文）→ 占位豁免准入；"
                                  "痛点未经聚类确认，S/A 须待评论回补")
                reasons.append("临时规则准入（占位，最终门限与字段待人工确认）")
            else:
                return self._mk(p, "hit_improvement", "PENDING_DATA",
                                reasons + [f"缺：{m}" for m in missing],
                                missing_fields=missing,
                                refetch_tasks=self.cfg["tracks"]["hit_improvement"]["refetch_focus"])
        # 三项联合成立（或占位豁免）-> ELIGIBLE
        dims = self.cfg["tracks"]["hit_improvement"]["scoring_dims_draft"]
        base = sum(v for k, v in dims.items() if k != "risk_floor")
        d_demand = dims["demand_validated"] * min(1.0, (txn_pct or 0.5) + 0.3)
        d_pain = dims["pain_intensity"] * min(1.0, n_clusters / 4)
        d_fix = dims["fixability"] * 0.6
        comps = [
            {"name": "需求已验证程度", "score": round(d_demand, 1),
             "max": dims["demand_validated"]},
            {"name": "痛点强度(聚类数量)", "score": round(d_pain, 1),
             "max": dims["pain_intensity"],
             **({"note": "留槽：负面聚类占位豁免，待评论全文回补"}
                if not n_clusters else {})},
            {"name": "可改性(草案系数)", "score": round(d_fix, 1),
             "max": dims["fixability"]},
            {"name": "改后差异化", "score": 0,
             "max": dims["differentiation_after_fix"],
             "note": "留槽：L2/L3 改良方案评审后计分"},
            {"name": "竞争与价格空间", "score": 0,
             "max": dims["competition_price_room"],
             "note": "留槽：L2/L3 竞品比对后计分"},
        ]
        score = d_demand + d_pain + d_fix
        pct = round(max(0.0, score) / base * 100, 1)
        ev = (p.evidence_for("basic_facts.rating")
              + p.evidence_for("context.review_tags_raw")
              + [e for c in clusters for e in c.get("evidence_refs", [])])
        provisional = bool(prov_notes)
        return self._mk(p, "hit_improvement", "ELIGIBLE", reasons,
                        grade=self._grade_from(
                            "hit_improvement", pct,
                            cap=prov.get("grade_cap") if provisional else None),
                        score=pct,
                        components=comps,
                        evidence_refs=sorted(set(ev)),
                        confidence="low" if provisional else "medium",
                        missing_fields=missing,
                        admission_basis="provisional_rule" if provisional else "confirmed",
                        provisional_notes=prov_notes,
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
        price_missing = not _has(p, "basic_facts.price")
        if price_missing:
            missing.append("价格与币种（价格带匹配无法判断）")
        prov = self.cfg["tracks"]["long_tail_direct"].get("provisional_admission", {})
        prov_notes = []
        if missing:
            # 临时可跑规则（业务指示 2026-07-14）：仅缺价格时可占位准入
            # （价格/利润结构不可判 -> 占位等级上限）；无交易证据仍 PENDING
            # （稳定需求是本赛道前提），单次暴涨排除不豁免（抗噪红线）
            only_price = (price_missing and has_txn and not spike
                          and all(m.startswith("价格") for m in missing))
            if prov.get("enabled") and prov.get("allow_missing_price") and only_price:
                prov_notes.append("价格缺失（源无该点位）→ 占位准入；"
                                  "价格/利润结构不可判，等级上限见占位规则")
                reasons.append("临时规则准入（占位，最终门限与字段待人工确认）")
            else:
                return self._mk(p, "long_tail_direct", "PENDING_DATA",
                                reasons + [f"缺：{m}" for m in missing],
                                missing_fields=missing, risks=risks,
                                refetch_tasks=self.cfg["tracks"]["long_tail_direct"]["refetch_focus"])
        dims = self.cfg["tracks"]["long_tail_direct"]["scoring_dims_draft"]
        base = sum(v for k, v in dims.items() if k != "risk_floor")
        txn_pct = self._txn_percentile(p) or 0.5
        if price_missing:
            price_factor = prov.get("missing_price_factor", 0.3)
        else:
            pos = self.stats.price_position(p)
            price_factor = 1.0 if pos == "within_p25_p75" else 0.5
        d_demand = dims["demand_stability"] * min(1.0, txn_pct + 0.3)
        d_comp = dims["competition_breakthrough"] * 0.5
        d_price = dims["price_profit_structure"] * price_factor
        d_fact = dims["fact_completeness"] * min(1.0, len(p.evidence_pack) / 10)
        penalty = self._risk_penalty(dims, risks)   # 风险扣减（草案）
        comps = [
            {"name": "需求稳定性", "score": round(d_demand, 1),
             "max": dims["demand_stability"]},
            {"name": "竞争可突破", "score": round(d_comp, 1),
             "max": dims["competition_breakthrough"],
             "note": "中性占位0.5：竞争结构字段当前源结构性缺失，补证后计分"},
            {"name": "价格带与利润结构"
             + ("(缺价格占位)" if price_missing else ""),
             "score": round(d_price, 1), "max": dims["price_profit_structure"]},
            {"name": "事实完整度", "score": round(d_fact, 1),
             "max": dims["fact_completeness"]},
            {"name": "直接承接可行性", "score": 0,
             "max": dims["direct_feasibility"],
             "note": "留槽：L2/L3 供应/成本/MOQ 事实回补后计分"},
        ]
        if penalty:
            comps.append({"name": "风险扣减(草案)", "score": round(penalty, 1),
                          "max": dims.get("risk_floor", 0),
                          "note": "；".join(risks)[:120]})
        score = d_demand + d_comp + d_price + d_fact + penalty
        pct = round(max(0.0, score) / base * 100, 1)
        ev = p.evidence_for("basic_facts.price") + p.evidence_for(
            "basic_facts.review_count")
        for s in SIGNAL_REGISTRY["transaction"][:5]:
            ev += p.evidence_for(s)
        provisional = bool(prov_notes)
        return self._mk(p, "long_tail_direct", "ELIGIBLE", reasons,
                        grade=self._grade_from(
                            "long_tail_direct", pct,
                            cap=prov.get("grade_cap_if_missing_price")
                            if provisional else None),
                        score=pct,
                        components=comps,
                        evidence_refs=sorted(set(ev)),
                        confidence="low" if provisional else "medium",
                        risks=risks,
                        missing_fields=missing,
                        admission_basis="provisional_rule" if provisional else "confirmed",
                        provisional_notes=prov_notes,
                        refetch_tasks=self.cfg["tracks"]["long_tail_direct"]["refetch_focus"])

    # ---------- 运行 ----------

    def run(self, packets: List[CandidateDataPacket]):
        # 组内 peers 索引（适量销量相对口径用）
        self._peers = defaultdict(list)
        for p in packets:
            self._peers[p.context.get("source_group") or p.platform].append(p)
        for p in packets:
            assess_freshness(p)
        # 业务确认证据（人工登记，见 configs/business_confirmations.yaml）
        self._apply_business_confirmations(packets)
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
        # 赛道独立预算（互不挤占）。两类预算拆分（P0-03 解死锁）：
        #   refetch_queue —— 准入补证预算：临时规则准入但证据未补齐的高分候选优先
        #                    （补证后可确证/升级），其后是 PENDING_DATA 待补候选；
        #   l2/l3_queue  —— 已准入深挖预算：从 ELIGIBLE（含临时准入）按分取。
        #   同一候选可同时占深挖与补证名额（两类预算目的不同，不算冲突）。
        budgets = {}
        for tid in TRACK_IDS:
            tcfg = self.cfg["tracks"][tid]
            eligible = sorted([e for e in evaluations
                               if e.track_id == tid and e.admission_status == "ELIGIBLE"],
                              key=lambda e: -(e.score or 0))
            prov_needy = sorted(
                [e for e in eligible
                 if e.admission_basis == "provisional_rule" and e.missing_fields],
                key=lambda e: (-(e.score or 0), e.subject_id))
            pending = sorted([e for e in evaluations
                              if e.track_id == tid and e.admission_status == "PENDING_DATA"],
                             key=lambda e: (-len(e.evidence_refs), e.subject_id))
            refetch_pool = prov_needy + pending
            budgets[tid] = {
                "l2_queue": [e.subject_id for e in eligible[: tcfg["l2_budget_draft"]]],
                "l3_queue": [e.subject_id for e in eligible[: tcfg["l3_budget_draft"]]],
                "refetch_queue": [
                    {"subject_id": e.subject_id, "missing": e.missing_fields,
                     "tasks": e.refetch_tasks, "status": "open",
                     "owner": None, "due": None,
                     "basis": ("provisional_eligible"
                               if e.admission_status == "ELIGIBLE" else "pending")}
                    for e in refetch_pool[: tcfg.get("refetch_budget_draft", 0)]],
            }
        return evaluations, clusters, budgets
