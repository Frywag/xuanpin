"""三赛道主链 v2（2026-07-16 业务重构）。

流程：公共 L0 技术检查 → 开发方向判定（铺货/改款）→ 三赛道扇出
（新品/爆款/长尾，按销量表现形态）→ 各赛道内部 L1→L2→L3 分层淘汰
（S 必须走完 L3）→ 各自 S/A/B。趋势独立为词级 ABCDE 体系
（头部独立站+社媒发现源；小红书源缺失已登记）。

红线不变：不编造数值、结论绑证据、缺失记 missing 不记 0、
等级确定性可重算、rule_status=calibration。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from .models import CandidateDataPacket
from .track_eval import (SIGNAL_REGISTRY, TrackEvaluation, TrackEvaluator,
                         _get, _has, assess_freshness)

TRACK_IDS_V2 = ("new_product", "hit", "long_tail")

_WORD_RE = re.compile(r"[a-z][a-z\-]{2,}")
_GENERIC_WORDS = {"women", "womens", "the", "and", "for", "with", "set",
                  "piece", "pack", "size", "plus", "new", "sale", "shop",
                  "free", "people", "urban", "blue", "black", "white",
                  "pink", "red", "green", "one", "two", "high", "low"}


def title_feature_words(title: str) -> List[str]:
    """标题款式/设计特性词（去通用词；趋势词体系与新品词频稀有度共用）。"""
    return [w for w in _WORD_RE.findall((title or "").lower())
            if w not in _GENERIC_WORDS]


def build_amazon_word_freq(packets: List[CandidateDataPacket]) -> Counter:
    """Amazon 标题语料词频（新品特性词稀有度对照；无 Amazon 源则为空=留槽）。"""
    freq: Counter = Counter()
    for p in packets:
        grp = (p.context.get("source_group") or p.platform or "").lower()
        if "amazon" in grp:
            freq.update(set(title_feature_words(p.basic_facts.get("title"))))
    return freq


class TracksV2Evaluator(TrackEvaluator):
    """复用 v1 的组内分位/等级/证据机制，实现 v2 主链语义。"""

    def __init__(self, cfg, stats, run_id, amazon_freq: Counter = None):
        super().__init__(cfg, stats, run_id)
        self.amazon_freq = amazon_freq or Counter()

    # ---------- 公共 L0 技术检查（唯一公共 Gate） ----------

    def l0_check(self, p: CandidateDataPacket) -> List[str]:
        problems = []
        if not p.candidate_id:
            problems.append("候选身份无效")
        if not p.basic_facts.get("title"):
            problems.append("缺商品标题（身份/证据不可核）")
        if not p.evidence_pack:
            problems.append("无任何字段证据")
        return problems

    # ---------- 开发方向（进入系统第一步；影响赛道门限） ----------

    def determine_direction(self, p: CandidateDataPacket) -> Dict[str, Any]:
        dcfg = self.cfg.get("development_direction", {})
        rating = _get(p, "basic_facts.rating")
        rc = _get(p, "basic_facts.review_count") or 0
        options, notes, basis = ["铺货"], [], "confirmed"
        neg_ratio = None                       # 差评比例字段当前源结构性缺失
        if rc >= dcfg.get("gaiKuan_min_reviews", 50):
            if neg_ratio is not None:
                if neg_ratio >= dcfg.get("gaiKuan_min_negative_ratio", 0.15):
                    options.append("改款")
            elif rating is not None and rating <= dcfg.get(
                    "gaiKuan_proxy_rating_ceiling", 4.2):
                options.append("改款")
                basis = "provisional_rule"
                notes.append("差评比例字段缺失→以评分≤"
                             f"{dcfg.get('gaiKuan_proxy_rating_ceiling', 4.2)} 代理（占位）")
            else:
                p.mark_missing("voc.negative_review_ratio")
        direction = {"options": options, "basis": basis, "notes": notes,
                     "evidence_refs": p.evidence_for("basic_facts.rating")
                     + p.evidence_for("basic_facts.review_count")}
        p.context["development_direction"] = direction
        return direction

    def _relax(self, p: CandidateDataPacket, threshold: float) -> float:
        """改款方向：有差评的产品销量要求适当放低（业务指示）。"""
        d = p.context.get("development_direction") or {}
        if "改款" in d.get("options", []):
            return threshold * self.cfg.get("development_direction", {}).get(
                "sales_requirement_relax_factor", 0.7)
        return threshold

    # ---------- 赛道 L1（低成本准入 + 基础分） ----------

    def eval_new_product(self, p, cluster_id=None) -> TrackEvaluation:
        tcfg = self.cfg["tracks"]["new_product"]
        adm, dims = tcfg["admission"], tcfg["scoring_dims_draft"]
        f = p.freshness
        reasons, missing, prov_notes = [], [], []
        if f.get("freshness_status") == "old":
            return self._mk(p, "new_product", "REJECTED", ["确证旧品（近四个月窗口外）"],
                            evidence_refs=f.get("freshness_evidence_refs", []))
        fresh = f.get("freshness_status") in ("confirmed", "inferred")
        if fresh:
            reasons.append(f"近期推出证据（{f['freshness_status']}）")
        elif adm.get("provisional_freshness"):
            missing.append(f"上架时间证据（近{adm.get('recent_window_days', 120)}天窗口不可判）")
            prov_notes.append("上架时间缺失（源无该点位）→ 占位视为待验证新品")
        else:
            return self._mk(p, "new_product", "PENDING_DATA", ["缺上架时间证据"],
                            missing_fields=["上架时间证据"],
                            refetch_tasks=tcfg["refetch_focus"])
        txn = self._txn_percentile(p)
        need = self._relax(p, adm.get("min_txn_percentile", 0.4))
        if txn is None:
            missing.append("近期销量表现（无组内交易分位）")
            return self._mk(p, "new_product", "PENDING_DATA",
                            reasons + ["缺近期销量证据"], missing_fields=missing,
                            refetch_tasks=tcfg["refetch_focus"],
                            provisional_notes=prov_notes)
        if txn < need:
            return self._mk(p, "new_product", "REJECTED",
                            reasons + [f"近期销量分位 {txn:.2f} 低于门限 {need:.2f}"
                                       "（销量表现不足，非「表现不错的新品」）"],
                            evidence_refs=self._txn_ev(p))
        reasons.append(f"近期销量表现不错（组内分位 {txn:.2f} ≥ {need:.2f}"
                       + ("，改款方向放宽" if need < adm.get("min_txn_percentile", 0.4)
                          else "") + "）")
        comps, ev = [], list(f.get("freshness_evidence_refs", [])) + self._txn_ev(p)
        fresh_factor = (1.0 if f.get("freshness_status") == "confirmed"
                        else 0.7 if f.get("freshness_status") == "inferred" else 0.3)
        comps.append({"name": "新鲜度可信度", "score": round(dims["freshness_credibility"]
                                                       * fresh_factor, 1),
                      "max": dims["freshness_credibility"]})
        comps.append({"name": "销量势能", "score": round(dims["sales_momentum"]
                                                     * min(1.0, txn + 0.2), 1),
                      "max": dims["sales_momentum"]})
        rarity = self._word_rarity(p)
        if rarity is None:
            comps.append({"name": "特性词稀有度(Amazon对照)", "score": 0,
                          "max": dims["style_word_rarity"],
                          "note": "留槽：无 Amazon 语料或无特性词"})
        else:
            comps.append({"name": "特性词稀有度(Amazon对照)",
                          "score": round(dims["style_word_rarity"] * rarity, 1),
                          "max": dims["style_word_rarity"]})
        comps.append({"name": "跨平台共振(召回级)",
                      "score": dims["cross_platform_resonance"] * 0.5 if cluster_id else 0,
                      "max": dims["cross_platform_resonance"],
                      **({} if cluster_id else {"note": "留槽：未命中机会簇"})})
        comps.append({"name": "事实完整度",
                      "score": round(dims["fact_completeness"]
                                     * min(1.0, len(p.evidence_pack) / 10), 1),
                      "max": dims["fact_completeness"]})
        return self._finalize(p, "new_product", reasons, comps, ev, missing,
                              prov_notes, cluster_id=cluster_id)

    def eval_hit(self, p) -> TrackEvaluation:
        tcfg = self.cfg["tracks"]["hit"]
        adm, dims = tcfg["admission"], tcfg["scoring_dims_draft"]
        reasons, missing, prov_notes = [], [], []
        txn = self._txn_percentile(p)
        if txn is None:
            has_txn = any(_has(p, s) for s in SIGNAL_REGISTRY["transaction"][:5])
            m = "交易类销量证据" if not has_txn else "组内销量分位（同组样本不足）"
            return self._mk(p, "hit", "PENDING_DATA", [f"缺：{m}"],
                            missing_fields=[m], refetch_tasks=tcfg["refetch_focus"])
        need = self._relax(p, adm.get("min_txn_percentile", 0.75))
        if txn < need:
            return self._mk(p, "hit", "REJECTED",
                            [f"销量分位 {txn:.2f} 低于爆款门限 {need:.2f}"],
                            evidence_refs=self._txn_ev(p))
        d = p.context.get("development_direction") or {}
        reasons.append(f"销量很高（组内分位 {txn:.2f} ≥ {need:.2f}）；"
                       f"可选开发方向：{'/'.join(d.get('options', ['铺货']))}")
        comps = [{"name": "需求规模", "score": round(dims["demand_scale"]
                                                 * min(1.0, txn + 0.1), 1),
                  "max": dims["demand_scale"]}]
        growth = _get(p, "market_metrics.sales_growth_rate")
        if growth is not None:
            sust = 0.8 if abs(growth) < 0.5 else 0.4   # 平稳优于暴涨暴跌（草案）
            comps.append({"name": "需求持续性", "score": round(
                dims["demand_sustainability"] * sust, 1),
                "max": dims["demand_sustainability"]})
        else:
            comps.append({"name": "需求持续性", "score": 0,
                          "max": dims["demand_sustainability"],
                          "note": "留槽：缺多周期销量序列"})
            missing.append("多周期销量序列")
        n_neg = len(p.product_opportunity.get("negative_review_clusters") or [])
        if "改款" in d.get("options", []):
            comps.append({"name": "改良空间(VOC)", "score": round(
                dims["voc_improvement_room"] * min(1.0, n_neg / 4), 1),
                "max": dims["voc_improvement_room"],
                **({} if n_neg else {"note": "留槽：待评论全文聚类回补"})})
            if not n_neg:
                missing.append("评论全文/负面聚类（改款方向核心证据）")
        else:
            comps.append({"name": "改良空间(VOC)", "score": 0,
                          "max": dims["voc_improvement_room"],
                          "note": "铺货方向不计改良空间"})
        comps.append({"name": "竞争与价格空间", "score": 0,
                      "max": dims["competition_price_room"],
                      "note": "留槽：L2/L3 竞品比对后计分"})
        comps.append({"name": "事实完整度", "score": round(
            dims["fact_completeness"] * min(1.0, len(p.evidence_pack) / 10), 1),
            "max": dims["fact_completeness"]})
        ev = self._txn_ev(p) + p.evidence_for("basic_facts.rating")
        return self._finalize(p, "hit", reasons, comps, ev, missing, prov_notes)

    def eval_long_tail_v2(self, p) -> TrackEvaluation:
        tcfg = self.cfg["tracks"]["long_tail"]
        adm, dims = tcfg["admission"], tcfg["scoring_dims_draft"]
        reasons, missing, prov_notes, risks = [], [], [], []
        has_txn = any(_has(p, s) for s in SIGNAL_REGISTRY["transaction"][:6])
        growth = _get(p, "market_metrics.sales_growth_rate")
        spike = growth is not None and growth >= adm["exclude_single_spike_growth"]
        if not has_txn:
            return self._mk(p, "long_tail", "PENDING_DATA",
                            ["缺交易类稳定需求证据（热度不计入）"],
                            missing_fields=["交易类稳定需求证据"],
                            refetch_tasks=tcfg["refetch_focus"])
        if spike:
            return self._mk(p, "long_tail", "PENDING_DATA",
                            [f"增长率 {growth:+.0%} 属单次暴涨，稳定性待多周期验证"],
                            missing_fields=["多周期需求稳定性证据"],
                            refetch_tasks=tcfg["refetch_focus"])
        reasons.append("存在交易类需求证据且非单次暴涨（销量表现稳定形态）")
        rating = _get(p, "basic_facts.rating")
        d = p.context.get("development_direction") or {}
        if (rating is not None and rating < adm["rating_floor"]
                and "改款" not in d.get("options", [])):
            return self._mk(p, "long_tail", "REJECTED",
                            [f"评分 {rating} 低于直接铺货下限且无改款方向"],
                            evidence_refs=p.evidence_for("basic_facts.rating"))
        if _get(p, "competition_metrics.seller_type") == "BRAND":
            risks.append("品牌自营，直接跟卖有 IP 风险（人工确认前不阻断）")
        price_missing = not _has(p, "basic_facts.price")
        if price_missing:
            if not adm.get("allow_missing_price"):
                return self._mk(p, "long_tail", "PENDING_DATA", ["缺价格与币种"],
                                missing_fields=["价格与币种"], risks=risks,
                                refetch_tasks=tcfg["refetch_focus"])
            missing.append("价格与币种（价格带匹配无法判断）")
            prov_notes.append("价格缺失（源无该点位）→ 占位准入，等级上限见占位规则")
        txn = self._txn_percentile(p) or 0.5
        if price_missing:
            pf = adm.get("missing_price_factor", 0.3)
        else:
            pf = 1.0 if self.stats.price_position(p) == "within_p25_p75" else 0.5
        comps = [
            {"name": "需求稳定性", "score": round(dims["demand_stability"]
                                             * min(1.0, txn + 0.3), 1),
             "max": dims["demand_stability"]},
            {"name": "竞争可突破", "score": round(dims["competition_breakthrough"] * 0.5, 1),
             "max": dims["competition_breakthrough"],
             "note": "中性占位0.5：竞争结构字段源缺失"},
            {"name": "价格带与利润结构" + ("(缺价格占位)" if price_missing else ""),
             "score": round(dims["price_profit_structure"] * pf, 1),
             "max": dims["price_profit_structure"]},
            {"name": "事实完整度", "score": round(
                dims["fact_completeness"] * min(1.0, len(p.evidence_pack) / 10), 1),
             "max": dims["fact_completeness"]},
            {"name": "直接承接可行性", "score": 0, "max": dims["direct_feasibility"],
             "note": "留槽：L2/L3 供应/成本事实回补后计分"},
        ]
        pen = self._risk_penalty(dims, risks)
        if pen:
            comps.append({"name": "风险扣减(草案)", "score": round(pen, 1),
                          "max": dims.get("risk_floor", 0), "note": "；".join(risks)[:120]})
        ev = p.evidence_for("basic_facts.price") + self._txn_ev(p)
        cap = adm.get("grade_cap_if_missing_price") if price_missing else None
        return self._finalize(p, "long_tail", reasons, comps, ev, missing,
                              prov_notes, risks=risks, extra_cap=cap)

    # ---------- 公共收尾（L1 基础分；等级在分层后定） ----------

    def _txn_ev(self, p) -> List[str]:
        ev = []
        for s in SIGNAL_REGISTRY["transaction"][:5]:
            ev += p.evidence_for(s)
        return ev

    def _finalize(self, p, tid, reasons, comps, ev, missing, prov_notes,
                  cluster_id=None, risks=None, extra_cap=None) -> TrackEvaluation:
        dims = self.cfg["tracks"][tid]["scoring_dims_draft"]
        base = sum(v for k, v in dims.items() if k != "risk_floor")
        score = sum(c["score"] for c in comps)
        pct = round(max(0.0, score) / base * 100, 1)
        provisional = bool(prov_notes)
        e = self._mk(p, tid, "ELIGIBLE",
                     reasons + (["临时规则准入（占位）"] if provisional else []),
                     grade=None, score=pct, components=comps,
                     evidence_refs=sorted(set(ev)), missing_fields=missing,
                     confidence="low" if provisional else "medium",
                     cluster_id=cluster_id, risks=risks or [],
                     admission_basis="provisional_rule" if provisional else "confirmed",
                     provisional_notes=prov_notes,
                     refetch_tasks=self.cfg["tracks"][tid]["refetch_focus"])
        e.legacy_grade = None
        e.extra_cap = extra_cap        # 占位上限（如缺价格 B），分层定级时生效
        return e

    def _word_rarity(self, p) -> Optional[float]:
        """特性词在 Amazon 语料中的稀有度 0-1（低频=新；无语料返回 None 留槽）。"""
        if not self.amazon_freq:
            return None
        words = title_feature_words(p.basic_facts.get("title"))
        if not words:
            return None
        hi = self.cfg.get("trend_words", {}).get("amazon_rarity_high_freq", 30)
        vals = [max(0.0, 1.0 - min(1.0, self.amazon_freq.get(w, 0) / hi))
                for w in words]
        return round(sum(vals) / len(vals), 3)

    # ---------- 赛道内 L1→L2→L3 分层淘汰与定级 ----------

    def _layer_and_grade(self, evaluations: List[TrackEvaluation]):
        caps = self.cfg.get("layers", {}).get("grade_layer_caps",
                                              {"L1": "B", "L2": "A", "L3": "S"})
        budgets = {}
        for tid in TRACK_IDS_V2:
            tcfg = self.cfg["tracks"][tid]
            eligible = sorted([e for e in evaluations
                               if e.track_id == tid and e.admission_status == "ELIGIBLE"],
                              key=lambda e: (-(e.score or 0), e.subject_id))
            l2 = eligible[: tcfg["l2_budget_draft"]]
            l3 = l2[: tcfg["l3_budget_draft"]]
            l2_ids, l3_ids = {e.subject_id for e in l2}, {e.subject_id for e in l3}
            for e in eligible:
                e.layer_reached = ("L3" if e.subject_id in l3_ids
                                   else "L2" if e.subject_id in l2_ids else "L1")
                cap = caps.get(e.layer_reached, "B")
                g = self._grade_from(tid, e.score or 0, cap=None)
                order = {"S": 0, "A": 1, "B": 2}
                if order[g] < order.get(cap, 2):
                    g = cap                     # 分层淘汰：未走完 L2/L3 不放开 A/S
                extra = getattr(e, "extra_cap", None)
                if extra and order[g] < order[extra]:
                    g = extra                   # 占位上限（如缺价格 B）
                e.grade = g
                e.legacy_grade = None
            pending = sorted([e for e in evaluations
                              if e.track_id == tid and e.admission_status == "PENDING_DATA"],
                             key=lambda e: (-len(e.evidence_refs), e.subject_id))
            prov_needy = [e for e in eligible
                          if e.admission_basis == "provisional_rule" and e.missing_fields]
            pool = prov_needy + pending
            budgets[tid] = {
                "l2_queue": sorted(l2_ids),
                "l3_queue": sorted(l3_ids),
                "refetch_queue": [
                    {"subject_id": e.subject_id, "missing": e.missing_fields,
                     "tasks": e.refetch_tasks, "status": "open", "owner": None,
                     "due": None,
                     "basis": ("provisional_eligible" if e.admission_status == "ELIGIBLE"
                               else "pending")}
                    for e in pool[: tcfg.get("refetch_budget_draft", 0)]],
            }
        return budgets

    # ---------- 趋势独立体系：词级 ABCDE ----------

    def eval_trend_words(self, packets: List[CandidateDataPacket]) -> List[Dict[str, Any]]:
        wcfg = self.cfg.get("trend_words", {})
        if not wcfg.get("enabled"):
            return []
        roles_req = set(wcfg.get("source_roles_required", ["discovery"]))
        stats: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"sites": set(), "n": 0, "new_arrival": 0, "content": 0,
                     "examples": [], "evidence_refs": []})
        for p in packets:
            roles = set(p.context.get("source_roles") or [])
            if not roles & roles_req:
                continue                       # 只针对头部独立站/社媒发现源
            grp = p.context.get("source_group") or p.platform
            is_new = p.freshness.get("freshness_status") in ("confirmed", "inferred")
            has_content = bool(p.context.get("trend_tags")
                               or _has(p, "market_metrics.video_views_max"))
            for w in set(title_feature_words(p.basic_facts.get("title"))):
                s = stats[w]
                s["sites"].add(grp)
                s["n"] += 1
                s["new_arrival"] += 1 if is_new else 0
                s["content"] += 1 if has_content else 0
                if len(s["examples"]) < 3:
                    s["examples"].append(p.candidate_id)
                s["evidence_refs"] = (s["evidence_refs"]
                                      + p.evidence_for("basic_facts.title"))[:6]
        sd = wcfg.get("scoring_draft", {})
        th = wcfg.get("grade_thresholds_draft", {"A": 75, "B": 60, "C": 40, "D": 20})
        hi = wcfg.get("amazon_rarity_high_freq", 30)
        max_sites = max((len(s["sites"]) for s in stats.values()), default=1)
        words = []
        for w, s in stats.items():
            if s["n"] < 2:
                continue
            breadth = len(s["sites"]) / max_sites
            new_share = s["new_arrival"] / s["n"]
            rarity = (max(0.0, 1.0 - min(1.0, self.amazon_freq.get(w, 0) / hi))
                      if self.amazon_freq else 0.0)
            content = min(1.0, s["content"] / max(1, s["n"]) * 2)
            score = round(breadth * sd.get("site_breadth", 40)
                          + new_share * sd.get("new_arrival_share", 25)
                          + rarity * sd.get("amazon_rarity", 20)
                          + content * sd.get("content_signal", 15), 1)
            grade = next((g for g in ("A", "B", "C", "D") if score >= th[g]), "E")
            words.append({
                "word": w, "score": score, "grade": grade,
                "sites": sorted(s["sites"]), "occurrences": s["n"],
                "new_arrival_share": round(new_share, 2),
                "amazon_freq": self.amazon_freq.get(w, 0) if self.amazon_freq else None,
                "content_signal": s["content"],
                "examples": s["examples"],
                "evidence_refs": s["evidence_refs"],
                "rule_status": "calibration_pending_business_approval",
                "missing_fields": [] if self.amazon_freq else ["Amazon语料（稀有度留槽）"],
                "note": "词级趋势对象（小红书源待接入，当前=头部独立站+TikTok信号）",
            })
        words.sort(key=lambda x: (-x["score"], x["word"]))
        return words[: wcfg.get("max_words", 200)]

    # ---------- 主入口 ----------

    def run_v2(self, packets: List[CandidateDataPacket]):
        self._peers = defaultdict(list)
        for p in packets:
            self._peers[p.context.get("source_group") or p.platform].append(p)
        for p in packets:
            assess_freshness(p)
        self._apply_business_confirmations(packets)
        from .track_eval import build_opportunity_clusters
        clusters = build_opportunity_clusters(packets, self.cfg, self.run_id)
        cluster_of = {}
        for c in clusters:
            for m in c["members"]:
                cluster_of.setdefault(m["candidate_id"], c["cluster_id"])
        evaluations: List[TrackEvaluation] = []
        directions: Dict[str, Any] = {}
        for p in packets:
            problems = self.l0_check(p)
            if problems:
                for tid in TRACK_IDS_V2:
                    evaluations.append(self._mk(p, tid, "REJECTED",
                                                ["L0 技术检查未通过：" + "；".join(problems)]))
                continue
            directions[p.candidate_id] = self.determine_direction(p)
            evaluations.append(self.eval_new_product(p, cluster_of.get(p.candidate_id)))
            evaluations.append(self.eval_hit(p))
            evaluations.append(self.eval_long_tail_v2(p))
        budgets = self._layer_and_grade(evaluations)
        words = self.eval_trend_words(packets)
        return evaluations, clusters, budgets, words, directions
