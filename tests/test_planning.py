from copy import deepcopy
import json
import math
import pytest
from resilient_water.demo import load_network, generate_scenarios, held_out_scenario
from resilient_water.quality import validate_data, validate_network, validate_scenarios
from resilient_water.engine import (optimize_preparedness_design, counterfactual_without_asset, optimize_emergency_operations,
                                    design_alternatives, dominates, scenario_conditions)
from resilient_water.reporting import build_preparedness_plan, export_preparedness_plan, network_figure


@pytest.fixture(scope="module")
def bundle():
    return build_preparedness_plan(load_network(), generate_scenarios(), 8e8)


def test_quality_zero_negative_original():
    raw = [dict(kind="rainfall_mm", value=0), dict(kind="rainfall_mm", value=-1), dict(kind="diameter_mm", value=0),
           dict(kind="rainfall_mm", value=10000), dict(kind="rainfall_mm", value=None)]
    original = deepcopy(raw)
    report = validate_data(raw)
    assert raw == original
    assert [r["quality"] for r in report] == ["PASS", "QUARANTINED", "QUARANTINED", "PASS", "QUARANTINED"]


def test_raw_cannot_bypass_treatment():
    n = load_network()
    n["candidates"][0].update(from_node="R1S")
    with pytest.raises(ValueError, match="mixing"):
        validate_network(n)


def test_candidate_gate_and_disconnected_region(bundle):
    for p in [bundle["baseline"], bundle["best"]]:
        for o in p["operations"]:
            for r in o["records"]:
                if r["kind"] == "flow" and r["asset_id"].startswith("C") and r["asset_id"] not in p["selected_assets"]:
                    assert r["value"] < 1e-5
    baseline = next(o for o in bundle["baseline"]["operations"] if o["scenario_id"] == "PIPE_BREAK")
    proposal = next(o for o in bundle["best"]["operations"] if o["scenario_id"] == "PIPE_BREAK")
    assert proposal["shortfall_m3"] < baseline["shortfall_m3"]
    assert any(r["kind"] == "flow" and r["asset_id"] in ["C1", "C2"] and r["value"] > 1e-5 for r in proposal["records"])


def test_insufficient_budget():
    n = load_network()
    p = optimize_preparedness_design(n, n["candidates"], generate_scenarios(), 1)
    assert p["selected_assets"] == [] and p["capex_estimate"] == 0


def test_budget_and_independent_balance_validation(bundle):
    for p in bundle["alternatives"]:
        assert p["capex_estimate"] <= p["budget"] + 1e-5
        assert all(o["validation"]["passed"] for o in p["operations"])
        assert all(o["validation"]["max_mass_balance_residual_m3"] < 1e-4 for o in p["operations"])


def test_extreme_essential_shortfall_exact(bundle):
    p = bundle["best"]
    s = held_out_scenario()
    op = optimize_emergency_operations(p, s)
    nodes = {v["id"]: v for v in p["input"]["network"]["nodes"]}
    expected = sum(max(0, nodes[r["asset_id"]]["essential_demand_m3_day"]
                       * scenario_conditions(s, r["day"], p["prep_days"])["demand_factor"] - r["value"])
                   for r in op["records"] if r["kind"] == "supply")
    assert expected > 0
    assert op["essential_shortfall_m3"] == pytest.approx(expected, abs=1e-4)


def test_counterfactual_is_reoptimized_and_repeatable(bundle):
    p = bundle["best"]
    aid = p["selected_assets"][0]
    x = counterfactual_without_asset(p, aid)
    y = counterfactual_without_asset(p, aid)
    assert aid not in x["selected_assets"]
    assert x["run_id"] != p["run_id"] and x["run_id"] == y["run_id"]
    assert x["worst_essential_m3_day"] == pytest.approx(y["worst_essential_m3_day"], abs=1e-5)
    assert x["delta_worst_essential_m3_day"] >= -1e-4


def test_explanation_numbers_and_evidence(bundle):
    p = bundle["best"]
    for e in bundle["explanations"]:
        assert f"{p['worst_essential_m3_day']:.6f}" in e["text"]
        assert f"{e['counterfactual']['worst_essential_m3_day']:.6f}" in e["text"]
        assert e["run_id"] == p["run_id"] and p["run_id"] in e["evidence_refs"]["proposal"]


def test_synthetic_visual_and_no_fake_geometry(bundle):
    fig = network_figure(load_network(), bundle["best"]["selected_assets"])
    assert "실제 지리" in fig.layout.title.text
    geo = json.loads(export_preparedness_plan(bundle, "geojson"))
    assert all(f["geometry"] is None for f in geo["features"])


def test_no_false_hydraulic_validation(bundle):
    assert bundle["best"]["status"] == "GRAPH_FEASIBLE"
    assert bundle["best"]["hydraulic_status"] == "NOT_VALIDATED"
    assert not any(o["validation"]["hydraulic_validation"] for o in bundle["best"]["operations"])


def test_scenario_change_recalculates(bundle):
    n = load_network()
    s = generate_scenarios()
    s[-1]["source_factor"] = 0
    p = optimize_preparedness_design(n, n["candidates"], s, 8e8)
    assert p["run_id"] != bundle["best"]["run_id"]
    assert p["worst_shortfall_m3_day"] > bundle["best"]["worst_shortfall_m3_day"]


def test_no_temporal_learning_leakage():
    # No predictive model, observation ingestion or training exists. Scenarios are explicit assumptions.
    assert generate_scenarios(seed=13) == generate_scenarios(seed=13)
    assert all("probability" not in s for s in generate_scenarios())


def test_reproducible(bundle):
    n = load_network()
    p = optimize_preparedness_design(n, n["candidates"], generate_scenarios(), 8e8)
    assert p["run_id"] == bundle["best"]["run_id"]
    assert p["selected_assets"] == bundle["best"]["selected_assets"]
    assert p["worst_shortfall_m3_day"] == pytest.approx(bundle["best"]["worst_shortfall_m3_day"], abs=1e-5)


def test_three_distinct_non_dominated_alternatives_and_exports(bundle):
    alts = bundle["alternatives"]
    assert len({tuple(p["selected_assets"]) for p in alts}) == len(alts) == 3
    for i, p in enumerate(alts):
        assert not any(dominates(q, p) for q in alts[:i])
    for d in bundle["dominated"]:
        assert d["dominated_by"] in {p["plan_id"] for p in alts}
    for fmt in ["json", "csv", "html", "geojson"]:
        assert len(export_preparedness_plan(bundle, fmt)) > 100
    assert json.loads(export_preparedness_plan(bundle, "json"))["best"]["run_id"] == bundle["best"]["run_id"]


def test_dominated_superset_is_not_an_alternative():
    # v0.1 listed C2+C3+C4 (8억) next to C2+C4 (6억) with identical performance.
    n = load_network()
    alts, dominated = design_alternatives(n, generate_scenarios(), 8e8)
    designs = [set(p["selected_assets"]) for p in alts]
    assert {"C2", "C3", "C4"} not in designs
    assert any(set(d["selected_assets"]) == {"C2", "C3", "C4"} for d in dominated)


def test_permit_and_exclusive_constraints():
    n = load_network()
    n["candidates"][0]["permit_status"] = "DENIED"
    for a in n["candidates"][1:3]:
        a["exclusive_group"] = "G1"
    p = optimize_preparedness_design(n, n["candidates"], generate_scenarios(), 20e8)
    assert "C1" not in p["selected_assets"]
    assert len(set(p["selected_assets"]) & {"C2", "C3"}) <= 1


def test_negative_and_missing_input_rejected_as_value_error():
    n = load_network()
    n["candidates"][0]["capex_estimate"] = -1
    with pytest.raises(ValueError):
        validate_network(n)
    for bad in [None, float("nan"), "12"]:
        n = load_network()
        n["candidates"][0]["capacity"] = bad
        with pytest.raises(ValueError):
            validate_network(n)
    n = load_network()
    n["candidates"][0]["to_node"] = "NOPE"
    with pytest.raises(ValueError):
        validate_network(n)
    n = load_network()
    n["candidates"][0]["to_node"] = n["candidates"][0]["from_node"]
    with pytest.raises(ValueError, match="Self-loop"):
        validate_network(n)


def test_scenario_validation():
    n = load_network()
    s = generate_scenarios()
    s[0]["failed_sources"] = ["R1B"]
    with pytest.raises(ValueError, match="source node"):
        validate_scenarios(n, s, 1)
    s = generate_scenarios()
    s[0]["initial_storage"] = {"R1B": 20}
    with pytest.raises(ValueError, match="prep_days=0"):
        validate_scenarios(n, s, 1)
    validate_scenarios(n, s, 0)


def test_operations_cannot_add_investment(bundle):
    p = bundle["best"]
    op = optimize_emergency_operations(p, held_out_scenario())
    for r in op["records"]:
        if r["kind"] == "flow" and r["asset_id"].startswith("C") and r["asset_id"] not in p["selected_assets"]:
            assert r["value"] < 1e-5


def test_baseline_forbids_even_free_new_assets():
    n = load_network()
    for a in n["candidates"]:
        a["capex_estimate"] = 0
    bundle = build_preparedness_plan(n, generate_scenarios(), 0, with_policies=False)
    assert bundle["baseline"]["selected_assets"] == []


def test_treatment_capacity_is_not_bypassed():
    n = load_network()
    next(v for v in n["nodes"] if v["id"] == "R1T")["treatment_capacity_m3_day"] = 1
    p = optimize_preparedness_design(n, n["candidates"], generate_scenarios(), 8e8)
    assert all(r["value"] <= 1 + 1e-5 for o in p["operations"] for r in o["records"] if r["kind"] == "flow" and r["asset_id"] == "R1-process")


# ---------------------------------------------------------------- v0.2 regressions

@pytest.mark.parametrize("days", [1, 7, 14, 30])
def test_long_horizons_solve(days):
    # v0.1 failed with "infeasible" at 14 days because of an absolute 1e-6 stage tolerance.
    n = load_network()
    p = optimize_preparedness_design(n, n["candidates"], generate_scenarios(days=days), 8e8)
    assert p["status"] == "GRAPH_FEASIBLE"


@pytest.mark.parametrize("days", [1, 3, 7, 14])
def test_normal_scenario_has_no_shortfall(days):
    n = load_network()
    p = optimize_preparedness_design(n, n["candidates"], generate_scenarios(days=days), 8e8)
    normal = next(o for o in p["operations"] if o["scenario_id"] == "NORMAL")
    assert normal["shortfall_m3"] == pytest.approx(0, abs=1e-6)


def test_prep_days_are_non_anticipative_and_serve_normal_demand(bundle):
    p = bundle["best"]
    assert p["prep_days"] == 1 and p["nonanticipativity_max_gap"] < 1e-6
    for o in p["operations"]:
        assert o["prep_shortfall_m3"] == pytest.approx(0, abs=1e-6)
    # Different scenarios share one prep-day storage plan.
    assert len({json.dumps(o["storage_after_prep_m3"], sort_keys=True) for o in p["operations"]}) == 1


def test_unused_storage_is_kept():
    n = load_network()
    p = optimize_preparedness_design(n, n["candidates"], generate_scenarios(), 8e8)
    normal = next(o for o in p["operations"] if o["scenario_id"] == "NORMAL")
    start = sum(v["storage_m3"] for v in n["nodes"] if v["type"] == "reservoir")
    assert sum(normal["terminal_storage_m3"].values()) >= start - 1e-6


def test_equity_stage_lowers_worst_regional_ratio(bundle):
    p = bundle["best"]
    worst = max(max(o["regional_shortfall_ratio"].values()) for o in p["operations"])
    assert worst == pytest.approx(p["worst_regional_shortfall_ratio"], abs=1e-6)
    assert p["worst_regional_shortfall_ratio"] <= bundle["baseline"]["worst_regional_shortfall_ratio"] + 1e-9


def test_unbounded_budget_with_fixed_design():
    n = load_network()
    p = optimize_preparedness_design(n, n["candidates"], generate_scenarios(), math.inf, fixed=["C2"])
    assert p["selected_assets"] == ["C2"]
    json.dumps(p["input"], allow_nan=False)
