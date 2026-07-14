# -*- coding: utf-8 -*-
"""端到端集成测试：消费仓库内真实数据源工作簿。

这些测试依赖仓库根目录的 `02.插件数据源/` 与 `03.前端数据源/`。
"""
import json
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "02.插件数据源").exists(),
    reason="真实数据源不在仓库中")


def _missing_workbooks():
    """sources_p0.yaml 里配置但当前环境缺失的工作簿（跨环境回放常见）。"""
    import yaml
    cfg = yaml.safe_load((PROJECT_DIR / "configs/sources_p0.yaml")
                         .read_text(encoding="utf-8"))
    books = [spec["workbook"]
             for key in ("plugin_sources", "frontend_workbooks")
             for spec in cfg.get(key, []) or []]
    return [b for b in books if not (REPO_ROOT / b).exists()]


@pytest.fixture(scope="module")
def pipeline_output(tmp_path_factory):
    missing = _missing_workbooks()
    if missing:
        # 明确跳过而非报错（P0-07）：换环境回放时数据文件不全属于环境问题，
        # 不是代码缺陷；严格回放请使用不可变基线目录并核对文件清单（docs/07）
        pytest.skip("数据源工作簿缺失，端到端测试跳过：" + "；".join(missing))
    from grading_system.pipeline import run_pipeline
    out_dir = tmp_path_factory.mktemp("run")
    return run_pipeline(
        repo_root=REPO_ROOT, out_dir=out_dir,
        sources_cfg_path=PROJECT_DIR / "configs/sources_p0.yaml",
        scoring_cfg_path=PROJECT_DIR / "configs/scoring_v0.yaml",
        gates_cfg_path=PROJECT_DIR / "configs/layer_gates_v0.yaml"), out_dir


class TestEndToEnd:
    def test_candidate_count(self, pipeline_output):
        out, _ = pipeline_output
        assert len(out["packets"]) > 2000   # 全量候选进入 L1

    def test_p0_minimum_three_packets(self, pipeline_output):
        _, out_dir = pipeline_output
        samples = list((out_dir / "candidate_packets").glob("*.json"))
        assert len([s for s in samples if not s.name.endswith(".analysis.json")]) >= 3

    def test_cross_source_merge_oeak(self, pipeline_output):
        """OEAK 文胸必须被 Kalodata × Tabcut 双源合并。"""
        out, _ = pipeline_output
        p = next(p for p in out["packets"]
                 if p.candidate_id == "tiktok_shop_1731406217255686451")
        sources = {s["source_id"] for s in p.source_refs}
        assert {"kalodata", "tabcut"} <= sources

    def test_echotik_prefix_merge(self, pipeline_output):
        """EchoTik 截断 id 必须前缀合并到 Tabcut 完整 id（medicube）。"""
        out, _ = pipeline_output
        p = next(p for p in out["packets"]
                 if p.candidate_id == "tiktok_shop_1729508370969629931")
        sources = {s["source_id"] for s in p.source_refs}
        assert {"tabcut", "echotik"} <= sources
        assert any(i.issue == "id_truncated_in_source" for i in p.field_issues)

    def test_every_grade_s_is_multi_source(self, pipeline_output):
        out, _ = pipeline_output
        by_id = {p.candidate_id: p for p in out["packets"]}
        for r in out["results"].values():
            if r.priority_score["grade"] == "S":
                assert by_id[r.candidate_id].source_count >= 2

    def test_claims_all_have_evidence(self, pipeline_output):
        out, _ = pipeline_output
        for r in out["results"].values():
            for c in r.claims:
                assert c.evidence_refs

    def test_missing_fields_never_zero_filled(self, pipeline_output):
        """无供应链输入 -> 全部候选的 owned_supply_inputs 保持 null。"""
        out, _ = pipeline_output
        for p in out["packets"][:50]:
            assert p.owned_supply_inputs["moq"] is None
            assert "owned_supply_inputs.moq" in p.missing_fields

    def test_prices_always_have_currency(self, pipeline_output):
        out, _ = pipeline_output
        for p in out["packets"]:
            price = p.basic_facts.get("price")
            if price is not None:
                assert price.get("currency"), f"{p.candidate_id} 价格缺币种"

    def test_l2_l3_cost_caps(self, pipeline_output):
        out, _ = pipeline_output
        assert len(out["l2_ids"]) <= 40
        assert len(out["l3_ids"]) <= 10
        # L3 是 L2 的子集
        assert set(out["l3_ids"]) <= set(out["l2_ids"])

    def test_xlsx_written_with_all_sheets(self, pipeline_output):
        _, out_dir = pipeline_output
        from openpyxl import load_workbook
        wb = load_workbook(out_dir / "选品推荐表.xlsx", read_only=True)
        expected = {"推荐总表", "评分明细", "数据源覆盖", "差评改良",
                    "多平台比对", "风险清单", "证据索引", "运行记录"}
        assert expected <= set(wb.sheetnames)
        wb.close()

    def test_no_credentials_in_outputs(self, pipeline_output):
        """输出中不得出现明文 token/cookie/Auth-Token。"""
        _, out_dir = pipeline_output
        text = (out_dir / "run_meta.json").read_text(encoding="utf-8").lower()
        for kw in ("auth-token", "cookie:", "bearer "):
            assert kw not in text

    def test_envelope_json_serializable(self, pipeline_output):
        _, out_dir = pipeline_output
        for f in (out_dir / "envelopes").glob("*.json"):
            data = json.loads(f.read_text(encoding="utf-8"))
            assert data["schema_version"] == "source.envelope.v1"
            assert data["market"] == "US"
