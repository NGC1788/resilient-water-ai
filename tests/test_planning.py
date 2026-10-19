from copy import deepcopy
import json
import pytest
from resilient_water.demo import load_network,generate_scenarios,held_out_scenario
from resilient_water.quality import validate_data,validate_network
from resilient_water.engine import optimize_preparedness_design,counterfactual_without_asset,optimize_emergency_operations,digest
from resilient_water.reporting import build_preparedness_plan,export_preparedness_plan,network_figure


@pytest.fixture(scope="module")
def bundle(): return build_preparedness_plan(load_network(),generate_scenarios(),8e8)


def test_quality_zero_negative_original():
    raw=[dict(kind="rainfall_mm",value=0),dict(kind="rainfall_mm",value=-1),dict(kind="diameter_mm",value=0),dict(kind="rainfall_mm",value=10000)]
    original=deepcopy(raw); report=validate_data(raw)
    assert raw==original
    assert [r["quality"] for r in report]==["PASS","QUARANTINED","QUARANTINED","PASS"]


def test_raw_cannot_bypass_treatment():
    n=load_network(); n["candidates"][0].update(from_node="R1S")
    with pytest.raises(ValueError,match="mixing"): validate_network(n)


def test_candidate_gate_and_disconnected_region(bundle):
    for p in [bundle["baseline"],bundle["best"]]:
        for o in p["operations"]:
            for r in o["records"]:
                if r["kind"]=="flow" and r["asset_id"].startswith("C") and r["asset_id"] not in p["selected_assets"]: assert r["value"]<1e-5
    baseline=next(o for o in bundle["baseline"]["operations"] if o["scenario_id"]=="PIPE_BREAK")
    proposal=next(o for o in bundle["best"]["operations"] if o["scenario_id"]=="PIPE_BREAK")
    assert proposal["shortfall_m3"]<baseline["shortfall_m3"]
    assert any(r["kind"]=="flow" and r["asset_id"] in ["C1","C2"] and r["value"]>1e-5 for r in proposal["records"])


def test_insufficient_budget():
    n=load_network(); p=optimize_preparedness_design(n,n["candidates"],generate_scenarios(),1)
    assert p["selected_assets"]==[] and p["capex_estimate"]==0


def test_budget_and_independent_balance_validation(bundle):
    for p in bundle["alternatives"]:
        assert p["capex_estimate"]<=p["budget"]+1e-5
        assert all(o["validation"]["passed"] for o in p["operations"])
        assert all(o["validation"]["max_mass_balance_residual_m3"]<1e-5 for o in p["operations"])


def test_extreme_essential_shortfall_exact(bundle):
    p=bundle["best"]; s=held_out_scenario(); op=optimize_emergency_operations(p,s)
    nodes={v["id"]:v for v in p["input"]["network"]["nodes"]}
    expected=sum(max(0,nodes[r["asset_id"]]["essential_demand_m3_day"]*s["demand_factor"]-r["value"]) for r in op["records"] if r["kind"]=="supply")
    assert expected>0
    assert op["essential_shortfall_m3"]==pytest.approx(expected,abs=1e-4)


def test_counterfactual_is_reoptimized_and_repeatable(bundle):
    p=bundle["best"]; aid=p["selected_assets"][0]
    x=counterfactual_without_asset(p,aid); y=counterfactual_without_asset(p,aid)
    assert aid not in x["selected_assets"]
    assert x["run_id"]!=p["run_id"] and x["run_id"]==y["run_id"]
    assert x["worst_essential_m3_day"]==pytest.approx(y["worst_essential_m3_day"],abs=1e-5)
    assert x["delta_worst_essential_m3_day"]>=-1e-4


def test_explanation_numbers_and_evidence(bundle):
    p=bundle["best"]
    for e in bundle["explanations"]:
        assert f"{p['worst_essential_m3_day']:.6f}" in e["text"]
        assert f"{e['counterfactual']['worst_essential_m3_day']:.6f}" in e["text"]
        assert e["run_id"]==p["run_id"] and p["run_id"] in e["evidence_refs"]["proposal"]


def test_synthetic_visual_and_no_fake_geometry(bundle):
    fig=network_figure(load_network(),bundle["best"]["selected_assets"])
    assert "실제 지리" in fig.layout.title.text
    geo=json.loads(export_preparedness_plan(bundle,"geojson"))
    assert all(f["geometry"] is None for f in geo["features"])


def test_no_false_hydraulic_validation(bundle):
    assert bundle["best"]["status"]=="GRAPH_FEASIBLE"
    assert bundle["best"]["hydraulic_status"]=="NOT_VALIDATED"
    assert not any(o["validation"]["hydraulic_validation"] for o in bundle["best"]["operations"])


def test_scenario_change_recalculates(bundle):
    n=load_network(); s=generate_scenarios(); s[-1]["source_factor"]=0
    p=optimize_preparedness_design(n,n["candidates"],s,8e8)
    assert p["run_id"]!=bundle["best"]["run_id"]
    assert p["worst_essential_m3_day"]>bundle["best"]["worst_essential_m3_day"]


def test_no_temporal_learning_leakage():
    # No predictive model, observation ingestion or training exists in P0.
    # Scenario generator consumes only explicit planning assumptions.
    assert generate_scenarios(seed=13)==generate_scenarios(seed=13)
    assert all("probability" not in s for s in generate_scenarios())


def test_reproducible(bundle):
    n=load_network(); p=optimize_preparedness_design(n,n["candidates"],generate_scenarios(),8e8)
    assert p["run_id"]==bundle["best"]["run_id"]
    assert p["selected_assets"]==bundle["best"]["selected_assets"]
    assert p["worst_shortfall_m3_day"]==pytest.approx(bundle["best"]["worst_shortfall_m3_day"],abs=1e-5)


def test_three_distinct_alternatives_and_exports(bundle):
    assert len({tuple(p["selected_assets"]) for p in bundle["alternatives"]})==3
    for fmt in ["json","csv","html","geojson"]: assert len(export_preparedness_plan(bundle,fmt))>100
    assert json.loads(export_preparedness_plan(bundle,"json"))["best"]["run_id"]==bundle["best"]["run_id"]


def test_permit_and_exclusive_constraints():
    n=load_network(); n["candidates"][0]["permit_status"]="DENIED"
    for a in n["candidates"][1:3]: a["exclusive_group"]="G1"
    p=optimize_preparedness_design(n,n["candidates"],generate_scenarios(),20e8)
    assert "C1" not in p["selected_assets"]
    assert len(set(p["selected_assets"])&{"C2","C3"})<=1


def test_negative_input_rejected():
    n=load_network(); n["candidates"][0]["capex_estimate"]=-1
    with pytest.raises(ValueError): validate_network(n)


def test_operations_cannot_add_investment(bundle):
    p=bundle["best"]; op=optimize_emergency_operations(p,held_out_scenario())
    for r in op["records"]:
        if r["kind"]=="flow" and r["asset_id"].startswith("C") and r["asset_id"] not in p["selected_assets"]: assert r["value"]<1e-5


def test_baseline_forbids_even_free_new_assets():
    n=load_network()
    for a in n["candidates"]: a["capex_estimate"]=0
    bundle=build_preparedness_plan(n,generate_scenarios(),0)
    assert bundle["baseline"]["selected_assets"]==[]


def test_treatment_capacity_is_not_bypassed():
    n=load_network(); next(v for v in n["nodes"] if v["id"]=="R1T")["treatment_capacity_m3_day"]=1
    p=optimize_preparedness_design(n,n["candidates"],generate_scenarios(),8e8)
    assert all(r["value"]<=1+1e-5 for o in p["operations"] for r in o["records"] if r["kind"]=="flow" and r["asset_id"]=="R1-process")
